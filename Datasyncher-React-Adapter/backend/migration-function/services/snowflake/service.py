import logging
import snowflake.connector
from snowflake.connector.errors import DatabaseError, ProgrammingError, OperationalError
from fastapi import HTTPException
import os
import psycopg2  
from datetime import datetime
import json

def postgres_connection():
    from datasyncher_connections.postgres import create_postgres_connection

    connection = create_postgres_connection()
    print("connection", connection)
    return connection

import snowflake.connector
from snowflake.connector.errors import DatabaseError, ProgrammingError, OperationalError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
import re

logger = logging.getLogger(__name__)


class SnowflakeService:
    def __init__(
        self,
        snowflake_details,
        destination_schema,
        validate_destination_schema=True,
        source_schema=None,
        validate_source_schema=False,
    ):
        if snowflake_details is None:
            raise ValueError("snowflake_details cannot be None")
        if not isinstance(snowflake_details, dict):
            raise ValueError("snowflake_details must be a dictionary")
        
        # Normalize field names for backward compatibility
        self.snowflake_details = self._normalize_snowflake_details(snowflake_details)
        
        # Sanitize configuration based on authentication type to prevent conflicts
        self.snowflake_details = self._sanitize_config_for_auth_type(self.snowflake_details)
        
        self.destination_schema = destination_schema
        self.validate_destination_schema = bool(validate_destination_schema)
        self.source_schema = source_schema
        self.validate_source_schema = bool(validate_source_schema)
        
        # Mask sensitive credentials in logs
        masked_details = dict(self.snowflake_details) if isinstance(self.snowflake_details, dict) else {}
        for key in ["password", "private_key", "passphrase", "aws_secret_access_key", "secret"]:
            if key in masked_details:
                masked_details[key] = "***MASKED***"
        print("inside snowflake service", masked_details)

    def _normalize_case_if_simple_identifier(self, value):
        """
        Normalize identifier casing to Snowflake's default behavior.

        Snowflake uppercases unquoted identifiers. In this codebase we frequently quote
        identifiers (via `_q()`), which makes them case-sensitive. If a user types
        `mydb` but the database was created unquoted (stored as `MYDB`), quoting
        `"mydb"` will fail with "database not found".

        For simple identifiers (letters/numbers/underscore), uppercase before quoting.
        """
        if value is None:
            return None
        raw = str(value).strip()
        if not raw:
            return None
        if re.match(r"^[A-Za-z0-9_]+$", raw):
            return raw.upper()
        return raw

    def _normalize_identifier(self, value):
        """Normalize Snowflake identifier to safe uppercase form."""
        if value is None:
            return None
        raw = str(value).strip()
        if not raw:
            return None
        normalized = re.sub(r"[^A-Za-z0-9_]", "_", raw).upper()
        # Keep identifier compatible with app-level rule: cannot start with number.
        if normalized and normalized[0].isdigit():
            normalized = "_" + normalized
        return normalized

    def _q(self, identifier):
        """Quote Snowflake identifier safely."""
        return '"' + str(identifier).replace('"', '""') + '"'
    
    def _normalize_snowflake_details(self, details):
        """Normalize field names to ensure compatibility"""
        normalized = details.copy()
        
        # Map 'username' to 'user' if 'user' doesn't exist
        if 'username' in normalized and 'user' not in normalized:
            normalized['user'] = normalized['username']
        elif 'user' in normalized and 'username' not in normalized:
            normalized['username'] = normalized['user']
        
        return normalized
    
    def _sanitize_config_for_auth_type(self, config):
        """Remove irrelevant authentication fields based on authentication_type.
        
        This prevents conflicts when both password and key-pair fields are present
        but only one authentication method should be used.
        """
        if not isinstance(config, dict):
            return config
            
        sanitized = config.copy()
        auth_type = config.get('authentication_type', 'password').lower()
        
        if auth_type == 'password':
            # Remove key-pair related fields when using password auth
            sanitized.pop('private_key', None)
            sanitized.pop('public_key', None)
            sanitized.pop('passphrase', None)
            print(f"[AUTH_SANITIZE] Using password auth, removed key-pair fields")
        elif auth_type == 'key_pair':
            # Remove password field when using key-pair auth
            sanitized.pop('password', None)
            print(f"[AUTH_SANITIZE] Using key-pair auth, removed password field")
        else:
            print(f"[AUTH_SANITIZE] Warning: Unknown authentication type '{auth_type}', not sanitizing")
        
        return sanitized
    
    def _load_private_key_from_string(self, private_key_str, private_key_passphrase=None):
        """Load private key for key-pair authentication from string.
        
        Args:
            private_key_str: PEM-formatted private key string
            private_key_passphrase: Optional passphrase for encrypted keys. 
                                    If None or empty, assumes unencrypted key.
        
        Returns:
            Loaded private key object
            
        Raises:
            ValueError: If key cannot be loaded or passphrase is incorrect
        """
        # Basic validation
        if not private_key_str:
            raise ValueError("Private key string cannot be empty")
        
        if not isinstance(private_key_str, str):
            raise ValueError(f"Private key must be a string, got {type(private_key_str)}")
        
        # Check for basic PEM structure
        key_upper = private_key_str.upper()
        if "-----BEGIN" not in key_upper or "-----END" not in key_upper:
            raise ValueError(
                "Error: Private key must be in PEM format with -----BEGIN and -----END headers."
            )
        
        # ============================================================
        # COMPREHENSIVE DEBUG: KEY LOADING
        # ============================================================
        print("[KEY-PAIR DEBUG] _load_private_key_from_string() called")
        print(f"  - Key length: {len(private_key_str)}")
        print(f"  - Passphrase provided: {private_key_passphrase is not None}")
        if private_key_passphrase:
            passphrase_str = str(private_key_passphrase)
            print(f"  - Passphrase type: {type(private_key_passphrase)}")
            print(f"  - Passphrase length: {len(passphrase_str)}")
            print(f"  - Passphrase is empty: {passphrase_str.strip() == ''}")
        else:
            print(f"  - âš ï¸ Passphrase is None")
        
        # Detect key format
        is_pkcs1 = "BEGIN RSA PRIVATE KEY" in key_upper
        is_encrypted = "BEGIN ENCRYPTED PRIVATE KEY" in key_upper
        is_pkcs8 = "BEGIN PRIVATE KEY" in key_upper
        print(f"  - Key format: PKCS#1={is_pkcs1}, PKCS#8={is_pkcs8}, Encrypted={is_encrypted}")
        
        password_bytes = None
        
        # Only use passphrase if it's provided and not empty
        if private_key_passphrase and private_key_passphrase.strip():
            password_bytes = private_key_passphrase.encode("utf-8")
            print(f"[KEY-PAIR DEBUG] Attempting to load encrypted private key with passphrase (length: {len(password_bytes)})")
        else:
            print(f"[KEY-PAIR DEBUG] Attempting to load unencrypted private key (no passphrase)")
        
        try:
            # Try loading the key
            p_key = serialization.load_pem_private_key(
                private_key_str.encode(),
                password=password_bytes,
                backend=default_backend()
            )
            print(f"[KEY-PAIR DEBUG] âœ… Private key loaded successfully")
            return p_key
            
        except ValueError as e:
            error_msg = str(e)
            print(f"[KEY-PAIR DEBUG] âŒ Key loading failed: {error_msg}")
            print(f"[KEY-PAIR DEBUG] Error details: {type(e).__name__}: {repr(e)}")
            
            # If we tried without passphrase and it failed, try with passphrase if provided
            if not password_bytes and private_key_passphrase and private_key_passphrase.strip():
                print(f"[KEY-PAIR DEBUG] First attempt failed (unencrypted), retrying with passphrase...")
                try:
                    password_bytes = private_key_passphrase.encode("utf-8")
                    p_key = serialization.load_pem_private_key(
                        private_key_str.encode(),
                        password=password_bytes,
                        backend=default_backend()
                    )
                    print(f"[KEY-PAIR DEBUG] âœ… Private key loaded successfully with passphrase on retry")
                    return p_key
                except ValueError as retry_err:
                    print(f"[KEY-PAIR DEBUG] âŒ Retry with passphrase also failed: {retry_err}")
                    print(f"[KEY-PAIR DEBUG] Retry error details: {type(retry_err).__name__}: {repr(retry_err)}")
                    raise ValueError(
                        "Error: Failed to load private key. Please verify the passphrase is correct and the key format is valid PEM."
                    ) from retry_err
            
            # If we tried with passphrase and it failed, try without (in case key is actually unencrypted)
            elif password_bytes:
                print(f"[KEY-PAIR DEBUG] Failed with passphrase, retrying without passphrase...")
                try:
                    p_key = serialization.load_pem_private_key(
                        private_key_str.encode(),
                        password=None,
                        backend=default_backend()
                    )
                    # âœ… ERROR: Passphrase was provided but key is not encrypted
                    print(f"[KEY-PAIR DEBUG] âŒ Key loaded without passphrase - key is not encrypted but passphrase was provided")
                    raise ValueError(
                        "Error: Unexpected error validating the private key. A passphrase was provided, but the private key is not encrypted for the given passphrase."
                    )
                except (ValueError, TypeError) as no_pass_err:
                    print(f"[KEY-PAIR DEBUG] âŒ Retry without passphrase also failed: {no_pass_err}")
                    print(f"[KEY-PAIR DEBUG] Retry error details: {type(no_pass_err).__name__}: {repr(no_pass_err)}")
                    # Both attempts failed - provide concise error
                    raise ValueError(
                        "Error: Failed to load private key. The key may be encrypted with a different passphrase, corrupted, or in an unsupported format."
                    ) from e
            
            # If no passphrase was provided and loading failed, suggest it might be encrypted
            raise ValueError(
                "Error: Failed to load private key. If the key is encrypted, provide the correct passphrase."
            ) from e
            
        except Exception as e:
            print(f"[KEY-PAIR DEBUG] âŒ Unexpected error type: {type(e).__name__}: {e}")
            print(f"[KEY-PAIR DEBUG] Full exception: {repr(e)}")
            
            # Check for specific library error messages and provide user-friendly messages
            error_str = str(e).lower()
            if "password was not given" in error_str and "encrypted" in error_str:
                raise ValueError(
                    "Error: Private key is encrypted but no passphrase was provided. Please provide the passphrase."
                ) from e
            elif "could not deserialize" in error_str or "unsupported" in error_str:
                raise ValueError(
                    "Error: Invalid private key format. The key may be corrupted, in an unsupported format, or encrypted with an unsupported algorithm."
                ) from e
            elif "password was given but private key is not encrypted" in error_str:
                raise ValueError(
                    "Error: Unexpected error validating the private key. A passphrase was provided, but the private key is not encrypted for the given passphrase."
                ) from e
            else:
                # Extract first sentence of error for concise message
                error_first_part = str(e).split('.')[0] if '.' in str(e) else str(e)
                raise ValueError(
                    f"Error: Unexpected error validating the private key: {error_first_part}"
                ) from e

    def _extract_public_key_from_private(self, private_key):
        """
        Extract public key in PEM format from a private key object.
        
        Args:
            private_key: Loaded private key object from cryptography library
            
        Returns:
            Public key string in PEM format
        """
        try:
            print("[PUBLIC_KEY_VALIDATION] Extracting public key from private key...")
            public_key = private_key.public_key()
            public_key_pem = public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            ).decode('utf-8')
            normalized_public_key = normalize_pem_key(public_key_pem)
            print("[PUBLIC_KEY_VALIDATION] âœ… Successfully extracted public key from private key")
            print(f"[PUBLIC_KEY_VALIDATION] Extracted public key length: {len(normalized_public_key)} characters")
            return normalized_public_key
        except Exception as e:
            print(f"[PUBLIC_KEY_VALIDATION] âŒ Failed to extract public key from private key: {str(e)}")
            raise ValueError(f"Failed to extract public key from private key: {str(e)}")

    def _normalize_public_key_for_comparison(self, public_key_str):
        """
        Normalize public key string for comparison by removing whitespace and normalizing format.
        
        Args:
            public_key_str: Public key string in PEM format
            
        Returns:
            Normalized public key string
        """
        if not public_key_str:
            print("[PUBLIC_KEY_VALIDATION] âš ï¸ Empty public key string provided for normalization")
            return ""
        
        print("[PUBLIC_KEY_VALIDATION] Normalizing public key for comparison...")
        # Normalize the key format
        normalized = normalize_pem_key(str(public_key_str))
        
        # Remove all whitespace for comparison (keys should match regardless of whitespace)
        normalized_no_whitespace = ''.join(normalized.split())
        print(f"[PUBLIC_KEY_VALIDATION] Normalized key length (no whitespace): {len(normalized_no_whitespace)} characters")
        return normalized_no_whitespace

    def _validate_public_key_match(self, extracted_public_key, stored_public_key):
        """
        Validate that the extracted public key matches the stored public key.
        
        Args:
            extracted_public_key: Public key extracted from private key (PEM format)
            stored_public_key: Public key stored in config (PEM format)
            
        Returns:
            bool: True if keys match, False otherwise
        """
        if not stored_public_key:
            print("[PUBLIC_KEY_VALIDATION] âš ï¸ No stored public key provided for validation")
            return False
        
        print("[PUBLIC_KEY_VALIDATION] Comparing extracted public key with stored public key...")
        # Normalize both keys for comparison
        extracted_normalized = self._normalize_public_key_for_comparison(extracted_public_key)
        stored_normalized = self._normalize_public_key_for_comparison(stored_public_key)
        
        keys_match = extracted_normalized == stored_normalized
        
        if keys_match:
            print("[PUBLIC_KEY_VALIDATION] âœ… Public keys match!")
        else:
            print("[PUBLIC_KEY_VALIDATION] âŒ Public keys do NOT match!")
            print(f"[PUBLIC_KEY_VALIDATION] Extracted key (first 100 chars): {extracted_normalized[:100]}...")
            print(f"[PUBLIC_KEY_VALIDATION] Stored key (first 100 chars): {stored_normalized[:100]}...")
        
        return keys_match

    def _get_snowflake_public_keys(self, cursor, username):
        """
        Query Snowflake to get all registered public keys for a user.
        
        Args:
            cursor: Snowflake cursor object
            username: Snowflake username
            
        Returns:
            List of public key information from Snowflake
        """
        try:
            print(f"[PUBLIC_KEY_VALIDATION] Querying Snowflake for public keys for user: {username}")
            # Query to get user's public key information
            # Note: Snowflake stores public key fingerprints, not full keys
            # We'll use DESCRIBE USER to get key information
            query = f"DESC USER {username}"
            print(f"[PUBLIC_KEY_VALIDATION] Executing query: {query}")
            cursor.execute(query)
            result = cursor.fetchall()
            print(f"[PUBLIC_KEY_VALIDATION] Query result rows: {len(result)}")
            
            # Extract public key information from the result
            public_keys_info = []
            for row in result:
                row_str = str(row)
                if 'RSA_PUBLIC_KEY' in row_str or 'PUBLIC_KEY' in row_str:
                    public_keys_info.append(row)
                    print(f"[PUBLIC_KEY_VALIDATION] Found public key info: {row}")
            
            if public_keys_info:
                print(f"[PUBLIC_KEY_VALIDATION] âœ… Found {len(public_keys_info)} public key(s) in Snowflake")
            else:
                print("[PUBLIC_KEY_VALIDATION] âš ï¸ No public key information found in DESCRIBE USER result")
            
            return public_keys_info
        except Exception as e:
            print(f"[PUBLIC_KEY_VALIDATION] âš ï¸ Could not query Snowflake for public keys: {str(e)}")
            return []

    def create_connection(self):
        """Create and return a snowflake.connector connection handling password or key pair."""
        snowflake_username = self.snowflake_details.get('user') or self.snowflake_details.get('username')
        snowflake_account = self.snowflake_details.get('account')
        snowflake_database = self.snowflake_details.get('database')
        snowflake_warehouse = self.snowflake_details.get('warehouse')
        authentication_type = self.snowflake_details.get('authentication_type', 'password').lower()
        logger.info(
            "Starting Snowflake connection setup | account=%s database=%s warehouse=%s user=%s auth_type=%s",
            snowflake_account,
            snowflake_database,
            snowflake_warehouse,
            snowflake_username,
            authentication_type,
        )
        print(
            f"Starting Snowflake connection setup | account={snowflake_account} database={snowflake_database} "
            f"warehouse={snowflake_warehouse} user={snowflake_username} auth_type={authentication_type}"
        )

        try:
            if authentication_type == "password":
                snowflake_password = self.snowflake_details.get('password')
                logger.info("Preparing password-based Snowflake connection parameters")
                print("Preparing password-based Snowflake connection parameters")
                conn_params = {
                    "user": snowflake_username,
                    "password": snowflake_password,
                    "account": snowflake_account,
                    "warehouse": snowflake_warehouse
                }
            elif authentication_type == "key_pair":
                private_key_str = self.snowflake_details.get('private_key')
                private_key_passphrase = self.snowflake_details.get('passphrase')

                if not private_key_str:
                    raise Exception("Private key string is required for key-pair authentication.")
                logger.info("Preparing key-pair Snowflake connection parameters")
                print("Preparing key-pair Snowflake connection parameters")
                logger.info(
                    "Key-pair auth passphrase state | exists=%s type=%s",
                    private_key_passphrase is not None,
                    type(private_key_passphrase),
                )
                if private_key_passphrase:
                    passphrase_str = str(private_key_passphrase)
                    logger.info(
                        "Key-pair passphrase details | length=%s is_empty=%s masked_prefix=%s",
                        len(passphrase_str),
                        passphrase_str.strip() == "",
                        f"{passphrase_str[:5]}...",
                    )
                else:
                    logger.info("Key-pair passphrase is missing or empty")

                key_str_check = str(private_key_str)
                has_newlines = '\n' in key_str_check
                has_escaped_newlines = '\\n' in key_str_check
                logger.info(
                    "Key-pair key before normalization | type=%s length=%s has_begin=%s has_end=%s has_newlines=%s has_escaped_newlines=%s",
                    type(private_key_str),
                    len(key_str_check),
                    '-----BEGIN' in key_str_check.upper(),
                    '-----END' in key_str_check.upper(),
                    has_newlines,
                    has_escaped_newlines,
                )

                private_key_str = normalize_pem_key(private_key_str)
                logger.info(
                    "Key-pair key after normalization | length=%s first_60=%r last_60=%r passphrase_provided=%s",
                    len(private_key_str),
                    private_key_str[:60],
                    private_key_str[-60:],
                    bool(private_key_passphrase and str(private_key_passphrase).strip()),
                )
                 
                private_key = self._load_private_key_from_string(private_key_str, private_key_passphrase)

                conn_params = {
                    "user": snowflake_username,
                    "account": snowflake_account,
                    "warehouse": snowflake_warehouse,
                    "private_key": private_key
                }
            else:
                raise Exception(f"Unsupported authentication type: {authentication_type}")

            logger.info(
                "Opening Snowflake connection | account=%s database=%s warehouse=%s user=%s auth_type=%s",
                snowflake_account,
                snowflake_database,
                snowflake_warehouse,
                snowflake_username,
                authentication_type,
            )
            print(
                f"Opening Snowflake connection | account={snowflake_account} database={snowflake_database} "
                f"warehouse={snowflake_warehouse} user={snowflake_username} auth_type={authentication_type}"
            )
            conn = snowflake.connector.connect(**conn_params)

            # Use database if specified
            if snowflake_database:
                logger.info("Switching Snowflake database | database=%s", snowflake_database)
                print(f"Switching Snowflake database | database={snowflake_database}")
                cur = conn.cursor()
                cur.execute(f"USE DATABASE {snowflake_database}")
                cur.close()

            logger.info(
                "Snowflake connection established | account=%s database=%s warehouse=%s user=%s auth_type=%s",
                snowflake_account,
                snowflake_database,
                snowflake_warehouse,
                snowflake_username,
                authentication_type,
            )
            print(
                f"Snowflake connection established | account={snowflake_account} database={snowflake_database} "
                f"warehouse={snowflake_warehouse} user={snowflake_username} auth_type={authentication_type}"
            )
            return conn

        except (DatabaseError, ProgrammingError) as snowflake_err:
            # Snowflake-specific errors - preserve the original error message
            error_msg = str(snowflake_err)
            print(f"[ERROR] Snowflake error: {error_msg}")
            raise Exception(f"Snowflake error: {error_msg}") from snowflake_err
        
        except OperationalError as op_err:
            # Network/connection errors
            error_msg = str(op_err)
            print(f"[ERROR] Connection error: {error_msg}")
            raise Exception(f"Failed to connect to Snowflake: {error_msg}") from op_err
        
        except ValueError as val_err:
            # Validation errors (missing key, wrong auth type, key loading errors)
            print(f"[ERROR] Validation error: {val_err}")
            raise
        
        except Exception as e:
            # Generic catch-all - log full details
            error_msg = str(e)
            error_type = type(e).__name__
            logger.exception(
                "Unexpected Snowflake connection error | account=%s database=%s warehouse=%s user=%s auth_type=%s",
                snowflake_account,
                snowflake_database,
                snowflake_warehouse,
                snowflake_username,
                authentication_type,
            )
            print(f"[ERROR] Unexpected error ({error_type}): {error_msg}")
            print(f"[ERROR] Full exception: {repr(e)}")
            raise Exception(f"Connection failed: {error_msg}") from e
    def engine_creation(self):
        # Validate required fields
        snowflake_username = self.snowflake_details.get('user') or self.snowflake_details.get('username')
        if not snowflake_username:
            raise ValueError("Error: Username is required.")
        
        snowflake_account = self.snowflake_details.get('account')
        if not snowflake_account:
            raise ValueError("Error: Account is required.")
        
        snowflake_database = self._normalize_case_if_simple_identifier(self.snowflake_details.get('database'))
        if not snowflake_database:
            raise ValueError("Error: Database is required.")
        
        snowflake_warehouse = self._normalize_case_if_simple_identifier(self.snowflake_details.get('warehouse'))
        if not snowflake_warehouse:
            raise ValueError("Error: Warehouse is required.")
        
        authentication_type = self.snowflake_details.get('authentication_type', 'password').lower()

        # Initialize variables for key pair validation (used later)
        stored_public_key = None
        private_key = None

        try:
            # Choose authentication method
            if authentication_type == "password":
                snowflake_password = self.snowflake_details.get('password')
                if not snowflake_password:
                    raise ValueError("Error: Password is required for password authentication.")
                conn_params = {
                    "user": snowflake_username,
                    "password": snowflake_password,
                    "account": snowflake_account,
                    "warehouse": snowflake_warehouse
                }

            elif authentication_type == "key_pair":
                private_key_str = self.snowflake_details.get('private_key')
                private_key_passphrase = self.snowflake_details.get('passphrase')
                stored_public_key = self.snowflake_details.get('public_key')  # Get stored public key
                # Note: private_key will be assigned below

                if not private_key_str:
                    raise Exception("Private key string is required for key-pair authentication.")
                
                # ============================================================
                # COMPREHENSIVE DEBUG: CONNECTION CREATION PATH (engine_creation)
                # ============================================================
                print("=" * 80)
                print("[KEY-PAIR DEBUG] engine_creation() - CONNECTION CREATION PATH")
                print("=" * 80)
                
                # Debug: Passphrase analysis
                print(f"[KEY-PAIR DEBUG] Passphrase Analysis:")
                print(f"  - Passphrase exists: {private_key_passphrase is not None}")
                print(f"  - Passphrase type: {type(private_key_passphrase)}")
                if private_key_passphrase:
                    passphrase_str = str(private_key_passphrase)
                    print(f"  - Passphrase length: {len(passphrase_str)}")
                    print(f"  - Passphrase is empty: {passphrase_str.strip() == ''}")
                    print(f"  - Passphrase first 5 chars: '{passphrase_str[:5]}...' (masked)")
                else:
                    print(f"  - âš ï¸ Passphrase is None or missing")
                
                # Debug: Key before normalization
                key_str_check = str(private_key_str)
                has_newlines = '\n' in key_str_check
                has_escaped_newlines = '\\n' in key_str_check
                print(f"[KEY-PAIR DEBUG] Key Before Normalization:")
                print(f"  - Type: {type(private_key_str)}")
                print(f"  - Length: {len(key_str_check)}")
                print(f"  - Has BEGIN header: {'-----BEGIN' in key_str_check.upper()}")
                print(f"  - Has END header: {'-----END' in key_str_check.upper()}")
                print(f"  - Has actual newlines (\\n): {has_newlines}")
                print(f"  - Has escaped newlines (\\\\n): {has_escaped_newlines}")
                print(f"  - First 60 chars: {repr(key_str_check[:60])}")
                print(f"  - Last 60 chars: {repr(key_str_check[-60:])}")

                private_key_str = normalize_pem_key(private_key_str)
                
                # Debug: Key after normalization
                print(f"[KEY-PAIR DEBUG] Key After Normalization:")
                print(f"  - Length: {len(private_key_str)}")
                print(f"  - First 60 chars: {repr(private_key_str[:60])}")
                print(f"  - Last 60 chars: {repr(private_key_str[-60:])}")
                print(f"[KEY-PAIR DEBUG] Calling _load_private_key_from_string() with:")
                print(f"  - Key length: {len(private_key_str)}")
                print(f"  - Passphrase provided: {bool(private_key_passphrase and str(private_key_passphrase).strip())}")
                print("=" * 80)
                
                private_key = self._load_private_key_from_string(private_key_str, private_key_passphrase)

                # âœ… VALIDATE PUBLIC KEY: Extract from private key and compare with stored public key
                if stored_public_key:
                    try:
                        print("[PUBLIC_KEY_VALIDATION] ========================================")
                        print("[PUBLIC_KEY_VALIDATION] Starting public key validation...")
                        print("[PUBLIC_KEY_VALIDATION] ========================================")
                        
                        print("[PUBLIC_KEY_VALIDATION] Step 1: Extracting public key from private key...")
                        extracted_public_key = self._extract_public_key_from_private(private_key)
                        
                        print("[PUBLIC_KEY_VALIDATION] Step 2: Comparing extracted public key with stored public key...")
                        if not self._validate_public_key_match(extracted_public_key, stored_public_key):
                            error_msg = (
                                "Error: Public key validation failed. The public key extracted from the private key "
                                "does not match the stored public key. Please ensure the private key and "
                                "public key are from the same key pair."
                            )
                            print(f"[PUBLIC_KEY_VALIDATION] âŒ {error_msg}")
                            raise ValueError(error_msg)
                        
                        print("[PUBLIC_KEY_VALIDATION] âœ… Public key matches stored public key")
                        print("[PUBLIC_KEY_VALIDATION] ========================================")
                    except Exception as e:
                        if isinstance(e, ValueError): raise e
                        error_msg = f"Error: Public key validation failed: {str(e)}"
                        print(f"[PUBLIC_KEY_VALIDATION] âŒ {error_msg}")
                        raise ValueError(error_msg) from e
                else:
                    print("[PUBLIC_KEY_VALIDATION] âš ï¸ No stored public key provided - skipping public key validation")

                conn_params = {
                    "user": snowflake_username,
                    "account": snowflake_account,
                    "warehouse": snowflake_warehouse,
                    "private_key": private_key
                }

            else:
                raise Exception(f"Unsupported authentication type: {authentication_type}")

            # Establish a connection to Snowflake
            conn = snowflake.connector.connect(**conn_params)
            cur = conn.cursor()
            print("Created connection")

            # âœ… VALIDATE PUBLIC KEY IN SNOWFLAKE: Verify stored public key matches Snowflake's registered key
            if authentication_type == "key_pair" and stored_public_key:
                try:
                    print("[PUBLIC_KEY_VALIDATION] ========================================")
                    print("[PUBLIC_KEY_VALIDATION] Verifying public key against Snowflake...")
                    print("[PUBLIC_KEY_VALIDATION] ========================================")
                    
                    # Query Snowflake for registered public keys
                    snowflake_public_keys = self._get_snowflake_public_keys(cur, snowflake_username)
                    
                    # Extract public key from private key for reference
                    if private_key:
                        extracted_public_key = self._extract_public_key_from_private(private_key)
                        print("[PUBLIC_KEY_VALIDATION] Extracted public key for Snowflake verification")
                    
                    # Note: Snowflake stores key fingerprints, so we verify by attempting connection
                    # If connection succeeds, the public key is valid in Snowflake
                    # The actual key matching happens during authentication
                    print("[PUBLIC_KEY_VALIDATION] âœ… Connection successful - public key is valid in Snowflake")
                    print("[PUBLIC_KEY_VALIDATION] If connection failed, Snowflake would have rejected the key pair")
                    print("[PUBLIC_KEY_VALIDATION] ========================================")
                    
                except Exception as e:
                    print(f"[PUBLIC_KEY_VALIDATION] âš ï¸ Could not verify public key in Snowflake: {str(e)}")
                    # Don't fail here - connection already succeeded, which means key is valid
                    print("[PUBLIC_KEY_VALIDATION] Connection succeeded, so key is valid despite query error")

            # Use the database before creating schema
            if snowflake_database:
                cur.execute(f"USE DATABASE {self._q(snowflake_database)}")
                print(f"Database '{snowflake_database}' selected.")
            else:
                raise Exception("No database provided.")

            # Check warehouse
            if snowflake_warehouse:
                try:
                    cur.execute(f"USE WAREHOUSE {self._q(snowflake_warehouse)}")
                    print(f"Warehouse '{snowflake_warehouse}' selected.")
                except ProgrammingError as warehouse_err:
                    raise Exception(f"Error: Warehouse '{snowflake_warehouse}' does not exist or you are not authorized to use it.")

            # Optional source schema validation for Snowflake-as-source flows.
            if self.validate_source_schema:
                source_schema_to_validate = self.source_schema or self.snowflake_details.get('schema')
                source_schema_canonical = self._normalize_case_if_simple_identifier(source_schema_to_validate)
                if not source_schema_canonical:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "Error: schema is required for Snowflake source authorization. "
                            "Provide the source schema so we can validate it exists."
                        ),
                    )
                try:
                    cur.execute(
                        """
                        SELECT COUNT(*)
                        FROM INFORMATION_SCHEMA.SCHEMATA
                        WHERE SCHEMA_NAME = %s
                        """,
                        (source_schema_canonical,)
                    )
                    schema_exists = (cur.fetchone() or [0])[0] > 0
                    if not schema_exists:
                        raise Exception(
                            f"Source schema '{source_schema_canonical}' does not exist in database '{snowflake_database}'."
                        )
                    cur.execute(f"USE SCHEMA {self._q(source_schema_canonical)}")
                    print(f"âœ… Source schema '{source_schema_canonical}' validated and selected.")
                except Exception as source_schema_err:
                    raise Exception(
                        f"Source schema validation failed for '{source_schema_canonical}' in database "
                        f"'{snowflake_database}': {source_schema_err}"
                    ) from source_schema_err

            # Strict destination schema validation is optional.
            # - test destination endpoint: disabled (credential connectivity only)
            # - test connection / save flows: enabled (schema create/use/DDL validation)
            schema_to_validate = self.destination_schema
            schema_canonical = None
            if self.validate_destination_schema:
                if schema_to_validate:
                    schema_canonical = self._normalize_identifier(schema_to_validate)
                    if schema_canonical != str(schema_to_validate).strip().upper():
                        print(
                            f"ðŸ”§ Normalized destination schema for validation: "
                            f"'{schema_to_validate}' -> '{schema_canonical}'"
                        )
                    print(
                        f"ðŸ”§ Validating destination schema '{schema_canonical}' in database '{snowflake_database}'..."
                    )
                    try:
                        # Check if schema exists first (for clear logging), then create if missing.
                        cur.execute(
                            """
                            SELECT COUNT(*)
                            FROM INFORMATION_SCHEMA.SCHEMATA
                            WHERE SCHEMA_NAME = %s
                            """,
                            (schema_canonical,)
                        )
                        schema_exists = (cur.fetchone() or [0])[0] > 0
                        if schema_exists:
                            print(f"âœ… Schema '{schema_canonical}' already exists")
                        else:
                            print(f"â„¹ï¸ Schema '{schema_canonical}' not found - creating it")
                            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {self._q(schema_canonical)}")
                            print(f"âœ… Schema '{schema_canonical}' created")

                        cur.execute(f"USE SCHEMA {self._q(schema_canonical)}")
                        print(f"âœ… Using schema '{schema_canonical}'")

                        test_table = f"DS_SCHEMA_TEST_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}"
                        fq_test = f"{self._q(snowflake_database)}.{self._q(schema_canonical)}.{self._q(test_table)}"
                        try:
                            cur.execute(f"CREATE TABLE {fq_test} (TEST_COL NUMBER)")
                            cur.execute(f"INSERT INTO {fq_test} (TEST_COL) VALUES (1)")
                            print(f"âœ… Schema test table created successfully: {schema_canonical}.{test_table}")
                        finally:
                            try:
                                cur.execute(f"DROP TABLE IF EXISTS {fq_test}")
                                print(f"âœ… Schema test table dropped: {schema_canonical}.{test_table}")
                            except Exception as drop_err:
                                print(f"âš ï¸ Failed to drop schema test table '{test_table}': {drop_err}")
                    except Exception as schema_err:
                        raise Exception(
                            f"Destination schema validation failed for '{schema_canonical}' in database "
                            f"'{snowflake_database}': {schema_err}"
                        ) from schema_err
                else:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "Error: destination_schema is required for Snowflake authorization. "
                            "Provide the destination schema so we can create/use and validate it."
                        ),
                    )
            else:
                print("â„¹ï¸ Skipping destination schema validation (validate_destination_schema=False)")

            cur.close()
            conn.close()

            return {
                "body": "Connection established successfully",
                "status_code": 200,
                "normalized_destination_schema": schema_canonical,
            }

        except DatabaseError as db_err:
            raw_error = str(db_err)
            error_str = raw_error.lower()
            trial_expired_markers = [
                "your free trial has ended",
                "trial has ended",
                "trial expired",
                "virtual warehouses have been suspended",
                "add billing information in the snowflake web ui",
                "billing information",
            ]
            if any(marker in error_str for marker in trial_expired_markers):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Error: Snowflake trial expired or billing is inactive. "
                        "Please add billing in Snowflake or reactivate your warehouse."
                    ),
                ) from db_err

            # Prioritize specific missing-object errors over generic authorization wording.
            missing_db = re.search(r"database\s+'([^']+)'\s+does not exist", raw_error, flags=re.IGNORECASE)
            missing_wh = re.search(r"warehouse\s+'([^']+)'\s+does not exist", raw_error, flags=re.IGNORECASE)
            missing_schema = re.search(r"schema\s+'([^']+)'\s+does not exist", raw_error, flags=re.IGNORECASE)

            if missing_db:
                raise HTTPException(
                    status_code=404,
                    detail=f"Error: Database '{missing_db.group(1)}' not found or not authorized."
                ) from db_err
            elif missing_wh:
                raise HTTPException(
                    status_code=404,
                    detail=f"Error: Warehouse '{missing_wh.group(1)}' not found or not authorized."
                ) from db_err
            elif missing_schema:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Error: Destination schema '{missing_schema.group(1)}' validation failed. "
                        f"Unable to create/use schema (check role privileges and database access)."
                    )
                ) from db_err
            elif "does not exist" in error_str:
                raise HTTPException(status_code=404, detail=f"Error: Database '{snowflake_database}' not found.") from db_err
            elif "incorrect username or password" in error_str or "invalid username or password" in error_str:
                raise HTTPException(status_code=401, detail='Error: Invalid username or password.') from db_err
            elif "not authorized" in error_str and "does not exist" not in error_str:
                raise HTTPException(status_code=401, detail='Error: Not authorized to access the specified Snowflake objects.') from db_err
            else:
                # Check for JWT/token errors
                if any(keyword in error_str for keyword in ['jwt', 'token', 'authentication', 'authorization']):
                    if 'expired' in error_str or 'invalid' in error_str:
                        raise HTTPException(status_code=401, detail='Error:  Authentication token has expired for the user specified. Please refresh the token and try again.') from db_err
                    else:
                        raise HTTPException(status_code=401, detail='Error: Authentication failed. Please check your credentials.') from db_err
                # Generic database error - extract main message, remove error codes
                error_msg = str(db_err)
                if '):' in error_msg:
                    error_msg = error_msg.split('):', 1)[1].strip()
                if '.' in error_msg and len(error_msg.split('.')) > 2:
                    error_msg = error_msg.split('.')[0] + '.'
                raise HTTPException(status_code=400, detail=f'Error: {error_msg}.') from db_err

        except ProgrammingError as prog_err:
            raw_error = str(prog_err)
            error_str = raw_error.lower()
            trial_expired_markers = [
                "your free trial has ended",
                "trial has ended",
                "trial expired",
                "virtual warehouses have been suspended",
                "add billing information in the snowflake web ui",
                "billing information",
            ]
            if any(marker in error_str for marker in trial_expired_markers):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Error: Snowflake trial expired or billing is inactive. "
                        "Please add billing in Snowflake or reactivate your warehouse."
                    ),
                ) from prog_err
            missing_db = re.search(r"database\s+'([^']+)'\s+does not exist", raw_error, flags=re.IGNORECASE)
            missing_wh = re.search(r"warehouse\s+'([^']+)'\s+does not exist", raw_error, flags=re.IGNORECASE)
            missing_schema = re.search(r"schema\s+'([^']+)'\s+does not exist", raw_error, flags=re.IGNORECASE)
            if missing_db:
                raise HTTPException(
                    status_code=404,
                    detail=f"Error: Database '{missing_db.group(1)}' not found or not authorized."
                ) from prog_err
            elif missing_wh:
                raise HTTPException(
                    status_code=404,
                    detail=f"Error: Warehouse '{missing_wh.group(1)}' not found or not authorized."
                ) from prog_err
            elif missing_schema:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Error: Destination schema '{missing_schema.group(1)}' validation failed. "
                        f"Unable to create/use schema (check role privileges and database access)."
                    )
                ) from prog_err
            elif "does not exist" in error_str:
                raise HTTPException(status_code=404, detail=f"Error: Schema '{self.destination_schema}' not found.") from prog_err
            elif "sql compilation error" in error_str:
                error_msg = str(prog_err)
                if '):' in error_msg:
                    error_msg = error_msg.split('):', 1)[1].strip()
                error_lower = error_msg.lower()
                if 'sql compilation error:' in error_lower:
                    specific_error = error_msg.split(':', 1)[1].strip() if ':' in error_msg else ''
                elif 'sql compilation error.' in error_lower:
                    specific_error = error_msg.split('.', 1)[1].strip() if '.' in error_msg else ''
                else:
                    if ' error ' in error_lower:
                        parts = error_msg.split(' error ', 1)
                        if len(parts) > 1:
                            specific_error = parts[1].strip(' :.')
                        else:
                            specific_error = error_msg
                specific_error = specific_error.strip(' .')
                if specific_error:
                    formatted_msg = f"SQL Compilation error, {specific_error}"
                else:
                    formatted_msg = "SQL Compilation error"
                raise HTTPException(status_code=400, detail=f'Error: {formatted_msg}.') from prog_err
            else:
                error_msg = str(prog_err)
                if '):' in error_msg:
                    error_msg = error_msg.split('):', 1)[1].strip()
                raise HTTPException(status_code=400, detail=f'Error: {error_msg}.') from prog_err

        except OperationalError as op_err:
            error_msg = str(op_err)
            error_str = error_msg.lower()
            trial_expired_markers = [
                "your free trial has ended",
                "trial has ended",
                "trial expired",
                "virtual warehouses have been suspended",
                "add billing information in the snowflake web ui",
                "billing information",
            ]
            if any(marker in error_str for marker in trial_expired_markers):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Error: Snowflake trial expired or billing is inactive. "
                        "Please add billing in Snowflake or reactivate your warehouse."
                    ),
                ) from op_err
            if '):' in error_msg:
                error_msg = error_msg.split('):', 1)[1].strip()
            raise HTTPException(status_code=500, detail=f'Error: {error_msg}.') from op_err

        except Exception as e:
            if isinstance(e, HTTPException): raise e
            raw_error = str(e)
            error_str = raw_error.lower()
            trial_expired_markers = [
                "your free trial has ended",
                "trial has ended",
                "trial expired",
                "virtual warehouses have been suspended",
                "add billing information in the snowflake web ui",
                "billing information",
            ]
            if any(marker in error_str for marker in trial_expired_markers):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Error: Snowflake trial expired or billing is inactive. "
                        "Please add billing in Snowflake or reactivate your warehouse."
                    ),
                ) from e
            missing_db = re.search(r"database\s+'([^']+)'\s+does not exist", raw_error, flags=re.IGNORECASE)
            missing_wh = re.search(r"warehouse\s+'([^']+)'\s+does not exist", raw_error, flags=re.IGNORECASE)
            missing_schema = re.search(r"schema\s+'([^']+)'\s+does not exist", raw_error, flags=re.IGNORECASE)
            if missing_db:
                raise HTTPException(
                    status_code=404,
                    detail=f"Error: Database '{missing_db.group(1)}' not found or not authorized."
                ) from e
            if missing_wh:
                raise HTTPException(
                    status_code=404,
                    detail=f"Error: Warehouse '{missing_wh.group(1)}' not found or not authorized."
                ) from e
            if missing_schema:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Error: Destination schema '{missing_schema.group(1)}' validation failed. "
                        f"Unable to create/use schema (check role privileges and database access)."
                    )
                ) from e
            if any(keyword in error_str for keyword in ['jwt', 'token', 'authentication', 'authorization']):
                if 'expired' in error_str or 'invalid' in error_str:
                    raise HTTPException(status_code=401, detail='Error:  Authentication token has expired for the user specified. Please refresh the token and try again.') from e
                else:
                    raise HTTPException(status_code=401, detail='Error: Authentication failed. Please check your credentials.') from e
            error_msg = str(e).strip()
            if not error_msg.lower().startswith("error:"):
                error_msg = f"Error: {error_msg}"
            raise HTTPException(status_code=400, detail=error_msg) from e
        
    def get_tables_and_columns(self, connection_id, job_name, destination_schema):
        """
        Fetch tables, views, dynamic tables, and their column details, including primary keys.
        """
        print("=" * 80)
        print("Inside get_tables_and_columns -----------")
        print(f"[DEBUG] snowflake_details: {self.snowflake_details}")
        print(f"[DEBUG] snowflake_details keys: {list(self.snowflake_details.keys())}")
        print(f"[DEBUG] destination_schema parameter: {destination_schema}")
        print("=" * 80)
        
        try:
            print("[Step 1] Creating Snowflake connection...")
            conn = self.create_connection()
            print("[Step 2] Connection established.")
            cursor = conn.cursor()
            # Use SOURCE schema from snowflake_details for fetching tables (not destination_schema)
            source_schema = self.snowflake_details.get('schema')
            print(f"[DEBUG] snowflake_details schema (SOURCE): {source_schema}")
            
            if not source_schema:
                error_msg = "Snowflake source configuration missing required 'schema' field."
                print(f"âŒ ERROR: {error_msg}")
                raise ValueError(error_msg)
            
            print(f"[DEBUG] destination_schema parameter (for writing only): {destination_schema}")
            schema = f"{self.snowflake_details['database']}.{source_schema}"
            print(f"[Step 2] Using SOURCE schema for fetching tables: {schema}")

            # Step 3: Fetch tables
            try:
                print("[Step 3] Fetching tables...")
                cursor.execute(f"SHOW TABLES IN SCHEMA {schema}")
                tables = {row[1]: "table" for row in cursor.fetchall()}
                print("    [Step 3] Tables found:", list(tables.keys()))
            except Exception as e:
                print("[Error] SHOW TABLES failed:", e)
                raise

            # Step 4: Fetch views
            try:
                print("[Step 4] Fetching views...")
                cursor.execute(f"SHOW VIEWS IN SCHEMA {schema}")
                views = {row[1]: "view" for row in cursor.fetchall()}
                print("    [Step 4] Views found:", list(views.keys()))
            except Exception as e:
                print("[Error] SHOW VIEWS failed:", e)
                raise

            # Step 5: Fetch dynamic tables
            try:
                print("[Step 5] Fetching dynamic tables...")
                cursor.execute(f"SHOW DYNAMIC TABLES IN SCHEMA {schema}")
                dynamic_tables = {row[1]: "dynamic_table" for row in cursor.fetchall()}
                print("    [Step 5] Dynamic tables found:", list(dynamic_tables.keys()))
            except Exception as e:
                print("[Error] SHOW DYNAMIC TABLES failed:", e)
                dynamic_tables = {}

            # Combine all objects into a single dictionary
            all_objects = {**tables, **views, **dynamic_tables}
            print("[Step 6] All objects collected:", list(all_objects.keys()))

            # Step 7: Fetch primary keys
            primary_keys = {}
            try:
                print("[Step 7] Fetching primary keys...")
                cursor.execute(f"SHOW PRIMARY KEYS IN SCHEMA {schema}")
                pk_results = cursor.fetchall()
                print("    [Step 7] PK Results (raw):", pk_results)
                for row in pk_results:
                    table_name = row[3]
                    column_name = row[4]
                    if table_name not in primary_keys:
                        primary_keys[table_name] = []
                    primary_keys[table_name].append(column_name)
                print("    [Step 7] Primary key dictionary:", primary_keys)
            except Exception as e:
                print("[Error] SHOW PRIMARY KEYS failed:", e)
                primary_keys = {}

            # Step 8: Fetch columns for each object and add PK flag
            columns_info = {}
            for table_name, table_type in all_objects.items():
                try:
                    print(f"[Step 8] DESCRIBE {table_type.upper()} {table_name}...")
                    cursor.execute(f"DESCRIBE TABLE {schema}.{table_name}")
                    describe_rows = cursor.fetchall()
                    columns_info[table_name] = {
                        row[0]: {
                            "data_type": row[1],
                            "is_primary_key": row[0] in primary_keys.get(table_name, []),
                            "ordinal_position": index + 1,
                        }
                        for index, row in enumerate(describe_rows)
                    }
                    print(f"    [Step 8] {table_name} columns:", list(columns_info[table_name].keys()))
                except Exception as e:
                    print(f"[Error] DESCRIBE failed for {table_name}: {e}")
                    columns_info[table_name] = {}

            print("[Step 9] Closing connection...")
            conn.close()

            print("[Step 10] Final Columns Info:", columns_info)
            print("[Step 11] Calling refresh_and_update_tables_in_database...")
            refresh_and_update_tables_in_database(connection_id, all_objects, columns_info, job_name)

            print("[Success] All steps complete, returning result.")
            return {
                'status_code': 200,
                "updated_tables": all_objects,
                "columns": columns_info,
                'body': "Tables fetched successfully"
            }

        except Exception as e:
            print(f"[Critical Error] Exception in get_tables_and_columns: {str(e)}")
            
            # â”€â”€ Log schema refresh failure â”€â”€
            try:
                from utils.error_utils import update_migration_status_log
                from utils.websocket_utils import trigger_update_schema_ws_status_update
                
                error_str = str(e).lower()
                if any(k in error_str for k in ['connection', 'timeout', 'refused', 'network']):
                    failure_tag = "[CONNECTION]"
                elif any(k in error_str for k in ['jwt', 'authentication', 'token', '401']):
                    failure_tag = "[AUTH]"
                else:
                    failure_tag = "[CRASH]"
                
                if job_name:
                    update_migration_status_log(
                        job_name=job_name,
                        message=f"Update schema failed: {failure_tag} {str(e)[:300]}",
                        status="F"
                    )
                
                # âœ… Trigger WebSocket update for failure
                trigger_update_schema_ws_status_update(connection_id)
                
            except Exception as log_err:
                print(f"âš ï¸ Failed to log schema refresh failure (non-fatal): {log_err}")
            
            raise Exception("Error: Failed to fetch Snowflake schema details.")
        

