"""
ASGI config for datasyncher project.

Supports HTTP (Django / DRF) and WebSocket (Channels; auth in consumers).
"""

import os

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    os.getenv("DJANGO_SETTINGS_MODULE", "datasyncher.settings.developer"),
)

from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator

django_asgi_app = get_asgi_application()

from apps.elttool.websocket.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            URLRouter(websocket_urlpatterns),
        ),
    }
)
