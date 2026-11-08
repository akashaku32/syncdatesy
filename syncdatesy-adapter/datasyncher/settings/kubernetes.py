"""
Kubernetes deployment settings.

All runtime configuration is injected via environment variables from the cluster
Secret (see k8s/secrets/secrets.yaml). Do not hardcode hosts, credentials, or URLs here.
"""
from .base import *  # noqa: F401,F403

# --- Database ---
DATABASES = {
    "default": env.db("DATABASE_URL"),
}
# Recycle idle Django/Celery connections so workers do not pin Postgres slots.
# CONN_MAX_AGE=0 (Django default) still leaves Celery prefork children holding
# open sockets until the process exits unless close_old_connections runs.
DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = env.bool(
    "CONN_HEALTH_CHECKS", default=True
)
_db_options = DATABASES["default"].setdefault("OPTIONS", {})
_db_options.setdefault("connect_timeout", env.int("DB_CONNECT_TIMEOUT", default=10))
# libpq TCP keepalives — drop dead NAT/firewall holds sooner.
_db_options.setdefault("keepalives", 1)
_db_options.setdefault(
    "keepalives_idle", env.int("DB_KEEPALIVES_IDLE", default=30)
)
_db_options.setdefault(
    "keepalives_interval", env.int("DB_KEEPALIVES_INTERVAL", default=10)
)
_db_options.setdefault(
    "keepalives_count", env.int("DB_KEEPALIVES_COUNT", default=5)
)

# --- Hosts / domain / CORS ---
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")
DOMAIN = env("DOMAIN")
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_CREDENTIALS = env.bool("CORS_ALLOW_CREDENTIALS", default=True)
FRONTEND_URL = env("FRONTEND_URL", default=f"https://{DOMAIN}")

# --- HTTPS / cookies (TLS terminated at ingress) ---
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=True)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=True)
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=3600)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=False)
SECURE_CONTENT_TYPE_NOSNIFF = env.bool("SECURE_CONTENT_TYPE_NOSNIFF", default=True)
SECURE_BROWSER_XSS_FILTER = env.bool("SECURE_BROWSER_XSS_FILTER", default=True)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SAMESITE = env("SESSION_COOKIE_SAMESITE", default="None")

# --- Email (SMTP keys already loaded in base.py from env) ---
DEFAULT_TO_EMAIL = env("DEFAULT_TO_EMAIL", default=DEFAULT_FROM_EMAIL)
ERROR_NOTIFICATION_RECIPIENTS = env.list("ERROR_NOTIFICATION_RECIPIENTS", default=[])

# --- Billing / payments ---
STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY", default="")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", default="")

# --- Internal services ---
SCHEDULER_SERVICE_URL = env("SCHEDULER_SERVICE_URL", default="http://scheduler-service:5000")
FASTAPI_DOMAIN = env("FASTAPI_DOMAIN", default="fastapi-svc:9090")
MIGRATION_FUNCTION_INTERNAL_URL = env(
    "MIGRATION_FUNCTION_INTERNAL_URL",
    default="http://migration-function-svc:8001",
)
USE_INTERNAL_MIGRATION_API = env.bool("USE_INTERNAL_MIGRATION_API", default=True)
DJANGO_INTERNAL_BASE_URL = env("DJANGO_INTERNAL_BASE_URL", default="http://django-svc:8000")