from datasyncher_connections.snowflake.pem import normalize_pem_key as _normalize_pem_key


def normalize_pem_key(key_str):
    """Re-export shared PEM normalization (datasyncher_connections)."""
    print("inside normalize")
    return _normalize_pem_key(key_str)

    




        
    
# def insert_tool_log(cmp_id, connection_id, usr_id, table_name, status, message, session):
#     """
#     Inserts a log entry into the `tool_log` table.
#     Args:
#         cmp_id (int): Company ID.
#         connection_id (int): Connection ID.
#         usr_id (int): User ID.
#         table_name (str): Name of the table being logged.
#         status (str): Status of the operation (e.g., 'S' for Success).
#         message (str): Log message describing the operation.
#         session (str): Session identifier.
#     Returns:
#         int: The newly inserted log ID, or None if an error occurs.
#     """
#     try:
#         connection = postgres_connection()  # Ensure this function is correctly implemented
#         if connection is None:
#             print("Failed to establish connection to the database.")
#             return None
#         cursor = connection.cursor()
#         # Retrieve the maximum log_id from the `tool_log` table
#         cursor.execute("SELECT MAX(log_id) FROM tool_log")
#         max_log_id = cursor.fetchone()[0]
#         # Increment the maximum log_id to generate a new unique log_id
#         new_log_id = max_log_id + 1 if max_log_id is not None else 1
#         # Get the current UTC timestamp
#         current_time = datetime.utcnow()
#         # Prepare and execute the insert query
#         log_insert_query = '''
#             INSERT INTO tool_log (log_id, cmp_id, connection_id, user_id, "table", time, status, message, session)
#             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
#             RETURNING log_id
#         '''
#         cursor.execute(
#             log_insert_query,
#             (new_log_id, cmp_id, connection_id, usr_id, table_name, current_time, status, message, session)
#         )
#         # Retrieve the new log_id
#         log_id = cursor.fetchone()[0]
#         # Commit the transaction
#         connection.commit()
#         print(f"Log entry inserted successfully with log_id: {log_id}")
#         return log_id
#     except psycopg2.Error as e:
#         print(f"Error inserting into tool_log: {str(e)}")
#         return None
#     finally:
#         # Ensure the cursor and connection are closed
#         if connection:
#             cursor.close()
#             connection.close()



