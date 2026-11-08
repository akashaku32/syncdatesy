"""SAP OData Celery fan-out / child-mode compatibility."""

from models.sap_odata_models import SAPODataADLSMigrationRequest, SAPODataSnowflakeMigrationRequest
from services.sap.as_source.to_snowflake import (
    SAPODataSnowflakeMigrationRequest as ReqAlias,
    _build_connector_response,
    _resolve_celery_child_mode,
)


def test_sap_models_accept_celery_child_mode():
    payload = {
        "source_config": {"host": "sap.example.com"},
        "destination_config": {"schema": "SAP"},
        "connection_id": 1,
        "cmp_id": 1,
        "user_id": 1,
        "session": 1,
        "migration_session_id": 99,
        "selected_tables": ["EntityA"],
        "execution_order": "parallel",
        "chunk_count": 50000,
        "safety_interval": "120",
        "celery_child_mode": True,
        "table_group_index": 2,
    }
    sf_req = SAPODataSnowflakeMigrationRequest(**payload)
    adls_req = SAPODataADLSMigrationRequest(**payload)

    assert sf_req.celery_child_mode is True
    assert adls_req.celery_child_mode is True
    assert adls_req.table_group_index == 2


def test_resolve_celery_child_mode_from_fanout_flag():
    req = SAPODataSnowflakeMigrationRequest(
        source_config={},
        destination_config={},
        connection_id=1,
        cmp_id=1,
        user_id=1,
        session=1,
        migration_session_id=1,
        selected_tables=["T"],
        celery_fanout_mode=True,
    )
    assert _resolve_celery_child_mode(req) is True


def test_build_connector_response_skips_terminal_log_in_child_mode(monkeypatch):
    calls = []

    def fake_update(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "services.sap.as_source.to_snowflake.update_migration_status_log",
        fake_update,
    )

    req = ReqAlias(
        source_config={},
        destination_config={},
        connection_id=1,
        cmp_id=1,
        user_id=1,
        session=1,
        migration_session_id=1,
        selected_tables=["T"],
        job_name="sap_job",
        celery_child_mode=True,
    )

    class Tracker:
        @staticmethod
        def has_failures():
            return False

        @staticmethod
        def get_failure_summary():
            return {"failed_tables": []}

    _build_connector_response(req, [{"table_name": "T", "status": "success"}], 1.0, Tracker())

    assert len(calls) == 1
    assert calls[0]["celery_child_mode"] is True
