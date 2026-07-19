"""WSGI config for butchery_system project."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'butchery_system.settings')

# Run migrations on startup (for Render deployment)
from django.core.management import call_command
try:
    call_command('migrate', '--noinput', verbosity=0)
    call_command('create_superuser_if_not_exists', verbosity=0)
except Exception:
    pass  # Fail silently if migrations fail

application = get_wsgi_application()
