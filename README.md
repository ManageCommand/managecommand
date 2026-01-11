# djangocommand

Python client for [DjangoCommand](https://djangocommand.com) - run, schedule, and audit Django management commands without SSH access.

## Installation

```bash
pip install djangocommand
```

## Quick Start

1. Add to your `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    # ...
    'djangocommand',
]
```

2. Add your API key to `settings.py`:

```python
DJANGOCOMMAND_API_KEY = "dc_your_api_key_here"
```

3. Start the agent:

```bash
python manage.py djangocommand start
```

The agent will connect to DjangoCommand, sync your available commands, and start polling for executions.

## Configuration

### Required

```python
# Your project's API key (get this from the DjangoCommand dashboard)
DJANGOCOMMAND_API_KEY = "dc_..."
```

### Optional

```python
# Server URL (default: https://app.djangocommand.com)
DJANGOCOMMAND_SERVER_URL = "https://app.djangocommand.com"

# Agent heartbeat interval in seconds (default: 30, minimum: 5)
DJANGOCOMMAND_HEARTBEAT_INTERVAL = 30

# HTTP request timeout in seconds (default: 30)
DJANGOCOMMAND_REQUEST_TIMEOUT = 30

# Max retries for failed requests (default: 3)
DJANGOCOMMAND_MAX_RETRIES = 3

# Hosts allowed to use HTTP instead of HTTPS (default: localhost only)
DJANGOCOMMAND_ALLOW_HTTP_HOSTS = ['localhost', '127.0.0.1', '::1']
```

## Command Security

The agent includes a security layer that controls which commands can be executed remotely. This protects against accidental or malicious execution of dangerous commands.

### Default Blocklist

By default, these commands are blocked:

| Category | Commands |
|----------|----------|
| Database destruction | `flush`, `sqlflush`, `reset_db` |
| Interactive shells | `shell`, `shell_plus`, `dbshell` |
| Development servers | `runserver`, `runserver_plus`, `testserver` |
| Security sensitive | `createsuperuser`, `changepassword` |
| File modifications | `makemigrations`, `squashmigrations` |
| Other dangerous | `drop_test_database`, `delete_squashed_migrations`, `clean_pyc` |

Blocked commands are:
- **Not synced** to the server (won't appear in the UI)
- **Rejected at runtime** with an error message (defense in depth)

### Extending the Blocklist

Add more commands to the default blocklist:

```python
from djangocommand import DEFAULT_DISALLOWED_COMMANDS

DJANGOCOMMAND_DISALLOWED_COMMANDS = DEFAULT_DISALLOWED_COMMANDS + (
    'my_dangerous_command',
    'another_risky_command',
)
```

### Removing Commands from the Blocklist

Use list comprehension to remove specific commands while keeping future updates:

```python
from djangocommand import DEFAULT_DISALLOWED_COMMANDS

# Allow 'createsuperuser' but keep everything else blocked
DJANGOCOMMAND_DISALLOWED_COMMANDS = tuple(
    cmd for cmd in DEFAULT_DISALLOWED_COMMANDS
    if cmd != 'createsuperuser'
)
```

To remove multiple commands:

```python
from djangocommand import DEFAULT_DISALLOWED_COMMANDS

ALLOW_THESE = {'createsuperuser', 'makemigrations'}

DJANGOCOMMAND_DISALLOWED_COMMANDS = tuple(
    cmd for cmd in DEFAULT_DISALLOWED_COMMANDS
    if cmd not in ALLOW_THESE
)
```

### Using an Allowlist Instead

For maximum security, use an allowlist. When set, **only** these commands can run (the blocklist is ignored):

```python
# Only these 3 commands can be executed remotely
DJANGOCOMMAND_ALLOWED_COMMANDS = (
    'migrate',
    'collectstatic',
    'clearsessions',
)
```

### Replacing the Blocklist Entirely

If you need full control, define your own blocklist from scratch:

```python
# Your custom blocklist (won't receive updates from new client versions)
DJANGOCOMMAND_DISALLOWED_COMMANDS = (
    'flush',
    'shell',
    'runserver',
)
```

> **Note:** This approach won't automatically include new dangerous commands that may be added to `DEFAULT_DISALLOWED_COMMANDS` in future versions. Prefer extending the default list when possible.

## Running the Agent

### Foreground (development)

```bash
python manage.py djangocommand start
```

### Background (production)

Use a process manager like systemd or supervisor:

```ini
# /etc/supervisor/conf.d/djangocommand.conf
[program:djangocommand]
command=/path/to/venv/bin/python manage.py djangocommand start
directory=/path/to/your/project
user=www-data
autostart=true
autorestart=true
```

### Docker

```dockerfile
CMD ["python", "manage.py", "djangocommand", "start"]
```

## Programmatic Usage

You can also use the client programmatically:

```python
from djangocommand import Agent

# Create agent from Django settings
agent = Agent.from_settings()

# Run the agent (blocks until stopped)
agent.run()

# Or just run a single heartbeat cycle
agent.run_once()
```

### Checking Command Permissions

```python
from djangocommand import is_command_allowed, DEFAULT_DISALLOWED_COMMANDS

# Check if a command is allowed
allowed, reason = is_command_allowed('flush')
# (False, 'in DJANGOCOMMAND_DISALLOWED_COMMANDS blocklist')

allowed, reason = is_command_allowed('migrate')
# (True, '')

# Get the current blocklist
from djangocommand import get_disallowed_commands
blocked = get_disallowed_commands()  # frozenset of command names
```

## License

MIT
