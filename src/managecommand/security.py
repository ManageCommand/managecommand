"""
Security utilities for ManageCommand client.

Handles command allowlisting/blocklisting based on Django settings.

Security Model:
- DEFAULT: Allowlist approach (most secure) - only commands in
  MANAGECOMMAND_ALLOWED_COMMANDS can run
- OPTIONAL: Blocklist approach - set MANAGECOMMAND_USE_BLOCKLIST = True
  to allow all commands except those in MANAGECOMMAND_DISALLOWED_COMMANDS
"""

import logging

from .constants import DEFAULT_ALLOWED_COMMANDS, DEFAULT_DISALLOWED_COMMANDS

logger = logging.getLogger(__name__)


class CommandDisallowedError(Exception):
    """Raised when a command is not allowed to execute."""

    def __init__(self, command: str, reason: str):
        self.command = command
        self.reason = reason
        super().__init__(f"Command '{command}' is not allowed: {reason}")


def is_using_blocklist() -> bool:
    """
    Check if the blocklist approach is enabled.

    Returns True if MANAGECOMMAND_USE_BLOCKLIST = True in settings.
    """
    from django.conf import settings
    return getattr(settings, 'MANAGECOMMAND_USE_BLOCKLIST', False)


def get_allowed_commands() -> frozenset[str]:
    """
    Get the set of allowed commands from Django settings.

    Returns MANAGECOMMAND_ALLOWED_COMMANDS if set, otherwise
    DEFAULT_ALLOWED_COMMANDS.

    Note: This returns the allowlist regardless of whether blocklist
    mode is enabled. Use is_using_blocklist() to check the mode.
    """
    from django.conf import settings

    allowed = getattr(
        settings,
        'MANAGECOMMAND_ALLOWED_COMMANDS',
        DEFAULT_ALLOWED_COMMANDS
    )
    return frozenset(allowed)


def get_disallowed_commands() -> frozenset[str]:
    """
    Get the set of disallowed commands from Django settings.

    Returns MANAGECOMMAND_DISALLOWED_COMMANDS if set, otherwise
    DEFAULT_DISALLOWED_COMMANDS.

    Note: This returns the blocklist regardless of whether blocklist
    mode is enabled. Use is_using_blocklist() to check the mode.
    """
    from django.conf import settings

    disallowed = getattr(
        settings,
        'MANAGECOMMAND_DISALLOWED_COMMANDS',
        DEFAULT_DISALLOWED_COMMANDS
    )
    return frozenset(disallowed)


def is_command_allowed(command: str) -> tuple[bool, str]:
    """
    Check if a command is allowed to execute.

    Security Model:
    - By default (allowlist mode): Only commands in MANAGECOMMAND_ALLOWED_COMMANDS
      (or DEFAULT_ALLOWED_COMMANDS) can run.
    - If MANAGECOMMAND_USE_BLOCKLIST = True: All commands can run EXCEPT those
      in MANAGECOMMAND_DISALLOWED_COMMANDS (or DEFAULT_DISALLOWED_COMMANDS).

    Args:
        command: The management command name to check

    Returns:
        Tuple of (is_allowed, reason).
        If allowed: (True, "")
        If not allowed: (False, "reason why")
    """
    # Lazy import to avoid Django settings access at module load time
    from django.conf import settings

    # Check if using blocklist mode
    use_blocklist = getattr(settings, 'MANAGECOMMAND_USE_BLOCKLIST', False)

    if use_blocklist:
        # Blocklist mode: allow all except blocked commands
        disallowed_commands = getattr(
            settings,
            'MANAGECOMMAND_DISALLOWED_COMMANDS',
            DEFAULT_DISALLOWED_COMMANDS
        )
        disallowed_set = frozenset(disallowed_commands)

        if command in disallowed_set:
            return False, "in MANAGECOMMAND_DISALLOWED_COMMANDS blocklist"

        return True, ""
    else:
        # Allowlist mode (default): only allow explicitly listed commands
        allowed_commands = getattr(
            settings,
            'MANAGECOMMAND_ALLOWED_COMMANDS',
            DEFAULT_ALLOWED_COMMANDS
        )
        allowed_set = frozenset(allowed_commands)

        if command in allowed_set:
            return True, ""
        else:
            return False, "not in MANAGECOMMAND_ALLOWED_COMMANDS allowlist"


def check_command_allowed(command: str) -> None:
    """
    Check if a command is allowed, raising CommandDisallowedError if not.

    This is a convenience wrapper around is_command_allowed() for cases
    where you want exception-based flow control.

    Args:
        command: The management command name to check

    Raises:
        CommandDisallowedError: If the command is not allowed
    """
    allowed, reason = is_command_allowed(command)
    if not allowed:
        raise CommandDisallowedError(command, reason)
