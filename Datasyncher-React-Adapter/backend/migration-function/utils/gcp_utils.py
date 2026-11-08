import os
from typing import List

from google.cloud import storage

from utils.s3_utils import log


def gcs_client(gcs_service_account_json: str = None, gcp_project_id: str = None) -> storage.Client:
    """Create GCS client."""
    if gcs_service_account_json and os.path.exists(gcs_service_account_json):
        return storage.Client.from_service_account_json(gcs_service_account_json, project=gcp_project_id)
    adc = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if adc and os.path.exists(adc):
        return storage.Client(project=gcp_project_id)
    raise RuntimeError("GCS credentials not found")


def ensure_gcs_storage_integration(conn, integration_name: str, bucket_url_prefix: str):
    """Ensure the Snowflake storage integration exists and is allowed to read the given GCS URL prefix."""
    with conn.cursor() as cur:
        cur.execute(f"SHOW STORAGE INTEGRATIONS LIKE '{integration_name}'")
        rows = cur.fetchall()
        if rows:
            log(f"✅ Storage integration '{integration_name}' already exists.")
            cur.execute(f"DESC INTEGRATION {integration_name}")
            actual_locations: List[str] = []
            for prop, typ, val, default in cur.fetchall():
                if prop == "STORAGE_ALLOWED_LOCATIONS":
                    actual_locations = [s.strip().strip("[]'\" ") for s in val.split(",")]
                    break
            if bucket_url_prefix in actual_locations:
                log(f"✅ Storage integration '{integration_name}' already allows {bucket_url_prefix}")
                return True
            else:
                log(f"⚠️ Storage integration '{integration_name}' has different allowed locations.")
                try:
                    cur.execute(f"DROP STORAGE INTEGRATION {integration_name}")
                    log(f"✓ Dropped existing storage integration '{integration_name}'")
                except Exception as drop_error:
                    log(f"⚠️ Error dropping storage integration: {drop_error}")

        log(f"🚧 Creating storage integration '{integration_name}' …")
        create_sql = f"""
            CREATE STORAGE INTEGRATION {integration_name}
            TYPE = EXTERNAL_STAGE
            STORAGE_PROVIDER = 'GCS'
            ENABLED = TRUE
            STORAGE_ALLOWED_LOCATIONS = ('{bucket_url_prefix}');
        """
        cur.execute(create_sql)
        log(f"✅ Storage integration '{integration_name}' created with allowed location: {bucket_url_prefix}")
        return False


def get_gcp_service_account(conn, integration_name: str) -> str:
    """Read the auto-provisioned service account that Snowflake uses for this integration."""
    with conn.cursor() as cur:
        cur.execute(f"DESC INTEGRATION {integration_name}")
        for prop, typ, val, default in cur.fetchall():
            if prop == "STORAGE_GCP_SERVICE_ACCOUNT":
                log(f"✅ GCP service account for '{integration_name}': {val}")
                return val
    raise RuntimeError(f"STORAGE_GCP_SERVICE_ACCOUNT not found in integration '{integration_name}'")


def ensure_bucket_iam_for_integration(gcs: storage.Client, bucket_name: str, member_sa: str):
    """Grant roles/storage.objectAdmin to the Snowflake integration's service account on the bucket."""
    member = f"serviceAccount:{member_sa}"
    role = "roles/storage.objectAdmin"
    bucket = gcs.bucket(bucket_name)
    policy = bucket.get_iam_policy(requested_policy_version=3)
    for b in policy.bindings:
        if b["role"] == role:
            mems = set(b.get("members", []))
            if member in mems:
                log(f"✅ '{member}' already has '{role}' on '{bucket_name}'.")
                return
            mems.add(member)
            b["members"] = sorted(list(mems))
            bucket.set_iam_policy(policy)
            log(f"✅ IAM updated: added '{member}' to existing '{role}'.")
            return
    policy.bindings.append({"role": role, "members": [member]})
    bucket.set_iam_policy(policy)
    log(f"✅ IAM updated: bound '{member}' → '{role}' on '{bucket_name}'.")


def delete_blob_from_bucket(
    bucket_name: str,
    blob_name: str,
    client: storage.Client = None,
) -> bool:
    """Delete a blob from GCS. Returns True if deleted or absent, False on error."""
    try:
        gcs = client or storage.Client()
        bucket = gcs.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        if not blob.exists():
            log(f"Blob not found (already absent): {bucket_name}/{blob_name}")
            return True
        blob.delete()
        log(f"Deleted blob: {bucket_name}/{blob_name}")
        return True
    except Exception as e:
        log(f"Failed to delete blob {bucket_name}/{blob_name}: {e}")
        return False


def assert_allowed_prefix_matches(conn, expected_prefix: str, integration_name: str):
    """Fail fast if the Snowflake storage integration doesn't allow the exact GCS prefix we will use."""
    actual: List[str] = []
    with conn.cursor() as cur:
        cur.execute(f"DESC INTEGRATION {integration_name}")
        for prop, typ, val, default in cur.fetchall():
            if prop == "STORAGE_ALLOWED_LOCATIONS":
                actual = [s.strip().strip("[]'\" ") for s in val.split(",")]
                break
    if expected_prefix not in actual:
        raise RuntimeError(
            f"❌ STORAGE_ALLOWED_LOCATIONS mismatch.\n"
            f"Expected: {expected_prefix}\n"
            f"Found:    {actual}\n\n"
            f"Fix in Snowflake:\n"
            f"  DROP STORAGE INTEGRATION {integration_name};\n"
            f"  CREATE STORAGE INTEGRATION {integration_name}\n"
            f"    TYPE=EXTERNAL_STAGE STORAGE_PROVIDER='GCS' ENABLED=TRUE\n"
            f"    STORAGE_ALLOWED_LOCATIONS=(' {expected_prefix} ');"
        )
    log(f"✅ Storage integration '{integration_name}' allows {expected_prefix}")


__all__ = [
    "gcs_client",
    "ensure_gcs_storage_integration",
    "get_gcp_service_account",
    "ensure_bucket_iam_for_integration",
    "assert_allowed_prefix_matches",
    "delete_blob_from_bucket",
]


