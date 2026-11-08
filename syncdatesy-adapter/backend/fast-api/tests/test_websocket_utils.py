"""Tests for internal Django WebSocket push URL helpers."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

FAST_API_DIR = Path(__file__).resolve().parents[1]
if str(FAST_API_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_API_DIR))

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


def test_django_internal_base_url_defaults_to_django_svc():
    import websocket_utils as wu

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("DJANGO_INTERNAL_BASE_URL", None)
        assert wu._django_internal_base_url() == "http://django-svc:8000"


def test_django_internal_base_url_honors_env_override():
    import websocket_utils as wu

    with patch.dict(os.environ, {"DJANGO_INTERNAL_BASE_URL": "http://django-svc:8000"}):
        assert wu._django_internal_base_url() == "http://django-svc:8000"


@patch("websocket_utils.requests.post")
def test_trigger_ws_status_update_uses_internal_base_url(mock_post):
    import websocket_utils as wu

    mock_post.return_value = MagicMock(status_code=200)
    with patch.dict(os.environ, {"DJANGO_INTERNAL_BASE_URL": "http://django-svc:8000"}):
        wu.trigger_ws_status_update(282)

    mock_post.assert_called_once()
    url = mock_post.call_args.args[0]
    assert url.startswith("http://django-svc:8000/api/v1/internal/migration-status-push/282/")
    assert "web:8000" not in url
    headers = mock_post.call_args.kwargs.get("headers") or mock_post.call_args[1].get("headers")
    assert headers.get("X-Forwarded-Proto") == "https"


@patch("websocket_utils.requests.post")
def test_internal_push_headers_include_forwarded_proto(mock_post):
    import websocket_utils as wu

    headers = wu._internal_push_headers()
    assert headers["X-Forwarded-Proto"] == "https"


@patch("websocket_utils.requests.post")
def test_trigger_connection_ws_status_update_uses_internal_base_url(mock_post):
    import websocket_utils as wu

    mock_post.return_value = MagicMock(status_code=200)
    with patch.dict(os.environ, {"DJANGO_INTERNAL_BASE_URL": "http://django-svc:8000"}):
        wu.trigger_connection_ws_status_update(10)

    url = mock_post.call_args.args[0]
    assert url.startswith("http://django-svc:8000/api/v1/internal/connection-table-status-push/10/")
