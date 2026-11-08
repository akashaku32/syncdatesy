"""
Email Utility Module for Error Notifications

This module provides email functionality to send error notifications when 
table migration failures occur in the data synchronization services.

Features:
- SMTP email sending with authentication
- HTML and plain text email support
- Error details formatting
- Configurable email settings via environment variables
- Retry mechanism for failed email sends
- Logging of email operations

Environment Variables Required:
- EMAIL_HOST: SMTP server hostname (e.g., smtp.gmail.com)
- EMAIL_PORT: SMTP server port (e.g., 587 for TLS)
- EMAIL_USER: Email account username
- EMAIL_PASSWORD: Email account password or app password
- EMAIL_USE_TLS: Whether to use TLS (True/False)
- ERROR_NOTIFICATION_RECIPIENTS: Comma-separated list of recipient emails
- EMAIL_FROM_NAME: Display name for sender (optional)

Usage Example:
    from utils.email_utils import send_error_notification
    
    try:
        # Your table processing code here
        pass
    except Exception as e:
        send_error_notification(
            table_name="Account",
            error_message=str(e),
            connection_id=123,
            service_name="Salesforce",
            additional_context={
                "job_name": "daily_sync",
                "user_id": 456,
                "company_name": "ACME Corp"
            }
        )
"""

import smtplib
import os
import logging
import traceback
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from typing import Dict, List, Optional, Any
import time
import psycopg2

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    # Load .env file from the migration-function directory
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
    load_dotenv(env_path)
    print(f"✅ Loaded environment variables from: {env_path}")
except ImportError:
    print("⚠️ python-dotenv not installed. Using system environment variables only.")
except Exception as e:
    print(f"⚠️ Could not load .env file: {e}. Using system environment variables only.")

# Set up logging for this module
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Console handler for email operations
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


# Transparent white DataSyncher mark, embedded so email clients do not need to
# download a remote image. The source artwork is static/billing/receipt-logo.png.
DATASYNCHER_LOGO_DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAAXNSR0IArs4c6QAAAARnQU1B"
    "AACxjwv8YQUAAAAJcEhZcwAADsMAAA7DAcdvqGQAAALBSURBVFhH7ZZZiI5hFMcHJWULKQmR"
    "LI2UuTEUJhfITqYUjUgTEyLZLxQiRZIMN5YsdxI3tmxxZcnkVpNskWRJ9vWn/3Tecb7zvXyf"
    "7/vcza/em/d/lvc9z/Oc55SVtVAAQAVQB6wGqoEu0ea/AHQHTpDNM2BxtC8pwGDgXswcWBr9"
    "SgIwCnjhEl0BxgLlwDLgnb3/DPSM/kVha/zJJT8ItAo2q5xe47WiUEldYLEl2ghgiLOZG/WC"
    "ALa5oKIu2niAicD8+P6fAVoDh13ij8B0p/e3dd8F7AXqgf3ADmAncAw4Cuy2Cg7LzJAD4Lh"
    "L/hwY6bQlwHun58sFbeTMTCkA85zTA2Cg02Y4Tby2k6GPfAo8Np9G4D7wKtiLlZkZHUA74K"
    "EZquxDg37dNFVgDtAN6Ax0BNqbf1tbwjZAV2AMcMR9gFjh4zZjxgn1QVPQ5OOueS0fgMlh6"
    "SqijYy0sRImpeg3TXsDVEU9F8BsF/901GWw3hmMSNFrnP4TuAFcAi7bcx7Yo0pG3wRVz/y/"
    "Rk3iApegOurCjlo+bIq+AlieGERN4iAXILtEBjAa2Gd/c9ueW8Bd5y+ae4fznZqIUWvClUhM"
    "i3ougHHAB/M/l6IvSoJHrQmg8nf+pl07M9rkwnqBaEjRrpr2PWrNAGvcR4iTqgbQx862Hg0m"
    "PYBeQF9rz7qat9oGFYdCXH8KLnotCxu1fjgHobte3U/PW6uQGtYX4Fuw1btyF2+K2SZUZmZM"
    "ARgOnAqzQD6oDY93cWqD/ud2nAbQz8q30W7A7bZMGkL0boP1EB0xdbwOzndzSL4uM3oBWJ9"
    "X4FlRS9DEBBxwibWcxc8KIvxV1vxnl9MZZ6P9MiHaFQyw1gWvDZpOxR2nP0q9eIoBGOCOmk"
    "6C9kAVsBB44pI3AL2jf0lI6RWRs0Cn6FdSbHrS9ON5qT0SbVv4G78AwWel9NRLNzMAAAAASU"
    "VORK5CYII="
)


