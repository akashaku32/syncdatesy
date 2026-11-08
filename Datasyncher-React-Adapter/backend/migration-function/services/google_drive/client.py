import json
import time
import urllib.parse
from typing import Any, Dict, Optional, Tuple

import requests
from fastapi import HTTPException

GOOGLE_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
GOOGLE_DRIVE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"


def _extract_drive_details(payload: Dict[str, Any]) -> Dict[str, Any]:
    details = payload.get("GoogleDrive")
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid GoogleDrive JSON payload: {exc}")

    if details is None:
        details = {}

    if not isinstance(details, dict):
        raise HTTPException(status_code=400, detail="GoogleDrive must be an object")

    return details


def get_google_drive_authorize_url(state: str, data: Dict[str, Any], redirect_uri: Optional[str] = None) -> str:
    client_id = data.get("CLIENT_ID") or data.get("client_id")
    if not client_id:
        raise HTTPException(status_code=400, detail="CLIENT_ID is required")

    redirect = redirect_uri or data.get("redirect_uri")
    if not redirect:
        raise HTTPException(status_code=400, detail="redirect_uri is required")

    scope = data.get("scope") or GOOGLE_DRIVE_SCOPE
    params = {
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": scope,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


def get_access_token(code: str, state: str, data: Dict[str, Any], redirect_uri: Optional[str] = None) -> Dict[str, Any]:
    client_id = data.get("CLIENT_ID") or data.get("client_id")
    client_secret = data.get("CLIENT_SECRET") or data.get("client_secret")
    redirect = redirect_uri or data.get("redirect_uri")

    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="CLIENT_ID and CLIENT_SECRET are required")
    if not redirect:
        raise HTTPException(status_code=400, detail="redirect_uri is required")

    payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect,
        "grant_type": "authorization_code",
    }
    resp = requests.post(GOOGLE_TOKEN_URL, data=payload, timeout=30)
    if resp.status_code != 200:
        detail = _extract_google_error(resp)
        raise HTTPException(status_code=resp.status_code, detail=detail)

    tokens = resp.json()
    expires_in = int(tokens.get("expires_in", 3600))
    return {
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "expires_in": expires_in,
        "token_expires_at": int(time.time()) + expires_in,
        "token_type": tokens.get("token_type"),
        "state": state,
    }


def refresh_token(refresh_token: str, client_id: str, client_secret: str) -> Dict[str, Any]:
    payload = {
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
    }
    resp = requests.post(GOOGLE_TOKEN_URL, data=payload, timeout=30)
    if resp.status_code != 200:
        detail = _extract_google_error(resp)
        raise HTTPException(status_code=resp.status_code, detail=detail)

    tokens = resp.json()
    expires_in = int(tokens.get("expires_in", 3600))
    return {
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token") or refresh_token,
        "expires_in": expires_in,
        "token_expires_at": int(time.time()) + expires_in,
        "token_type": tokens.get("token_type"),
    }


def _extract_google_error(resp: requests.Response) -> str:
    try:
        payload = resp.json()
    except Exception:
        return resp.text or f"Request failed with HTTP {resp.status_code}"

    if isinstance(payload, dict):
        if isinstance(payload.get("error"), dict):
            error = payload["error"]
            msg = error.get("message") or payload.get("message") or resp.text
            code = error.get("code")
            status = error.get("status")
            return f"Google API error (code={code}, status={status}): {msg}"
        if payload.get("error_description"):
            return str(payload["error_description"])
        if payload.get("error"):
            return str(payload["error"])
        if payload.get("message"):
            return str(payload["message"])
    return resp.text or f"Request failed with HTTP {resp.status_code}"


def _format_folder_display(folder_id: Optional[str], folder_name: Optional[str]) -> str:
    folder_name = str(folder_name or "").strip()
    folder_id = str(folder_id or "").strip()
    if folder_name and folder_id:
        return f"'{folder_name}' (ID: {folder_id})"
    if folder_name:
        return f"'{folder_name}'"
    if folder_id:
        return f"ID: {folder_id}"
    return "the configured folder"


def _raise_google_drive_folder_error(
    resp: requests.Response,
    *,
    folder_id: Optional[str] = None,
    folder_name: Optional[str] = None,
    action: str = "access Google Drive folder",
) -> None:
    display = _format_folder_display(folder_id, folder_name)
    if resp.status_code == 404:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Configured Google Drive root folder was not found or has been deleted: {display}. "
                "Please restore the folder from Google Drive trash or update the connection to use an existing folder."
            ),
        )
    if resp.status_code in (401, 403):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Configured Google Drive root folder is not accessible: {display}. "
                "Please verify the connected Google account has permission to create files in this folder."
            ),
        )
    raise HTTPException(status_code=422, detail=f"Failed to {action}: {_extract_google_error(resp)}")


