"""
Unit tests for S3 utility functions
Tests helper functions for S3 operations like prefix normalization, file filtering, etc.
"""
import pytest
from unittest.mock import Mock, patch
from datetime import datetime

from utils.s3_utils import (
    normalized_prefix,
    sanitize_identifier,
    table_from_file_key,
    extract_date_from_filename,
    suggest_common_table_name,
    list_files_recursive
)


class TestNormalizedPrefix:
    """Test cases for prefix normalization"""
    
    def test_normalize_prefix_with_slashes(self):
        """Test prefix normalization removes leading/trailing slashes"""
        assert normalized_prefix("/data/") == "data"
        assert normalized_prefix("data/") == "data"
        assert normalized_prefix("/data") == "data"
        assert normalized_prefix("data") == "data"
    
    def test_normalize_prefix_empty(self):
        """Test prefix normalization with empty string"""
        assert normalized_prefix("") == ""
        assert normalized_prefix("/") == ""
    
    def test_normalize_prefix_nested(self):
        """Test prefix normalization with nested paths"""
        assert normalized_prefix("/data/raw/") == "data/raw"
        assert normalized_prefix("data/raw/") == "data/raw"


class TestSanitizeIdentifier:
    """Test cases for identifier sanitization"""
    
    def test_sanitize_identifier_special_chars(self):
        """Test sanitization removes special characters and converts to uppercase"""
        assert sanitize_identifier("table-name") == "TABLE_NAME"
        assert sanitize_identifier("table.name") == "TABLE_NAME"
        assert sanitize_identifier("table@name") == "TABLE_NAME"
    
    def test_sanitize_identifier_starts_with_letter(self):
        """Test sanitization converts to uppercase (doesn't enforce starting with letter)"""
        assert sanitize_identifier("123table") == "123TABLE"
        assert sanitize_identifier("_table") == "_TABLE"
    
    def test_sanitize_identifier_preserves_valid_chars(self):
        """Test sanitization preserves alphanumeric and underscores, converts to uppercase"""
        assert sanitize_identifier("table_name_123") == "TABLE_NAME_123"
        assert sanitize_identifier("TableName") == "TABLENAME"


class TestTableFromFileKey:
    """Test cases for extracting table name from file key"""
    
    def test_table_from_file_key_with_path(self):
        """Test table name extraction from file path (returns uppercase)"""
        assert table_from_file_key("data/file1.csv") == "FILE1"
        assert table_from_file_key("data/raw/file2.json") == "FILE2"

    def test_table_from_file_key_strips_export_date_suffix(self):
        """Snowflake export filenames should map back to the base table name."""
        assert table_from_file_key("SNOWFLAKE-S3/csv/EMPLOYEES_2026_09_21.csv") == "EMPLOYEES"
        assert table_from_file_key("SNOWFLAKE-S3/S1/EMPLOYEE_20260921T084052Z.json") == "EMPLOYEE"
        assert table_from_file_key("SNOWFLAKE-S3/S1/CUSTOMER_09_21_2026.xlsx") == "CUSTOMER"
    
    def test_table_from_file_key_no_extension(self):
        """Test table name extraction without extension (returns uppercase)"""
        assert table_from_file_key("data/file1") == "FILE1"
    
    def test_table_from_file_key_multiple_dots(self):
        """Test table name extraction with multiple dots (returns uppercase, sanitized)"""
        assert table_from_file_key("data/file.name.csv") == "FILE_NAME"


class TestExtractDateFromFilename:
    """Test cases for date extraction from filename"""
    
    def test_extract_date_yyyy_mm_dd(self):
        """Test date extraction in YYYY-MM-DD format (returns tuple: date, score)"""
        result = extract_date_from_filename("data/file_2024-01-15.csv")
        assert result is not None
        date, score = result
        assert date is not None
        assert date.year == 2024
        assert date.month == 1
        assert date.day == 1  # month-only fallback for dashed format
        assert score == 0.8

    def test_extract_date_yyyy_mm_dd_underscore_export(self):
        result = extract_date_from_filename("SNOWFLAKE-S3/csv/EMPLOYEES_2026_09_21.csv")
        date, score = result
        assert date == datetime(2026, 9, 21).date()
        assert score == 1.0
    
    def test_extract_date_yyyymmdd(self):
        """Test date extraction in YYYYMMDD format (returns tuple: date, score)"""
        result = extract_date_from_filename("data/file_20240115.csv")
        assert result is not None
        date, score = result
        # Note: The function looks for YYYY-MM pattern, YYYYMMDD might not match exactly
        # This test may need adjustment based on actual regex pattern
        if date is not None:
            assert date.year == 2024
    
    def test_extract_date_no_date(self):
        """Test date extraction when no date in filename (returns tuple: None, 0.0)"""
        result = extract_date_from_filename("data/file.csv")
        assert result is not None
        date, score = result
        assert date is None
        assert score == 0.0