def get_user_email_from_database(user_id: int) -> Optional[str]:
    """
    Fetch user email from the database using user_id
    
    Args:
        user_id: The user ID to look up
        
    Returns:
        User's email address if found, None otherwise
    """
    try:
        # Import the postgres_connection function from database_utils
        from utils.database_utils import postgres_connection
        
        conn = postgres_connection()
        cursor = conn.cursor()
        
        # Query the user table to get the company_email
        cursor.execute("""
            SELECT company_email, first_name, last_name, company_name 
            FROM "user" 
            WHERE user_id = %s
        """, (user_id,))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if result:
            email, first_name, last_name, company_name = result
            logger.info(f"✅ Found user email for user_id {user_id}: {email}")
            return email
        else:
            logger.warning(f"⚠️ No user found with user_id: {user_id}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Error fetching user email for user_id {user_id}: {e}")
        logger.error(traceback.format_exc())
        return None


def get_user_details_from_database(user_id: int) -> Optional[Dict[str, str]]:
    """
    Fetch user details from the database using user_id
    
    Args:
        user_id: The user ID to look up
        
    Returns:
        Dictionary with user details if found, None otherwise
    """
    try:
        # Import the postgres_connection function from database_utils
        from utils.database_utils import postgres_connection
        
        conn = postgres_connection()
        cursor = conn.cursor()
        
        # Query the user table to get user details
        cursor.execute("""
            SELECT company_email, first_name, last_name, company_name 
            FROM "user" 
            WHERE user_id = %s
        """, (user_id,))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if result:
            email, first_name, last_name, company_name = result
            user_details = {
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "company_name": company_name,
                "full_name": f"{first_name} {last_name}".strip()
            }
            logger.info(f"✅ Found user details for user_id {user_id}: {user_details['full_name']} ({email})")
            return user_details
        else:
            logger.warning(f"⚠️ No user found with user_id: {user_id}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Error fetching user details for user_id {user_id}: {e}")
        logger.error(traceback.format_exc())
        return None


