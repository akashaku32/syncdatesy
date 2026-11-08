"""
Table Utility Functions for Snowflake Operations

This module provides utility functions for Snowflake table operations,
particularly for dropping tables during full reload operations.

Functions:
    - drop_snowflake_table: Drop a table from Snowflake using existing engine
    - normalize_table_name: Normalize table names to Snowflake conventions
"""

import logging
import re
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


def drop_snowflake_table(destination_engine, table_name, schema, connection_id=None, cmp_id=None):
    """
    Drop a table from Snowflake destination database using existing engine.
    
    This function explicitly drops the destination table to ensure a clean slate
    before reloading data. Uses DROP TABLE IF EXISTS to prevent errors if table
    doesn't exist. Follows the same pattern as existing staging table drops.
    
    Args:
        destination_engine: SQLAlchemy engine connected to Snowflake
        table_name (str): Name of the table to drop (e.g., 'Account', 'AccountHistory')
        schema (str): Snowflake schema name (e.g., 'PUBLIC')
        connection_id (str, optional): Connection ID for logging purposes
        cmp_id (str, optional): Company ID for error logging
    
    Returns:
        dict: Result dictionary containing:
            - success (bool): Whether the drop operation succeeded
            - message (str): Success or error message
            - table_name (str): Name of the dropped table
            - qualified_name (str): Fully qualified table name (if success)
    
    Example:
        result = drop_snowflake_table(
            destination_engine=engine,
            table_name='Account',
            schema='PUBLIC',
            connection_id='conn_123',
            cmp_id='cmp_456'
        )
        
        if result['success']:
            print(f"✅ {result['message']}")
        else:
            print(f"⚠️  {result['message']}")
    
    Notes:
        - Uses DROP TABLE IF EXISTS - no error if table doesn't exist
        - Normalizes table names: "AccountHistory" → "ACCOUNT_HISTORY"
        - Fully qualifies table name with schema: "PUBLIC"."ACCOUNT"
        - Follows same pattern as staging table drops in snowflake_migration
        - Uses existing engine connection (no new connection created)
        - Thread-safe, can be called from background threads
    
    Workflow:
        1. Normalize table name to Snowflake conventions (UPPER_SNAKE_CASE)
        2. Build fully qualified table name with schema
        3. Construct DROP TABLE IF EXISTS statement
        4. Execute drop using existing engine connection
        5. Return success/failure result
    """
    print("=" * 80)
    print("🗑️  DROP SNOWFLAKE TABLE UTILITY CALLED")
    print(f"   Table: {table_name}")
    print(f"   Schema: {schema}")
    print(f"   Connection ID: {connection_id}")
    print("=" * 80)
    
    try:
        print("🔵 Dropping table from Snowflake using existing engine...")
        
        # Validate required parameters
        if not destination_engine:
            error_msg = "Missing required parameter: destination_engine"
            logger.error(error_msg)
            print(f"❌ {error_msg}")
            return {
                'success': False,
                'message': error_msg,
                'table_name': table_name
            }
        
        if not schema:
            error_msg = "Missing required parameter: schema"
            logger.error(error_msg)
            print(f"❌ {error_msg}")
            return {
                'success': False,
                'message': error_msg,
                'table_name': table_name
            }
        
        # Normalize table name to Snowflake conventions
        normalized_table = normalize_table_name(table_name)
        
        print(f"📝 Table name normalization:")
        print(f"   Original: {table_name}")
        print(f"   Normalized: {normalized_table}")
        
        # Build fully qualified table name with schema
        # Same pattern as staging table drops: schema.table_name
        qualified_table = f'{schema}."{normalized_table}"'
        
        # Construct DROP TABLE statement (exact same pattern as staging drops)
        drop_sql = f'DROP TABLE IF EXISTS {qualified_table}'
        
        print(f"🗑️  Executing DROP statement:")
        print(f"   SQL: {drop_sql}")
        
        # Execute DROP TABLE using existing engine (same pattern as staging drops)
        with destination_engine.connect() as connection:
            connection.execute(text(drop_sql))
            print(f"✅ DROP TABLE executed successfully")
        
        success_msg = f"Table {qualified_table} dropped successfully from Snowflake"
        logger.info(success_msg)
        print(f"✅ {success_msg}")
        print("=" * 80)
        
        return {
            'success': True,
            'message': success_msg,
            'table_name': table_name,
            'qualified_name': qualified_table
        }
    
    except SQLAlchemyError as e:
        error_msg = f"SQLAlchemy error dropping Snowflake table {table_name}: {str(e)}"
        logger.error(error_msg)
        print(f"❌ {error_msg}")
        print("=" * 80)
        
        # Log error to database if cmp_id and connection_id provided
        if cmp_id and connection_id:
            try:
                from utils.error_utils import insert_error_log
                insert_error_log(cmp_id, connection_id, error_msg, "drop_snowflake_table")
            except Exception as log_error:
                print(f"⚠️  Could not log error to database: {log_error}")
        
        return {
            'success': False,
            'message': error_msg,
            'table_name': table_name
        }
    
    except Exception as e:
        error_msg = f"Unexpected error dropping Snowflake table {table_name}: {str(e)}"
        logger.error(error_msg)
        print(f"❌ {error_msg}")
        print("=" * 80)
        
        # Log error to database if cmp_id and connection_id provided
        if cmp_id and connection_id:
            try:
                from utils.error_utils import insert_error_log
                insert_error_log(cmp_id, connection_id, error_msg, "drop_snowflake_table")
            except Exception as log_error:
                print(f"⚠️  Could not log error to database: {log_error}")
        
        return {
            'success': False,
            'message': error_msg,
            'table_name': table_name
        }


