import json
from unittest.mock import MagicMock, patch

from sap_proxy_agent.hub.progress_relay import relay_progress_to_django


def test_relay_missing_internal_key():
    with patch.dict("os.environ", {}, clear=True):
        status, body = relay_progress_to_django(
            agent_uid="agent-1",
            body={"event": "table_started"},
            raw_body=b"{}",
        )
    assert status == 503
    assert body["accepted"] is False


@patch("sap_proxy_agent.hub.progress_relay.httpx.Client")
def test_relay_forwards_to_django(mock_client_cls):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "accepted": True,
        "overall_status": "in_progress",
        "tables_remaining": 1,
    }
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.post.return_value = mock_resp
    mock_client_cls.return_value = mock_client

    payload = {
        "event": "table_completed",
        "migration_session_id": 1,
        "job_name": "j",
        "connection_id": 2,
        "table_name": "T",
    }
    raw = json.dumps(payload).encode("utf-8")

    with patch.dict("os.environ", {"INTERNAL_API_KEY": "test-key"}, clear=False):
        status, data = relay_progress_to_django(
            agent_uid="agent-1",
            body=payload,
            raw_body=raw,
        )

    assert status == 200
    assert data["accepted"] is True
    call_kwargs = mock_client.post.call_args.kwargs
    assert call_kwargs["headers"]["X-INTERNAL-KEY"] == "test-key"
    assert call_kwargs["headers"]["X-Agent-UID"] == "agent-1"
