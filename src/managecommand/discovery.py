"""
Django management command discovery.

Discovers all available management commands in the Django project
and computes a hash for delta sync.
"""

import hashlib
import json
import logging
from importlib import import_module

from django.core.management import get_commands, load_command_class

logger = logging.getLogger(__name__)


def _clear_commands_cache():
    """Clear Django's get_commands() cache to discover new commands."""
    # Django's get_commands() uses @functools.cache, so we need to clear it
    if hasattr(get_commands, 'cache_clear'):
        get_commands.cache_clear()
        logger.debug('Cleared get_commands cache')


def _get_command_help(command_instance, name: str) -> str:
    """
    Get the full --help output for a command.

    Uses the command's argument parser to generate the same help text
    that would be shown with `manage.py <command> --help`.

    Note: load_command_class() returns an instance despite the name.
    """
    try:
        parser = command_instance.create_parser('manage.py', name)
        return parser.format_help()
    except Exception as e:
        logger.debug(f'Failed to get full help for {name}: {e}')
        # Fall back to short help attribute
        return getattr(command_instance, 'help', '') or ''


def discover_commands(
    exclude: list[str] = None,
    include: list[str] = None,
) -> list[dict]:
    """
    Discover all management commands in the Django project.

    Args:
        exclude: List of command names to exclude (blocklist mode).
            Commands in this list will be skipped.
        include: List of command names to include (allowlist mode).
            If provided, ONLY commands in this list will be returned.
            Takes precedence over exclude.

    Returns:
        List of command dicts with name, app_label, help_text
    """
    # Clear cache to discover newly added commands
    _clear_commands_cache()

    exclude_set = set(exclude or [])
    include_set = set(include) if include else None
    commands = []

    # get_commands() returns {command_name: app_label_or_module}
    for name, app in get_commands().items():
        # Allowlist mode: only include if in include_set
        if include_set is not None:
            if name not in include_set:
                continue
        # Blocklist mode: skip if in exclude_set
        elif name in exclude_set:
            continue

        try:
            # Load the command class to get help text
            command_class = load_command_class(app, name)

            # Get full --help output (includes usage and argument descriptions)
            help_text = _get_command_help(command_class, name)

            # Get app label (handle both module paths and app labels)
            if isinstance(app, str):
                app_label = app
            else:
                app_label = app.__name__ if hasattr(app, '__name__') else str(app)

            commands.append({
                'name': name,
                'app_label': app_label,
                'help_text': help_text,
            })
        except Exception as e:
            logger.warning(f'Failed to load command {name}: {e}')
            # Still include the command with minimal info
            commands.append({
                'name': name,
                'app_label': str(app) if isinstance(app, str) else '',
                'help_text': '',
            })

    return commands


def compute_commands_hash(commands: list[dict]) -> str:
    """
    Compute deterministic SHA-256 hash of command list.

    This hash is used for delta sync - only sync when hash changes.

    Args:
        commands: List of command dicts

    Returns:
        Hash string in format "sha256:abc123..."
    """
    # Sort by name for determinism
    sorted_cmds = sorted(commands, key=lambda c: c.get('name', ''))

    # Create canonical JSON (sorted keys, minimal whitespace)
    canonical = json.dumps(sorted_cmds, sort_keys=True, separators=(',', ':'))

    # Compute SHA-256
    hash_value = hashlib.sha256(canonical.encode()).hexdigest()

    return f'sha256:{hash_value}'


def get_commands_with_hash(
    exclude: list[str] = None,
    include: list[str] = None,
) -> tuple[list[dict], str]:
    """
    Discover commands and compute their hash.

    Convenience function that returns both commands and hash.

    Args:
        exclude: List of command names to exclude (blocklist mode)
        include: List of command names to include (allowlist mode).
            If provided, takes precedence over exclude.

    Returns:
        Tuple of (commands_list, commands_hash)
    """
    commands = discover_commands(exclude=exclude, include=include)
    commands_hash = compute_commands_hash(commands)
    return commands, commands_hash
