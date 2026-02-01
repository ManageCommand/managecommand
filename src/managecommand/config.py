"""
Configuration for ManageCommand client.

Loads configuration from Django settings.
"""

from dataclasses import dataclass, field
from typing import List
from urllib.parse import urlparse

from django.conf import settings


class ConfigurationError(Exception):
    """Raised when configuration is invalid or missing."""
    pass


# Default hosts that are allowed to use HTTP (when setting is omitted)
DEFAULT_ALLOW_HTTP_HOSTS = ['localhost', '127.0.0.1', '::1']

# Default server URL (the hosted service)
DEFAULT_SERVER_URL = 'https://app.managecommand.com'


@dataclass
class RunnerConfig:
    """Runner configuration container."""

    server_url: str
    api_key: str
    heartbeat_interval: int = 30
    request_timeout: int = 30
    max_retries: int = 3
    allow_http_hosts: List[str] = field(default_factory=lambda: DEFAULT_ALLOW_HTTP_HOSTS.copy())
    metadataonly_commands: tuple[str, ...] = field(default_factory=tuple)

    def validate(self):
        """Validate configuration and raise ConfigurationError if invalid."""
        if not self.api_key:
            raise ConfigurationError(
                'MANAGECOMMAND_API_KEY is required in Django settings.'
            )

        if not self.api_key.startswith('dc_'):
            raise ConfigurationError(
                'MANAGECOMMAND_API_KEY must start with "dc_". '
                'Check your API key format.'
            )

        # Validate URL format
        parsed = urlparse(self.server_url)
        if not parsed.scheme or not parsed.netloc:
            raise ConfigurationError(
                f'MANAGECOMMAND_SERVER_URL must be a valid URL. Got: {self.server_url}'
            )

        # HTTPS enforcement (except for allowed HTTP hosts)
        if parsed.scheme != 'https' and parsed.hostname not in self.allow_http_hosts:
            raise ConfigurationError(
                f'MANAGECOMMAND_SERVER_URL must use HTTPS. '
                f'Got: {self.server_url}. '
                f'To allow HTTP, add "{parsed.hostname}" to MANAGECOMMAND_ALLOW_HTTP_HOSTS.'
            )

        if self.heartbeat_interval < 5:
            raise ConfigurationError(
                f'MANAGECOMMAND_HEARTBEAT_INTERVAL must be at least 5 seconds. '
                f'Got: {self.heartbeat_interval}'
            )


def load_config() -> RunnerConfig:
    """
    Load runner configuration from Django settings.

    Required settings:
    - MANAGECOMMAND_API_KEY: API key for authentication (dc_xxx format)

    Optional settings:
    - MANAGECOMMAND_SERVER_URL: Server base URL (default: https://app.managecommand.com)
    - MANAGECOMMAND_HEARTBEAT_INTERVAL: Heartbeat interval in seconds (default: 30)
    - MANAGECOMMAND_REQUEST_TIMEOUT: Request timeout in seconds (default: 30)
    - MANAGECOMMAND_MAX_RETRIES: Max retries for failed requests (default: 3)
    - MANAGECOMMAND_ALLOW_HTTP_HOSTS: List of hosts allowed to use HTTP
                                       (default: ['localhost', '127.0.0.1', '::1'])
    - MANAGECOMMAND_METADATAONLY_COMMANDS: Tuple of command names that run in
                                            metadata-only mode (no output capture)
                                            (default: ())

    Security settings (see managecommand.security):
    - MANAGECOMMAND_ALLOWED_COMMANDS: If set, ONLY these commands can execute
                                       (blocklist is ignored)
    - MANAGECOMMAND_DISALLOWED_COMMANDS: Commands that cannot execute
                                          (default: DEFAULT_DISALLOWED_COMMANDS)
    """
    # Validate metadataonly_commands before converting to tuple
    # (a string would be silently converted to tuple of characters)
    raw_metadataonly = getattr(settings, 'MANAGECOMMAND_METADATAONLY_COMMANDS', ())
    if isinstance(raw_metadataonly, str):
        raise ConfigurationError(
            'MANAGECOMMAND_METADATAONLY_COMMANDS must be a tuple or list, not a string. '
            'Use ("command_name",) with a trailing comma for single items.'
        )

    config = RunnerConfig(
        server_url=getattr(settings, 'MANAGECOMMAND_SERVER_URL', DEFAULT_SERVER_URL),
        api_key=getattr(settings, 'MANAGECOMMAND_API_KEY', ''),
        heartbeat_interval=getattr(settings, 'MANAGECOMMAND_HEARTBEAT_INTERVAL', 30),
        request_timeout=getattr(settings, 'MANAGECOMMAND_REQUEST_TIMEOUT', 30),
        max_retries=getattr(settings, 'MANAGECOMMAND_MAX_RETRIES', 3),
        allow_http_hosts=list(getattr(
            settings,
            'MANAGECOMMAND_ALLOW_HTTP_HOSTS',
            DEFAULT_ALLOW_HTTP_HOSTS
        )),
        metadataonly_commands=tuple(raw_metadataonly),
    )

    config.validate()
    return config
