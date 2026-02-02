"""
Django management command discovery.

Discovers all available management commands in the Django project
and computes a hash for delta sync.

Supports bound commands via MANAGECOMMAND_BOUND_COMMANDS setting:
    MANAGECOMMAND_BOUND_COMMANDS = {
        "runscript": [
            {"args": "backup_db --verbose", "label": "Backup database"},
            {"args": "cleanup --days=30", "label": "Cleanup old data"},
        ],
        # Or without labels (args used as label):
        "other_command": ["--option1", "--option2"],
    }
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


def get_bound_commands() -> dict[str, list[dict]]:
    """
    Get bound command configurations from Django settings.

    Returns a dict mapping command names to lists of allowed argument sets.
    Each argument set is a dict with 'args' and 'label' keys.

    The setting MANAGECOMMAND_BOUND_COMMANDS can be:
    - A dict with command names as keys and lists of arg sets as values
    - Each arg set can be a dict {"args": "...", "label": "..."} or just a string

    Example:
        MANAGECOMMAND_BOUND_COMMANDS = {
            "runscript": [
                {"args": "backup_db --verbose", "label": "Backup database"},
                {"args": "cleanup --days=30", "label": "Cleanup old data"},
            ],
            "other_command": ["--option1", "--option2"],  # labels auto-generated
        }
    """
    from django.conf import settings

    raw_config = getattr(settings, 'MANAGECOMMAND_BOUND_COMMANDS', {})
    if not raw_config:
        return {}

    result = {}
    for command_name, arg_sets in raw_config.items():
        normalized_sets = []
        for arg_set in arg_sets:
            if isinstance(arg_set, str):
                # Simple string form: use args as label
                normalized_sets.append({
                    'args': arg_set,
                    'label': arg_set or '(no arguments)',
                })
            elif isinstance(arg_set, dict):
                # Dict form: extract args and label
                args = arg_set.get('args', '')
                label = arg_set.get('label', args or '(no arguments)')
                normalized_sets.append({
                    'args': args,
                    'label': label,
                })
            else:
                logger.warning(
                    f'Invalid bound args entry for {command_name}: {arg_set}'
                )
        if normalized_sets:
            result[command_name] = normalized_sets

    return result


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
        List of command dicts with name, app_label, help_text, and optionally bound_args
    """
    # Clear cache to discover newly added commands
    _clear_commands_cache()

    exclude_set = set(exclude or [])
    include_set = set(include) if include else None
    commands = []

    # Get bound commands configuration
    bound_commands = get_bound_commands()

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

            cmd_data = {
                'name': name,
                'app_label': app_label,
                'help_text': help_text,
            }

            # Add bound_args if this command has restrictions
            if name in bound_commands:
                cmd_data['bound_args'] = bound_commands[name]
                logger.debug(
                    f'Command {name} is bound with {len(bound_commands[name])} arg sets'
                )

            commands.append(cmd_data)
        except Exception as e:
            logger.warning(f'Failed to load command {name}: {e}')
            # Still include the command with minimal info
            cmd_data = {
                'name': name,
                'app_label': str(app) if isinstance(app, str) else '',
                'help_text': '',
            }
            # Include bound_args even for failed loads
            if name in bound_commands:
                cmd_data['bound_args'] = bound_commands[name]
            commands.append(cmd_data)

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
