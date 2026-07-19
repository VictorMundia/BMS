#!/bin/bash
python manage.py migrate --noinput
python manage.py create_superuser_if_not_exists
gunicorn butchery_system.wsgi:application
