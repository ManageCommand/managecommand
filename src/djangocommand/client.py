"""
HTTP client for DjangoCommand server communication.

Provides:
- API key authentication
- Replay protection (timestamp + nonce headers)
- HTTPS enforcement (except localhost)
- Retry logic with exponential backoff
"""

import logging
import time
import uuid
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class DjangoCommandClientError(Exception):
    """Base exception for client errors."""
    pass


class HTTPSRequiredError(DjangoCommandClientError):
    """Raised when HTTPS is required but HTTP was used."""
    pass


class AuthenticationError(DjangoCommandClientError):
    """Raised when authentication fails."""
    pass


class DjangoCommandClient:
    """
    HTTP client for communicating with DjangoCommand server.

    Handles:
    - Bearer token authentication
    - Replay protection headers (X-Request-Timestamp, X-Request-Nonce)
    - HTTPS enforcement (configurable allowed HTTP hosts)
    - Automatic retries with exponential backoff
    """

    DEFAULT_ALLOW_HTTP_HOSTS = ['localhost', '127.0.0.1', '::1']

    def __init__(
        self,
        server_url: str,
        api_key: str,
        timeout: int = 30,
        max_retries: int = 3,
        allow_http_hosts: list[str] = None,
    ):
        """
        Initialize the client.

        Args:
            server_url: Base URL of DjangoCommand server (e.g., https://app.djangocommand.com)
            api_key: API key for authentication (dc_xxx format)
            timeout: Request timeout in seconds
            max_retries: Maximum number of retries for failed requests
            allow_http_hosts: List of hostnames allowed to use HTTP (default: localhost only)
        """
        self.server_url = server_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.allow_http_hosts = (
            allow_http_hosts if allow_http_hosts is not None
            else self.DEFAULT_ALLOW_HTTP_HOSTS.copy()
        )

        # Validate URL security
        self._validate_url_security()

        # Set up session with retry logic
        self.session = requests.Session()
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=1,  # 1s, 2s, 4s...
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=['HEAD', 'GET', 'POST', 'PUT', 'DELETE'],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

    def _validate_url_security(self):
        """Validate that HTTPS is used unless host is in allow_http_hosts."""
        parsed = urlparse(self.server_url)

        is_http_allowed = parsed.hostname in self.allow_http_hosts
        is_https = parsed.scheme == 'https'

        if not is_https and not is_http_allowed:
            raise HTTPSRequiredError(
                f"HTTPS is required for non-localhost URLs. Got: {self.server_url}. "
                f"To allow HTTP, add '{parsed.hostname}' to allow_http_hosts."
            )

    def _get_headers(self) -> dict:
        """Get headers for API requests including replay protection."""
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
            'X-Request-Timestamp': str(int(time.time())),
            'X-Request-Nonce': str(uuid.uuid4()),
        }

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        """
        Make an authenticated request to the server.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., '/api/agent/heartbeat/')
            **kwargs: Additional arguments passed to requests

        Returns:
            Parsed JSON response

        Raises:
            AuthenticationError: If authentication fails
            DjangoCommandClientError: For other API errors
        """
        url = f'{self.server_url}{endpoint}'
        headers = self._get_headers()

        # Merge any additional headers
        if 'headers' in kwargs:
            headers.update(kwargs.pop('headers'))

        try:
            response = self.session.request(
                method,
                url,
                headers=headers,
                timeout=self.timeout,
                **kwargs
            )

            # Handle authentication errors
            if response.status_code == 401:
                raise AuthenticationError('Invalid API key')

            # Handle other errors
            if response.status_code >= 400:
                try:
                    error_data = response.json()
                    error_msg = error_data.get('error', response.text)
                except ValueError:
                    error_msg = response.text
                raise DjangoCommandClientError(
                    f'API error ({response.status_code}): {error_msg}'
                )

            return response.json()

        except requests.exceptions.Timeout:
            raise DjangoCommandClientError(f'Request timed out: {url}')
        except requests.exceptions.ConnectionError as e:
            raise DjangoCommandClientError(f'Connection failed: {e}')

    def get(self, endpoint: str, **kwargs) -> dict:
        """Make a GET request."""
        return self._request('GET', endpoint, **kwargs)

    def post(self, endpoint: str, data: dict = None, **kwargs) -> dict:
        """Make a POST request with JSON body."""
        return self._request('POST', endpoint, json=data, **kwargs)

    # Convenience methods for agent API

    def heartbeat(
        self,
        agent_version: str,
        python_version: str,
        django_version: str,
        commands_hash: str,
    ) -> dict:
        """
        Send heartbeat to server.

        Returns:
            dict with keys: ok, commands_in_sync, pending_executions
        """
        return self.post('/api/agent/heartbeat/', {
            'agent_version': agent_version,
            'python_version': python_version,
            'django_version': django_version,
            'commands_hash': commands_hash,
        })

    def sync_commands(self, commands: list[dict]) -> dict:
        """
        Sync commands with server.

        Args:
            commands: List of command dicts with name, app_label, help_text

        Returns:
            dict with keys: ok, synced_count, commands_hash
        """
        return self.post('/api/agent/commands/sync/', {
            'commands': commands,
        })

    def get_pending_executions(self) -> list[dict]:
        """
        Get pending executions for this agent.

        Returns:
            List of execution dicts
        """
        response = self.get('/api/agent/pending/')
        return response.get('executions', [])

    def start_execution(self, execution_id: str) -> dict:
        """Mark an execution as started."""
        return self.post(f'/api/agent/executions/{execution_id}/start/')

    def send_output(
        self,
        execution_id: str,
        segments: list[dict],
        is_stderr: bool,
        chunk_number: int,
    ) -> dict:
        """
        Send output chunk for a running execution with per-line timestamps.

        Args:
            execution_id: Execution UUID
            segments: List of segment dicts with 'timestamp' and 'content' keys.
                      timestamp=None means continuation of previous line.
            is_stderr: Whether this chunk is from stderr
            chunk_number: Sequence number for ordering and idempotency
        """
        return self.post(f'/api/agent/executions/{execution_id}/output/', {
            'chunk_number': chunk_number,
            'segments': segments,
            'is_stderr': is_stderr,
        })

    def complete_execution(
        self,
        execution_id: str,
        exit_code: int,
        status: str = 'success',
    ) -> dict:
        """
        Mark an execution as complete.

        Args:
            execution_id: Execution UUID
            exit_code: Process exit code
            status: One of 'success', 'failed', 'cancelled'
        """
        return self.post(f'/api/agent/executions/{execution_id}/complete/', {
            'exit_code': exit_code,
            'status': status,
        })

    def check_cancel_status(self, execution_id: str) -> dict:
        """
        Check if cancellation has been requested.

        Returns:
            dict with keys: cancel_requested, force_kill
        """
        return self.get(f'/api/agent/executions/{execution_id}/cancel-status/')
