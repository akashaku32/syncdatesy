import pytest
from fastapi import HTTPException

from services import snowflake_to_sharepoint_service


def test_ensure_sharepoint_subdir_tolerates_parallel_create_conflict(monkeypatch):
    calls = {"get": 0}

    monkeypatch.setattr(
        snowflake_to_sharepoint_service,
        "_resolve_root_folder_item",
        lambda access_token, drive_id, root_folder: {"id": "root-id", "folder": {}},
    )

    def _fake_request(method, url, access_token, **kwargs):
        if method == "GET":
            calls["get"] += 1
            if calls["get"] == 1:
                raise HTTPException(status_code=404, detail="missing")
            return {"id": "csv-id", "folder": {}}
        if method == "POST":
            raise HTTPException(status_code=409, detail="Name already exists")
        raise AssertionError(f"Unexpected method: {method}")

    monkeypatch.setattr(snowflake_to_sharepoint_service, "_sharepoint_request", _fake_request)

    resolved = snowflake_to_sharepoint_service._ensure_sharepoint_subdir(
        "token",
        "drive-id",
        "qa.Datasyncher",
        "CSV",
    )

    assert resolved == "qa.Datasyncher/CSV"

