"""WebSocket consumer for platform admin ops dashboard (superuser only)."""
from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)

ADMIN_OPS_GROUP = "admin_ops_platform"


def authenticate_ws_token_superuser(scope) -> object | None:
    """Validate ?token= from query string (DRF Token or JWT); require is_superuser."""
    query = parse_qs(scope.get("query_string", b"").decode())
    raw = (query.get("token") or [None])[0]
    if not raw:
        return None

    User = get_user_model()

    try:
        from rest_framework.authtoken.models import Token

        token_row = Token.objects.select_related("user").get(key=raw)
        user = token_row.user
        if user.is_active and user.is_superuser:
            return user
    except Exception:
        pass

    try:
        from rest_framework_simplejwt.tokens import AccessToken

        validated = AccessToken(raw)
        user_id = validated.get("user_id")
        user = User.objects.get(pk=user_id)
        if user.is_active and user.is_superuser:
            return user
    except Exception:
        pass

    return None


class AdminOpsConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = await sync_to_async(authenticate_ws_token_superuser)(self.scope)
        if user is None:
            await self.close(code=4403)
            return

        self.group_name = ADMIN_OPS_GROUP
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        try:
            from apps.elttool.services.ops_monitoring_service import build_ops_snapshot

            snapshot = await sync_to_async(build_ops_snapshot)()
            await self.send(text_data=json.dumps(snapshot))
        except Exception as exc:
            logger.exception("AdminOpsConsumer initial snapshot failed: %s", exc)

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def admin_ops_update(self, event):
        await self.send(text_data=json.dumps(event["payload"]))
