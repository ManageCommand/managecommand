"""
DjangoCommand Agent core.

The agent maintains a heartbeat with the server, syncs commands,
and executes pending commands with output streaming.
"""

import logging
import os
import platform
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import django

from .client import DjangoCommandClient, DjangoCommandClientError
from .config import AgentConfig, load_config
from .discovery import get_commands_with_hash
from .executor import CommandExecutor, ExecutionResult
from .security import get_disallowed_commands, is_command_allowed

logger = logging.getLogger(__name__)

# Package version
try:
    from . import __version__
except ImportError:
    __version__ = 'unknown'


class Agent:
    """
    DjangoCommand agent that maintains connection with server.

    Responsibilities:
    - Heartbeat every N seconds
    - Sync commands when hash changes
    - Poll for and execute pending commands
    - Graceful shutdown on SIGTERM/SIGINT
    """

    # Exclude our own command to avoid recursion
    EXCLUDED_COMMANDS = ['djangocommand']

    # Execution settings
    MAX_CONCURRENT_EXECUTIONS = 4
    EXECUTION_POLL_INTERVAL = 2.0  # seconds

    def __init__(self, config: AgentConfig):
        self.config = config
        self.client = DjangoCommandClient(
            server_url=config.server_url,
            api_key=config.api_key,
            timeout=config.request_timeout,
            max_retries=config.max_retries,
            allow_http_hosts=config.allow_http_hosts,
        )

        # State
        self._running = False
        self._commands: list[dict] = []
        self._commands_hash: str = ''

        # Version info
        self._agent_version = __version__
        self._python_version = platform.python_version()
        self._django_version = django.get_version()

        # Execution state
        self._executor_pool: ThreadPoolExecutor | None = None
        self._active_executions: dict[str, CommandExecutor] = {}  # execution_id -> executor
        self._executions_lock = threading.Lock()

        # Project path (for running commands)
        self._project_path = self._find_project_path()

    @classmethod
    def from_settings(cls) -> 'Agent':
        """Create agent from Django settings."""
        config = load_config()
        return cls(config)

    def _find_project_path(self) -> str:
        """Find the Django project root (directory containing manage.py)."""
        # Start from current working directory
        path = os.getcwd()

        # Look for manage.py
        while path != '/':
            if os.path.exists(os.path.join(path, 'manage.py')):
                return path
            path = os.path.dirname(path)

        # Fall back to current directory
        return os.getcwd()

    def discover_commands(self):
        """Discover local management commands and compute hash.

        Excludes:
        - Agent's own command (djangocommand)
        - Commands in DJANGOCOMMAND_DISALLOWED_COMMANDS blocklist
        """
        # Combine agent exclusions with security blocklist
        disallowed = get_disallowed_commands()
        exclude = list(set(self.EXCLUDED_COMMANDS) | disallowed)

        self._commands, self._commands_hash = get_commands_with_hash(
            exclude=exclude
        )
        logger.info(
            f'Discovered {len(self._commands)} commands (hash: {self._commands_hash[:20]}...)'
        )
        if disallowed:
            logger.debug(f'Excluded {len(disallowed)} disallowed commands from discovery')

    def sync_commands(self):
        """Sync commands with server."""
        logger.info('Syncing commands with server...')
        try:
            response = self.client.sync_commands(self._commands)
            server_hash = response.get('commands_hash', '')

            if server_hash and server_hash != self._commands_hash:
                logger.warning(
                    f'Hash mismatch after sync. Local: {self._commands_hash[:20]}, '
                    f'Server: {server_hash[:20]}'
                )
                # Update our hash to server's to avoid continuous syncing
                self._commands_hash = server_hash

            logger.info(f'Synced {response.get("synced_count", 0)} commands')
            return True

        except DjangoCommandClientError as e:
            logger.error(f'Failed to sync commands: {e}')
            return False

    def heartbeat(self) -> Optional[dict]:
        """
        Send heartbeat to server.

        Returns:
            Response dict or None if failed
        """
        try:
            response = self.client.heartbeat(
                agent_version=self._agent_version,
                python_version=self._python_version,
                django_version=self._django_version,
                commands_hash=self._commands_hash,
            )

            # Check if commands need syncing
            if not response.get('commands_in_sync', True):
                logger.info('Commands out of sync, triggering sync...')
                self.sync_commands()

            pending = response.get('pending_executions', 0)
            if pending > 0:
                logger.debug(f'{pending} pending executions')

            return response

        except DjangoCommandClientError as e:
            logger.error(f'Heartbeat failed: {e}')
            return None

    def poll_and_execute(self):
        """
        Poll for pending executions and start executing them.

        Runs each execution in a thread pool for parallel execution.
        """
        try:
            # Get pending executions from server
            pending = self.client.get_pending_executions()

            for execution in pending:
                execution_id = execution['id']

                # Skip if already running
                with self._executions_lock:
                    if execution_id in self._active_executions:
                        continue

                    # Check if we're at capacity
                    if len(self._active_executions) >= self.MAX_CONCURRENT_EXECUTIONS:
                        logger.debug('At max concurrent executions, skipping...')
                        break

                # Submit execution to thread pool
                logger.info(f"Starting execution {execution_id}: {execution['command']}")
                self._executor_pool.submit(
                    self._run_execution,
                    execution_id,
                    execution['command'],
                    execution.get('args', ''),
                    execution.get('timeout', 300),
                )

        except DjangoCommandClientError as e:
            logger.error(f'Failed to poll executions: {e}')

    def _run_execution(
        self,
        execution_id: str,
        command: str,
        args: str,
        timeout: int,
    ):
        """
        Run a single execution (called from thread pool).

        Handles the full lifecycle: start -> run -> complete.
        Rejects disallowed commands with an error message.
        """
        # Check if command is allowed before doing anything
        allowed, reason = is_command_allowed(command)
        if not allowed:
            logger.warning(
                f"Execution {execution_id}: command '{command}' rejected - {reason}"
            )
            self._reject_execution(execution_id, command, reason)
            return

        executor = CommandExecutor(
            project_path=self._project_path,
            client=self.client,
        )

        # Track active execution
        with self._executions_lock:
            self._active_executions[execution_id] = executor

        try:
            # Mark execution as started on server
            try:
                self.client.start_execution(execution_id)
            except DjangoCommandClientError as e:
                logger.error(f'Failed to start execution {execution_id}: {e}')
                return

            # Start cancel status polling
            cancel_event = threading.Event()
            cancel_thread = threading.Thread(
                target=self._poll_cancel_status,
                args=(execution_id, executor, cancel_event),
                daemon=True
            )
            cancel_thread.start()

            # Execute the command
            result = executor.execute(
                execution_id=execution_id,
                command=command,
                args=args,
                timeout=timeout,
            )

            # Stop cancel polling
            cancel_event.set()
            cancel_thread.join(timeout=2.0)

            # Report completion to server
            try:
                self.client.complete_execution(
                    execution_id=execution_id,
                    exit_code=result.exit_code,
                    status=result.status,
                )
                logger.info(
                    f'Execution {execution_id} completed: {result.status} '
                    f'(exit code: {result.exit_code})'
                )
            except DjangoCommandClientError as e:
                logger.error(f'Failed to complete execution {execution_id}: {e}')

        finally:
            # Remove from active executions
            with self._executions_lock:
                self._active_executions.pop(execution_id, None)

    def _reject_execution(self, execution_id: str, command: str, reason: str):
        """
        Reject an execution due to security policy.

        Marks the execution as started, sends an error message, and completes
        with failed status so the user can see why it was rejected.
        """
        try:
            # Mark as started so it shows up in the UI
            self.client.start_execution(execution_id)
        except DjangoCommandClientError as e:
            logger.error(f'Failed to start rejected execution {execution_id}: {e}')
            return

        # Send rejection message as output
        error_message = (
            f"Command '{command}' rejected by agent security policy.\n"
            f"Reason: {reason}\n"
            f"\n"
            f"To allow this command, update your Django settings:\n"
            f"  DJANGOCOMMAND_ALLOWED_COMMANDS = ('{command}', ...)\n"
            f"or remove it from DJANGOCOMMAND_DISALLOWED_COMMANDS.\n"
        )
        try:
            self.client.send_output(
                execution_id=execution_id,
                segments=[{'timestamp': time.time(), 'content': error_message}],
                is_stderr=True,
                chunk_number=1,
            )
        except DjangoCommandClientError as e:
            logger.error(f'Failed to send rejection message for {execution_id}: {e}')

        # Complete as failed
        try:
            self.client.complete_execution(
                execution_id=execution_id,
                exit_code=-1,
                status='failed',
            )
            logger.info(f'Execution {execution_id} rejected: {reason}')
        except DjangoCommandClientError as e:
            logger.error(f'Failed to complete rejected execution {execution_id}: {e}')

    def _poll_cancel_status(
        self,
        execution_id: str,
        executor: CommandExecutor,
        stop_event: threading.Event,
    ):
        """Poll server for cancellation requests."""
        while not stop_event.is_set():
            try:
                status = self.client.check_cancel_status(execution_id)
                if status.get('cancel_requested'):
                    force = status.get('force_kill', False)
                    logger.info(
                        f'Cancellation requested for {execution_id} '
                        f'(force={force})'
                    )
                    executor.cancel(force=force)
                    break
            except DjangoCommandClientError:
                pass  # Ignore errors, keep polling

            # Wait before next poll
            stop_event.wait(timeout=2.0)

    def _setup_signal_handlers(self):
        """Set up graceful shutdown handlers."""
        # Signal handlers can only be set from the main thread.
        # When running under Django's autoreload, we're in a worker thread
        # and the reloader handles shutdown signals itself.
        if threading.current_thread() is not threading.main_thread():
            logger.debug('Running in worker thread, reloader handles signals')
            return

        def handler(signum, frame):
            signame = signal.Signals(signum).name
            logger.info(f'Received {signame}, shutting down...')
            self._running = False

        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)

    def run(self):
        """
        Main agent loop.

        Runs heartbeat and execution polling until stopped.
        """
        self._setup_signal_handlers()
        self._running = True

        logger.info(
            f'Starting DjangoCommand agent v{self._agent_version}\n'
            f'  Server: {self.config.server_url}\n'
            f'  Heartbeat interval: {self.config.heartbeat_interval}s\n'
            f'  Execution poll interval: {self.EXECUTION_POLL_INTERVAL}s\n'
            f'  Max concurrent executions: {self.MAX_CONCURRENT_EXECUTIONS}\n'
            f'  Project path: {self._project_path}\n'
            f'  Python: {self._python_version}\n'
            f'  Django: {self._django_version}'
        )

        # Initial command discovery
        self.discover_commands()

        # Initial sync (always sync on startup)
        if not self.sync_commands():
            logger.error('Initial command sync failed. Continuing anyway...')

        # Start executor thread pool
        self._executor_pool = ThreadPoolExecutor(
            max_workers=self.MAX_CONCURRENT_EXECUTIONS,
            thread_name_prefix='executor'
        )

        # Main loop - interleave heartbeat and execution polling
        consecutive_failures = 0
        max_consecutive_failures = 5
        last_heartbeat = 0
        last_execution_poll = 0

        try:
            while self._running:
                now = time.time()

                # Heartbeat check
                if now - last_heartbeat >= self.config.heartbeat_interval:
                    response = self.heartbeat()
                    last_heartbeat = now

                    if response:
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                        if consecutive_failures >= max_consecutive_failures:
                            logger.error(
                                f'{consecutive_failures} consecutive heartbeat failures. '
                                f'Check server connectivity and API key.'
                            )

                # Execution polling
                if now - last_execution_poll >= self.EXECUTION_POLL_INTERVAL:
                    self.poll_and_execute()
                    last_execution_poll = now

                # Sleep a bit before next iteration
                time.sleep(0.5)

        finally:
            # Shutdown executor pool
            if self._executor_pool:
                logger.info('Shutting down executor pool...')
                self._executor_pool.shutdown(wait=True, cancel_futures=False)
                self._executor_pool = None

        logger.info('Agent stopped')

    def run_once(self) -> bool:
        """
        Run a single heartbeat cycle.

        Useful for testing or one-shot operations.

        Returns:
            True if heartbeat succeeded
        """
        self.discover_commands()
        response = self.heartbeat()

        if response and not response.get('commands_in_sync', True):
            self.sync_commands()

        return response is not None
