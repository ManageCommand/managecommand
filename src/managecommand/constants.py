"""
Constants for ManageCommand client.

This module contains only pure Python constants with NO Django imports,
making it safe to import in Django settings.py files.
"""

# =============================================================================
# DEFAULT ALLOWED COMMANDS (security-first approach)
# =============================================================================
# By default, ManageCommand uses an allowlist approach - only commands in this
# list can be executed remotely. This is the most secure default.
#
# To extend this list in your settings.py:
#   from managecommand import DEFAULT_ALLOWED_COMMANDS
#   MANAGECOMMAND_ALLOWED_COMMANDS = DEFAULT_ALLOWED_COMMANDS + (
#       'my_custom_command',
#   )
#
# To restrict to specific commands:
#   MANAGECOMMAND_ALLOWED_COMMANDS = (
#       'migrate',
#       'collectstatic',
#   )

DEFAULT_ALLOWED_COMMANDS = (
    # === Core Django - Database ===
    "migrate",  # Apply database migrations
    "showmigrations",  # List migrations and their status
    "dbbackup",  # django-dbbackup: backup database
    "createcachetable",  # Create cache table for database cache backend
    # === Core Django - Static Files ===
    "collectstatic",  # Collect static files to STATIC_ROOT
    "findstatic",  # Find location of static files
    # === Core Django - Maintenance ===
    "clearsessions",  # Clear expired sessions
    "check",  # Run Django system checks
    "diffsettings",  # Display differences between current and default settings
    "inspectdb",  # Introspect database tables (read-only)
    "sendtestemail",  # Send a test email
    "showmigrations",  # Show all migrations
    "sqlmigrate",  # Display SQL for a migration (read-only)
    "sqlsequencereset",  # Output SQL to reset sequences
    # === Core Django - Data ===
    "dumpdata",  # Export data to JSON/XML
    # === Core Django - Internationalization ===
    "compilemessages",  # Compile .po files to .mo files
    # === Testing ===
    "test",  # Run Django tests
    # === django-extensions (popular third-party) ===
    "show_urls",  # Display all URL routes
    "validate_templates",  # Validate Django templates
    "list_signals",  # List all signals
    "notes",  # Show all TODO/FIXME/etc in code
    "show_template_tags",  # List all template tags
    "graph_models",  # Generate model graph (read-only output)
    "pipchecker",  # Check pip packages for updates
    "clear_cache",  # Clear Django cache
    # === django-celery-beat ===
    # (no safe commands - beat management should be done via admin)
    # === django-health-check ===
    "health_check",  # Run health checks
    # === django-silk (profiling) ===
    "silk_clear_request_log",  # Clear profiling data
    # === django-redis ===
    # (no management commands)
    # === wagtail (popular CMS) ===
    "fixtree",  # Fix Wagtail page tree
    "publish_scheduled",  # Publish scheduled pages
    "rebuild_references_index",  # Rebuild reference index
    "search_garbage_collect",  # Clean up search index
    "update_index",  # Update search index
    # === django-haystack (search) ===
    "rebuild_index",  # Rebuild search index
    "update_index",  # Update search index
    "clear_index",  # Clear search index
)


# =============================================================================
# DEFAULT DISALLOWED COMMANDS (blocklist approach - optional)
# =============================================================================
# If you prefer a blocklist approach (allow all except dangerous commands),
# set MANAGECOMMAND_USE_BLOCKLIST = True in your settings and optionally
# customize MANAGECOMMAND_DISALLOWED_COMMANDS.
#
# To extend the blocklist in your settings.py:
#   from managecommand import DEFAULT_DISALLOWED_COMMANDS
#   MANAGECOMMAND_DISALLOWED_COMMANDS = DEFAULT_DISALLOWED_COMMANDS + (
#       'my_dangerous_command',
#   )

DEFAULT_DISALLOWED_COMMANDS = (
    # === Database destruction ===
    "flush",  # Deletes ALL data from database
    "sqlflush",  # Outputs SQL to delete all data (could be piped)
    "reset_db",  # django-extensions: DROP + CREATE database
    "dbrestore",  # django-dbbackup: restore overwrites entire database
    "loaddata",  # Load fixtures can overwrite existing data
    # === Interactive shells (would hang waiting for input) ===
    "shell",  # Python REPL
    "shell_plus",  # django-extensions: enhanced shell
    "dbshell",  # Database CLI client
    # === Development servers (would block runner, wrong context) ===
    "runserver",
    "runserver_plus",  # django-extensions
    "testserver",
    # === Security sensitive ===
    "createsuperuser",  # Can create admin with --noinput + env vars
    "changepassword",  # Modify user credentials
    # === File system modifications (runner shouldn't write code) ===
    "makemigrations",  # Creates migration files
    "squashmigrations",  # Modifies migration files
    # === Other potentially dangerous third-party commands ===
    "drop_test_database",  # Drops the test database
    "delete_squashed_migrations",  # django-extensions: deletes files
    "clean_pyc",  # django-extensions: deletes .pyc files
)
