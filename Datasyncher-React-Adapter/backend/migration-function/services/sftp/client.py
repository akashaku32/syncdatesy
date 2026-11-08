import io
import logging
import socket
import uuid
from typing import Optional

from fastapi import HTTPException

from models.sftp_models import SFTPAuthorizationRequest

logger = logging.getLogger(__name__)


def _extract_config(request: SFTPAuthorizationRequest) -> dict:
    config = request.SFTP or request.destination_config or request.config or {}
    if not isinstance(config, dict):
        raise HTTPException(status_code=422, detail="Invalid SFTP config; expected object.")
    return config


def _map_sftp_connection_error(error: Exception, host: str, port: int) -> HTTPException:
    msg = str(error or "").strip()
    msg_low = msg.lower()

    if isinstance(error, socket.gaierror):
        return HTTPException(
            status_code=422,
            detail=f"SFTP host could not be resolved: {host}. Please verify the server address.",
        )

    if isinstance(error, TimeoutError) or isinstance(error, socket.timeout):
        return HTTPException(
            status_code=422,
            detail=f"SFTP connection timed out while reaching {host}:{port}. Please verify the host, port, and network access.",
        )

    if isinstance(error, ConnectionRefusedError):
        return HTTPException(
            status_code=422,
            detail=f"SFTP server refused the connection on {host}:{port}. Please verify the port and that the SFTP service is running.",
        )

    if isinstance(error, OSError):
        if getattr(error, "errno", None) in {101, 113}:
            return HTTPException(
                status_code=422,
                detail=f"SFTP network route is unreachable for {host}:{port}. Please verify firewall, VPN, or network routing.",
            )
        if getattr(error, "errno", None) == 111:
            return HTTPException(
                status_code=422,
                detail=f"SFTP server refused the connection on {host}:{port}. Please verify the port and that the SFTP service is running.",
            )

    if "authentication failed" in msg_low or "auth fail" in msg_low:
        return HTTPException(status_code=422, detail="SFTP authentication failed. Please verify credentials.")
    if "name or service not known" in msg_low or "nodename nor servname provided" in msg_low:
        return HTTPException(
            status_code=422,
            detail=f"SFTP host could not be resolved: {host}. Please verify the server address.",
        )
    if "connection refused" in msg_low:
        return HTTPException(
            status_code=422,
            detail=f"SFTP server refused the connection on {host}:{port}. Please verify the port and that the SFTP service is running.",
        )
    if "network is unreachable" in msg_low or "no route to host" in msg_low:
        return HTTPException(
            status_code=422,
            detail=f"SFTP network route is unreachable for {host}:{port}. Please verify firewall, VPN, or network routing.",
        )
    if "timed out" in msg_low or "timeout" in msg_low:
        return HTTPException(
            status_code=422,
            detail=f"SFTP connection timed out while reaching {host}:{port}. Please verify the host, port, and network access.",
        )

    return HTTPException(status_code=422, detail="Failed to connect to SFTP. Please verify connection details.")


