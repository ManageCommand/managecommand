"""
ManageCommand Runner core.

The runner maintains a heartbeat with the server, syncs commands,
and executes pending commands with output streaming.
"""

import logging
import os
import platform
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import django

from .client import AuthenticationError, ManageCommandClient, ManageCommandClientError
from .config import RunnerConfig, load_config
from .discovery import get_commands_with_hash
from .executor import CommandExecutor
from .security import (
    are_args_allowed,
    get_allowed_commands,
    get_disallowed_commands,
    is_command_allowed,
    is_command_bound,
    is_using_blocklist,
)

logger = logging.getLogger(__name__)

# Package version
try:
    from . import __version__
except ImportError:
    __version__ = 'unknown'


class Runner:
    """
    ManageCommand runner that maintains connection with server.

    Responsibilities:
    - Heartbeat every N seconds
    - Sync commands when hash changes
    - Poll for and execute pending commands
    - Graceful shutdown on SIGTERM/SIGINT
    """

    # Exclude our own command to avoid recursion
    EXCLUDED_COMMANDS = ['managecommand']

    # Execution settings
    MAX_CONCURRENT_EXECUTIONS = 4
    EXECUTION_POLL_INTERVAL = 2.0  # seconds

    # Auth state constants
    AUTH_UNKNOWN = 'AUTH_UNKNOWN'   # Startup, never validated
    AUTH_VALID = 'AUTH_VALID'       # Validated, may do work
    AUTH_BACKOFF = 'AUTH_BACKOFF'   # Failed, waiting to retry

    def __init__(self, config: RunnerConfig):
        self.config = config
        self.client = ManageCommandClient(
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
        self._runner_version = __version__
        self._python_version = platform.python_version()
        self._django_version = django.get_version()

        # Execution state
        self._executor_pool: ThreadPoolExecutor | None = None
        self._active_executions: dict[str, CommandExecutor] = {}  # execution_id -> executor
        self._executions_lock = threading.Lock()

        # Auth state machine
        self._auth_state = self.AUTH_UNKNOWN
        self._auth_failure_count = 0
        self._auth_backoff_until = 0.0
        self._last_auth_status_log = 0.0  # For periodic status logging
        # Backoff schedule in seconds: 1s, 2s, 5s, 10s, 30s, 2min, 5min (cap)
        self._AUTH_BACKOFF_SCHEDULE = [1, 2, 5, 10, 30, 120, 300]

        # Runner suspension state (quota exceeded, plan limits, etc.)
        # When suspended, we continue heartbeating but skip execution polling
        self._runner_suspended = False
        self._suspension_reason: str | None = None

        # Project path (for running commands)
        self._project_path = self._find_project_path()

    @classmethod
    def from_settings(cls) -> 'Runner':
        """Create runner from Django settings."""
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

        Uses security mode from settings:
        - Allowlist mode (default): Only include commands in
          MANAGECOMMAND_ALLOWED_COMMANDS
        - Blocklist mode: Include all commands except those in
          MANAGECOMMAND_DISALLOWED_COMMANDS

        Always excludes:
        - Runner's own command (managecommand)
        """
        if is_using_blocklist():
            # Blocklist mode: exclude disallowed commands
            disallowed = get_disallowed_commands()
            exclude = list(set(self.EXCLUDED_COMMANDS) | disallowed)

            self._commands, self._commands_hash = get_commands_with_hash(
                exclude=exclude
            )
            logger.info(
                f'Discovered {len(self._commands)} commands (hash: {self._commands_hash[:20]}...)'
            )
            logger.debug(
                f'Using blocklist mode: excluded {len(disallowed)} disallowed commands'
            )
        else:
            # Allowlist mode (default): only include allowed commands
            allowed = get_allowed_commands()
            # Remove runner's own command from allowlist to avoid recursion
            allowed_filtered = allowed - set(self.EXCLUDED_COMMANDS)

            self._commands, self._commands_hash = get_commands_with_hash(
                include=list(allowed_filtered)
            )
            logger.info(
                f'Discovered {len(self._commands)} commands (hash: {self._commands_hash[:20]}...)'
            )
            logger.debug(
                f'Using allowlist mode: {len(allowed)} commands in allowlist'
            )

    def sync_commands(self) -> bool:
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

        except AuthenticationError as err:
            self._auth_disable_and_backoff(err)
            return False
        except ManageCommandClientError as err:
            logger.error(f'Failed to sync commands: {err}')
            return False

    # -------------------------------------------------------------------------
    # Auth state machine helpers
    # -------------------------------------------------------------------------

    def _auth_is_valid(self) -> bool:
        """Check if auth has been validated (may do non-heartbeat work)."""
        return self._auth_state == self.AUTH_VALID

    def _auth_mark_valid(self) -> None:
        """Mark auth as validated, reset failure counters."""
        if self._auth_state != self.AUTH_VALID:
            logger.info('Authentication validated.')
        self._auth_state = self.AUTH_VALID
        self._auth_failure_count = 0
        self._auth_backoff_until = 0.0

    def _auth_disable_and_backoff(self, error: Exception) -> None:
        """Disable auth and enter backoff state."""
        was_valid = self._auth_state == self.AUTH_VALID
        self._auth_state = self.AUTH_BACKOFF
        self._handle_auth_failure(error)
        if was_valid:
            logger.warning(f'Authentication lost: {error}')

    def _log_auth_status_if_due(self) -> None:
        """Log auth status periodically (every 5 min) while not valid."""
        if self._auth_is_valid():
            return
        now = time.time()
        if now - self._last_auth_status_log >= 300:  # 5 minutes
            next_attempt = max(0, self._auth_backoff_until - now)
            logger.info(
                f'Auth status: {self._auth_state}, '
                f'failures: {self._auth_failure_count}, '
                f'next attempt in {next_attempt:.0f}s'
            )
            self._last_auth_status_log = now

    def _handle_auth_failure(self, error: Exception) -> None:
        """Handle authentication failure with progressive backoff (internal)."""
        self._auth_failure_count += 1

        # Get backoff duration (cap at last value in schedule)
        schedule = self._AUTH_BACKOFF_SCHEDULE
        backoff_index = min(self._auth_failure_count - 1, len(schedule) - 1)
        backoff_seconds = schedule[backoff_index]

        self._auth_backoff_until = time.time() + backoff_seconds

        # Only log on first failure (subsequent logged by periodic status)
        if self._auth_failure_count == 1:
            logger.warning(
                f'Authentication failed: {error}. '
                f'Retrying in {backoff_seconds}s. '
                f'Check API key configuration.'
            )

    def heartbeat(self) -> Optional[dict]:
        """
        Send heartbeat to server. This is the auth validation path.

        Returns:
            Response dict or None if failed
        """
        logger.debug(f'Sending heartbeat to {self.config.server_url}...')
        try:
            response = self.client.heartbeat(
                runner_version=self._runner_version,
                python_version=self._python_version,
                django_version=self._django_version,
                commands_hash=self._commands_hash,
            )

            logger.info(f'Heartbeat OK -> {self.config.server_url}')

            # Mark auth as valid on successful heartbeat
            self._auth_mark_valid()

            # Check runner suspension state
            runner_state = response.get('runner_state', 'active')
            new_suspended = (runner_state == 'suspended')
            suspension_reason = response.get('suspension_reason')

            if new_suspended and not self._runner_suspended:
                # Newly suspended
                logger.warning(
                    f'Runner suspended by server: {suspension_reason or "unknown reason"}. '
                    f'Execution polling paused.'
                )
            elif not new_suspended and self._runner_suspended:
                # Suspension lifted
                logger.info('Runner suspension lifted. Resuming normal operation.')

            self._runner_suspended = new_suspended
            self._suspension_reason = suspension_reason

            # Check if commands need syncing
            if not response.get('commands_in_sync', True):
                logger.info('Commands out of sync, triggering sync...')
                self.sync_commands()

            pending = response.get('pending_executions', 0)
            if pending > 0:
                logger.debug(f'{pending} pending executions')

            return response

        except AuthenticationError as err:
            logger.warning(f'Heartbeat FAILED (auth) -> {self.config.server_url}: {err}')
            self._auth_disable_and_backoff(err)
            return None
        except ManageCommandClientError as err:
            logger.error(f'Heartbeat FAILED -> {self.config.server_url}: {err}')
            return None

    def poll_and_execute(self):
        """
        Poll for pending executions and start executing them.

        Runs each execution in a thread pool for parallel execution.
        Only operates when auth is valid.
        """
        # Gate: only poll if auth is valid
        if not self._auth_is_valid():
            return

        try:
            # Get pending executions from server
            pending = self.client.get_pending_executions()

            for execution in pending:
                # Re-check auth before each submission (may have changed)
                if not self._auth_is_valid():
                    break

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
                    execution.get('use_metadata_only_mode', False),
                )

        except AuthenticationError as err:
            self._auth_disable_and_backoff(err)
        except ManageCommandClientError as err:
            logger.error(f'Failed to poll executions: {err}')

    def _run_execution(
        self,
        execution_id: str,
        command: str,
        args: str,
        timeout: int,
        use_metadata_only_mode: bool = False,
    ):
        """
        Run a single execution (called from thread pool).

        Handles the full lifecycle: start -> run -> complete.
        Rejects disallowed commands or invalid args with an error message.
        """
        # Check if command is allowed before doing anything
        allowed, reason = is_command_allowed(command)
        if not allowed:
            logger.warning(
                f"Execution {execution_id}: command '{command}' rejected - {reason}"
            )
            self._reject_execution(execution_id, command, reason)
            return

        # Check if args are allowed for bound commands
        args_allowed, args_reason = are_args_allowed(command, args)
        if not args_allowed:
            logger.warning(
                f"Execution {execution_id}: args for '{command}' rejected - {args_reason}"
            )
            self._reject_execution_args(execution_id, command, args, args_reason)
            return

        # Determine if metadata-only mode should be used
        # Either server flag or client setting triggers metadata-only mode
        metadata_only = (
            use_metadata_only_mode or
            command in self.config.metadataonly_commands
        )

        executor = CommandExecutor(
            project_path=self._project_path,
            client=self.client,
            auth_check=self._auth_is_valid,
            on_auth_error=self._auth_disable_and_backoff,
        )

        # Track active execution
        with self._executions_lock:
            self._active_executions[execution_id] = executor

        try:
            # Mark execution as started on server
            try:
                self.client.start_execution(execution_id)
            except ManageCommandClientError as err:
                logger.error(f'Failed to start execution {execution_id}: {err}')
                return

            # If metadata-only mode, send simulated output before execution
            if metadata_only:
                self._send_metadata_only_output(execution_id)

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
                metadata_only=metadata_only,
            )

            # Stop cancel polling
            cancel_event.set()
            cancel_thread.join(timeout=2.0)

            # Report completion to server
            if not self._auth_is_valid():
                logger.warning(
                    f'Cannot report completion for {execution_id}: auth not valid'
                )
            else:
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
                except AuthenticationError as err:
                    self._auth_disable_and_backoff(err)
                    logger.warning(
                        f'Completion report failed for {execution_id}: auth revoked'
                    )
                except ManageCommandClientError as err:
                    logger.error(f'Failed to complete execution {execution_id}: {err}')

        finally:
            # Remove from active executions
            with self._executions_lock:
                self._active_executions.pop(execution_id, None)

    def _send_metadata_only_output(self, execution_id: str):
        """Send simulated output chunks for metadata-only mode."""
        simulated_message = "(metadata-only mode, no output captured)\n"

        try:
            # Send stdout simulated chunk
            self.client.send_output(
                execution_id=execution_id,
                segments=[{'timestamp': time.time(), 'content': simulated_message}],
                is_stderr=False,
                chunk_number=1,
            )
            # Send stderr simulated chunk
            self.client.send_output(
                execution_id=execution_id,
                segments=[{'timestamp': time.time(), 'content': simulated_message}],
                is_stderr=True,
                chunk_number=2,
            )
        except ManageCommandClientError as err:
            logger.warning(f'Failed to send metadata-only output for {execution_id}: {err}')

    def _reject_execution(self, execution_id: str, command: str, reason: str):
        """
        Reject an execution due to security policy.

        Marks the execution as started, sends an error message, and completes
        with failed status so the user can see why it was rejected.
        """
        try:
            # Mark as started so it shows up in the UI
            self.client.start_execution(execution_id)
        except ManageCommandClientError as err:
            logger.error(f'Failed to start rejected execution {execution_id}: {err}')
            return

        # Send rejection message as output
        if is_using_blocklist():
            error_message = (
                f"Command '{command}' rejected by runner security policy.\n"
                f"Reason: {reason}\n"
                f"\n"
                f"To allow this command, remove it from MANAGECOMMAND_DISALLOWED_COMMANDS\n"
                f"in your Django settings.\n"
            )
        else:
            error_message = (
                f"Command '{command}' rejected by runner security policy.\n"
                f"Reason: {reason}\n"
                f"\n"
                f"To allow this command, add it to MANAGECOMMAND_ALLOWED_COMMANDS\n"
                f"in your Django settings:\n"
                f"\n"
                f"  from managecommand import DEFAULT_ALLOWED_COMMANDS\n"
                f"  MANAGECOMMAND_ALLOWED_COMMANDS = DEFAULT_ALLOWED_COMMANDS + ('{command}',)\n"
            )
        try:
            self.client.send_output(
                execution_id=execution_id,
                segments=[{'timestamp': time.time(), 'content': error_message}],
                is_stderr=True,
                chunk_number=1,
            )
        except ManageCommandClientError as err:
            logger.error(f'Failed to send rejection message for {execution_id}: {err}')

        # Complete as failed
        try:
            self.client.complete_execution(
                execution_id=execution_id,
                exit_code=-1,
                status='failed',
            )
            logger.info(f'Execution {execution_id} rejected: {reason}')
        except ManageCommandClientError as err:
            logger.error(f'Failed to complete rejected execution {execution_id}: {err}')

    def _reject_execution_args(
        self, execution_id: str, command: str, args: str, reason: str
    ):
        """
        Reject an execution due to disallowed arguments for a bound command.

        Marks the execution as started, sends an error message, and completes
        with failed status so the user can see why it was rejected.
        """
        try:
            # Mark as started so it shows up in the UI
            self.client.start_execution(execution_id)
        except ManageCommandClientError as err:
            logger.error(f'Failed to start rejected execution {execution_id}: {err}')
            return

        # Send rejection message as output
        error_message = (
            f"Arguments rejected by runner security policy.\n"
            f"Command: {command}\n"
            f"Args: {args}\n"
            f"Reason: {reason}\n"
            f"\n"
            f"This command is configured as a 'bound command' which restricts\n"
            f"execution to specific predefined argument sets.\n"
            f"\n"
            f"To modify allowed arguments, update MANAGECOMMAND_BOUND_COMMANDS\n"
            f"in your Django settings.\n"
        )
        try:
            self.client.send_output(
                execution_id=execution_id,
                segments=[{'timestamp': time.time(), 'content': error_message}],
                is_stderr=True,
                chunk_number=1,
            )
        except ManageCommandClientError as err:
            logger.error(f'Failed to send rejection message for {execution_id}: {err}')

        # Complete as failed
        try:
            self.client.complete_execution(
                execution_id=execution_id,
                exit_code=-1,
                status='failed',
            )
            logger.info(
                f'Execution {execution_id} rejected: args not allowed for bound command'
            )
        except ManageCommandClientError as err:
            logger.error(f'Failed to complete rejected execution {execution_id}: {err}')

    def _poll_cancel_status(
        self,
        execution_id: str,
        executor: CommandExecutor,
        stop_event: threading.Event,
    ):
        """Poll server for cancellation requests."""
        while not stop_event.is_set():
            # Skip API call if auth not valid
            if not self._auth_is_valid():
                stop_event.wait(timeout=2.0)
                continue

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
            except AuthenticationError as err:
                self._auth_disable_and_backoff(err)
                # Don't break, just stop making requests until auth recovers
            except ManageCommandClientError:
                pass  # Ignore errors, keep polling

            # Wait before next poll
            stop_event.wait(timeout=2.0)

    def _startup_connect(self) -> bool:
        """
        Attempt initial connection to server with exponential backoff.

        Used during startup to handle cases where the backend isn't ready yet
        (e.g., in Docker Compose when services start simultaneously).

        Returns:
            True if connection succeeded, False if all retries exhausted.
        """
        max_wait_seconds = 300  # 5 minutes total
        initial_delay = 10
        max_delay = 60
        elapsed = 0
        attempt = 0
        delay = initial_delay

        while elapsed < max_wait_seconds:
            attempt += 1
            logger.info(f'Startup connection attempt {attempt}...')

            response = self.heartbeat()
            if response is not None:
                logger.info('Startup connection successful.')
                return True

            # Calculate next delay with exponential backoff, capped at max_delay
            remaining = max_wait_seconds - elapsed
            actual_delay = min(delay, max_delay, remaining)

            if actual_delay <= 0:
                break

            logger.warning(
                f'Startup connection failed. Retrying in {actual_delay}s '
                f'({int(remaining)}s remaining)...'
            )
            time.sleep(actual_delay)
            elapsed += actual_delay
            delay *= 2  # Exponential backoff

        logger.error(
            f'Failed to connect to server after {max_wait_seconds}s. '
            f'Check server URL and network connectivity.'
        )
        return False

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
        Main runner loop.

        Runs heartbeat and execution polling until stopped.
        """
        self._setup_signal_handlers()
        self._running = True

        logger.info(
            f'Starting ManageCommand runner v{self._runner_version}\n'
            f'  Server: {self.config.server_url}\n'
            f'  Heartbeat interval: {self.config.heartbeat_interval}s\n'
            f'  Execution poll interval: {self.EXECUTION_POLL_INTERVAL}s\n'
            f'  Max concurrent executions: {self.MAX_CONCURRENT_EXECUTIONS}\n'
            f'  Project path: {self._project_path}\n'
            f'  Python: {self._python_version}\n'
            f'  Django: {self._django_version}'
        )

        # Initial command discovery (local, no network)
        self.discover_commands()

        # Initial connection with retry logic (handles backend not ready yet)
        if not self._startup_connect():
            logger.error('Startup connection failed. Exiting.')
            self._running = False
            return

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

                # Periodic auth status log while not valid
                self._log_auth_status_if_due()

                # Skip if in auth backoff period
                if self._auth_backoff_until > now:
                    time.sleep(0.5)
                    continue

                # Heartbeat check (always allowed, validates auth)
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

                # Execution polling (only if auth is valid and not suspended)
                if self._auth_is_valid() and not self._runner_suspended:
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

        logger.info('Runner stopped')

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