class EmailConfiguration:
    """Email configuration class to manage SMTP settings"""
    
    def __init__(self, user_id: Optional[int] = None, company_id: Optional[int] = None):
        self.host = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
        self.port = int(os.environ.get("EMAIL_PORT", "587"))
        self.username = os.environ.get("EMAIL_USER")
        self.password = os.environ.get("EMAIL_PASSWORD")
        self.use_tls = os.environ.get("EMAIL_USE_TLS", "True").lower() == "true"
        self.from_name = os.environ.get("EMAIL_FROM_NAME", "DataSyncher Alert System")
        
        # Initialize recipients list
        self.recipients = []
        self.user_details = None
        
        # Try to get user email from database if user_id is provided
        if user_id:
            self.user_details = get_user_details_from_database(user_id)
            if self.user_details and self.user_details.get("email"):
                self.recipients.append(self.user_details["email"])
                logger.info(f"📧 Added user email to recipients: {self.user_details['email']}")
        
        # Get emails from CommunicationSupport table if company_id is provided
        if company_id:
            logger.info(f"🔍 DEBUG: Attempting to fetch CommunicationSupport emails for company_id={company_id}")
            communication_emails = self.get_communication_support_emails(company_id)
            logger.info(f"🔍 DEBUG: get_communication_support_emails() returned: {communication_emails}")
            
            if communication_emails:
                self.recipients.extend(communication_emails)
                logger.info(f"📧 Added {len(communication_emails)} communication support emails to recipients")
                for email in communication_emails:
                    logger.info(f"   📧 CommunicationSupport email: {email}")
            else:
                logger.warning(f"⚠️ DEBUG: No CommunicationSupport emails returned for company_id={company_id}")
            
            # ✨ NEW: Get company admin email
            try:
                from utils.database_utils import get_company_email
                company_email = get_company_email(company_id)
                if company_email and company_email not in self.recipients:
                    self.recipients.append(company_email)
                    logger.info(f"📧 Added company admin email: {company_email}")
            except Exception as e:
                logger.warning(f"⚠️ Could not fetch company admin email: {e}")
        
        # Also get recipient list from environment variable (for admin notifications)
        recipients_env = os.environ.get("ERROR_NOTIFICATION_RECIPIENTS", "")
        env_recipients = [email.strip() for email in recipients_env.split(",") if email.strip()]
        self.recipients.extend(env_recipients)
        
        # Remove duplicates while preserving order
        seen = set()
        self.recipients = [email for email in self.recipients if not (email in seen or seen.add(email))]
    
    def get_communication_support_emails(self, company_id: int) -> List[str]:
        """
        Fetch communication support email addresses from the database
        
        Args:
            company_id: The company ID to look up
            
        Returns:
            List of email addresses from CommunicationSupport table
        """
        try:
            from utils.database_utils import postgres_connection
            
            logger.info(f"🔍 DEBUG: [CommSupport] Starting query for company_id={company_id}")
            
            conn = postgres_connection()
            cursor = conn.cursor()
            
            # Query the CommunicationSupport table to get active email addresses
            query = """
                SELECT email_addresses 
                FROM communication_support 
                WHERE company_id = %s AND is_active = TRUE
            """
            logger.info(f"🔍 DEBUG: [CommSupport] Executing SQL: {query.strip()}")
            logger.info(f"🔍 DEBUG: [CommSupport] Parameters: company_id={company_id}")
            
            cursor.execute(query, (company_id,))
            
            result = cursor.fetchone()
            logger.info(f"🔍 DEBUG: [CommSupport] Query result: {result}")
            
            if result:
                logger.info(f"🔍 DEBUG: [CommSupport] result[0] = {result[0]}")
                logger.info(f"🔍 DEBUG: [CommSupport] result[0] type = {type(result[0])}")
            
            cursor.close()
            conn.close()
            
            if result and result[0]:
                # email_addresses is stored as JSON array
                import json
                
                if isinstance(result[0], str):
                    logger.info(f"🔍 DEBUG: [CommSupport] Parsing from JSON string")
                    email_list = json.loads(result[0])
                elif isinstance(result[0], list):
                    logger.info(f"🔍 DEBUG: [CommSupport] Already a Python list")
                    email_list = result[0]
                else:
                    logger.warning(f"⚠️ DEBUG: [CommSupport] Unexpected type: {type(result[0])}")
                    email_list = result[0]
                
                logger.info(f"🔍 DEBUG: [CommSupport] Parsed email_list = {email_list}")
                logger.info(f"🔍 DEBUG: [CommSupport] email_list type = {type(email_list)}")
                
                if isinstance(email_list, list):
                    valid_emails = [email.strip() for email in email_list if email.strip()]
                    logger.info(f"✅ Found {len(valid_emails)} communication support emails for company {company_id}")
                    for i, email in enumerate(valid_emails, 1):
                        logger.info(f"   📧 CommunicationSupport email {i}: {email}")
                    logger.info(f"🔍 DEBUG: [CommSupport] Returning {len(valid_emails)} emails: {valid_emails}")
                    return valid_emails
                else:
                    logger.warning(f"⚠️ DEBUG: [CommSupport] email_list is not a list: {type(email_list)}")
                    return []
            else:
                logger.warning(f"⚠️ DEBUG: [CommSupport] No result or empty result")
                logger.info(f"ℹ️ No communication support emails found for company {company_id}")
                return []
            
        except Exception as e:
            logger.error(f"❌ Error fetching communication support emails for company {company_id}: {e}")
            logger.error(f"🔍 DEBUG: [CommSupport] Exception type: {type(e).__name__}")
            logger.error(f"🔍 DEBUG: [CommSupport] Exception details: {str(e)}")
            logger.error(traceback.format_exc())
            return []
        
    def is_configured(self) -> bool:
        """Check if email configuration is properly set up"""
        return bool(
            self.host and 
            self.port and 
            self.username and 
            self.password and 
            self.recipients
        )
    
    def get_missing_config(self) -> List[str]:
        """Get list of missing configuration parameters"""
        missing = []
        if not self.host:
            missing.append("EMAIL_HOST")
        if not self.port:
            missing.append("EMAIL_PORT")
        if not self.username:
            missing.append("EMAIL_USER")
        if not self.password:
            missing.append("EMAIL_PASSWORD")
        if not self.recipients:
            missing.append("ERROR_NOTIFICATION_RECIPIENTS")
        return missing