def validate_sftp_authorization(request: SFTPAuthorizationRequest) -> None:
    """
    Validate SFTP destination config fields.

    Note: This is config validation only (no connectivity test).
    """
    config = _extract_config(request)

    host = str(config.get("sftp_host") or config.get("host") or "").strip()
    port = config.get("sftp_port") if config.get("sftp_port") is not None else config.get("port")
    username = str(config.get("sftp_username") or config.get("username") or "").strip()
    auth_type = str(config.get("authentication_type") or "password").strip().lower()

    if not host:
        raise HTTPException(status_code=422, detail="Missing required field: sftp_host")
    if not username:
        raise HTTPException(status_code=422, detail="Missing required field: sftp_username")

    try:
        port_int = int(port) if port is not None and str(port).strip() != "" else 22
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Invalid sftp_port; must be an integer")
    if port_int <= 0 or port_int > 65535:
        raise HTTPException(status_code=422, detail="Invalid sftp_port; must be 1..65535")

    if auth_type not in ("password", "key_pair"):
        raise HTTPException(status_code=422, detail="Invalid authentication_type; expected password or key_pair")

    if auth_type == "password":
        password = config.get("sftp_password") or config.get("password")
        if not password:
            raise HTTPException(status_code=422, detail="Missing required field: sftp_password (password auth)")
    else:
        private_key = config.get("sftp_private_key") or config.get("private_key")
        if not private_key:
            raise HTTPException(status_code=422, detail="Missing required field: sftp_private_key (key_pair auth)")

    folder_path = str(config.get("root_folder") or config.get("folder_path") or "").strip()
    if not folder_path:
        raise HTTPException(status_code=422, detail="Missing required field: root_folder")

    is_source = getattr(request, "is_source", None)
    if is_source is None and isinstance(config, dict):
        is_source = config.get("is_source")

    # file_format is optional for backwards-compatibility with older destination forms.
    # When omitted, default to CSV.
    if not is_source:
        raw_file_format = config.get("file_format")
        file_format = str(raw_file_format).strip().lower() if raw_file_format not in ("", None) else "csv"
        if file_format not in ("csv", "json", "parquet", "excel"):
            raise HTTPException(status_code=422, detail="Invalid file_format; expected csv, json, parquet, or excel")

        if file_format == "csv":
            delimiter = config.get("csv_delimiter")
            quotechar = config.get("csv_quote_char") if config.get("csv_quote_char") is not None else config.get("csv_quotechar")
            if delimiter is not None and len(str(delimiter)) != 1:
                raise HTTPException(status_code=422, detail="csv_delimiter must be exactly 1 character")
            if quotechar is not None and len(str(quotechar)) != 1:
                raise HTTPException(status_code=422, detail="csv_quotechar must be exactly 1 character")



def _load_private_key(private_key_pem: str, passphrase: Optional[str]):
    try:
        import paramiko
    except Exception:
        raise HTTPException(status_code=500, detail="SFTP dependency missing: paramiko is not installed")

    key_stream = io.StringIO(private_key_pem)
    password = passphrase if passphrase not in ("", None) else None

    key_loaders = (
        paramiko.RSAKey,
        paramiko.ECDSAKey,
        getattr(paramiko, "Ed25519Key", None),
        paramiko.DSSKey,
    )
    last_error = None
    for key_cls in key_loaders:
        if key_cls is None:
            continue
        try:
            key_stream.seek(0)
            return key_cls.from_private_key(key_stream, password=password)
        except Exception as e:
            last_error = e

    raise HTTPException(status_code=422, detail=f"Invalid sftp_private_key; failed to parse key ({last_error})")


def _ensure_remote_dir(sftp, remote_dir: str) -> None:
    # Normalize and create directories iteratively. We avoid os.path since remote paths are POSIX-like.
    path = (remote_dir or "").strip()
    if not path:
        raise HTTPException(status_code=422, detail="Missing required field: root_folder")

    if path == "/":
        return

    parts = [p for p in path.split("/") if p]
    current = "/" if path.startswith("/") else "."
    for part in parts:
        next_path = f"{current.rstrip('/')}/{part}" if current not in (".", "") else part
        try:
            sftp.chdir(next_path)
        except Exception:
            try:
                sftp.mkdir(next_path)
                sftp.chdir(next_path)
            except Exception as e:
                raise HTTPException(status_code=422, detail=f"Remote folder not accessible: {path}") from e
        current = next_path


def _join_remote_path(remote_dir: str, filename: str) -> str:
    base_dir = (remote_dir or "").strip()
    if base_dir in ("", "."):
        return filename
    if base_dir == "/":
        return f"/{filename}"
    return f"{base_dir.rstrip('/')}/{filename}"


def _assert_remote_dir_writable(sftp, remote_dir: str) -> None:
    normalized_dir = (remote_dir or "").strip()
    probe_name = f".datasyncher_write_probe_{uuid.uuid4().hex}"
    if normalized_dir and not normalized_dir.startswith("/"):
        # _ensure_remote_dir() leaves the SFTP client cwd inside relative paths,
        # so using the relative folder again here would duplicate it (e.g. upload/upload/*).
        probe_path = probe_name
    else:
        probe_path = _join_remote_path(normalized_dir, probe_name)
    created = False
    try:
        # Write and immediately delete a tiny probe file so destination creation
        # verifies that the configured root folder is actually writable.
        if hasattr(sftp, "open"):
            remote_file = sftp.open(probe_path, "w")
            try:
                remote_file.write("")
            finally:
                remote_file.close()
        else:
            sftp.putfo(io.BytesIO(b""), probe_path)
        created = True
    except Exception as e:
        logger.warning(
            "SFTP root folder write probe failed | remote_dir=%s probe_path=%s error_type=%s error=%s",
            remote_dir,
            probe_path,
            type(e).__name__,
            str(e),
        )
        folder_display = remote_dir or "."
        folder_label = "root folder" if folder_display.strip() in {".", "/"} else "target folder"
        raise HTTPException(
            status_code=422,
            detail=(
                f"The configured {folder_label} is not writable: {folder_display}. "
                "The SFTP connection succeeded, but the configured user does not have permission "
                "to create files in this folder. Please grant write permission on this folder "
                "or choose a different destination folder."
            ),
        ) from e
    finally:
        if created:
            try:
                sftp.remove(probe_path)
            except Exception:
                logger.warning(
                    "SFTP root folder write probe cleanup failed | remote_dir=%s probe_path=%s",
                    remote_dir,
                    probe_path,
                )
                pass


