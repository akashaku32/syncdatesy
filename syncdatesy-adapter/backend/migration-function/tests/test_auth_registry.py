from connectors.auth_registry import CONNECTOR_AUTH_REGISTRY, get_auth_path


def test_auth_registry_has_core_connectors():
    for name in ("Snowflake", "Salesforce", "AmazonS3", "MicrosoftDynamics365_FO"):
        assert name in CONNECTOR_AUTH_REGISTRY
        assert CONNECTOR_AUTH_REGISTRY[name].startswith("/")


def test_get_auth_path_source_oauth():
    assert get_auth_path("Salesforce", source_oauth=True) == "/Source-Salesforce-Authorization"
    assert get_auth_path("Unknown") is None
