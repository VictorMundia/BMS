release: python manage.py migrate --noinput
web: gunicorn butchery_system.wsgi:application --log-file -
