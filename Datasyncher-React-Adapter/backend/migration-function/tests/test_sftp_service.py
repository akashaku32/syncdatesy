"""
Unit tests for SFTP service (connection test) with paramiko mocked.
No real network calls are made.
"""
import socket
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from models.sftp_models import SFTPAuthorizationRequest
from services.sftp.client import (
    test_sftp_connection as run_sftp_connection_test,
    validate_sftp_authorization,
)


class _FakeSFTPClient:
    def __init__(self):
        self._dirs = set(["/", "."])
        self.cwd = "."
        self._files = set()
        self.last_open_path = None

    def chdir(self, path: str):
        if path in self._dirs:
            self.cwd = path
            return
        raise FileNotFoundError(path)

    def listdir(self, path: str = "."):
        return []


    def mkdir(self, path: str):
        self._dirs.add(path)

    def open(self, path: str, mode: str):
        self.last_open_path = path
        self._files.add(path)

        class _FakeRemoteFile:
            def write(self, _content):
                return

            def close(self):
                return

        return _FakeRemoteFile()

    def remove(self, path: str):
        self._files.discard(path)

    def close(self):
        return


class _FakeTransport:
    def __init__(self, sock):
        self.sock = sock
        self.banner_timeout = None
        self.auth_timeout = None
        self.connected_kwargs = None

    def connect(self, **kwargs):
        self.connected_kwargs = kwargs

    def close(self):
        return


class _FakeKey:
    @classmethod
    def from_private_key(cls, stream, password=None):
        # consume stream to simulate parsing
        _ = stream.read()
        return ("pkey", password)


class _FailingKey:
    @classmethod
    def from_private_key(cls, stream, password=None):
        raise ValueError("bad key")


class _AuthFailTransport(_FakeTransport):
    def connect(self, **kwargs):
        raise Exception("Authentication failed")


class _InaccessibleSFTPClient(_FakeSFTPClient):
    def mkdir(self, path: str):
        raise PermissionError(path)


class _ReadOnlySFTPClient(_FakeSFTPClient):
    def open(self, path: str, mode: str):
        raise PermissionError(path)


def _install_fake_paramiko(
    monkeypatch,
    *,
    transport_cls=_FakeTransport,
    key_cls=_FakeKey,
    sftp_client_cls=_FakeSFTPClient,
):
    fake_paramiko = SimpleNamespace()
    fake_paramiko.Transport = transport_cls

    fake_paramiko.RSAKey = key_cls
    fake_paramiko.ECDSAKey = key_cls
    fake_paramiko.DSSKey = key_cls
    fake_paramiko.Ed25519Key = key_cls

    class _SFTPClientFactory:
        @staticmethod
        def from_transport(transport):
            return sftp_client_cls()

    fake_paramiko.SFTPClient = _SFTPClientFactory

    monkeypatch.setitem(sys.modules, "paramiko", fake_paramiko)


def _valid_config(**overrides):
    config = {
        "sftp_host": "example.com",
        "sftp_port": 22,
        "sftp_username": "user",
        "authentication_type": "password",
        "sftp_password": "secret",
        "root_folder": "/upload",
        "csv_delimiter": ",",
        "csv_quotechar": '"',
    }
    config.update(overrides)
    return config


def _request(config):
    return SFTPAuthorizationRequest(SFTP=config)


def _assert_validation_error(config, expected_detail):
    with pytest.raises(HTTPException) as exc:
        validate_sftp_authorization(_request(config))

    assert exc.value.status_code == 422
    assert exc.value.detail == expected_detail


def test_validate_sftp_authorization_accepts_password_payload():
    validate_sftp_authorization(_request(_valid_config()))


def test_validate_sftp_authorization_accepts_missing_file_format_defaults_to_csv():
    config = _valid_config()
    config.pop("file_format", None)
    validate_sftp_authorization(_request(config))


def test_validate_sftp_authorization_accepts_key_pair_payload():
    validate_sftp_authorization(
        _request(
            _valid_config(
                authentication_type="key_pair",
                sftp_password=None,
                sftp_private_key="-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
                file_format="json",
            )
        )
    )


