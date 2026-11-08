"""
Pytest configuration and shared fixtures
This file is automatically loaded by pytest and mocks Django dependencies
"""
import os
import sys
from unittest.mock import MagicMock

os.environ.setdefault("SNOWFLAKE_STORAGE_INTEGRATION", "test_integration")
os.environ.setdefault("GCS_BUCKET_URL", "gs://test-bucket")
os.environ.setdefault("GCS_BUCKET_NAME", "test-bucket")
os.environ.setdefault("GCP_PROJECT_ID", "test-project")

# Mock Django modules before any test imports
sys.modules['apps'] = MagicMock()
sys.modules['apps.elttool'] = MagicMock()
sys.modules['apps.elttool.models'] = MagicMock()

# Mock the Django models
mock_s3_mapping = MagicMock()
mock_connection = MagicMock()

sys.modules['apps.elttool.models'].S3Mapping = mock_s3_mapping
sys.modules['apps.elttool.models'].Connection = mock_connection

# Mock other Django-related modules that might be imported
sys.modules['django'] = MagicMock()
sys.modules['django.db'] = MagicMock()
sys.modules['django.db.models'] = MagicMock()

# Optional shared package used by snowflake_service PEM helpers
sys.modules.setdefault('datasyncher_connections', MagicMock())
sys.modules.setdefault('datasyncher_connections.snowflake', MagicMock())
sys.modules.setdefault('datasyncher_connections.snowflake.pem', MagicMock())

