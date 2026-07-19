#!/bin/bash
set -e
python manage.py migrate --noinput || echo "Migrations failed"
python manage.py create_superuser_if_not_exists || echo "Superuser creation failed"
exec gunicorn butchery_system.wsgi:application
