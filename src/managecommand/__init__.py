"""
ManageCommand client library.

Run, schedule, and audit Django management commands without SSH access.

Usage:
    1. Install: pip install managecommand
    2. Add to INSTALLED_APPS: 'managecommand'
    3. Configure in settings.py:
        MANAGECOMMAND_API_KEY = "dc_your_api_key"
    4. Run: python manage.py managecommand start
"""

__version__ = "0.1.0"

from .client import ManageCommandClient, ManageCommandClientError
from .config import DEFAULT_SERVER_URL, ConfigurationError, RunnerConfig, load_config
from .constants import DEFAULT_ALLOWED_COMMANDS, DEFAULT_DISALLOWED_COMMANDS
from .discovery import compute_commands_hash, discover_commands
from .runner import Runner
from .security import (
    CommandDisallowedError,
    check_command_allowed,
    get_allowed_commands,
    get_disallowed_commands,
    is_command_allowed,
    is_using_blocklist,
)

__all__ = [
    "DEFAULT_ALLOWED_COMMANDS",
    "DEFAULT_DISALLOWED_COMMANDS",
    "DEFAULT_SERVER_URL",
    "CommandDisallowedError",
    "ConfigurationError",
    "ManageCommandClient",
    "ManageCommandClientError",
    "Runner",
    "RunnerConfig",
    "check_command_allowed",
    "compute_commands_hash",
    "discover_commands",
    "get_allowed_commands",
    "get_disallowed_commands",
    "is_command_allowed",
    "is_using_blocklist",
    "load_config",
]
