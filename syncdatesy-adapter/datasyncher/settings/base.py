"""
Base settings for DataSyncher project.
"""
import os
from pathlib import Path
import environ


BASE_DIR = Path(__file__).resolve().parent.parent
# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# Only add STATICFILES_DIRS if the directory exists
static_dir = os.path.join(BASE_DIR, 'static')
if os.path.exists(static_dir):
    STATICFILES_DIRS = [static_dir]
else:
    STATICFILES_DIRS = []

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
# Initialize environment variables
env = environ.Env(
    DEBUG=(bool, False),
)

# Build paths inside the project
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Read .env file
env_file = os.path.join(BASE_DIR, '.env')
if os.path.exists(env_file):
    environ.Env.read_env(env_file)
environ.Env.read_env(os.path.join(BASE_DIR, ".env.developer"))
# SECURITY WARNING: keep the secret key used in production secret!
# Fail closed — never ship with a committed fallback secret.
SECRET_KEY = env('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env('DEBUG', default=False)

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=[])
DOMAIN = env('DOMAIN', default='qa-kubernetes.datasyncher.com')

# Application definition
DJANGO_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.postgres',
    "channels",
]

THIRD_PARTY_APPS = [
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    'corsheaders',
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'django_celery_beat',
    'django_celery_results',
]

LOCAL_APPS = [
    'apps.elttool',
    'apps.utils',  # if you have utils as a Django app
    'apps.support_admin',
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.gzip.GZipMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',
]

ROOT_URLCONF = 'datasyncher.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            BASE_DIR / 'templates',
            BASE_DIR / 'apps' / 'elttool' / 'templates',
        ],
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

WSGI_APPLICATION = 'datasyncher.wsgi.application'
ASGI_APPLICATION = 'datasyncher.asgi.application'

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {
            'min_length': 8,
        }
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Time formats
TIME_FORMAT = 'H:i:s'
DATETIME_FORMAT = 'Y-m-d H:i:s'

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Custom User Model
AUTH_USER_MODEL = 'elttool.User'

# Redis Configuration
REDIS_URL = env('REDIS_URL', default='redis://localhost:6379/0')

# Shared cache for OTP/session-like data. Prefer Redis when available.
REDIS_CACHE_URL = env('REDIS_CACHE_URL', default=REDIS_URL)
try:
    from django import VERSION as _DJANGO_VERSION
except Exception:  # pragma: no cover
    _DJANGO_VERSION = (3, 2)

if _DJANGO_VERSION >= (4, 0):
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': REDIS_CACHE_URL,
            'KEY_PREFIX': 'datasyncher',
            'TIMEOUT': 600,
        }
    }
else:
    CACHES = {
        'default': {
            # Django 3.x: LocMem until django-redis or Django 4+ RedisCache.
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'datasyncher-cache',
            'TIMEOUT': 600,
            'KEY_PREFIX': 'datasyncher',
        }
    }

# Celery Configuration
CELERY_BROKER_URL = env('CELERY_BROKER_URL', default=REDIS_URL)
CELERY_RESULT_BACKEND = 'django-db'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_DEFAULT_QUEUE = 'celery'
# Ack after the task finishes so a dead child redelivers its message.
# With PIPELINE_NONBLOCKING_ORCHESTRATION (default), poll tasks are short so the
# Django worker can use ~1h. Migration control workers that still run long
# blocking table loads keep a multi-day timeout via their own env.
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_TRANSPORT_OPTIONS = {
    'visibility_timeout': int(os.getenv(
        'CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS',
        str(3600),
    )),
}
# Pipeline orchestration must not share a queue with ops snapshots. A full ops
# backlog otherwise sits in front of every organisation's flow start.
CELERY_TASK_ROUTES = {
    'apps.elttool.tasks.execute_pipeline_run': {'queue': 'pipeline'},
    'apps.elttool.tasks.start_pipeline_level': {'queue': 'pipeline'},
    'apps.elttool.tasks.poll_pipeline_run': {'queue': 'pipeline'},
    'apps.elttool.tasks.finalize_deferred_pipeline_failure': {'queue': 'pipeline'},
    'apps.elttool.tasks.reap_stuck_pipeline_runs': {'queue': 'ops'},
    'apps.elttool.tasks.ops_monitoring_tasks.push_admin_ops_snapshot': {'queue': 'ops'},
}

# Django REST Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '100/hour',
        'user': '1000/hour',
        'auth': '10/minute',
    },
    'DEFAULT_PAGINATION_CLASS': 'datasyncher.pagination.CustomPagination',
    'PAGE_SIZE': 10,
    'EXCEPTION_HANDLER': 'apps.elttool.v1.exceptions.custom_exception_handler',
}

# SimpleJWT
from datetime import timedelta

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=60),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'USER_ID_FIELD': 'user_id',
    'USER_ID_CLAIM': 'user_id',
}

