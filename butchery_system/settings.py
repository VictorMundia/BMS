"""Django settings for butchery_system project."""
import os
from pathlib import Path

from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = config('SECRET_KEY', default='django-insecure-change-this-key-in-production-bms-2024')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config('DEBUG', default=True, cast=bool)

ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='*').split(',')

# Trusted origins for CSRF (includes the IDE browser-preview proxy port).
# If the preview opens on a different port, add it here.
CSRF_TRUSTED_ORIGINS = [
    'http://127.0.0.1:8000',
    'http://localhost:8000',
    'http://127.0.0.1:64897',
    'http://localhost:64897',
    'http://127.0.0.1:51560',
    'http://localhost:51560',
]


# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'inventory.apps.InventoryConfig',
    'jazzmin',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
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
# Use PostgreSQL on Render (when DATABASE_URL is set), otherwise SQLite for local dev
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


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'

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
