"""
Django management command to run the ManageCommand runner.

Usage:
    python manage.py managecommand                    # Show status
    python manage.py managecommand start              # Start foreground
    python manage.py managecommand start -d           # Start detached
    python manage.py managecommand start -v 2         # Start with verbose output
    python manage.py managecommand start --no-reload  # Disable auto-reload
    python manage.py managecommand stop               # Graceful stop
    python manage.py managecommand stop --force       # Force stop
    python manage.py managecommand restart            # Restart
    python manage.py managecommand restart -d         # Restart detached

Documentation: https://managecommand.com/docs
"""

import json
import logging
import os
import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import autoreload

from managecommand.runner import Runner
from managecommand.config import ConfigurationError
from managecommand.daemon import (
    DaemonContext,
    ProcessController,
    get_state_dir,
)


class Command(BaseCommand):
    help = "Run the ManageCommand runner to sync commands and execute remote requests"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # State directory based on project's BASE_DIR
        base_dir = getattr(settings, "BASE_DIR", os.getcwd())
        if hasattr(base_dir, "__fspath__"):
            base_dir = os.fspath(base_dir)
        self.state_dir = get_state_dir(str(base_dir))
        self.controller = ProcessController(self.state_dir)

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="subcommand", help="Runner commands")

        # start subcommand
        start_parser = subparsers.add_parser("start", help="Start the runner")
        start_parser.add_argument(
            "-d",
            "--detach",
            action="store_true",
            help="Run runner in background (daemonize)",
        )
        start_parser.add_argument(
            "--no-reload",
            action="store_true",
            help="Disable auto-reload when DEBUG=True",
        )
        start_parser.add_argument(
            "-v",
            "--verbosity",
            type=int,
            choices=[0, 1, 2, 3],
            default=1,
            help="Verbosity level; 0=minimal, 1=normal, 2=verbose, 3=debug",
        )

        # stop subcommand
        stop_parser = subparsers.add_parser("stop", help="Stop the runner")
        stop_parser.add_argument(
            "--force",
            action="store_true",
            help="Force stop with SIGKILL (immediate, no cleanup)",
        )

        # restart subcommand
        restart_parser = subparsers.add_parser("restart", help="Restart the runner")
        restart_parser.add_argument(
            "--force",
            action="store_true",
            help="Force stop before restart",
        )
        restart_parser.add_argument(
            "-d",
            "--detach",
            action="store_true",
            help="Restart in background mode",
        )
        restart_parser.add_argument(
            "--no-reload",
            action="store_true",
            help="Disable auto-reload when DEBUG=True",
        )
        restart_parser.add_argument(
            "-v",
            "--verbosity",
            type=int,
            choices=[0, 1, 2, 3],
            default=1,
            help="Verbosity level; 0=minimal, 1=normal, 2=verbose, 3=debug",
        )

        # status subcommand (explicit)
        status_parser = subparsers.add_parser("status", help="Show runner status")
        status_parser.add_argument(
            "--json",
            action="store_true",
            help="Output status as JSON",
        )

    def handle(self, *args, **options):
        subcommand = options.get("subcommand")

        if subcommand is None or subcommand == "status":
            return self.handle_status(**options)
        elif subcommand == "start":
            return self.handle_start(**options)
        elif subcommand == "stop":
            return self.handle_stop(**options)
        elif subcommand == "restart":
            return self.handle_restart(**options)
        else:
            raise CommandError(f"Unknown subcommand: {subcommand}")

    def _setup_logging(self, options, detached: bool = False):
        """Configure logging based on verbosity level."""
        verbosity = options.get("verbosity", 1)
        if verbosity == 0:
            log_level = logging.ERROR
        elif verbosity == 1:
            log_level = logging.WARNING
        elif verbosity == 2:
            log_level = logging.INFO
        else:
            log_level = logging.DEBUG

        logging.basicConfig(
            level=log_level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # Reduce noise from third-party libraries
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("requests").setLevel(logging.WARNING)

        # Set up rotating file handler for detached mode
        if detached:
            self.controller.setup_logging(detached=True)

    def handle_status(self, **options):
        """Handle status subcommand."""
        status = self.controller.get_status()

        if options.get("json"):
            self.stdout.write(json.dumps(status, indent=2))
            return

        if status["status"] == "stopped":
            self.stdout.write("Runner status: " + self.style.WARNING("stopped"))
        else:
            self.stdout.write("Runner status: " + self.style.SUCCESS("running"))
            self.stdout.write(f"PID: {status['pid']}")
            self.stdout.write(f"Started: {status['started_at']}")

            # Format uptime
            uptime = int(status["uptime_seconds"])
            hours, remainder = divmod(uptime, 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 0:
                uptime_str = f"{hours}h {minutes}m {seconds}s"
            elif minutes > 0:
                uptime_str = f"{minutes}m {seconds}s"
            else:
                uptime_str = f"{seconds}s"
            self.stdout.write(f"Uptime: {uptime_str}")

    def handle_start(self, **options):
        """Handle start subcommand."""
        # Check if already running
        if self.controller.is_running():
            status = self.controller.get_status()
            raise CommandError(
                f"Runner already running (PID: {status['pid']}). "
                f"Use 'stop' first or 'restart'."
            )

        # Clean up any stale PID file
        if self.controller.pidfile.is_locked():
            self.stdout.write("Cleaning up stale PID file...")
            self.controller.pidfile.remove()

        detach = options.get("detach", False)

        if detach:
            self._start_detached(**options)
        else:
            self._start_foreground(**options)

    def _start_detached(self, **options):
        """Start runner in daemon mode."""
        if not hasattr(os, "fork"):
            raise CommandError(
                "Detached mode requires Unix (os.fork not available). "
                "Use foreground mode on Windows."
            )

        self.stdout.write("Starting runner in background...")
        self.stdout.write(f"Logs: {self.controller.logfile}")

        # Set up logging before daemonizing
        self._setup_logging(options, detached=True)

        # Daemonize
        daemon = DaemonContext(
            pidfile=self.controller.pidfile,
            logfile=self.controller.logfile,
            workdir=os.getcwd(),
        )
        daemon.daemonize()

        # Now running in daemon process - run the runner
        self._run_runner()

    def _start_foreground(self, **options):
        """Start runner in foreground mode."""
        # Set up logging first
        self._setup_logging(options)

        no_reload = options.get("no_reload", False)
        use_reloader = getattr(settings, "DEBUG", False) and not no_reload

        if use_reloader:
            # When using autoreload, the reloader spawns a child process.
            # Only acquire PID lock in the child (where RUN_MAIN=true).
            self.stdout.write(
                "Auto-reload enabled (DEBUG=True). Use --no-reload to disable."
            )
            autoreload.run_with_reloader(self._run_runner_with_lock)
        else:
            self._run_runner_with_lock()

    def _run_runner_with_lock(self):
        """Acquire PID lock and run the runner."""
        if not self.controller.pidfile.acquire():
            raise CommandError(
                "Failed to acquire PID lock. Another runner may be running."
            )
        self._run_runner()

    def _run_runner(self):
        """Run the runner main loop."""
        try:
            runner = Runner.from_settings()
        except ConfigurationError as err:
            self.stderr.write(self.style.ERROR(f"Configuration error: {err}"))
            sys.exit(1)

        self.stdout.write(
            self.style.SUCCESS("Starting ManageCommand runner...\n" "Press Ctrl+C to stop.")
        )

        try:
            runner.run()
        except KeyboardInterrupt:
            pass  # Already handled by signal handler
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Runner error: {exc}"))
            sys.exit(1)

        self.stdout.write(self.style.SUCCESS("Runner stopped"))

    def handle_stop(self, **options):
        """Handle stop subcommand."""
        force = options.get("force", False)

        if not self.controller.is_running():
            self.stdout.write("Runner not running")
            return

        status = self.controller.get_status()
        pid = status["pid"]

        if force:
            self.stdout.write(f"Force stopping runner (PID: {pid})...")
        else:
            self.stdout.write(f"Stopping runner (PID: {pid})...")

        success = self.controller.stop(force=force)

        if success:
            self.stdout.write(self.style.SUCCESS("Runner stopped"))
        else:
            self.stderr.write(
                self.style.ERROR(
                    f"Runner did not stop within {self.controller.STOP_TIMEOUT}s. "
                    f"Use --force to send SIGKILL."
                )
            )
            sys.exit(1)

    def handle_restart(self, **options):
        """Handle restart subcommand."""
        force = options.get("force", False)

        if self.controller.is_running():
            status = self.controller.get_status()
            self.stdout.write(f"Stopping runner (PID: {status['pid']})...")

            success = self.controller.stop(force=force)
            if not success:
                self.stderr.write(
                    self.style.ERROR(
                        f"Runner did not stop within {self.controller.STOP_TIMEOUT}s. "
                        f"Use --force to force restart."
                    )
                )
                sys.exit(1)

            self.stdout.write(self.style.SUCCESS("Runner stopped"))
        else:
            self.stdout.write("Runner not running, starting fresh...")

        # Start with the new options
        self.handle_start(**options)
