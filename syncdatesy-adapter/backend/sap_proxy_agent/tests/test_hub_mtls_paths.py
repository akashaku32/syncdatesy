import os
from pathlib import Path
from unittest.mock import patch

from sap_proxy_agent.hub.mtls_paths import (
    hub_ca_cert_path,
    hub_server_cert_path,
)


def test_hub_mtls_dir_override():
    with patch.dict(os.environ, {"HUB_MTLS_DIR": "/tmp/hub-mtls"}):
        assert hub_ca_cert_path() == Path("/tmp/hub-mtls") / "ca.crt"
        assert hub_server_cert_path().name in (
            "client_svr_server.crt",
            "server.crt",
        )