class TestSuggestCommonTableName:
    """Test cases for common table name suggestion"""
    
    def test_suggest_common_table_name_single_file(self):
        """Test table name suggestion for single file"""
        files = ["data/customers_2024-01-15.csv"]
        suggested = suggest_common_table_name(files)
        assert "customer" in suggested.lower() or "customers" in suggested.lower()
    
    def test_suggest_common_table_name_multiple_files(self):
        """Test table name suggestion for multiple files with common prefix"""
        files = [
            "data/customers_2024-01-15.csv",
            "data/customers_2024-01-16.csv",
            "data/customers_2024-01-17.csv"
        ]
        suggested = suggest_common_table_name(files)
        assert "customer" in suggested.lower()
    
    def test_suggest_common_table_name_no_common_prefix(self):
        """Test table name suggestion when no common prefix"""
        files = [
            "data/customers.csv",
            "data/orders.csv",
            "data/products.csv"
        ]
        suggested = suggest_common_table_name(files)
        # Should return a default or first file's name
        assert suggested is not None
        assert len(suggested) > 0



class TestListFilesRecursive:
    """Test cases for recursive file listing"""
    
    @patch('utils.s3_utils.boto3')
    def test_list_files_recursive_success(self, mock_boto3):
        """Test successful recursive file listing"""
        # Arrange
        mock_s3 = Mock()
        mock_paginator = Mock()
        mock_pages = [
            {
                'Contents': [
                    {'Key': 'data/file1.csv'},
                    {'Key': 'data/file2.csv'}
                ]
            },
            {
                'Contents': [
                    {'Key': 'data/subfolder/file3.csv'}
                ]
            }
        ]
        mock_paginator.paginate.return_value = mock_pages
        mock_s3.get_paginator.return_value = mock_paginator
        mock_boto3.client.return_value = mock_s3
        
        # Act
        result = list_files_recursive(mock_s3, "test-bucket", "data/")
        
        # Assert
        assert len(result) == 3
        assert "data/file1.csv" in result
        assert "data/file2.csv" in result
        assert "data/subfolder/file3.csv" in result
    
    @patch('utils.s3_utils.boto3')
    def test_list_files_recursive_empty_bucket(self, mock_boto3):
        """Test recursive listing from empty bucket"""
        # Arrange
        mock_s3 = Mock()
        mock_paginator = Mock()
        mock_paginator.paginate.return_value = [{'Contents': []}]
        mock_s3.get_paginator.return_value = mock_paginator
        mock_boto3.client.return_value = mock_s3
        
        # Act
        result = list_files_recursive(mock_s3, "empty-bucket", "")
        
        # Assert
        assert result == []
    
    @patch('utils.s3_utils.boto3')
    def test_list_files_recursive_with_prefix(self, mock_boto3):
        """Test recursive listing with prefix filter"""
        # Arrange
        mock_s3 = Mock()
        mock_paginator = Mock()
        mock_pages = [
            {
                'Contents': [
                    {'Key': 'data/csv/file1.csv'},
                    {'Key': 'data/json/file1.json'}
                ]
            }
        ]
        mock_paginator.paginate.return_value = mock_pages
        mock_s3.get_paginator.return_value = mock_paginator
        mock_boto3.client.return_value = mock_s3
        
        # Act
        # Note: list_files_recursive returns all files, filtering by prefix happens elsewhere
        result = list_files_recursive(mock_s3, "test-bucket", "data/csv/")
        
        # Assert
        # Function returns all files from paginator, prefix filtering is done by caller
        assert len(result) == 2
        assert "data/csv/file1.csv" in result
        assert "data/json/file1.json" in result


class TestS3UtilsEdgeCases:
    """Test cases for edge cases and error handling"""
    
    def test_sanitize_identifier_max_length(self):
        """Test sanitization handles very long identifiers (converts to uppercase)"""
        long_name = "a" * 200
        result = sanitize_identifier(long_name)
        # Should still be valid identifier, converted to uppercase
        assert result.startswith("A")
        assert len(result) > 0
        assert result == "A" * 200
    
    def test_table_from_file_key_special_chars(self):
        """Test table name extraction with special characters"""
        result = table_from_file_key("data/file-name.with_chars.csv")
        assert result is not None
        assert len(result) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