def test_sftp_connection(request: SFTPAuthorizationRequest) -> None:
    """
    Attempt to authenticate to SFTP and verify the remote folder is accessible.
    """
    try:
        import paramiko
    except Exception:
        raise HTTPException(status_code=500, detail="SFTP dependency missing: paramiko is not installed")

    config = _extract_config(request)

    host = str(config.get("sftp_host") or config.get("host") or "").strip()
    port = config.get("sftp_port") if config.get("sftp_port") is not None else config.get("port")
    username = str(config.get("sftp_username") or config.get("username") or "").strip()
    auth_type = str(config.get("authentication_type") or "password").strip().lower()

    # validate_sftp_authorization already checks required fields; keep minimal defensive checks here.
    if not host or not username:
        raise HTTPException(status_code=422, detail="Missing required SFTP connection fields")

    try:
        port_int = int(port) if port is not None and str(port).strip() != "" else 22
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Invalid sftp_port; must be an integer")

    connect_timeout = 10
    sock = None
    transport = None
    sftp = None
    try:
        sock = socket.create_connection((host, port_int), timeout=connect_timeout)
        transport = paramiko.Transport(sock)
        transport.banner_timeout = connect_timeout
        transport.auth_timeout = connect_timeout

        if auth_type == "password":
            password = config.get("sftp_password") or config.get("password")
            transport.connect(username=username, password=password)
        else:
            private_key = config.get("sftp_private_key") or config.get("private_key")
            passphrase = config.get("sftp_passphrase") or config.get("passphrase")
            pkey = _load_private_key(str(private_key or ""), str(passphrase or "") if passphrase is not None else None)
            transport.connect(username=username, pkey=pkey)

        sftp = paramiko.SFTPClient.from_transport(transport)

        remote_dir = str(config.get("root_folder") or config.get("folder_path") or "").strip()
        is_source = getattr(request, "is_source", None)
        if is_source is None and isinstance(config, dict):
            is_source = config.get("is_source")

        if is_source:
            # Source connections should NOT create directories or check write permission.
            # They should strictly verify the directory exists and is readable.
            try:
                if remote_dir:
                    sftp.chdir(remote_dir)
                sftp.listdir(".")
            except IOError as e:
                err_msg = str(e).lower()
                err_no = getattr(e, "errno", None)
                if isinstance(e, FileNotFoundError) or err_no == 2 or "no such file" in err_msg:
                    raise HTTPException(
                        status_code=404,
                        detail=f"The configured root folder does not exist on SFTP: {remote_dir}. Please verify the folder path.",
                    )
                elif isinstance(e, PermissionError) or err_no == 13 or "permission denied" in err_msg or "access denied" in err_msg:
                    raise HTTPException(
                        status_code=403,
                        detail=f"Permission denied to access root folder: {remote_dir}. Please verify read permissions for the user.",
                    )
                else:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Root folder '{remote_dir}' is not accessible on SFTP: {str(e)}",
                    )
        else:
            _ensure_remote_dir(sftp, remote_dir)
            _assert_remote_dir_writable(sftp, remote_dir)
    except HTTPException:
        raise
    except Exception as e:
        raise _map_sftp_connection_error(e, host, port_int) from e
    finally:
        try:
            if sftp is not None:
                sftp.close()
        except Exception:
            pass
        try:
            if transport is not None:
                transport.close()
        except Exception:
            pass
        try:
            if sock is not None:
                sock.close()
        except Exception:
            pass