def _headers(access_token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def _drive_request(method: str, url: str, access_token: str, **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {})
    merged = _headers(access_token)
    merged.update(headers)
    resp = requests.request(method, url, headers=merged, timeout=30, **kwargs)
    return resp


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _folder_path_parts(folder_name: Optional[str]) -> Tuple[list[str], bool]:
    """Return clean Google Drive folder path segments and whether it is relative."""
    raw_path = str(folder_name or "").strip().replace("\\", "/")
    relative_to_id = raw_path.startswith("/")
    return [part.strip() for part in raw_path.split("/") if part.strip()], relative_to_id


def _escape_drive_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _find_folder(
    access_token: str,
    folder_name: str,
    *,
    parent_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    safe_folder_name = _escape_drive_query_value(folder_name)
    query_parts = [
        "mimeType='application/vnd.google-apps.folder'",
        f"name='{safe_folder_name}'",
        "trashed=false",
    ]
    if parent_id:
        query_parts.append(f"'{_escape_drive_query_value(parent_id)}' in parents")

    resp = _drive_request(
        "GET",
        f"{GOOGLE_DRIVE_API_BASE}/files",
        access_token,
        params={
            "q": " and ".join(query_parts),
            "fields": "files(id,name)",
            "spaces": "drive",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        },
    )
    if resp.status_code != 200:
        _raise_google_drive_folder_error(
            resp,
            folder_name=folder_name,
            action="search Google Drive folders",
        )
    files = resp.json().get("files", []) or []
    return files[0] if files else None


def _create_folder(
    access_token: str,
    folder_name: str,
    *,
    parent_id: Optional[str] = None,
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        metadata["parents"] = [parent_id]

    resp = _drive_request(
        "POST",
        f"{GOOGLE_DRIVE_API_BASE}/files",
        access_token,
        json=metadata,
        params={"fields": "id,name", "supportsAllDrives": "true"},
    )
    if resp.status_code not in (200, 201):
        _raise_google_drive_folder_error(
            resp,
            folder_id=parent_id,
            folder_name=folder_name,
            action="create Google Drive folder",
        )
    return resp.json()


def _resolve_folder_segments(
    access_token: str,
    path_parts: list[str],
    *,
    parent_id: Optional[str] = None,
    create_folder_if_missing: bool = False,
    full_path: str,
) -> Dict[str, Any]:
    current_parent_id = parent_id
    current_folder: Optional[Dict[str, Any]] = None
    for part in path_parts:
        current_folder = _find_folder(access_token, part, parent_id=current_parent_id)
        if current_folder is None:
            if not create_folder_if_missing:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": f"Folder path '{full_path}' was not found. Confirm to create it.",
                        "error": "GOOGLE_DRIVE_FOLDER_CONFIRMATION_REQUIRED",
                        "folder_name": full_path,
                        "requires_confirmation": True,
                    },
                )
            current_folder = _create_folder(access_token, part, parent_id=current_parent_id)
        current_parent_id = current_folder.get("id")

    if current_folder is None:
        raise HTTPException(status_code=400, detail="Folder Name is required")
    return current_folder


def _resolve_folder(
    access_token: str,
    folder_id: Optional[str],
    folder_name: Optional[str],
    create_folder_if_missing: bool = False,
) -> Dict[str, Any]:
    folder_id = str(folder_id or "").strip()
    raw_folder_name = str(folder_name or "").strip()
    path_parts, relative_to_id = _folder_path_parts(raw_folder_name)
    folder_name = "/".join(path_parts)

    if folder_id and not folder_name:
        raise HTTPException(status_code=400, detail="Folder Name is required when Folder ID is provided")

    if folder_id:
        url = f"{GOOGLE_DRIVE_API_BASE}/files/{folder_id}"
        resp = _drive_request(
            "GET",
            url,
            access_token,
            params={
                "fields": "id,name,mimeType,trashed",
                "supportsAllDrives": "true",
            },
        )
        if resp.status_code != 200:
            _raise_google_drive_folder_error(
                resp,
                folder_id=folder_id,
                folder_name=folder_name,
                action="validate Google Drive folder",
            )
        folder = resp.json()
        if folder.get("mimeType") != "application/vnd.google-apps.folder":
            raise HTTPException(
                status_code=422,
                detail="The provided Folder ID does not belong to a Google Drive folder.",
            )
        if folder.get("trashed") is True:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Configured Google Drive root folder was deleted or is in trash: "
                    f"{_format_folder_display(folder.get('id') or folder_id, folder.get('name') or folder_name)}. "
                    "Please restore the folder from Google Drive trash or update the connection to use an existing folder."
                ),
            )
        actual_folder_name = str(folder.get("name") or "").strip()
        if len(path_parts) <= 1 and actual_folder_name != folder_name:
            raise HTTPException(
                status_code=422,
                detail="Folder ID and Folder Name do not match. Please enter the correct folder details.",
            )

        if len(path_parts) > 1 or relative_to_id:
            # A leading slash is always relative to the supplied Folder ID.
            # For "root/child", the ID may identify either the root (new form
            # input) or the final child (a previously resolved/saved config).
            if relative_to_id:
                remaining_parts = path_parts
                resolved_path = "/".join([actual_folder_name, *path_parts])
            elif actual_folder_name == path_parts[0]:
                remaining_parts = path_parts[1:]
                resolved_path = folder_name
            elif actual_folder_name == path_parts[-1]:
                return {"folder_id": folder.get("id"), "folder_name": folder_name}
            else:
                raise HTTPException(
                    status_code=422,
                    detail="Folder ID and Folder Name path do not match. Please enter the correct folder details.",
                )

            if remaining_parts:
                resolved_folder = _resolve_folder_segments(
                    access_token,
                    remaining_parts,
                    parent_id=folder.get("id"),
                    create_folder_if_missing=create_folder_if_missing,
                    full_path=resolved_path,
                )
                return {
                    "folder_id": resolved_folder.get("id"),
                    "folder_name": resolved_path,
                }
        return {
            "folder_id": folder.get("id"),
            "folder_name": actual_folder_name or folder_name,
        }

    if not folder_name:
        raise HTTPException(status_code=400, detail="Folder Name is required")

    folder = _resolve_folder_segments(
        access_token,
        path_parts,
        create_folder_if_missing=create_folder_if_missing,
        full_path=folder_name,
    )
    return {"folder_id": folder.get("id"), "folder_name": folder_name}


