import celery_worker as cw


def test_is_generic_failure_stub():
    assert cw._is_generic_failure_stub("4 child group(s) failed")
    assert cw._is_generic_failure_stub("child task failed")
    assert not cw._is_generic_failure_stub(
        "Databricks SQL warehouse request failed: Invalid access token."
    )


def test_collect_fanout_failure_reason_from_child_payloads():
    reason = cw._collect_fanout_failure_reason(
        "job_a",
        68,
        [
            {"status": "failed", "error": "child task failed"},
            {
                "status": "failed",
                "error": (
                    "An error occurred while processing the table 't1': "
                    "Databricks SQL warehouse request failed: Invalid access token."
                ),
            },
        ],
    )
    assert "Invalid access token" in reason
