"""Salesforce OAuth refresh URL + credential hydration tests."""

import sys
import threading
import time
from unittest.mock import MagicMock, patch

# client.py initializes GCS at import time; avoid metadata calls in unit tests.
if "google.cloud.storage" not in sys.modules:
    sys.modules["google.cloud.storage"] = MagicMock()
    sys.modules["google.cloud"] = MagicMock()

from services.salesforce.client import _refresh_salesforce_token_with_persist
from utils.salesforce_oauth_persist import (
    hydrate_salesforce_credentials,
    resolve_salesforce_token_url,
    run_with_salesforce_refresh_lock,
    try_return_post_rotation_tokens,
)


def test_resolve_sandbox_token_url_uses_custom_domain():
    url = resolve_salesforce_token_url(
        "sandbox",
        custom_domain="atirestoration--atiuat",
    )
    assert (
        url
        == "https://atirestoration--atiuat.sandbox.my.salesforce.com/services/oauth2/token"
    )


def test_resolve_sandbox_token_url_falls_back_to_instance_url():
    url = resolve_salesforce_token_url(
        "sandbox",
        instance_url="https://example--uat.sandbox.my.salesforce.com",
    )
    assert (
        url
        == "https://example--uat.sandbox.my.salesforce.com/services/oauth2/token"
    )


def test_hydrate_merges_latest_tokens_from_django():
    creds = {
        "access_token": "old-access",
        "instance_url": "https://old.example.sandbox.my.salesforce.com",
        "refresh_token": "token-v1",
    }
    with patch(
        "utils.salesforce_oauth_persist.fetch_salesforce_oauth_tokens",
        return_value={
            "access_token": "new-access",
            "instance_url": "https://new.example.sandbox.my.salesforce.com",
            "refresh_token": "token-v2",
        },
    ):
        merged = hydrate_salesforce_credentials(26, "source", creds)

    assert merged["access_token"] == "new-access"
    assert merged["refresh_token"] == "token-v2"
    assert "new.example" in merged["instance_url"]


def test_refresh_uses_latest_db_refresh_token_before_salesforce_call():
    oauth_context = {
        "connection_id": 26,
        "role": "source",
        "credentials": {"access_token": "stale-access", "refresh_token": "token-v1"},
    }
    refreshed = {
        "access_token": "fresh-access",
        "instance_url": "https://ati.example.sandbox.my.salesforce.com",
        "refresh_token": "token-v2",
    }

    with patch(
        "utils.salesforce_oauth_persist.run_with_salesforce_refresh_lock",
        side_effect=lambda _cid, _role, operation, **kwargs: operation(),
    ), patch(
        "services.salesforce.client.refresh_salesforce_token",
        return_value=refreshed,
    ) as refresh_mock, patch(
        "utils.salesforce_oauth_persist.fetch_salesforce_oauth_tokens",
        return_value={
            "access_token": "stale-access",
            "instance_url": "https://ati.example.sandbox.my.salesforce.com",
            "refresh_token": "token-v2",
        },
    ), patch(
        "services.salesforce.client._apply_refreshed_salesforce_tokens",
        return_value=True,
    ):
        token_data = _refresh_salesforce_token_with_persist(
            "token-v1",
            "client-id",
            "client-secret",
            "sandbox",
            custom_domain="ati",
            oauth_context=oauth_context,
            instance_url="https://ati.example.sandbox.my.salesforce.com",
            current_access_token="stale-access",
        )

    assert token_data == refreshed
    assert refresh_mock.call_args.args[0] == "token-v2"


def test_refresh_does_not_return_stale_db_access_token_on_invalid_grant():
    oauth_context = {"connection_id": 26, "role": "source", "credentials": {}}

    with patch(
        "utils.salesforce_oauth_persist.run_with_salesforce_refresh_lock",
        side_effect=lambda _cid, _role, operation, **kwargs: operation(),
    ), patch(
        "services.salesforce.client.refresh_salesforce_token",
        side_effect=ValueError("invalid_grant: expired access/refresh token"),
    ), patch(
        "utils.salesforce_oauth_persist.fetch_salesforce_oauth_tokens",
        return_value={
            "access_token": "stale-access",
            "instance_url": "https://ati.example.sandbox.my.salesforce.com",
            "refresh_token": "token-v1",
        },
    ), patch(
        "utils.salesforce_oauth_persist.wait_for_salesforce_token_rotation",
        return_value=None,
    ), patch("services.salesforce.client.time.sleep"):
        try:
            _refresh_salesforce_token_with_persist(
                "token-v1",
                "client-id",
                "client-secret",
                "sandbox",
                oauth_context=oauth_context,
            )
            raised = False
        except ValueError:
            raised = True

    assert raised is True