def test_validate_sftp_authorization_accepts_excel_payload():
    validate_sftp_authorization(
        _request(
            _valid_config(
                file_format="excel",
                csv_delimiter=None,
                csv_quotechar=None,
            )
        )
    )


@pytest.mark.parametrize(
    "overrides,expected_detail",
    [
        ({"sftp_host": ""}, "Missing required field: sftp_host"),
        ({"sftp_username": ""}, "Missing required field: sftp_username"),
        ({"sftp_port": "abc"}, "Invalid sftp_port; must be an integer"),
        ({"sftp_port": 0}, "Invalid sftp_port; must be 1..65535"),
        ({"sftp_port": 70000}, "Invalid sftp_port; must be 1..65535"),
        ({"authentication_type": "oauth"}, "Invalid authentication_type; expected password or key_pair"),
        ({"sftp_password": ""}, "Missing required field: sftp_password (password auth)"),
        ({"root_folder": ""}, "Missing required field: root_folder"),
        ({"file_format": "xml"}, "Invalid file_format; expected csv, json, parquet, or excel"),
        ({"csv_delimiter": "||"}, "csv_delimiter must be exactly 1 character"),
        ({"csv_quotechar": "''"}, "csv_quotechar must be exactly 1 character"),
    ],
)
def test_validate_sftp_authorization_rejects_invalid_password_payloads(overrides, expected_detail):
    _assert_validation_error(_valid_config(**overrides), expected_detail)


def test_validate_sftp_authorization_rejects_key_pair_without_private_key():
    _assert_validation_error(
        _valid_config(authentication_type="key_pair", sftp_password=None),
        "Missing required field: sftp_private_key (key_pair auth)",
    )


def test_sftp_connection_password_success(monkeypatch):
    _install_fake_paramiko(monkeypatch)
    req = _request(_valid_config(root_folder="/a/b"))
    with patch("services.sftp.client.socket.create_connection", return_value=object()):
        run_sftp_connection_test(req)


def test_sftp_connection_relative_root_folder_uses_cwd_for_write_probe(monkeypatch):
    captured_client = {}

    class _CapturingSFTPClient(_FakeSFTPClient):
        def __init__(self):
            super().__init__()
            self._dirs.add("upload")
            captured_client["client"] = self

    _install_fake_paramiko(monkeypatch, sftp_client_cls=_CapturingSFTPClient)
    req = _request(_valid_config(root_folder="upload"))

    with patch("services.sftp.client.socket.create_connection", return_value=object()):
        run_sftp_connection_test(req)

    assert captured_client["client"].last_open_path
    assert "/" not in captured_client["client"].last_open_path
    assert captured_client["client"].last_open_path.startswith(".datasyncher_write_probe_")


def test_sftp_connection_keypair_success(monkeypatch):
    _install_fake_paramiko(monkeypatch)
    req = _request(
        _valid_config(
            authentication_type="key_pair",
            sftp_password=None,
            sftp_private_key="-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
            sftp_passphrase="pass",
            file_format="json",
        )
    )
    with patch("services.sftp.client.socket.create_connection", return_value=object()):
        run_sftp_connection_test(req)


def test_sftp_connection_maps_authentication_failure(monkeypatch):
    _install_fake_paramiko(monkeypatch, transport_cls=_AuthFailTransport)
    req = _request(_valid_config())

    with patch("services.sftp.client.socket.create_connection", return_value=object()):
        with pytest.raises(HTTPException) as exc:
            run_sftp_connection_test(req)

    assert exc.value.status_code == 422
    assert exc.value.detail == "SFTP authentication failed. Please verify credentials."


def test_sftp_connection_maps_timeout(monkeypatch):
    _install_fake_paramiko(monkeypatch)
    req = _request(_valid_config())

    with patch("services.sftp.client.socket.create_connection", side_effect=socket.timeout("timed out")):
        with pytest.raises(HTTPException) as exc:
            run_sftp_connection_test(req)

    assert exc.value.status_code == 422
    assert exc.value.detail == "SFTP connection timed out while reaching example.com:22. Please verify the host, port, and network access."


