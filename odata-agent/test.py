import google.auth
from google.auth import downscoped
from google.auth.transport.requests import Request
from google.auth.credentials import with_scopes_if_required

PROJECT_ID = "Datasyncher-QA-VM"
BUCKET_NAME = "meta_data_bucket_ds_development"
OBJECT_PREFIX = "reports/"

CLOUD_PLATFORM_SCOPE = (
    "https://www.googleapis.com/auth/cloud-platform"
)


def generate_downscoped_token():
    # Get source credentials using Application Default Credentials.
    source_credentials, _ = google.auth.default(
        quota_project_id=PROJECT_ID
    )

    source_credentials = with_scopes_if_required(
        source_credentials,
        [CLOUD_PLATFORM_SCOPE],
    )

    # Restrict the token to the specified Cloud Storage bucket.
    available_resource = (
        f"//storage.googleapis.com/projects/_/buckets/{BUCKET_NAME}"
    )

    # Allow read-only access to objects.
    available_permissions = [
        "inRole:roles/storage.objectUser"
    ]

    # Restrict access to objects under the specified prefix.
    expression = (
        "resource.name.startsWith("
        f"'projects/_/buckets/{BUCKET_NAME}/objects/{OBJECT_PREFIX}'"
        ")"
    )

    condition = downscoped.AvailabilityCondition(
        expression=expression
    )

    rule = downscoped.AccessBoundaryRule(
        available_resource=available_resource,
        available_permissions=available_permissions,
        availability_condition=condition,
    )

    access_boundary = downscoped.CredentialAccessBoundary(
        rules=[rule]
    )

    # Create and refresh the downscoped credentials.
    credentials = downscoped.Credentials(
        source_credentials=source_credentials,
        credential_access_boundary=access_boundary,
    )

    credentials.refresh(Request())

    print("Downscoped token generated successfully.")
    print("Project:", PROJECT_ID)
    print("Bucket:", BUCKET_NAME)
    print("Object prefix:", OBJECT_PREFIX)
    print("Expires at:", credentials.expiry)

    # Return the token and expiration; never log the token.
    return credentials.token, credentials.expiry


if __name__ == "__main__":
    token, expiry = generate_downscoped_token()
    print("Downscoped token:", token)