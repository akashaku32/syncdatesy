"""Development settings."""
from .base import *  # noqa

# ✅ Load environment file FIRST before using env variables
import environ
import os

# BASE_DIR = .../datasyncher
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Go up 2 folders to project root
env_file_path = os.path.join(BASE_DIR, '..','.env.developer')

# Load the env file
env = environ.Env()
environ.Env.read_env(env_file_path)

# Now env variables are available
DOMAIN = env('DOMAIN', default='qa.datasyncher.com')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = [
    'localhost',
    '127.0.0.1',
    'dev.datasyncher.com',
    'qa.datasyncher.com',
    'web',  # for Docker
]

# Database — DATABASE_URL must come from the environment or .env.developer.
# Never embed credentials in source.
DATABASES = {"default": env.db_url("DATABASE_URL")}
DATABASES["default"].setdefault("CONN_MAX_AGE", 600)
_db_connect_timeout = env.int("DB_CONNECT_TIMEOUT", default=30)
_db_options = DATABASES["default"].setdefault("OPTIONS", {})
_db_options.setdefault("connect_timeout", _db_connect_timeout)

# Development apps
INSTALLED_APPS += [
    'django_extensions',
    
]



# Debug toolbar config
INTERNAL_IPS = ['127.0.0.1']

# Email backend for development (console)
# EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
# Email Configuration (loaded after env file is read)
EMAIL_BACKEND = env(
    'EMAIL_BACKEND',
    default='django.core.mail.backends.smtp.EmailBackend'
)
EMAIL_HOST = env('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default=EMAIL_HOST_USER)
# reset_url = f"https://dev.datasyncher.com/reset-password-confirm/{uid}/{token}/"
# CORS for development
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "https://dev.datasyncher.com",
    "https://qa.datasyncher.com"
]

# Disable HTTPS requirements
SECURE_SSL_REDIRECT = False
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Vite (5173) → Django (8000) over HTTP: SameSite=None + Secure cookies do not work without HTTPS
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

CORS_ALLOW_CREDENTIALS = True
SESSION_COOKIE_SAMESITE = "Lax"

CSRF_TRUSTED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

# APScheduler Service Configuration
SCHEDULER_SERVICE_URL = os.environ.get(
    'SCHEDULER_SERVICE_URL',
    'http://scheduler-service:5000'
)

INTERNAL_API_KEY = env("INTERNAL_API_KEY")