def create_error_email_content(
    table_name: str,
    error_message: str,
    connection_name: str,
    service_name: str,
    additional_context: Optional[Dict[str, Any]] = None
) -> tuple[str, str]:
    """
    Create HTML and plain text content for error notification email
    
    Args:
        table_name: Name of the table that failed
        error_message: The error message/exception details
        connection_name: Name of the connection (e.g., "Salesforce Production", "Dev Environment")
        service_name: Name of the service (e.g., "Salesforce", "Snowflake")
        additional_context: Additional context information
        
    Returns:
        Tuple of (html_content, plain_text_content)
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    context = additional_context or {}
    company_name = str(context.get("company_name") or "").strip()
    company_display_name = company_name or "Not available"
    
    # Create plain text version
    plain_text = f"""
DATA SYNCHRONIZATION ERROR ALERT

An error occurred during table migration in the DataSyncher system.

ERROR DETAILS:
- Company : {company_display_name}
- Service: {service_name}
-Connection : {connection_name}
-Table : {table_name}
- Timestamp: {timestamp}

ERROR MESSAGE:
{error_message}

RECOMMENDED ACTIONS:
1. Check the table configuration and data integrity
2. Verify connection credentials and permissions
3. Review system logs for additional details
4. Contact the development team if the issue persists

