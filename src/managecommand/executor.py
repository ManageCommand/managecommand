"""
Command execution with output streaming and per-line timestamps.

Provides:
- LineBuffer: Tracks line boundaries and timestamps for stdout/stderr
- OutputStreamManager: Coordinates time-based flushing of output to server
- CommandExecutor: Runs Django management commands via subprocess
"""

import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import ManageCommandClient

from .client import AuthenticationError

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of a command execution."""

    exit_code: int
    status: str  # 'success', 'failed', 'cancelled', 'timed_out'


class LineBuffer:
    """
    Buffer that tracks line boundaries and timestamps.

    Each line gets a timestamp when its first character arrives.
    Partial lines (no trailing newline) are tracked for continuation.
    """

    def __init__(self, is_stderr: bool):
        self.is_stderr = is_stderr
        self.completed_lines: list[tuple[float, str]] = []  # (timestamp, content)
        self.current_line_timestamp: float | None = None
        self.current_line_content: str = ""
        self.lock = threading.Lock()

    def append(self, content: str):
        """
        Append content, tracking line start timestamps.

        Each new line (after a newline or at the start) gets a timestamp.
        """
        with self.lock:
            for char in content:
                if self.current_line_timestamp is None:
                    self.current_line_timestamp = time.time()
                self.current_line_content += char
                if char == "\n":
                    self.completed_lines.append(
                        (self.current_line_timestamp, self.current_line_content)
                    )
                    self.current_line_timestamp = None
                    self.current_line_content = ""

    def flush(self) -> list[dict]:
        """
        Return segments and reset buffer.

        Returns list of dicts with 'timestamp' and 'content' keys.
        timestamp=None means continuation of previous line (same stream).
        """
        with self.lock:
            segments = []

            # Add completed lines with their timestamps
            for ts, content in self.completed_lines:
                segments.append({"timestamp": ts, "content": content})
            self.completed_lines = []

            # Add partial line if any
            if self.current_line_content:
                segments.append(
                    {
                        "timestamp": self.current_line_timestamp,
                        "content": self.current_line_content,
                    }
                )
                # Clear content but keep timestamp as None for next append
                # (next content will be continuation - no new timestamp until \n)
                self.current_line_timestamp = None
                self.current_line_content = ""

            return segments


class OutputStreamManager:
    """
    Coordinates time-based flushing of output to server.

    Manages separate LineBuffers for stdout and stderr.
    Flushes both buffers periodically and sends chunks to server.
    """

    def __init__(
        self,
        client: "ManageCommandClient",
        execution_id: str,
        flush_interval: float = 1.5,
        auth_check: Callable[[], bool] | None = None,
        on_auth_error: Callable[[Exception], None] | None = None,
    ):
        self.client = client
        self.execution_id = execution_id
        self.stdout_buffer = LineBuffer(is_stderr=False)
        self.stderr_buffer = LineBuffer(is_stderr=True)
        self.chunk_number = 0
        self.flush_interval = flush_interval
        self.running = True
        self._flush_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._auth_check = auth_check
        self._on_auth_error = on_auth_error

    def append(self, content: str, is_stderr: bool):
        """Route content to appropriate buffer."""
        if is_stderr:
            self.stderr_buffer.append(content)
        else:
            self.stdout_buffer.append(content)

    def start_flush_loop(self):
        """Start background thread that flushes every flush_interval."""
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()

    def _flush_loop(self):
        """Background thread that flushes buffers periodically."""
        while self.running:
            time.sleep(self.flush_interval)
            self._flush()

    def _flush(self):
        """Flush both buffers, send as separate chunks."""
        # Always drain buffers to free memory
        stdout_segments = self.stdout_buffer.flush()
        stderr_segments = self.stderr_buffer.flush()

        # Skip network call if auth not valid
        if self._auth_check and not self._auth_check():
            return  # Discard segments, no network call

        # Send stdout
        if stdout_segments:
            if not self._send_chunk(stdout_segments, is_stderr=False):
                return  # Auth error, don't try stderr either

        # Send stderr
        if stderr_segments:
            self._send_chunk(stderr_segments, is_stderr=True)

    def _send_chunk(self, segments: list[dict], is_stderr: bool) -> bool:
        """
        Send a single chunk to the server.

        Returns True on success, False if auth error (caller should stop).
        """
        with self._lock:
            self.chunk_number += 1
            chunk_num = self.chunk_number

        try:
            self.client.send_output(self.execution_id, segments, is_stderr, chunk_num)
            return True
        except AuthenticationError as err:
            if self._on_auth_error:
                self._on_auth_error(err)
            return False
        except Exception as err:
            logger.warning(f"Failed to send output chunk {chunk_num}: {err}")
            return True  # Continue trying for non-auth errors

    def finalize(self):
        """Final flush and stop flush loop."""
        self.running = False
        if self._flush_thread and self._flush_thread.is_alive():
            self._flush_thread.join(timeout=2.0)
        self._flush()  # Final flush to send any remaining content


class CommandExecutor:
    """
    Executes Django management commands via subprocess.

    Streams output through OutputStreamManager for real-time delivery.
    Supports cancellation via SIGTERM/SIGKILL.
    """

    GRACE_PERIOD = 5.0  # Seconds to wait after SIGTERM before SIGKILL

    def __init__(
        self,
        project_path: str,
        client: "ManageCommandClient",
        auth_check: Callable[[], bool] | None = None,
        on_auth_error: Callable[[Exception], None] | None = None,
    ):
        self.project_path = project_path
        self.client = client
        self.process: subprocess.Popen | None = None
        self.cancelled = False
        self._cancel_lock = threading.Lock()
        self._auth_check = auth_check
        self._on_auth_error = on_auth_error

    def execute(
        self,
        execution_id: str,
        command: str,
        args: str,
        timeout: int,
        metadata_only: bool = False,
    ) -> ExecutionResult:
        """
        Run a Django management command via subprocess.

        Args:
            execution_id: Server execution ID for output streaming
            command: Django management command name
            args: Command arguments as string
            timeout: Maximum execution time in seconds
            metadata_only: If True, do not capture stdout/stderr (send simulated output instead)

        Returns:
            ExecutionResult with exit_code and status
        """
        self.cancelled = False

        # Build command using same Python interpreter as runner
        # Use -u flag for unbuffered output (instead of env var)
        manage_py = os.path.join(self.project_path, "manage.py")
        cmd = [sys.executable, "-u", manage_py, command]
        if args:
            # Split args string into list (handle quoted strings properly)
            import shlex

            cmd.extend(shlex.split(args))

        logger.info(f"Executing: {' '.join(cmd)}")
        if metadata_only:
            logger.info("Running in metadata-only mode (no output capture)")
        logger.debug(f"Working directory: {self.project_path}")
        logger.debug(
            f"DJANGO_SETTINGS_MODULE: {os.environ.get('DJANGO_SETTINGS_MODULE', 'not set')}"
        )

        if metadata_only:
            return self._execute_metadata_only(cmd, timeout)
        else:
            return self._execute_with_streaming(execution_id, cmd, timeout)

    def _execute_metadata_only(
        self,
        cmd: list[str],
        timeout: int,
    ) -> ExecutionResult:
        """
        Execute command without capturing output.

        In metadata-only mode, stdout/stderr go to /dev/null.
        Only exit code and timing are captured.
        """
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=self.project_path,
            )

            # Wait for process with timeout
            try:
                exit_code = self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.warning(f"Command timed out after {timeout}s")
                self._kill_process()
                return ExecutionResult(exit_code=-1, status="timed_out")

            # Check if cancelled
            with self._cancel_lock:
                if self.cancelled:
                    return ExecutionResult(exit_code=exit_code, status="cancelled")

            # Determine status
            status = "success" if exit_code == 0 else "failed"
            return ExecutionResult(exit_code=exit_code, status=status)

        except Exception as err:
            logger.exception(f"Execution failed: {err}")
            return ExecutionResult(exit_code=-1, status="failed")

        finally:
            self.process = None

    def _execute_with_streaming(
        self,
        execution_id: str,
        cmd: list[str],
        timeout: int,
    ) -> ExecutionResult:
        """
        Execute command with output streaming.

        Standard execution mode with real-time output capture and streaming.
        """
        # Set up output streaming
        stream_manager = OutputStreamManager(
            client=self.client,
            execution_id=execution_id,
            auth_check=self._auth_check,
            on_auth_error=self._on_auth_error,
        )

        try:
            # Don't pass env= explicitly - let subprocess inherit parent's
            # environment naturally. This ensures all shell env vars are passed.
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.project_path,
                text=True,
                bufsize=1,  # Line buffered
            )

            # Start output streaming
            stream_manager.start_flush_loop()

            # Start reader threads
            stdout_thread = threading.Thread(
                target=self._read_stream,
                args=(self.process.stdout, False, stream_manager),
                daemon=True,
            )
            stderr_thread = threading.Thread(
                target=self._read_stream,
                args=(self.process.stderr, True, stream_manager),
                daemon=True,
            )
            stdout_thread.start()
            stderr_thread.start()

            # Wait for process with timeout
            try:
                exit_code = self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.warning(f"Command timed out after {timeout}s")
                self._kill_process()
                stream_manager.finalize()
                return ExecutionResult(exit_code=-1, status="timed_out")

            # Wait for reader threads to finish
            stdout_thread.join(timeout=2.0)
            stderr_thread.join(timeout=2.0)

            # Finalize output streaming
            stream_manager.finalize()

            # Check if cancelled
            with self._cancel_lock:
                if self.cancelled:
                    return ExecutionResult(exit_code=exit_code, status="cancelled")

            # Determine status
            status = "success" if exit_code == 0 else "failed"
            return ExecutionResult(exit_code=exit_code, status=status)

        except Exception as err:
            logger.exception(f"Execution failed: {err}")
            stream_manager.finalize()
            return ExecutionResult(exit_code=-1, status="failed")

        finally:
            self.process = None

    def _read_stream(
        self, stream, is_stderr: bool, stream_manager: OutputStreamManager
    ):
        """Read from a stream and send to stream manager."""
        try:
            for line in stream:
                stream_manager.append(line, is_stderr)
        except Exception as err:
            logger.warning(f"Stream read error: {err}")

    def cancel(self, force: bool = False):
        """
        Request cancellation of the running command.

        Args:
            force: If True, use SIGKILL immediately. Otherwise SIGTERM first.
        """
        with self._cancel_lock:
            self.cancelled = True

        if self.process is None:
            return

        if force:
            self._kill_process(sigkill=True)
        else:
            self._kill_process(sigkill=False)

    def _kill_process(self, sigkill: bool = False):
        """Kill the process, optionally with SIGKILL."""
        if self.process is None:
            return

        try:
            if sigkill:
                logger.info("Sending SIGKILL")
                self.process.kill()
            else:
                logger.info("Sending SIGTERM")
                self.process.terminate()

                # Wait for graceful termination
                try:
                    self.process.wait(timeout=self.GRACE_PERIOD)
                except subprocess.TimeoutExpired:
                    logger.warning("Process did not terminate, sending SIGKILL")
                    self.process.kill()
                    self.process.wait(timeout=1.0)
        except ProcessLookupError:
            pass  # Process already dead
