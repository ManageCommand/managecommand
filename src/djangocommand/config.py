"""
Configuration for DjangoCommand client.

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
DEFAULT_SERVER_URL = 'https://app.djangocommand.com'


@dataclass
class AgentConfig:
    """Agent configuration container."""

    server_url: str
    api_key: str
    heartbeat_interval: int = 30
    request_timeout: int = 30
    max_retries: int = 3
    allow_http_hosts: List[str] = field(default_factory=lambda: DEFAULT_ALLOW_HTTP_HOSTS.copy())

    def validate(self):
        """Validate configuration and raise ConfigurationError if invalid."""
        if not self.api_key:
            raise ConfigurationError(
                'DJANGOCOMMAND_API_KEY is required in Django settings.'
            )

        if not self.api_key.startswith('dc_'):
            raise ConfigurationError(
                'DJANGOCOMMAND_API_KEY must start with "dc_". '
                'Check your API key format.'
            )

        # Validate URL format
        parsed = urlparse(self.server_url)
        if not parsed.scheme or not parsed.netloc:
            raise ConfigurationError(
                f'DJANGOCOMMAND_SERVER_URL must be a valid URL. Got: {self.server_url}'
            )

        # HTTPS enforcement (except for allowed HTTP hosts)
        if parsed.scheme != 'https' and parsed.hostname not in self.allow_http_hosts:
            raise ConfigurationError(
                f'DJANGOCOMMAND_SERVER_URL must use HTTPS. '
                f'Got: {self.server_url}. '
                f'To allow HTTP, add "{parsed.hostname}" to DJANGOCOMMAND_ALLOW_HTTP_HOSTS.'
            )

        if self.heartbeat_interval < 5:
            raise ConfigurationError(
                f'DJANGOCOMMAND_HEARTBEAT_INTERVAL must be at least 5 seconds. '
                f'Got: {self.heartbeat_interval}'
            )


def load_config() -> AgentConfig:
    """
    Load agent configuration from Django settings.

    Required settings:
    - DJANGOCOMMAND_API_KEY: API key for authentication (dc_xxx format)

    Optional settings:
    - DJANGOCOMMAND_SERVER_URL: Server base URL (default: https://app.djangocommand.com)
    - DJANGOCOMMAND_HEARTBEAT_INTERVAL: Heartbeat interval in seconds (default: 30)
    - DJANGOCOMMAND_REQUEST_TIMEOUT: Request timeout in seconds (default: 30)
    - DJANGOCOMMAND_MAX_RETRIES: Max retries for failed requests (default: 3)
    - DJANGOCOMMAND_ALLOW_HTTP_HOSTS: List of hosts allowed to use HTTP
                                       (default: ['localhost', '127.0.0.1', '::1'])

    Security settings (see djangocommand.security):
    - DJANGOCOMMAND_ALLOWED_COMMANDS: If set, ONLY these commands can execute
                                       (blocklist is ignored)
    - DJANGOCOMMAND_DISALLOWED_COMMANDS: Commands that cannot execute
                                          (default: DEFAULT_DISALLOWED_COMMANDS)
    """
    config = AgentConfig(
        server_url=getattr(settings, 'DJANGOCOMMAND_SERVER_URL', DEFAULT_SERVER_URL),
        api_key=getattr(settings, 'DJANGOCOMMAND_API_KEY', ''),
        heartbeat_interval=getattr(settings, 'DJANGOCOMMAND_HEARTBEAT_INTERVAL', 30),
        request_timeout=getattr(settings, 'DJANGOCOMMAND_REQUEST_TIMEOUT', 30),
        max_retries=getattr(settings, 'DJANGOCOMMAND_MAX_RETRIES', 3),
        allow_http_hosts=list(getattr(
            settings,
            'DJANGOCOMMAND_ALLOW_HTTP_HOSTS',
            DEFAULT_ALLOW_HTTP_HOSTS
        )),
    )

    config.validate()
    return config
