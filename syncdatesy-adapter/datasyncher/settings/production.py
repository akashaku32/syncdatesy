"""Production cluster — all config from k8s Secret (datasyncher-secrets)."""
from .kubernetes import *  # noqa: F401,F403

# Stricter HSTS defaults for production; override per cluster in Secret if needed.
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True)
SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=True)
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