This is an automated notification from the DataSyncher Alert System.
"""

    # Create HTML version
    html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>DataSyncher Error Alert</title>
    <style>
        body {{ margin: 0; padding: 24px; background-color: #f5f5f7; color: #1a1a22; font-family: Arial, sans-serif; }}
        .container {{ max-width: 640px; margin: 0 auto; overflow: hidden; background-color: #ffffff; border: 1px solid #e7e5ec; border-radius: 10px; }}
        .header {{ background-color: #6E2FD5; color: #ffffff; padding: 24px 32px; }}
        .header-table {{ border-collapse: collapse; }}
        .header-logo {{ display: block; width: 24px; height: 24px; margin-right: 16px; }}
        .header h1 {{ margin: 0; color: #ffffff; font-size: 21px; line-height: 1.3; }}
        .content {{ padding: 30px 32px 32px; }}
        .timestamp {{ margin: 0 0 22px; color: #8b8797; font-size: 12px; }}
        .intro {{ margin: 0 0 28px; line-height: 1.5; }}
        .section {{ margin: 0 0 28px; }}
        .section h3 {{ margin: 0 0 12px; color: #1a1a22; font-size: 14px; }}
        .error-details {{ background-color: #ffffff; }}
        .info-table {{ width: 100%; border-collapse: collapse; }}
        .info-table th, .info-table td {{ padding: 4px 0; text-align: left; border-bottom: 1px solid #e7e5ec; font-size: 14px; line-height: 1.2; }}
        .info-table th {{ width: 25%; color: #7f7b8d; font-weight: normal; }}
        .info-table tr:last-child th, .info-table tr:last-child td {{ border-bottom: 0; }}
        .error-message {{ padding: 16px; background-color: #fff3cd; border: 1px solid #ffeaa7; border-radius: 8px; }}
        .error-message pre {{ margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; color: #1a1a22; font-family: Consolas, "Courier New", monospace; font-size: 13px; line-height: 1.5; }}
        .actions {{ padding: 12px 16px; background-color: #EEE6FA; border-radius: 8px; }}
        .actions ul {{ margin: 0; padding: 0; list-style: none; }}
        .actions li {{ padding: 2px 0; line-height: 1.25; }}
        .action-arrow {{ display: inline-block; width: 20px; color: #6E2FD5; font-weight: bold; }}
        .footer {{ padding: 20px 32px; border-top: 1px solid #e7e5ec; color: #9a96a5; font-size: 12px; }}
        .footer p {{ margin: 2px 0; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <table class="header-table" role="presentation">
                <tr>
                    <td><img class="header-logo" src="{DATASYNCHER_LOGO_DATA_URI}" width="24" height="24" alt="DataSyncher"></td>
                    <td><h1>Data Synchronization Error Alert</h1></td>
                </tr>
            </table>
        </div>

        <div class="content">
            <p class="timestamp">{timestamp}</p>
            <p class="intro">An error occurred while migrating a table in the DataSyncher system. Review the details below and take the recommended action.</p>

            <div class="section error-details">
                <h3>Migration details</h3>
                <table class="info-table">
                    <tr><th>Company</th><td>{company_display_name}</td></tr>
                    <tr><th>Service</th><td>{service_name}</td></tr>
                    <tr><th>Connection</th><td>{connection_name}</td></tr>
                    <tr><th>Table</th><td>{table_name}</td></tr>
                </table>
            </div>

            <div class="section">
                <h3>Error message</h3>
                <div class="error-message">
                    <pre>{error_message}</pre>
                </div>
            </div>

            <div class="section">
                <h3>Recommended actions</h3>
                <div class="actions">
                    <ul>
                        <li><span class="action-arrow">→</span>Check the table configuration and data integrity</li>
                        <li><span class="action-arrow">→</span>Verify connection credentials and permissions</li>
                        <li><span class="action-arrow">→</span>Review system logs for additional details</li>
                        <li><span class="action-arrow">→</span>Contact the development team if the issue persists</li>
                    </ul>
                </div>
            </div>
        </div>

        <div class="footer">
            <p>This is an automated notification from the DataSyncher Alert System.</p>
            <p>Generated at: {timestamp}</p>
        </div>
    </div>
</body>
</html>
"""
    
    return html_content, plain_text