def test_sftp_connection_maps_dns_resolution_failure(monkeypatch):
    _install_fake_paramiko(monkeypatch)
    req = _request(_valid_config())

    with patch("services.sftp.client.socket.create_connection", side_effect=socket.gaierror("Name or service not known")):
        with pytest.raises(HTTPException) as exc:
            run_sftp_connection_test(req)

    assert exc.value.status_code == 422
    assert exc.value.detail == "SFTP host could not be resolved: example.com. Please verify the server address."


def test_sftp_connection_maps_connection_refused(monkeypatch):
    _install_fake_paramiko(monkeypatch)
    req = _request(_valid_config())

    with patch("services.sftp.client.socket.create_connection", side_effect=ConnectionRefusedError("Connection refused")):
        with pytest.raises(HTTPException) as exc:
            run_sftp_connection_test(req)

    assert exc.value.status_code == 422
    assert exc.value.detail == "SFTP server refused the connection on example.com:22. Please verify the port and that the SFTP service is running."


def test_sftp_connection_rejects_invalid_private_key(monkeypatch):
    _install_fake_paramiko(monkeypatch, key_cls=_FailingKey)
    req = _request(
        _valid_config(
            authentication_type="key_pair",
            sftp_password=None,
            sftp_private_key="bad-key",
            file_format="json",
        )
    )

    with patch("services.sftp.client.socket.create_connection", return_value=object()):
        with pytest.raises(HTTPException) as exc:
            run_sftp_connection_test(req)

    assert exc.value.status_code == 422
    assert exc.value.detail.startswith("Invalid sftp_private_key; failed to parse key")


def test_sftp_connection_rejects_inaccessible_remote_folder(monkeypatch):
    _install_fake_paramiko(monkeypatch, sftp_client_cls=_InaccessibleSFTPClient)
    req = _request(_valid_config(root_folder="/locked"))

    with patch("services.sftp.client.socket.create_connection", return_value=object()):
        with pytest.raises(HTTPException) as exc:
            run_sftp_connection_test(req)

    assert exc.value.status_code == 422
    assert exc.value.detail == "Remote folder not accessible: /locked"


def test_sftp_connection_rejects_non_writable_remote_folder(monkeypatch):
    _install_fake_paramiko(monkeypatch, sftp_client_cls=_ReadOnlySFTPClient)
    req = _request(_valid_config(root_folder="/readonly"))

    with patch("services.sftp.client.socket.create_connection", return_value=object()):
        with pytest.raises(HTTPException) as exc:
            run_sftp_connection_test(req)

    assert exc.value.status_code == 422
    assert exc.value.detail == (
        "The configured target folder is not writable: /readonly. "
        "The SFTP connection succeeded, but the configured user does not have permission "
        "to create files in this folder. Please grant write permission on this folder "
        "or choose a different destination folder."
    )


def test_sftp_connection_source_success(monkeypatch):
    _install_fake_paramiko(monkeypatch)
    req = _request(_valid_config(root_folder="/", is_source=True))
    with patch("services.sftp.client.socket.create_connection", return_value=object()):
        run_sftp_connection_test(req)


def test_sftp_connection_source_missing_folder(monkeypatch):
    _install_fake_paramiko(monkeypatch)
    req = _request(_valid_config(root_folder="/missing", is_source=True))
    with patch("services.sftp.client.socket.create_connection", return_value=object()):
        with pytest.raises(HTTPException) as exc:
            run_sftp_connection_test(req)
    assert exc.value.status_code == 404
    assert "does not exist on SFTP" in exc.value.detail


def test_sftp_connection_source_permission_denied(monkeypatch):
    class _PermissionDeniedSFTPClient(_FakeSFTPClient):
        def chdir(self, path: str):
            raise PermissionError("Permission denied")

    _install_fake_paramiko(monkeypatch, sftp_client_cls=_PermissionDeniedSFTPClient)
    req = _request(_valid_config(root_folder="/protected", is_source=True))
    with patch("services.sftp.client.socket.create_connection", return_value=object()):
        with pytest.raises(HTTPException) as exc:
            run_sftp_connection_test(req)
    assert exc.value.status_code == 403
    assert "Permission denied to access root folder" in exc.value.detail

