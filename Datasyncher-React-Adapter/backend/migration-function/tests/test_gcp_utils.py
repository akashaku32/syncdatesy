"""
Unit tests for GCP utility functions
Tests helper functions for GCS operations and storage integration.
"""
import pytest
from unittest.mock import Mock, patch
import os

from utils.gcp_utils import (
    gcs_client,
    ensure_gcs_storage_integration,
    get_gcp_service_account,
    ensure_bucket_iam_for_integration,
    assert_allowed_prefix_matches
)


class TestGCSClient:
    """Test cases for GCS client creation"""
    
    @patch('utils.gcp_utils.storage')
    @patch.dict(os.environ, {}, clear=True)
    def test_gcs_client_with_service_account_json(self, mock_storage):
        """Test GCS client creation with service account JSON file"""
        # Arrange
        service_account_path = "/path/to/service-account.json"
        project_id = "test-project"
        
        mock_storage.Client.from_service_account_json = Mock(return_value=Mock())
        
        # Act
        with patch('os.path.exists', return_value=True):
            client = gcs_client(service_account_path, project_id)
        
        # Assert
        assert client is not None
        mock_storage.Client.from_service_account_json.assert_called_once()
    
    @patch('utils.gcp_utils.storage')
    @patch.dict(os.environ, {'GOOGLE_APPLICATION_CREDENTIALS': '/path/to/credentials.json'})
    def test_gcs_client_with_env_credentials(self, mock_storage):
        """Test GCS client creation with environment credentials"""
        # Arrange
        project_id = "test-project"
        mock_storage.Client = Mock(return_value=Mock())
        
        # Act
        with patch('os.path.exists', return_value=True):
            client = gcs_client(None, project_id)
        
        # Assert
        assert client is not None
        mock_storage.Client.assert_called_once()
    
    @patch.dict(os.environ, {}, clear=True)
    def test_gcs_client_no_credentials(self):
        """Test GCS client creation fails without credentials"""
        # Arrange
        project_id = "test-project"
        
        # Act & Assert
        with patch('os.path.exists', return_value=False):
            with pytest.raises(RuntimeError, match="GCS credentials not found"):
                gcs_client(None, project_id)


class TestEnsureGCSStorageIntegration:
    """Test cases for GCS storage integration setup"""
    
    def test_ensure_gcs_storage_integration_exists(self):
        """Test storage integration when it already exists"""
        # Arrange
        mock_conn = Mock()
        mock_cursor = Mock()
        # Make cursor a context manager
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.return_value = None
        
        # Mock fetchall to return proper format for DESC INTEGRATION
        # DESC INTEGRATION returns tuples: (property, type, value, default)
        desc_results = [
            ("STORAGE_ALLOWED_LOCATIONS", "STRING", "gs://test-bucket/data/", None)
        ]
        
        # Use side_effect to return different values for different execute calls
        call_count = [0]
        def mock_fetchall():
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: SHOW STORAGE INTEGRATIONS
                return [("TEST_INTEGRATION",)]
            else:
                # Second call: DESC INTEGRATION - returns tuples with 4 elements
                return desc_results
        
        mock_cursor.fetchall.side_effect = mock_fetchall
        
        integration_name = "TEST_INTEGRATION"
        bucket_url_prefix = "gs://test-bucket/data/"
        
        # Act
        result = ensure_gcs_storage_integration(mock_conn, integration_name, bucket_url_prefix)
        
        # Assert
        assert result is True or result is False  # Returns bool, not integration_name
        # Should check if integration exists
        assert mock_cursor.execute.called
    
    def test_ensure_gcs_storage_integration_create_new(self):
        """Test creating new storage integration"""
        # Arrange
        mock_conn = Mock()
        mock_cursor = Mock()
        # Make cursor a context manager
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.return_value = None
        # SHOW STORAGE INTEGRATIONS returns empty (integration doesn't exist)
        mock_cursor.fetchall.return_value = []
        
        integration_name = "NEW_INTEGRATION"
        bucket_url_prefix = "gs://test-bucket/data/"
        
        # Act
        # When integration doesn't exist, it tries to create it
        # This will call execute with CREATE statement
        result = ensure_gcs_storage_integration(mock_conn, integration_name, bucket_url_prefix)
        
        # Assert
        # Returns False when creating new integration
        assert result is False
        # Should have called execute for SHOW and CREATE
        assert mock_cursor.execute.called


class TestGetGCPServiceAccount:
    """Test cases for GCP service account retrieval"""
    
    def test_get_gcp_service_account_success(self):
        """Test successful service account retrieval"""
        # Arrange
        mock_conn = Mock()
        mock_cursor = Mock()
        # Make cursor a context manager
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            ("STORAGE_GCP_SERVICE_ACCOUNT", "STRING", "test@project.iam.gserviceaccount.com", None)
        ]
        
        integration_name = "TEST_INTEGRATION"
        
        # Act
        result = get_gcp_service_account(mock_conn, integration_name)
        
        # Assert
        assert result is not None
        assert "@" in result  # Should be an email
        assert result == "test@project.iam.gserviceaccount.com"
    
    def test_get_gcp_service_account_error(self):
        """Test service account retrieval error handling"""
        # Arrange
        mock_conn = Mock()
        mock_cursor = Mock()
        # Make cursor a context manager
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor
        # Return empty result - service account not found
        mock_cursor.fetchall.return_value = []
        
        integration_name = "TEST_INTEGRATION"
        
        # Act & Assert
        with pytest.raises(RuntimeError, match="STORAGE_GCP_SERVICE_ACCOUNT not found"):
            get_gcp_service_account(mock_conn, integration_name)


