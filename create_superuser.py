import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'butchery_system.settings')
django.setup()

from django.core.management import call_command

# Reads DJANGO_SUPERUSER_USERNAME / _PASSWORD / _EMAIL from the environment.
call_command('create_superuser_if_not_exists')
