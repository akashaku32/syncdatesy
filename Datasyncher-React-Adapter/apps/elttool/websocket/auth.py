"""JWT / DRF Token authentication helpers for Channels WebSockets."""
from __future__ import annotations

import logging
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser

logger = logging.getLogger(__name__)


def _token_from_scope(scope) -> str | None:
    query = parse_qs(scope.get("query_string", b"").decode())
    raw = (query.get("token") or [None])[0]
    if raw:
        return raw
    headers = {
        k.decode().lower(): v.decode()
        for k, v in scope.get("headers", [])
        if isinstance(k, (bytes, bytearray))
    }
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return None


def authenticate_ws_user(scope):
    raw = _token_from_scope(scope)
    if not raw:
        return AnonymousUser()

    User = get_user_model()

    try:
        from rest_framework.authtoken.models import Token

        token_row = Token.objects.select_related("user").get(key=raw)
        if token_row.user.is_active:
            return token_row.user
    except Exception:
        pass

    try:
        from rest_framework_simplejwt.tokens import AccessToken

        validated = AccessToken(raw)
        user = User.objects.select_related("cmp").get(pk=validated.get("user_id"))
        if user.is_active:
            return user
    except Exception as exc:
        logger.debug("WebSocket JWT auth failed: %s", exc)

    return AnonymousUser()


authenticate_ws_user_async = sync_to_async(authenticate_ws_user)


def user_belongs_to_company(user, cmp_id) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    try:
        return user.cmp_id is not None and int(user.cmp_id) == int(cmp_id)
    except (TypeError, ValueError):
        return False


def user_owns_connection(user, connection_id) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    from apps.elttool.models import Connection

    try:
        conn = Connection.objects.only("cmp_id").get(pk=connection_id)
    except Connection.DoesNotExist:
        return False
    return user_belongs_to_company(user, conn.cmp_id)


def user_owns_migration_session(user, migration_session_id) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    from apps.elttool.models import Connection

    try:
        conn = Connection.objects.only("cmp_id").get(pk=migration_session_id)
        return user_belongs_to_company(user, conn.cmp_id)
    except Exception:
        return bool(getattr(user, "cmp_id", None))


user_owns_connection_async = sync_to_async(user_owns_connection)
user_belongs_to_company_async = sync_to_async(user_belongs_to_company)
user_owns_migration_session_async = sync_to_async(user_owns_migration_session)