def test_try_return_post_rotation_tokens_when_db_has_new_refresh():
    with patch(
        "utils.salesforce_oauth_persist.fetch_salesforce_oauth_tokens",
        return_value={
            "access_token": "fresh-access",
            "instance_url": "https://ati.example.sandbox.my.salesforce.com",
            "refresh_token": "token-v2",
        },
    ):
        tokens = try_return_post_rotation_tokens(26, "source", "token-v1")

    assert tokens["access_token"] == "fresh-access"
    assert tokens["refresh_token"] == "token-v2"


def test_try_return_post_rotation_tokens_none_when_unchanged():
    with patch(
        "utils.salesforce_oauth_persist.fetch_salesforce_oauth_tokens",
        return_value={
            "access_token": "access",
            "instance_url": "https://ati.example.sandbox.my.salesforce.com",
            "refresh_token": "token-v1",
        },
    ):
        assert try_return_post_rotation_tokens(26, "source", "token-v1") is None


def test_try_return_post_rotation_tokens_none_when_access_still_stale():
    with patch(
        "utils.salesforce_oauth_persist.fetch_salesforce_oauth_tokens",
        return_value={
            "access_token": "stale-access",
            "instance_url": "https://ati.example.sandbox.my.salesforce.com",
            "refresh_token": "token-v2",
        },
    ):
        assert (
            try_return_post_rotation_tokens(
                26,
                "source",
                "token-v1",
                current_access_token="stale-access",
            )
            is None
        )


def test_refresh_uses_distributed_lock_when_oauth_context_present():
    oauth_context = {"connection_id": 26, "role": "source", "credentials": {}}
    refreshed = {
        "access_token": "fresh-access",
        "instance_url": "https://ati.example.sandbox.my.salesforce.com",
        "refresh_token": "token-v2",
    }
    lock_calls = []

    def fake_lock(connection_id, role, operation, **kwargs):
        lock_calls.append((connection_id, role))
        return operation()

    with patch(
        "utils.salesforce_oauth_persist.run_with_salesforce_refresh_lock",
        side_effect=fake_lock,
    ), patch(
        "utils.salesforce_oauth_persist.try_return_post_rotation_tokens",
        return_value=None,
    ), patch(
        "utils.salesforce_oauth_persist.fetch_salesforce_oauth_tokens",
        return_value={"refresh_token": "token-v1"},
    ), patch(
        "services.salesforce.client.refresh_salesforce_token",
        return_value=refreshed,
    ), patch(
        "services.salesforce.client._apply_refreshed_salesforce_tokens",
        return_value=True,
    ):
        token_data = _refresh_salesforce_token_with_persist(
            "token-v1",
            "client-id",
            "client-secret",
            "sandbox",
            oauth_context=oauth_context,
        )

    assert token_data == refreshed
    assert lock_calls == [(26, "source")]


def test_run_with_salesforce_refresh_lock_serializes_workers():
    state = {"active": 0, "max_active": 0}
    real_lock = threading.Lock()

    def worker():
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.05)
        state["active"] -= 1
        return "ok"

    from concurrent.futures import ThreadPoolExecutor

    class FakeLock:
        def __init__(self, client, name, timeout=None, blocking_timeout=None):
            self._lock = real_lock

        def acquire(self, blocking=True, blocking_timeout=None):
            return self._lock.acquire(blocking=blocking, timeout=blocking_timeout or -1)

        def release(self):
            self._lock.release()

    with patch("utils.salesforce_oauth_persist.Lock", FakeLock):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(
                pool.map(
                    lambda _: run_with_salesforce_refresh_lock(26, "source", worker),
                    range(4),
                )
            )

    assert results == ["ok", "ok", "ok", "ok"]
    assert state["max_active"] == 1