def send_email_with_retry(
    config: EmailConfiguration,
    subject: str,
    html_content: str,
    plain_text: str,
    max_retries: int = 3,
    retry_delay: int = 5
) -> bool:
    """
    Send email with retry mechanism
    
    Args:
        config: Email configuration object
        subject: Email subject
        html_content: HTML email content
        plain_text: Plain text email content
        max_retries: Maximum number of retry attempts
        retry_delay: Delay between retries in seconds
        
    Returns:
        True if email sent successfully, False otherwise
    """
    for attempt in range(max_retries):
        try:
            # Create message
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = formataddr((config.from_name, config.username))
            msg['To'] = ', '.join(config.recipients)
            
            # Create plain text and HTML parts
            text_part = MIMEText(plain_text, 'plain')
            html_part = MIMEText(html_content, 'html')
            
            # Attach parts
            msg.attach(text_part)
            msg.attach(html_part)
            
            # Send email
            with smtplib.SMTP(config.host, config.port) as server:
                if config.use_tls:
                    server.starttls()
                server.login(config.username, config.password)
                server.send_message(msg)
            
            logger.info(f"✅ Error notification email sent successfully to {len(config.recipients)} recipients")
            return True
            
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"❌ SMTP Authentication failed (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt == max_retries - 1:
                logger.error("❌ All email sending attempts failed due to authentication issues")
                return False
                
        except smtplib.SMTPException as e:
            logger.error(f"❌ SMTP error (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                logger.info(f"⏳ Retrying email send in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                logger.error("❌ All email sending attempts failed due to SMTP errors")
                return False
                
        except Exception as e:
            logger.error(f"❌ Unexpected error sending email (attempt {attempt + 1}/{max_retries}): {e}")
            logger.error(traceback.format_exc())
            if attempt < max_retries - 1:
                logger.info(f"⏳ Retrying email send in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                logger.error("❌ All email sending attempts failed due to unexpected errors")
                return False
    
    return False


def send_error_notification(
    table_name: str,
    error_message: str,
    connection_name: str,
    service_name: str = "DataSyncher",
    additional_context: Optional[Dict[str, Any]] = None,
    user_id: Optional[int] = None,
    company_id: Optional[int] = None,
    is_consolidated: bool = False
) -> bool:
    """
    Send error notification email for table migration failures

    This is the main function to call when a table migration error occurs.
    It handles email configuration validation, content creation, and sending.

    Args:
        table_name: Name of the table that failed (or "Migration Summary" for consolidated emails)
        error_message: The error message/exception details
        connection_name: Name of the connection (e.g., "Salesforce Production", "Dev Environment")
        service_name: Name of the service (e.g., "Salesforce", "Snowflake")
        additional_context: Additional context information (job_name, user_id, company_id, etc.)
        user_id: User ID to fetch email from database (optional)
        company_id: Company ID to fetch CommunicationSupport emails (optional)
        is_consolidated: Whether this is a consolidated email for multiple table failures

    Returns:
        True if email sent successfully, False otherwise

    Example:
        # Individual table failure with user email and company support emails
        success = send_error_notification(
            table_name="Account",
            error_message="Connection timeout after 30 seconds",
            connection_name="Salesforce Production",
            service_name="Salesforce",
            user_id=456,  # Will fetch user email from database
            company_id=2,  # Will fetch CommunicationSupport emails
            additional_context={
                "job_name": "daily_sync",
                "company_name": "ACME Corp",
                "migration_session_id": "session_789",
                "connection_id": 123
            }
        )

        # Consolidated migration summary
        success = send_error_notification(
            table_name="Migration Summary",
            error_message="Migration completed with 3 failed tables...",
            connection_name="Salesforce Production",
            service_name="Salesforce to Snowflake",
            user_id=456,
            is_consolidated=True,
            additional_context={
                "total_failed_tables": 3,
                "failed_tables_list": ["Account", "Contact", "Opportunity"]
            }
        )
    """
    try:
        # Extract user_id and company_id from additional_context if not provided directly
        if user_id is None and additional_context:
            user_id = additional_context.get("user_id")
        
        if company_id is None and additional_context:
            company_id = additional_context.get("company_id")
        
        # Initialize email configuration with user_id and company_id for database lookup
        config = EmailConfiguration(user_id=user_id, company_id=company_id)
        
        # Check if email is properly configured
        if not config.is_configured():
            missing_config = config.get_missing_config()
            logger.warning(f"⚠️ Email notification not sent - missing configuration: {', '.join(missing_config)}")
            logger.info("💡 To enable email notifications, set the following environment variables:")
            for var in missing_config:
                logger.info(f"   - {var}")
            return False
        
        # Create email subject
        context = dict(additional_context or {})
        
        # Use company name from user details if available, otherwise from context
        if config.user_details and config.user_details.get("company_name"):
            company_name = config.user_details["company_name"]
        else:
            company_name = context.get("company_name")

        # Create subject based on whether it's consolidated or individual table failure
        if is_consolidated:
            # For consolidated emails, include connection name
            subject = f"DataSyncher Alert: {connection_name} Migration Summary"
        else:
            # For individual table failures, include connection name and table
            subject = f"DataSyncher Alert: {connection_name} - {table_name} Migration Failed"
        
        # Create email content
        html_content, plain_text = create_error_email_content(
            table_name=table_name,
            error_message=error_message,
            connection_name=connection_name,
            service_name=service_name,
            additional_context={
                **context,
                "company_name": company_name,
            }
        )
        
        # Send email with retry
        success = send_email_with_retry(
            config=config,
            subject=subject,
            html_content=html_content,
            plain_text=plain_text
        )
        
        if success:
            logger.info(f"✅ Error notification sent for table '{table_name}' failure")
        else:
            logger.error(f"❌ Failed to send error notification for table '{table_name}'")
            
        return success
        
    except Exception as e:
        logger.error(f"❌ Unexpected error in send_error_notification: {e}")
        logger.error(traceback.format_exc())
        return False


def test_email_configuration(user_id: Optional[int] = None) -> bool:
    """
    Test email configuration by sending a test email
    
    Args:
        user_id: Optional user ID to test database email lookup
    
    Returns:
        True if test email sent successfully, False otherwise
    """
    try:
        config = EmailConfiguration(user_id=user_id)
        
        if not config.is_configured():
            missing_config = config.get_missing_config()
            logger.error(f"❌ Email configuration incomplete. Missing: {', '.join(missing_config)}")
            return False
        
        # Send test email
        subject = "🧪 DataSyncher Email Configuration Test"
        html_content = """
        <html>
        <body>
            <h2>✅ Email Configuration Test Successful</h2>
            <p>This is a test email to verify that your DataSyncher email notification system is working correctly.</p>
            <p><strong>Configuration Details:</strong></p>
            <ul>
                <li>SMTP Host: {}</li>
                <li>SMTP Port: {}</li>
                <li>Use TLS: {}</li>
                <li>Recipients: {}</li>
            </ul>
            <p>If you received this email, your error notification system is ready to use!</p>
        </body>
        </html>
        """.format(config.host, config.port, config.use_tls, len(config.recipients))
        
        plain_text = f"""
EMAIL CONFIGURATION TEST SUCCESSFUL

This is a test email to verify that your DataSyncher email notification system is working correctly.

Configuration Details:
- SMTP Host: {config.host}
- SMTP Port: {config.port}
- Use TLS: {config.use_tls}
- Recipients: {len(config.recipients)}

If you received this email, your error notification system is ready to use!
        """
        
        success = send_email_with_retry(
            config=config,
            subject=subject,
            html_content=html_content,
            plain_text=plain_text
        )
        
        if success:
            logger.info("✅ Email configuration test successful!")
        else:
            logger.error("❌ Email configuration test failed!")
            
        return success
        
    except Exception as e:
        logger.error(f"❌ Error testing email configuration: {e}")
        logger.error(traceback.format_exc())
        return False


# Example usage and testing
if __name__ == "__main__":
    # Test the email configuration
    print("Testing email configuration...")
    test_result = test_email_configuration()
    
    if test_result:
        print("\n✅ Email system is ready!")
        
        # Example error notification
        print("\nSending example error notification...")
        send_error_notification(
            table_name="Account",
            error_message="Example error: Connection timeout after 30 seconds",
            connection_id=123,
            service_name="Salesforce",
            additional_context={
                "job_name": "test_migration",
                "user_id": 456,
                "company_name": "Test Company",
                "migration_session_id": "test_session_789"
            }
        )
    else:
        print("\n❌ Email configuration needs to be set up first.")
