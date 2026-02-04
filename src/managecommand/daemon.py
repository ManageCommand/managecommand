"""
Daemon utilities for the ManageCommand runner.

Provides:
- Pidfile: PID file management with stale detection
- DaemonContext: Process daemonization via double-fork
- ProcessController: High-level start/stop/restart operations
- RotatingFileHandler: Simple log rotation
"""

import atexit
import errno
import hashlib
import json
import logging
import os
import signal
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger(__name__)


# Log rotation settings
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB
LOG_BACKUP_COUNT = 2  # Keep 2 backup files (runner.log.1, runner.log.2)


def get_state_dir(base_dir: str) -> Path:
    """
    Get the state directory for a Django project.

    Uses tempfile.gettempdir() with a project-specific hash to ensure
    unique state directories per project while being cross-platform.

    Args:
        base_dir: The Django project's BASE_DIR setting.

    Returns:
        Path to the state directory (e.g., /tmp/managecommand-abc123def456/)
    """
    # Create a short hash of the base directory
    dir_hash = hashlib.sha256(base_dir.encode()).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"managecommand-{dir_hash}"


@dataclass
class PidfileData:
    """Data stored in the PID file."""

    pid: int
    started_at: str  # ISO 8601 timestamp

    def to_dict(self) -> dict:
        return {"pid": self.pid, "started_at": self.started_at}

    @classmethod
    def from_dict(cls, data: dict) -> "PidfileData":
        return cls(pid=data["pid"], started_at=data["started_at"])

    @property
    def started_datetime(self) -> datetime:
        return datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))

    @property
    def uptime_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.started_datetime).total_seconds()


class Pidfile:
    """
    PID file management with stale detection.

    Stores PID and metadata in JSON format for richer status information.
    """

    def __init__(self, path: Path):
        self.path = Path(path).resolve()

    def read(self) -> PidfileData | None:
        """Read PID data from file. Returns None if file doesn't exist or is invalid."""
        try:
            with open(self.path) as f:
                data = json.load(f)
                return PidfileData.from_dict(data)
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return None

    def write(self) -> None:
        """Write current PID and timestamp to file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = PidfileData(
            pid=os.getpid(),
            started_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        # Write atomically via temp file
        tmp_path = self.path.with_suffix(".tmp")
        with open(tmp_path, "w") as f:
            json.dump(data.to_dict(), f)
            f.flush()
            os.fsync(f.fileno())
        tmp_path.rename(self.path)

    def remove(self) -> None:
        """Remove the PID file."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def is_locked(self) -> bool:
        """Check if PID file exists."""
        return self.path.exists()

    def is_stale(self) -> bool:
        """
        Check if the PID file refers to a dead process.

        Returns True if:
        - PID file doesn't exist
        - PID file is corrupt/unreadable
        - Process with that PID doesn't exist
        """
        data = self.read()
        if data is None:
            return True

        return not self._process_exists(data.pid)

    def _process_exists(self, pid: int) -> bool:
        """Check if a process with the given PID exists."""
        try:
            os.kill(pid, 0)  # Signal 0 doesn't kill, just checks
            return True
        except OSError as err:
            if err.errno == errno.ESRCH:  # No such process
                return False
            elif err.errno == errno.EPERM:  # Permission denied = process exists
                return True
            raise

    def acquire(self) -> bool:
        """
        Acquire the PID lock.

        Returns True if acquired successfully.
        Returns False if another process is running.
        Removes stale PID files automatically.
        """
        if self.is_locked():
            if self.is_stale():
                logger.info("Removing stale PID file")
                self.remove()
            else:
                return False

        self.write()
        atexit.register(self.remove)
        return True


