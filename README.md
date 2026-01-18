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

3. Start the runner:

```bash
python manage.py djangocommand start
```

The runner will connect to DjangoCommand, sync your available commands, and start polling for executions.


## Configuration

### API key

Your project's API key (get this from the DjangoCommand dashboard)

```python
DJANGOCOMMAND_API_KEY = "dc_..."
```

#### Enabled (allowed) commands

Enable additional commands:

```python
import djangocommand
DJANGOCOMMAND_ALLOWED_COMMANDS = tuple(
    *djangocommand.DEFAULT_ALLOWED_COMMANDS,
    "my_custom_command",
    "another_command",
)
```

Or explicitly allow only specific commands (skip the default ones):

```python
DJANGOCOMMAND_ALLOWED_COMMANDS = (
    "my_custom_command",
    "another_command",
)
```

Order does not matter.

By default, only commands in `DEFAULT_ALLOWED_COMMANDS` can be executed remotely:


Default value:

```python
DEFAULT_ALLOWED_COMMANDS = (
    # Database
    "migrate", "showmigrations", "dbbackup", "createcachetable",
    # Static files
    "collectstatic", "findstatic",
    # Maintenance
    "clearsessions", "check", "sendtestemail", "diffsettings",
    # Data
    "dumpdata", "inspectdb",
    # Testing
    "test",
    # django-extensions
    "show_urls", "validate_templates", "clear_cache",
    # Wagtail
    "fixtree", "publish_scheduled", "update_index",
)
```

> **DISCLAIMER**: This list is not fully vetted. You are encouraged to only allow commands that you trust and have vetted yourself.


### Alternative - Blocklist Mode

Alternative to the "ALLOWED" list is the "DISALLOWED" list. 

In this mode all commands are allowed except for the ones in the blocklist.

This mode is inherently less secure because newly added commands are automatically allowed.

> **CAUTION**: Any new commands will by allowed by default in this mode!

```python
DJANGOCOMMAND_USE_BLOCKLIST = True
```

Default value:

```python
DEFAULT_DISALLOWED_COMMANDS = (
    # Database destruction
    "flush", "sqlflush", "reset_db", "dbrestore", "loaddata",
    # Interactive shells
    "shell", "shell_plus", "dbshell",
    # Development servers
    "runserver", "runserver_plus", "testserver",
    # Security sensitive
    "createsuperuser", "changepassword",
    # File modifications
    "makemigrations", "squashmigrations",
)
```

> **DISCLAIMER**: This list is not exhaustive. You are responsible for ensuring that only safe commands are allowed.


### Optional / Advanced Settings

```python
# Server URL (default: https://app.djangocommand.com)
DJANGOCOMMAND_SERVER_URL = "https://app.djangocommand.com"

# Runner heartbeat interval in seconds (default: 30, minimum: 5)
DJANGOCOMMAND_HEARTBEAT_INTERVAL = 30

# HTTP request timeout in seconds (default: 30)
DJANGOCOMMAND_REQUEST_TIMEOUT = 30

# Max retries for failed requests (default: 3)
DJANGOCOMMAND_MAX_RETRIES = 3

# Hosts allowed to use HTTP instead of HTTPS (default: localhost only)
DJANGOCOMMAND_ALLOW_HTTP_HOSTS = ['localhost', '127.0.0.1', '::1']
```


## Running the Runner

Current architecture supports only 1 runner per project.

### Foreground

```bash
python manage.py djangocommand start
```

Starts the runner in the foreground. Press Ctrl+C to stop. 

This is the recommended option for Kubernetes, systemd, Docker, and similar process managers that handle lifecycle and restarts.

### Background (detached)

```bash
python manage.py djangocommand start -d
```

Starts the runner in the background. 

This is recommended for development and testing only.
It can be used in production where other process managers are not available.
YMMV.

### Managing the Runner

Use these commands to manage a runner.

Works whether the runner is running in the foreground or background.

```bash
# Check if the runner is running
python manage.py djangocommand status

# Stop the background runner
python manage.py djangocommand stop [--force]

# Restart the runner
python manage.py djangocommand restart [-d]
```

## License

MIT
