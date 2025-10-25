import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# =============================================================================
# SECURITY CRITICAL SETTINGS
# =============================================================================

# SECRET_KEY - MUST be loaded from environment variables
# SECURITY WARNING: Never hardcode SECRET_KEY in source code!
# Generate a new one: python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
SECRET_KEY = os.getenv('SECRET_KEY')

# Validate SECRET_KEY is set and secure
if not SECRET_KEY:
    raise ValueError(
        "SECRET_KEY environment variable is not set!\n"
        "Please set SECRET_KEY in your .env file.\n"
        "Generate one with: python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'"
    )

if SECRET_KEY.startswith('django-insecure-'):
    import warnings
    warnings.warn(
        "WARNING: You are using an insecure SECRET_KEY!\n"
        "This is ONLY acceptable in development.\n"
        "Generate a secure key for production with: python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'",
        stacklevel=2
    )

# DEBUG - MUST be False in production
# SECURITY WARNING: Never run production with DEBUG=True!
# DEBUG=True exposes sensitive information in error pages
DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 't', 'yes')

# ALLOWED_HOSTS - MUST be configured in production
# SECURITY WARNING: An empty ALLOWED_HOSTS in production is a security risk
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
ALLOWED_HOSTS = [host.strip() for host in ALLOWED_HOSTS if host.strip()]

# Validate production settings
if not DEBUG and not ALLOWED_HOSTS:
    raise ValueError(
        "ALLOWED_HOSTS must be configured when DEBUG=False!\n"
        "Set ALLOWED_HOSTS in your .env file with your domain(s)."
    )

INSTALLED_APPS = [
    'django.contrib.staticfiles',
    'apps.core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.common.CommonMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# No usar base de datos
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.dummy',
    }
}

LANGUAGE_CODE = 'es-es'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static'] 