"""
DjangoCommand client library.

Run, schedule, and audit Django management commands without SSH access.

Usage:
    1. Install: pip install djangocommand
    2. Add to INSTALLED_APPS: 'djangocommand'
    3. Configure in settings.py:
        DJANGOCOMMAND_API_KEY = "dc_your_api_key"
    4. Run: python manage.py djangocommand start
"""

__version__ = "0.1.0"

from .agent import Agent
from .client import DjangoCommandClient, DjangoCommandClientError
from .config import AgentConfig, ConfigurationError, DEFAULT_SERVER_URL, load_config
from .constants import DEFAULT_DISALLOWED_COMMANDS
from .discovery import compute_commands_hash, discover_commands
from .security import (
    CommandDisallowedError,
    check_command_allowed,
    get_disallowed_commands,
    is_command_allowed,
)

__all__ = [
    'Agent',
    'AgentConfig',
    'CommandDisallowedError',
    'ConfigurationError',
    'DEFAULT_DISALLOWED_COMMANDS',
    'DEFAULT_SERVER_URL',
    'DjangoCommandClient',
    'DjangoCommandClientError',
    'check_command_allowed',
    'compute_commands_hash',
    'discover_commands',
    'get_disallowed_commands',
    'is_command_allowed',
    'load_config',
]
