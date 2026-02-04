"""
Security utilities for ManageCommand client.

Handles command allowlisting/blocklisting based on Django settings.

Security Model:
- DEFAULT: Allowlist approach (most secure) - only commands in
  MANAGECOMMAND_ALLOWED_COMMANDS can run
- OPTIONAL: Blocklist approach - set MANAGECOMMAND_USE_BLOCKLIST = True
  to allow all commands except those in MANAGECOMMAND_DISALLOWED_COMMANDS

Bound Commands:
- Commands in MANAGECOMMAND_BOUND_COMMANDS can only be run with specific
  predefined argument sets, providing fine-grained control over command usage.
"""

import logging

from .constants import DEFAULT_ALLOWED_COMMANDS, DEFAULT_DISALLOWED_COMMANDS
from .discovery import get_bound_commands

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

    return getattr(settings, "MANAGECOMMAND_USE_BLOCKLIST", False)


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
        settings, "MANAGECOMMAND_ALLOWED_COMMANDS", DEFAULT_ALLOWED_COMMANDS
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
        settings, "MANAGECOMMAND_DISALLOWED_COMMANDS", DEFAULT_DISALLOWED_COMMANDS
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
    use_blocklist = getattr(settings, "MANAGECOMMAND_USE_BLOCKLIST", False)

    if use_blocklist:
        # Blocklist mode: allow all except blocked commands
        disallowed_commands = getattr(
            settings, "MANAGECOMMAND_DISALLOWED_COMMANDS", DEFAULT_DISALLOWED_COMMANDS
        )
        disallowed_set = frozenset(disallowed_commands)

        if command in disallowed_set:
            return False, "in MANAGECOMMAND_DISALLOWED_COMMANDS blocklist"

        return True, ""
    else:
        # Allowlist mode (default): only allow explicitly listed commands
        allowed_commands = getattr(
            settings, "MANAGECOMMAND_ALLOWED_COMMANDS", DEFAULT_ALLOWED_COMMANDS
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


def is_command_bound(command: str) -> bool:
    """
    Check if a command has bound arguments.

    A bound command can only be executed with specific predefined argument sets.

    Args:
        command: The management command name to check

    Returns:
        True if the command is bound, False otherwise
    """
    bound_commands = get_bound_commands()
    return command in bound_commands


def get_allowed_args_for_command(command: str) -> list[str] | None:
    """
    Get the allowed argument sets for a bound command.

    Args:
        command: The management command name

    Returns:
        List of allowed arg strings if command is bound, None if unbound
    """
    bound_commands = get_bound_commands()
    if command not in bound_commands:
        return None
    return [arg_set["args"] for arg_set in bound_commands[command]]


def are_args_allowed(command: str, args: str) -> tuple[bool, str]:
    """
    Check if the given arguments are allowed for a command.

    For unbound commands, any args are allowed.
    For bound commands, args must exactly match one of the allowed arg sets.

    Args:
        command: The management command name
        args: The argument string to check

    Returns:
        Tuple of (is_allowed, reason).
        If allowed: (True, "")
        If not allowed: (False, "reason why")
    """
    bound_commands = get_bound_commands()

    # Unbound commands allow any args
    if command not in bound_commands:
        return True, ""

    # Bound commands must match exactly one of the allowed arg sets
    allowed_args = [arg_set["args"] for arg_set in bound_commands[command]]

    # Normalize args for comparison (strip whitespace)
    normalized_args = args.strip() if args else ""

    for allowed in allowed_args:
        if normalized_args == allowed.strip():
            return True, ""

    # Build error message with allowed options
    allowed_str = ", ".join(f'"{a}"' for a in allowed_args)
    return (
        False,
        f"args '{args}' not in allowed set for bound command. Allowed: {allowed_str}",
    )


class ArgsDisallowedError(Exception):
    """Raised when command arguments are not allowed for a bound command."""

    def __init__(self, command: str, args: str, reason: str):
        self.command = command
        self.args = args
        self.reason = reason
        super().__init__(
            f"Arguments '{args}' not allowed for command '{command}': {reason}"
        )


def check_args_allowed(command: str, args: str) -> None:
    """
    Check if arguments are allowed for a command, raising ArgsDisallowedError if not.

    Args:
        command: The management command name
        args: The argument string to check

    Raises:
        ArgsDisallowedError: If the arguments are not allowed
    """
    allowed, reason = are_args_allowed(command, args)
    if not allowed:
        raise ArgsDisallowedError(command, args, reason)
