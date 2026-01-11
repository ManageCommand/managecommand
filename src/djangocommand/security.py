"""
Security utilities for DjangoCommand client.

Handles command allowlisting/blocklisting based on Django settings.
"""

import logging

from .constants import DEFAULT_DISALLOWED_COMMANDS

logger = logging.getLogger(__name__)


class CommandDisallowedError(Exception):
    """Raised when a command is not allowed to execute."""

    def __init__(self, command: str, reason: str):
        self.command = command
        self.reason = reason
        super().__init__(f"Command '{command}' is not allowed: {reason}")


def get_disallowed_commands() -> frozenset[str]:
    """
    Get the set of disallowed commands from Django settings.

    Returns DEFAULT_DISALLOWED_COMMANDS unless overridden by
    DJANGOCOMMAND_DISALLOWED_COMMANDS in settings.

    Note: If DJANGOCOMMAND_ALLOWED_COMMANDS is set, the blocklist is
    ignored for execution purposes, but this function still returns
    the blocklist for discovery filtering.
    """
    from django.conf import settings

    disallowed = getattr(
        settings,
        'DJANGOCOMMAND_DISALLOWED_COMMANDS',
        DEFAULT_DISALLOWED_COMMANDS
    )
    return frozenset(disallowed)


def is_command_allowed(command: str) -> tuple[bool, str]:
    """
    Check if a command is allowed to execute.

    Reads from Django settings:
    - DJANGOCOMMAND_ALLOWED_COMMANDS: If set (non-empty), ONLY these commands
      are allowed. The blocklist is ignored.
    - DJANGOCOMMAND_DISALLOWED_COMMANDS: Commands that cannot be executed.
      Defaults to DEFAULT_DISALLOWED_COMMANDS.

    Args:
        command: The management command name to check

    Returns:
        Tuple of (is_allowed, reason).
        If allowed: (True, "")
        If not allowed: (False, "reason why")
    """
    # Lazy import to avoid Django settings access at module load time
    from django.conf import settings

    # Check allowlist first (if set, it takes precedence)
    allowed_commands = getattr(settings, 'DJANGOCOMMAND_ALLOWED_COMMANDS', None)
    if allowed_commands:
        # Convert to frozenset for O(1) lookup
        allowed_set = frozenset(allowed_commands)
        if command in allowed_set:
            return True, ""
        else:
            return False, f"not in DJANGOCOMMAND_ALLOWED_COMMANDS allowlist"

    # Check blocklist
    disallowed_commands = getattr(
        settings,
        'DJANGOCOMMAND_DISALLOWED_COMMANDS',
        DEFAULT_DISALLOWED_COMMANDS
    )
    # Convert to frozenset for O(1) lookup
    disallowed_set = frozenset(disallowed_commands)

    if command in disallowed_set:
        return False, "in DJANGOCOMMAND_DISALLOWED_COMMANDS blocklist"

    return True, ""


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