def validate_google_drive_connection(payload: Dict[str, Any]) -> Dict[str, Any]:
    details = _extract_drive_details(payload)

    access_token = details.get("access_token")
    refresh_tok = details.get("refresh_token")
    client_id = details.get("CLIENT_ID") or details.get("client_id")
    client_secret = details.get("CLIENT_SECRET") or details.get("client_secret")
    folder_id = details.get("folder_id")
    folder_name = details.get("folder_name")
    create_folder_if_missing = _as_bool(details.get("create_folder_if_missing"))

    if not access_token:
        raise HTTPException(status_code=400, detail="access_token is required")

    if not folder_id and not folder_name:
        raise HTTPException(status_code=400, detail="Google Drive destination requires either folder_id or folder_name")
    if folder_id and not folder_name:
        raise HTTPException(status_code=400, detail="Folder Name is required when Folder ID is provided")

    if not refresh_tok or not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="refresh_token, CLIENT_ID, and CLIENT_SECRET are required")

    # ✅ VALIDATE CLIENT_ID and CLIENT_SECRET NOW (before folder lookup)
    # Force a token refresh to immediately prove credentials are correct
    # This prevents silent failures during migration
    try:
        test_refresh = refresh_token(refresh_tok, client_id, client_secret)
        # Update tokens with freshly validated ones
        access_token = test_refresh.get("access_token") or access_token
        details["access_token"] = access_token
        details["refresh_token"] = test_refresh.get("refresh_token") or refresh_tok
        if test_refresh.get("token_expires_at"):
            details["token_expires_at"] = test_refresh["token_expires_at"]
    except HTTPException as e:
        # Provide clear feedback if credentials are wrong
        error_msg = str(e.detail or "").lower()
        if "invalid_client" in error_msg or "client_id" in error_msg or "client_secret" in error_msg:
            raise HTTPException(
                status_code=422,
                detail="CLIENT_ID or CLIENT_SECRET is invalid. Please verify your OAuth app credentials."
            )
        # Re-raise if it's a different error
        raise

    try:
        resolved_folder = _resolve_folder(
            access_token,
            folder_id,
            folder_name,
            create_folder_if_missing=create_folder_if_missing,
        )
    except HTTPException as exc:
        if exc.status_code == 409:
            raise
        if refresh_tok:
            refreshed = refresh_token(refresh_tok, client_id, client_secret)
            access_token = refreshed.get("access_token") or access_token
            details["access_token"] = access_token
            details["refresh_token"] = refreshed.get("refresh_token") or refresh_tok
            if refreshed.get("token_expires_at"):
                details["token_expires_at"] = refreshed["token_expires_at"]
            resolved_folder = _resolve_folder(
                access_token,
                folder_id,
                folder_name,
                create_folder_if_missing=create_folder_if_missing,
            )
        else:
            raise

    return {
        "status_code": 200,
        "body": "Google Drive connection validated successfully",
        "access_token": access_token,
        "refresh_token": details.get("refresh_token"),
        "token_expires_at": details.get("token_expires_at"),
        "folder_id": resolved_folder.get("folder_id"),
        "folder_name": resolved_folder.get("folder_name"),
    }
