"""Public deployment URLs from cluster secrets (DOMAIN / FRONTEND_URL)."""

import os


def get_public_domain() -> str:
    return os.getenv("DOMAIN", "qa-kubernetes.datasyncher.com").strip().rstrip("/")


def get_frontend_url() -> str:
    explicit = os.getenv("FRONTEND_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    return f"https://{get_public_domain()}"


def get_api_redirect_uri(path: str) -> str:
    normalized = path if path.startswith("/") else f"/{path}"
    return f"https://{get_public_domain()}{normalized}"


def get_google_drive_oauth_return_url(query_string: str = "") -> str:
    base = f"{get_frontend_url()}/dashboard/connectors/add/select-destination/GoogleDrive"
    if query_string:
        return f"{base}?{query_string}"
    return base