def refresh_and_update_tables_in_database(connection_id, new_tables, attributes_dict, job_name):
        """
        Refreshes the tables and their attributes in the PostgreSQL database.
        Inserts new tables and updates existing ones, while handling migration records.
        """
        print("job name",job_name)
        try:
            # Establish PostgreSQL connection
            conn = postgres_connection()  # Ensure this function returns a valid connection
            cursor = conn.cursor()
            # Insert or update each new table in the source_table_selection table
            for table in new_tables:
                table_fields = attributes_dict.get(table)
                if not isinstance(table_fields, dict):
                    for attr_table, attr_fields in (attributes_dict or {}).items():
                        if str(attr_table).lower() == str(table).lower():
                            table_fields = attr_fields
                            break
                if not isinstance(table_fields, dict):
                    table_fields = {}

                primary_keys = [
                    col_name
                    for col_name, meta in table_fields.items()
                    if isinstance(meta, dict) and meta.get("is_primary_key")
                ]
                table_fields = json.dumps(table_fields)
                # Insert or update the table with added_on included
                # âœ… FIX: Don't overwrite 'selected' status on refresh - preserve user's selections
                sql_upsert = """
                    INSERT INTO source_table_selection 
                    (connection_id, "table", table_fields, primary_keys, selected, added_on, is_history, is_mapping_checked)
                    VALUES (%s, %s, %s, %s, FALSE, NOW(), FALSE, FALSE)
                    ON CONFLICT (connection_id, "table")
                    DO UPDATE SET 
                        table_fields = EXCLUDED.table_fields,
                        primary_keys = EXCLUDED.primary_keys,
                        added_on = NOW();
                        -- âœ… selected is NOT updated - preserves user's existing selection!

                """
                cursor.execute(sql_upsert, (connection_id, table, table_fields, json.dumps(primary_keys)))

            
            # âœ… FIX: Check if this is reverse ETL and if destination listing is also complete
            # For reverse ETL (Snowflake -> Salesforce), both source and destination listing must complete
            # Check if connection is reverse ETL by checking if source has is_rtl flag
            cursor.execute("""
                SELECT 
                    tc.connection_id,
                    tc.src_config_id,
                    sc.src_id,
                    sm.name,
                    sm.is_rtl
                FROM connection tc
                LEFT JOIN source_config sc ON tc.src_config_id = sc.src_config_id
                LEFT JOIN source_model sm ON sc.src_id = sm.src_id
                WHERE tc.connection_id = %s;
            """, (connection_id,))
            debug_row = cursor.fetchone()
            if debug_row:
                print(f"ðŸ” [DEBUG] connection_id={debug_row[0]}, src_config_id={debug_row[1]}, src_id={debug_row[2]}, source_name='{debug_row[3]}', is_rtl={debug_row[4]}")
            else:
                print(f"âš ï¸ [DEBUG] No connection row found for connection_id={connection_id}")
            
            cursor.execute("""
                SELECT COUNT(*) 
                FROM connection tc
                JOIN source_config sc ON tc.src_config_id = sc.src_config_id
                JOIN source_model sm ON sc.src_id = sm.src_id
                WHERE tc.connection_id = %s 
                AND sm.is_rtl = TRUE;
            """, (connection_id,))
            reverse_etl_count = cursor.fetchone()[0]
            is_reverse_etl = reverse_etl_count > 0
            # Query destination model name to see if it is Salesforce or SalesforceSandbox
            cursor.execute("""
                SELECT dm.name
                FROM connection tc
                JOIN destination_config dc ON tc.dst_config_id = dc.dst_config_id
                JOIN destination_model dm ON dc.dst_id = dm.dst_id
                WHERE tc.connection_id = %s;
            """, (connection_id,))
            row = cursor.fetchone()
            dest_name = row[0] if row else ""
            is_salesforce_dest = dest_name in ["Salesforce", "SalesforceSandbox"]
            print(f"🔍 [REVERSE_ETL_CHECK] dest_name={dest_name}, is_salesforce_dest={is_salesforce_dest}")

            is_completed = False
            if is_reverse_etl and is_salesforce_dest:
                # For reverse ETL (Snowflake -> Salesforce), check if destination listing has also completed
                # We check if destination tables were recently updated (within last 5 minutes)
                cursor.execute("""
                    SELECT COUNT(*) 
                    FROM destination_table_selection 
                    WHERE connection_id = %s 
                    AND added_on > NOW() - INTERVAL '5 minutes';
                """, (connection_id,))
                dest_recently_updated = cursor.fetchone()[0] > 0
                
                if dest_recently_updated:
                    is_completed = True
                    # Both source and destination listing are complete
                    sql_update_migration_record = """
                    UPDATE migration_record
                    SET status = %s, updated_at = NOW()
                    WHERE job_name = %s AND table_name IS NULL;
                    """
                    cursor.execute(sql_update_migration_record, ('completed', job_name))
                    print(f"✅ All listing jobs completed for reverse ETL job_name: {job_name}")
                else:
                    # Destination listing not complete yet, don't mark as COMPLETED
                    print(f"⏳ Destination listing not complete yet for reverse ETL job_name: {job_name}, not marking as COMPLETED yet")
            else:
                is_completed = True
                # Regular ETL or Snowflake -> S3/SharePoint/GoogleDrive listing, mark as COMPLETED immediately
                sql_update_migration_record = """
                UPDATE migration_record
                SET status = %s, updated_at = NOW()
                WHERE job_name = %s AND table_name IS NULL;
                """
                cursor.execute(sql_update_migration_record, ('completed', job_name))
                print(f"✅ Listing job completed for job_name: {job_name}")

            # Commit the transaction
            conn.commit()
            print(f"Successfully updated {len(new_tables)} tables, last_synced, and migration record status.")

            # ✅ Update the activity log and trigger WebSocket update on success
            if is_completed:
                try:
                    from utils.error_utils import update_migration_status_log
                    from utils.websocket_utils import trigger_update_schema_ws_status_update
                    
                    if job_name:
                        if is_reverse_etl and is_salesforce_dest:
                            # Count source and destination tables for reverse ETL
                            cursor.execute("SELECT COUNT(*) FROM source_table_selection WHERE connection_id = %s;", (connection_id,))
                            src_count = cursor.fetchone()[0]
                            cursor.execute("SELECT COUNT(*) FROM destination_table_selection WHERE connection_id = %s;", (connection_id,))
                            dest_count = cursor.fetchone()[0]
                            completion_message = f"Update schema completed successfully. Updated {src_count} source tables and {dest_count} destination tables."
                        else:
                            completion_message = f"Update schema completed successfully. Updated {len(new_tables)} tables."
                        update_migration_status_log(
                            job_name=job_name,
                            message=completion_message,
                            status="S"
                        )
                    
                    # ✅ Trigger WebSocket update for success
                    trigger_update_schema_ws_status_update(connection_id)
                    
                except Exception as log_err:
                    print(f"⚠️ Failed to log schema refresh success (non-fatal): {log_err}")
        except Exception as e:
            print(f"Error updating database: {e}")
            # Rollback the transaction and update the migration record to FAILED
            if conn:
                conn.rollback()
            try:
                conn = postgres_connection()  # Reopen the connection to update the status
                cursor = conn.cursor()
                sql_update_migration_record_error = """
                UPDATE migration_record
                SET status = %s, updated_on = NOW()
                WHERE job_name = %s;
                """
                cursor.execute(sql_update_migration_record_error, ('failed', job_name))
                conn.commit()

                # âœ… Update activity log and trigger WebSocket on failure within refresh_and_update
                try:
                    from utils.error_utils import update_migration_status_log
                    from utils.websocket_utils import trigger_update_schema_ws_status_update
                    
                    if job_name:
                        update_migration_status_log(
                            job_name=job_name,
                            message=f"Update schema failed during DB update: {str(e)[:300]}",
                            status="F"
                        )
                    trigger_update_schema_ws_status_update(connection_id)
                except Exception:
                    pass
            except Exception as error_update:
                print(f"Error updating migration record to FAILED: {error_update}")
            finally:
                if cursor:
                    cursor.close()
                if conn:
                    conn.close()
            raise e
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

