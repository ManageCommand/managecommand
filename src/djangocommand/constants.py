"""
Constants for DjangoCommand client.

This module contains only pure Python constants with NO Django imports,
making it safe to import in Django settings.py files.
"""

# Commands that are disallowed by default.
# Users can override via DJANGOCOMMAND_DISALLOWED_COMMANDS in settings.py
#
# To extend this list in your settings.py:
#   from djangocommand import DEFAULT_DISALLOWED_COMMANDS
#   DJANGOCOMMAND_DISALLOWED_COMMANDS = DEFAULT_DISALLOWED_COMMANDS + (
#       'my_dangerous_command',
#   )
#
# To allow a blocked command, define your own tuple without it:
#   DJANGOCOMMAND_DISALLOWED_COMMANDS = (
#       'flush',
#       'shell',
#       # ... your custom list
#   )

DEFAULT_DISALLOWED_COMMANDS = (
    # === Database destruction ===
    'flush',                # Deletes ALL data from database
    'sqlflush',             # Outputs SQL to delete all data (could be piped)
    'reset_db',             # django-extensions: DROP + CREATE database

    # === Interactive shells (would hang waiting for input) ===
    'shell',                # Python REPL
    'shell_plus',           # django-extensions: enhanced shell
    'dbshell',              # Database CLI client

    # === Development servers (would block agent, wrong context) ===
    'runserver',
    'runserver_plus',       # django-extensions
    'testserver',

    # === Security sensitive ===
    'createsuperuser',      # Can create admin with --noinput + env vars
    'changepassword',       # Modify user credentials

    # === File system modifications (agent shouldn't write code) ===
    'makemigrations',       # Creates migration files
    'squashmigrations',     # Modifies migration files

    # === Other potentially dangerous third-party commands ===
    'drop_test_database',   # Drops the test database
    'delete_squashed_migrations',  # django-extensions: deletes files
    'clean_pyc',            # django-extensions: deletes .pyc files
)