class DaemonContext:
    """
    Context manager for daemonizing a process.

    Uses the standard Unix double-fork technique to properly
    detach from the controlling terminal.
    """

    def __init__(
        self,
        pidfile: Pidfile | None = None,
        logfile: Path | None = None,
        workdir: str | None = None,
    ):
        self.pidfile = pidfile
        self.logfile = logfile
        self.workdir = workdir or os.getcwd()

    def daemonize(self) -> None:
        """
        Daemonize the current process using double-fork.

        After this call:
        - Process is detached from terminal
        - stdin/stdout/stderr redirected to /dev/null or logfile
        - Working directory changed to workdir
        - PID file written (if pidfile provided)
        """
        # Check if fork is available (not on Windows)
        if not hasattr(os, "fork"):
            raise RuntimeError("Daemonization requires os.fork() (Unix only)")

        # First fork
        pid = os.fork()
        if pid > 0:
            # Parent exits
            os._exit(0)

        # Create new session
        os.setsid()

        # Second fork
        pid = os.fork()
        if pid > 0:
            # First child exits
            os._exit(0)

        # Now in daemon process
        os.chdir(self.workdir)

        # Redirect standard file descriptors
        self._redirect_streams()

        # Write PID file
        if self.pidfile:
            self.pidfile.write()
            atexit.register(self.pidfile.remove)

    def _redirect_streams(self) -> None:
        """Redirect stdin/stdout/stderr to /dev/null or logfile."""
        sys.stdout.flush()
        sys.stderr.flush()

        # Open /dev/null for stdin
        devnull = os.open(os.devnull, os.O_RDWR)
        os.dup2(devnull, sys.stdin.fileno())

        if self.logfile:
            # Create log directory if needed
            self.logfile.parent.mkdir(parents=True, exist_ok=True)

            # Open log file for stdout/stderr
            log_fd = os.open(
                str(self.logfile),
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o644,
            )
            os.dup2(log_fd, sys.stdout.fileno())
            os.dup2(log_fd, sys.stderr.fileno())
            if log_fd > 2:
                os.close(log_fd)
        else:
            # Redirect to /dev/null
            os.dup2(devnull, sys.stdout.fileno())
            os.dup2(devnull, sys.stderr.fileno())

        if devnull > 2:
            os.close(devnull)


class ProcessController:
    """
    High-level process control for the runner.

    Provides start/stop/restart/status operations using
    PID file for process tracking.
    """

    STOP_TIMEOUT = 10.0  # seconds to wait for graceful shutdown
    STOP_POLL_INTERVAL = 0.5  # seconds between checks

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.pidfile = Pidfile(self.state_dir / "runner.pid")
        self.logfile = self.state_dir / "runner.log"

    def get_status(self) -> dict:
        """
        Get current runner status.

        Returns dict with:
        - status: "running" | "stopped"
        - pid: int (if running)
        - started_at: str (if running)
        - uptime_seconds: float (if running)
        """
        data = self.pidfile.read()

        if data is None or self.pidfile.is_stale():
            return {"status": "stopped"}

        return {
            "status": "running",
            "pid": data.pid,
            "started_at": data.started_at,
            "uptime_seconds": data.uptime_seconds,
        }

    def is_running(self) -> bool:
        """Check if runner is currently running."""
        return self.get_status()["status"] == "running"

    def stop(self, force: bool = False) -> bool:
        """
        Stop the running runner.

        Args:
            force: If True, use SIGKILL. Otherwise SIGTERM with timeout.

        Returns:
            True if runner was stopped (or wasn't running).
            False if graceful stop timed out.
        """
        data = self.pidfile.read()

        if data is None or self.pidfile.is_stale():
            # Already stopped or stale
            self.pidfile.remove()
            return True

        pid = data.pid
        sig = signal.SIGKILL if force else signal.SIGTERM

        try:
            os.kill(pid, sig)
        except OSError as err:
            if err.errno == errno.ESRCH:
                # Process already dead
                self.pidfile.remove()
                return True
            raise

        if force:
            # SIGKILL is immediate
            self.pidfile.remove()
            return True

        # Wait for graceful shutdown
        deadline = time.time() + self.STOP_TIMEOUT
        while time.time() < deadline:
            try:
                os.kill(pid, 0)  # Check if still alive
                time.sleep(self.STOP_POLL_INTERVAL)
            except OSError as err:
                if err.errno == errno.ESRCH:
                    # Process died
                    self.pidfile.remove()
                    return True
                raise

        # Timeout - process still running
        return False

    def setup_logging(self, detached: bool = False) -> None:
        """
        Configure logging with optional file rotation.

        Args:
            detached: If True, set up rotating file handler for daemon mode.
        """
        if detached:
            # Ensure state directory exists
            self.state_dir.mkdir(parents=True, exist_ok=True)

            # Set up rotating file handler
            handler = RotatingFileHandler(
                self.logfile,
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
            )
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )

            # Add to root logger
            root_logger = logging.getLogger()
            root_logger.addHandler(handler)