# Django Allauth
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

SITE_ID = 2
LOGIN_REDIRECT_URL = 'home'
LOGOUT_REDIRECT_URL = 'signin'

ACCOUNT_AUTHENTICATION_METHOD = 'username_email'
ACCOUNT_EMAIL_REQUIRED = False
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_USERNAME_REQUIRED = True

# Social Account Providers
SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'APP': {
            'client_id': env('GOOGLE_CLIENT_ID', default=''),
            'secret': env('GOOGLE_CLIENT_SECRET', default=''),
            'key': '',
        },
        'SCOPE': ['profile', 'email'],
        'AUTH_PARAMS': {'access_type': 'online'},
        'METHOD': 'oauth2',
        'VERIFIED_EMAIL': False,
        'LOGIN_URL': '/elttool/google/login/',
    },
}

# Email Configuration
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
SUPPORT_ADMIN_RESET_URL = env('SUPPORT_ADMIN_RESET_URL', default='https://qa.datasyncher.com/support-admin/reset-password')
FRONTEND_URL = env('FRONTEND_URL', default='https://qa.datasyncher.com')

# CORS Configuration
CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=[])

# Session Configuration
SESSION_COOKIE_AGE = 86400  # 24 hours
SESSION_EXPIRE_AT_BROWSER_CLOSE = env.bool('SESSION_EXPIRE_AT_BROWSER_CLOSE', default=False)

# Logging Configuration
# Django applies DEFAULT_LOGGING then this dict (see django.utils.log.configure_logging).
# A minimal dict that only sets ``root`` clears all handlers globally; ``django.server``
# then keeps a closed handler or none, so ``runserver`` stops printing HTTP access lines.
# Keep explicit ``django`` / ``django.server`` / ``django.request`` loggers with live handlers.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'require_debug_false': {
            '()': 'django.utils.log.RequireDebugFalse',
        },
        'require_debug_true': {
            '()': 'django.utils.log.RequireDebugTrue',
        },
    },
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
        'django.server': {
            '()': 'django.utils.log.ServerFormatter',
            'format': '[{server_time}] {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
        'django_console': {
            'level': 'INFO',
            'filters': ['require_debug_true'],
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
        'django_server': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'django.server',
        },
        'mail_admins': {
            'level': 'ERROR',
            'filters': ['require_debug_false'],
            'class': 'django.utils.log.AdminEmailHandler',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['django_console', 'mail_admins'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.server': {
            'handlers': ['django_server'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['django_console', 'mail_admins'],
            'level': 'WARNING',
            'propagate': False,
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
}
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [REDIS_URL],
            "capacity": 1500,  # Increase capacity to prevent blocking
            "expiry": 10,      # Reduce expiry time
            "group_expiry": 86400,  # Group expiration
            "channel_capacity": {
                "*": 100,  # Default capacity for all channels
                "migration_status_*": 100,
                "connection_table_status_*": 100,
                "connection_activity_log_*": 100,
                "update_schema_status_*": 100,
                "admin_ops_platform": 100,
            }
        },
    },
}
# Fail closed: internal service auth must never compare None == None.
INTERNAL_API_KEY = env("INTERNAL_API_KEY")
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", default="")
FIELD_ENCRYPTION_KEY_VERSION = env.int("FIELD_ENCRYPTION_KEY_VERSION", default=1)
# Proxy agent (SAP on-prem) — mTLS CA + public progress API host for config.json
PROXY_AGENT_CA_CERT_PATH = env("PROXY_AGENT_CA_CERT_PATH", default="")
PROXY_AGENT_CA_KEY_PATH = env("PROXY_AGENT_CA_KEY_PATH", default="")
PROXY_AGENT_SERVER_HOST = env("PROXY_AGENT_SERVER_HOST", default="qa.datasyncher.com")
PROXY_AGENT_SERVER_PORT = env.int("PROXY_AGENT_SERVER_PORT", default=50051)
PROXY_AGENT_PUBLIC_API_HOST = env(
    "PROXY_AGENT_PUBLIC_API_HOST",
    default="agent-api.datasyncher.com",
)
PROXY_AGENT_PROGRESS_API_URL = env("PROXY_AGENT_PROGRESS_API_URL", default="")
# grpc | grpc_with_https_fallback | https
# Primary: gRPC ReportProgress on server_host:server_port. HTTPS progress API is fallback.
PROXY_AGENT_PROGRESS_TRANSPORT = env(
    "PROXY_AGENT_PROGRESS_TRANSPORT",
    default="grpc_with_https_fallback",
)
STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY", default="")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", default="")
OPS_METRICS_URL = env(
    "OPS_METRICS_URL",
    default="http://celery-worker-control-metrics:9100/metrics",
)
