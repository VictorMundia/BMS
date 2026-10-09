"""Django settings for butchery_system project."""
import os
from datetime import date
from pathlib import Path

from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = config('DEBUG', default=False, cast=bool)

# Production must set SECRET_KEY; the fallback is for local development only.
SECRET_KEY = config('SECRET_KEY', default='django-insecure-local-dev-only' if DEBUG else '')

ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1', cast=Csv())

CSRF_TRUSTED_ORIGINS = config(
    'CSRF_TRUSTED_ORIGINS',
    default='http://127.0.0.1:8000,http://localhost:8000,https://bms-app-d4f68461bed5.herokuapp.com',
    cast=Csv(),
)

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = config('SECURE_HSTS_SECONDS', default=3600, cast=int)
    SECURE_CONTENT_TYPE_NOSNIFF = True


# Application definition
INSTALLED_APPS = [
    'jazzmin',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'inventory.apps.InventoryConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'inventory.middleware.InventoryAccessMiddleware',
]

ROOT_URLCONF = 'butchery_system.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'butchery_system.wsgi.application'


# Database
# PostgreSQL in production (DATABASE_URL), otherwise SQLite for local dev
import dj_database_url
DATABASES = {
    'default': dj_database_url.config(
        default=config('DATABASE_URL', default='sqlite:///db.sqlite3'),
        conn_max_age=600,
        ssl_require=True if config('DATABASE_URL', default=None) else False,
    )
}

# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    # Not the Manifest variant: Jazzmin's CSS references a .map file it doesn't ship.
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage'},
}


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Nairobi'
USE_I18N = True
USE_TZ = True

# Local hour at which a new business day starts (entries before it count for the previous day).
BUSINESS_DAY_CUTOFF_HOUR = config('BUSINESS_DAY_CUTOFF_HOUR', default=4, cast=int)

# Staff must fill in days they missed (from this date on) before recording other days.
MISSING_DAY_ENFORCE_FROM = config(
    'MISSING_DAY_ENFORCE_FROM', default='2026-10-09', cast=date.fromisoformat,
)

# Daily SMS summary via Africa's Talking (username "sandbox" uses their test environment).
AT_USERNAME = config('AT_USERNAME', default='')
AT_API_KEY = config('AT_API_KEY', default='')
AT_SENDER_ID = config('AT_SENDER_ID', default='')
SMS_RECIPIENTS = config('SMS_RECIPIENTS', default='', cast=Csv())

# Media files (uploaded receipt images, etc.)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Authentication redirects
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/inventory/'

# Session configuration
SESSION_COOKIE_AGE = 315360000  # 10 years (permanent session)
SESSION_SAVE_EVERY_REQUEST = False
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

# Email configuration (credentials sourced via python-decouple)
EMAIL_BACKEND = config(
    'EMAIL_BACKEND',
    default='django.core.mail.backends.console.EmailBackend',
)
EMAIL_HOST = config('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='bms@example.com')
LOW_STOCK_ALERT_EMAIL = config('LOW_STOCK_ALERT_EMAIL', default='admin@example.com')

# Jazzmin Admin Theme Configuration
JAZZMIN_SETTINGS = {
    'title': 'BMS Admin',
    'site_title': 'BMS Admin',
    'site_header': 'BMS Administration',
    'index_title': 'Welcome to BMS Administration',
    'language_selector': True,
    'show_apps': ['inventory', 'auth'],
    'hide_apps': [],
    'icons': {
        'inventory': 'fas fa-box',
        'auth': 'fas fa-users',
        'inventory.MeatCategory': 'fas fa-tags',
        'inventory.Butchery': 'fas fa-store',
        'inventory.MeatProduct': 'fas fa-drumstick-bite',
        'inventory.DailyStock': 'fas fa-calendar-day',
        'inventory.DailyBranchSummary': 'fas fa-chart-line',
        'inventory.ExpenseCategory': 'fas fa-receipt',
        'inventory.Expense': 'fas fa-money-bill',
        'inventory.Staff': 'fas fa-user-tie',
        'inventory.Shift': 'fas fa-clock',
        'inventory.StockTransfer': 'fas fa-truck',
        'inventory.AuditLog': 'fas fa-history',
        'inventory.DatePermission': 'fas fa-key',
        'inventory.BuyingPrice': 'fas fa-shopping-cart',
        'auth.User': 'fas fa-user',
        'auth.Group': 'fas fa-users-cog',
    },
    'navigation': [
        {'title': 'Inventory', 'apps': ['inventory'], 'icon': 'fas fa-box'},
        {'title': 'Users', 'apps': ['auth'], 'icon': 'fas fa-users'},
    ],
    'search_url': 'admin:search',
    'user_avatar': 'img/user-icon.png',
    'top_menu_links': [
        {'title': 'Dashboard', 'url': '/', 'icon': 'fas fa-home'},
        {'title': 'Support', 'url': 'https://github.com/farridav/django-jazzmin', 'icon': 'fas fa-life-ring'},
    ],
    'related_modal_active': True,
}