def normalize_table_name(table_name):
    """
    Normalize table names for Snowflake, aligned with Salesforce naming patterns used in reloads.

    Behavior (matches salesforce_service.normalize_name):
    - Replace Salesforce "__c" suffix with "_C"
    - Collapse multiple underscores to a single underscore
    - Insert underscore before uppercase letters preceded by lowercase (camelCase → camel_Case)
    - Insert underscore before digits if preceded by a letter (Field2 → Field_2)
    - Insert underscore after digits if followed by a letter (2Field → 2_Field)
    - Uppercase final result

    This is backward-compatible for non-Salesforce names and also normalizes CamelCase.

    Args:
        table_name (str): Original table name (e.g., "AccountHistory", "MyCustomObject__c")

    Returns:
        str: Normalized Snowflake table name (e.g., "ACCOUNT_HISTORY", "MY_CUSTOM_OBJECT_C")
    """
    if table_name is None:
        return None

    name = str(table_name)

    # Replace Salesforce-style __c with _C first
    name = name.replace("__c", "_C")

    # Remove wrapping quotes/backticks/brackets often seen in metadata
    name = re.sub(r'[`"\[\]\(\)]', '', name)

    # Replace common separators with underscore (space, hyphen, dot, slash)
    name = re.sub(r'[\s\-\./]+', '_', name)

    # Insert underscore before uppercase letters preceded by lowercase letters
    name = re.sub(r'(?<=[a-z])([A-Z])', r'_\1', name)

    # Insert underscore before digits (e.g., Field2 → Field_2)
    name = re.sub(r'(?<=[A-Za-z])(\d)', r'_\1', name)

    # Insert underscore after digits if followed by a letter (e.g., 2Field → 2_Field)
    name = re.sub(r'(\d)(?=[A-Za-z])', r'\1_', name)

    # Remove any remaining invalid characters (keep alphanumerics and underscore)
    name = re.sub(r'[^A-Za-z0-9_]', '', name)

    # Normalize duplicate underscores and trim edge underscores
    name = re.sub(r'_+', '_', name).strip('_')

    return name.upper()

