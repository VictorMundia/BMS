"""WSGI config for butchery_system project."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'butchery_system.settings')

application = get_wsgi_application()