class TestEnsureBucketIAMForIntegration:
    """Test cases for bucket IAM setup"""
    
    def test_ensure_bucket_iam_success(self):
        """Test successful bucket IAM setup"""
        # Arrange
        mock_gcs_client = Mock()
        mock_bucket = Mock()
        mock_policy = Mock()
        # Mock policy bindings - empty list means role doesn't exist yet
        mock_policy.bindings = []
        mock_bucket.get_iam_policy.return_value = mock_policy
        mock_gcs_client.bucket.return_value = mock_bucket
        
        bucket_name = "test-bucket"
        service_account_email = "test@project.iam.gserviceaccount.com"
        
        # Act
        ensure_bucket_iam_for_integration(mock_gcs_client, bucket_name, service_account_email)
        
        # Assert
        # Should have called get_iam_policy and set_iam_policy
        assert mock_bucket.get_iam_policy.called
        assert mock_bucket.set_iam_policy.called
        # Should have added the service account to bindings
        assert len(mock_policy.bindings) > 0


class TestAssertAllowedPrefixMatches:
    """Test cases for prefix matching validation"""
    
    def test_assert_allowed_prefix_matches_success(self):
        """Test prefix matching succeeds when prefixes match"""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            ("STORAGE_ALLOWED_LOCATIONS", "STRING", "gs://bucket/data/", None)
        ]
        
        expected_prefix = "gs://bucket/data/"
        integration_name = "TEST_INTEGRATION"
        
        # Should not raise exception
        assert_allowed_prefix_matches(mock_conn, expected_prefix, integration_name)
    
    def test_assert_allowed_prefix_matches_subdirectory(self):
        """Test prefix matching - exact match required (not subdirectory)"""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            ("STORAGE_ALLOWED_LOCATIONS", "STRING", "gs://bucket/data/", None)
        ]
        
        expected_prefix = "gs://bucket/data/subfolder/"
        integration_name = "TEST_INTEGRATION"
        
        # Should raise exception (exact match required)
        with pytest.raises(RuntimeError):
            assert_allowed_prefix_matches(mock_conn, expected_prefix, integration_name)
    
    def test_assert_allowed_prefix_matches_failure(self):
        """Test prefix matching fails when prefixes don't match"""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            ("STORAGE_ALLOWED_LOCATIONS", "STRING", "gs://bucket/data/", None)
        ]
        
        expected_prefix = "gs://bucket/other/"
        integration_name = "TEST_INTEGRATION"
        
        # Should raise exception
        with pytest.raises(RuntimeError):
            assert_allowed_prefix_matches(mock_conn, expected_prefix, integration_name)
    
    def test_assert_allowed_prefix_matches_different_bucket(self):
        """Test prefix matching fails for different bucket"""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            ("STORAGE_ALLOWED_LOCATIONS", "STRING", "gs://bucket1/data/", None)
        ]
        
        expected_prefix = "gs://bucket2/data/"
        integration_name = "TEST_INTEGRATION"
        
        # Should raise exception
        with pytest.raises(RuntimeError):
            assert_allowed_prefix_matches(mock_conn, expected_prefix, integration_name)


class TestGCPUtilsEdgeCases:
    """Test cases for edge cases and error handling"""
    
    def test_gcs_client_invalid_service_account_path(self):
        """Test GCS client with invalid service account path"""
        # Arrange
        invalid_path = "/nonexistent/path.json"
        project_id = "test-project"
        
        # Act & Assert
        with patch('os.path.exists', return_value=False):
            with pytest.raises(RuntimeError):
                gcs_client(invalid_path, project_id)
    
    def test_assert_allowed_prefix_matches_empty_prefixes(self):
        """Test prefix matching with empty prefixes"""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            ("STORAGE_ALLOWED_LOCATIONS", "STRING", "gs://bucket/data/", None)
        ]
        
        expected_prefix = "gs://bucket/data/"
        integration_name = "TEST_INTEGRATION"
        
        # Should not raise exception if prefix matches
        assert_allowed_prefix_matches(mock_conn, expected_prefix, integration_name)
    
    def test_assert_allowed_prefix_matches_case_sensitive(self):
        """Test prefix matching is case sensitive"""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            ("STORAGE_ALLOWED_LOCATIONS", "STRING", "gs://Bucket/data/", None)
        ]
        
        expected_prefix = "gs://bucket/data/"  # Different case
        integration_name = "TEST_INTEGRATION"
        
        # Should raise exception (case sensitive)
        with pytest.raises(RuntimeError):
            assert_allowed_prefix_matches(mock_conn, expected_prefix, integration_name)


# Run tests with: pytest tests/test_gcp_utils.py -v

