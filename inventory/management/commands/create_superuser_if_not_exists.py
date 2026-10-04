import os

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = 'Creates a superuser from DJANGO_SUPERUSER_* env vars if one does not exist'

    def handle(self, *args, **options):
        username = os.environ.get('DJANGO_SUPERUSER_USERNAME')
        password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')
        email = os.environ.get('DJANGO_SUPERUSER_EMAIL', '')
        if not username or not password:
            self.stdout.write(self.style.WARNING('DJANGO_SUPERUSER_USERNAME/PASSWORD not set; skipping'))
            return

        User = get_user_model()
        if User.objects.filter(username=username).exists():
            self.stdout.write(self.style.WARNING('Superuser already exists'))
            return
        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(self.style.SUCCESS('Superuser created successfully'))
