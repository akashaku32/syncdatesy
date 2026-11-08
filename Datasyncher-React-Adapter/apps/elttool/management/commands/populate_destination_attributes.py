from django.core.management.base import BaseCommand
from django.db.models import Max
from apps.elttool.models import DestinationModel, DestinationAttribute


def _attribute_choices_payload(attr: dict):
    """Store ChoiceField options and/or visibility metadata in choices JSON."""
    visibility_any_of = attr.get("visibility_any_of")
    visibility_all_of = attr.get("visibility_all_of")
    field_choices = attr.get("choices")
    if visibility_any_of or visibility_all_of:
        payload = {}
        if visibility_any_of:
            payload["_visibility_any_of"] = visibility_any_of
        if visibility_all_of:
            payload["_visibility_all_of"] = visibility_all_of
        if field_choices:
            payload["_field_choices"] = field_choices
        return payload
    return field_choices


# ✅ NAME-based structure (your original style, but enhanced)
destinations_data = [
    {
        "name": "Snowflake",
        "model_defaults": {
            "is_file_based": False,
            "supports_notification_groups": False,
        },
        "attributes": [
            {"attribute_name": "account", "input_type": "CharField", "label": "Account", "required": True, "is_visible": True, "read_only": True},
            {"attribute_name": "user", "input_type": "CharField", "label": "Username", "required": True, "is_visible": True, "read_only": True},

            {
                "attribute_name": "authentication_type",
                "input_type": "ChoiceField",
                "label": "Authentication Type",
                "choices": [
                    {"value": "password", "display": "Password"},
                    {"value": "key_pair", "display": "Key Pair Authentication"}
                ],
                "required": True,
                "is_visible": True,
                "read_only": False
            },
            {
                "attribute_name": "password",
                "input_type": "PasswordInput",
                "label": "Password",
                "widget": "PasswordInput",
                "required": False,
                "is_visible": False,
                "depend_on": "authentication_type",
                "dependency": "password",
                "read_only": False
            },
            {
                "attribute_name": "passphrase",
                "input_type": "CharField",
                "label": "Private Key Passphrase (Optional)",
                "required": False,
                "is_visible": False,
                "depend_on": "authentication_type",
                "dependency": "key_pair",
                "read_only": False
            },
            {
                "attribute_name": "private_key",
                "input_type": "TextField",
                "label": "Private Key (PEM format)",
                "required": False,
                "is_visible": False,
                "depend_on": "authentication_type",
                "dependency": "key_pair",
                "read_only": False
            },
            {
                "attribute_name": "public_key",
                "input_type": "TextField",
                "label": "Public Key (PEM format)",
                "required": False,
                "is_visible": False,
                "depend_on": "authentication_type",
                "dependency": "key_pair",
                "read_only": False
            },
            {"attribute_name": "database", "input_type": "CharField", "label": "Database", "required": True, "is_visible": True, "read_only": True},
            {"attribute_name": "warehouse", "input_type": "CharField", "label": "Warehouse", "required": True, "is_visible": True, "read_only": False},
        ]
    },

    {
        "name": "Databricks",
        "model_defaults": {
            "is_file_based": False,
            "supports_notification_groups": False,
        },
        "attributes": [
            {
                "attribute_name": "server_hostname",
                "input_type": "CharField",
                "label": "Server Hostname",
                "required": True,
                "is_visible": True,
                "read_only": True,
            },
            {
                "attribute_name": "http_path",
                "input_type": "CharField",
                "label": "HTTP Path (SQL warehouse or all-purpose compute)",
                "required": True,
                "is_visible": True,
                "read_only": True,
            },
            {
                "attribute_name": "access_token",
                "input_type": "PasswordInput",
                "label": "Access Token",
                "widget": "PasswordInput",
                "required": True,
                "is_visible": True,
                "read_only": False,
            },
            {
                "attribute_name": "catalog",
                "input_type": "CharField",
                "label": "Catalog",
                "required": True,
                "is_visible": True,
                "read_only": True,
            },
            {
                "attribute_name": "create_dedicated_dbx_serverless_job",
                "input_type": "ChoiceField",
                "label": "Dynamics F&O: dedicated serverless Lakeflow job",
                "choices": [
                    {"value": "false", "display": "Shared workspace job (default)"},
                    {
                        "value": "true",
                        "display": "Dedicated serverless job for this destination",
                    },
                ],
                "required": False,
                "is_visible": True,
                "read_only": False,
            },
        ]
    },

    {
        "name": "Salesforce",
        "model_defaults": {
            "is_file_based": False,
            "supports_notification_groups": False,
        },
        "attributes": [
            {
                "attribute_name": "authentication_type",
                "input_type": "ChoiceField",
                "label": "Authentication Type",
                "choices": [
                    {"value": "oauth", "display": "OAuth (User Authorization)"},
                    {"value": "jwt_bearer", "display": "JWT Bearer (Integration User)"},
                ],
                "required": True,
                "is_visible": True,
                "read_only": False,
            },
            {"attribute_name": "CLIENT_ID", "input_type": "CharField", "label": "Consumer Key (Client ID)", "required": True, "is_visible": True, "read_only": False},
            {
                "attribute_name": "CLIENT_SECRET",
                "input_type": "CharField",
                "label": "Client Secret",
                "required": False,
                "is_visible": False,
                "depend_on": "authentication_type",
                "dependency": "oauth",
                "read_only": False,
            },
            {"attribute_name": "CUSTOM_DOMAIN", "input_type": "CharField", "label": "Custom Domain", "required": False, "is_visible": True, "read_only": False},
            {
                "attribute_name": "integration_username",
                "input_type": "CharField",
                "label": "Integration Username",
                "required": False,
                "is_visible": False,
                "depend_on": "authentication_type",
                "dependency": "jwt_bearer",
                "read_only": False,
            },
            {
                "attribute_name": "private_key",
                "input_type": "TextField",
                "label": "Private Key (PEM format)",
                "required": False,
                "is_visible": False,
                "depend_on": "authentication_type",
                "dependency": "jwt_bearer",
                "read_only": False,
            },
            {
                "attribute_name": "passphrase",
                "input_type": "CharField",
                "label": "Private Key Passphrase (Optional)",
                "required": False,
                "is_visible": False,
                "depend_on": "authentication_type",
                "dependency": "jwt_bearer",
                "read_only": False,
            },
        ]
    },

    {
        "name": "SalesforceSandbox",
        "model_defaults": {
            "is_file_based": False,
            "supports_notification_groups": False,
        },
        "attributes": [
            {
                "attribute_name": "authentication_type",
                "input_type": "ChoiceField",
                "label": "Authentication Type",
                "choices": [
                    {"value": "oauth", "display": "OAuth (User Authorization)"},
                    {"value": "jwt_bearer", "display": "JWT Bearer (Integration User)"},
                ],
                "required": True,
                "is_visible": True,
                "read_only": False,
            },
            {"attribute_name": "CLIENT_ID", "input_type": "CharField", "label": "Consumer Key (Client ID)", "required": True, "is_visible": True, "read_only": False},
            {
                "attribute_name": "CLIENT_SECRET",
                "input_type": "CharField",
                "label": "Client Secret",
                "required": False,
                "is_visible": False,
                "depend_on": "authentication_type",
                "dependency": "oauth",
                "read_only": False,
            },
            {"attribute_name": "CUSTOM_DOMAIN", "input_type": "CharField", "label": "Custom Domain", "required": False, "is_visible": True, "read_only": False},
            {
                "attribute_name": "integration_username",
                "input_type": "CharField",
                "label": "Integration Username",
                "required": False,
                "is_visible": False,
                "depend_on": "authentication_type",
                "dependency": "jwt_bearer",
                "read_only": False,
            },
            {
                "attribute_name": "private_key",
                "input_type": "TextField",
                "label": "Private Key (PEM format)",
                "required": False,
                "is_visible": False,
                "depend_on": "authentication_type",
                "dependency": "jwt_bearer",
                "read_only": False,
            },
            {
                "attribute_name": "passphrase",
                "input_type": "CharField",
                "label": "Private Key Passphrase (Optional)",
                "required": False,
                "is_visible": False,
                "depend_on": "authentication_type",
                "dependency": "jwt_bearer",
                "read_only": False,
            },
        ]
    },
    # Snowflake (Source) -> SFTP (Destination): export Snowflake views/tables to files and upload via SFTP.
    {
        "name": "SFTP",
        "model_defaults": {
            "is_file_based": True,
            "supports_notification_groups": True,
            "path_label": "Remote Folder Path",
            "min_count": 10000,
            "max_count": 100000,
            "supported_formats": ["csv","excel", "json", "parquet" ],
            "default_format": "csv",
            "csv_defaults": {
                "delimiter": ",",
                "quote_char": '"',
            },
        },
        "attributes": [
            # Core SFTP connection fields
            {"attribute_name": "sftp_host", "input_type": "CharField", "label": "SFTP Server Host Address", "required": True, "is_visible": True, "read_only": False},
            {"attribute_name": "sftp_port", "input_type": "CharField", "label": "SFTP Server Port", "required": True, "is_visible": True, "read_only": False},
            {"attribute_name": "sftp_username", "input_type": "CharField", "label": "SFTP Server Username", "required": True, "is_visible": True, "read_only": False},
            # Auth method after username (as requested)
            {
                "attribute_name": "authentication_type",
                "input_type": "ChoiceField",
                "label": "Authentication Type",
                "choices": [
                    {"value": "password", "display": "Password"},
                    {"value": "key_pair", "display": "Key Pair Authentication"}
                ],
                "required": True,
                "is_visible": True,
                "read_only": False
            },
            {
                "attribute_name": "sftp_password",
                "input_type": "PasswordInput",
                "label": "SFTP Server Password",
                "widget": "PasswordInput",
                "required": False,
                "is_visible": False,
                "depend_on": "authentication_type",
                "dependency": "password",
                "read_only": False
            },
            {
                "attribute_name": "sftp_passphrase",
                "input_type": "CharField",
                "label": "Private Key Passphrase (Optional)",
                "required": False,
                "is_visible": False,
                "depend_on": "authentication_type",
                "dependency": "key_pair",
                "read_only": False
            },
            {
                "attribute_name": "sftp_private_key",
                "input_type": "TextField",
                "label": "Private Key (PEM format)",
                "required": False,
                "is_visible": False,
                "depend_on": "authentication_type",
                "dependency": "key_pair",
                "read_only": False
            },
            # Remote folder path to upload files
            {"attribute_name": "root_folder", "input_type": "CharField", "label": "Root Folder", "required": True, "is_visible": True, "read_only": False},
        ]
    },
    {
        "name": "SharePoint",
        "model_defaults": {
            "is_file_based": True,
            "supports_notification_groups": True,
            "path_label": "Sharepoint Folder Path",
            "min_count": 10000,
            "max_count": 100000,
            "supported_formats": ["csv","excel", "json", "parquet" ],
            "default_format": "csv",
            "csv_defaults": {
                "delimiter": ",",
                "quote_char": '"',
            },
        },
        "attributes": [
            {
                "attribute_name": "auth_type",
                "input_type": "ChoiceField",
                "label": "Authentication Type",
                "choices": [
                    {"value": "client_secret", "display": "Customer-registered app - Client secret"},
                    {"value": "client_certificate", "display": "Customer-registered app - Client certificate"},
                ],
                "required": True,
                "is_visible": True,
                "read_only": False,
            },
            {"attribute_name": "tenant_id", "input_type": "CharField", "label": "Tenant ID", "required": True, "is_visible": True, "read_only": False},
            {"attribute_name": "client_id", "input_type": "CharField", "label": "Client ID", "required": True, "is_visible": True, "read_only": False},
            {
                "attribute_name": "client_secret",
                "input_type": "PasswordInput",
                "label": "Client Secret",
                "widget": "PasswordInput",
                "required": False,
                "is_visible": False,
                "depend_on": "auth_type",
                "dependency": "client_secret",
                "read_only": False,
            },
            {
                "attribute_name": "client_certificate_file",
                "input_type": "CharField",
                "label": "Client Certificate File",
                "widget": "FileInput",
                "required": False,
                "is_visible": False,
                "depend_on": "auth_type",
                "dependency": "client_certificate",
                "read_only": False,
            },
            {
                "attribute_name": "certificate_password",
                "input_type": "PasswordInput",
                "label": "Certificate Password (Optional)",
                "widget": "PasswordInput",
                "required": False,
                "is_visible": False,
                "depend_on": "auth_type",
                "dependency": "client_certificate",
                "read_only": False,
            },
            {"attribute_name": "sharepoint_site_url", "input_type": "CharField", "label": "SharePoint Site URL", "required": True, "is_visible": True, "read_only": False},
            {"attribute_name": "document_library", "input_type": "CharField", "label": "Document Library", "required": True, "is_visible": True, "read_only": False},
            {"attribute_name": "root_folder", "input_type": "CharField", "label": "Root Folder", "required": False, "is_visible": True, "read_only": False},
        ]
    },
    {
        "name": "GoogleDrive",
        "model_defaults": {
            "is_file_based": True,
            "supports_notification_groups": True,
            "path_label": "Google Drive Folder Path",
            "min_count": 10000,
            "max_count": 100000,
            "supported_formats": ["csv","excel", "json", "parquet" ],
            "default_format": "csv",
            "csv_defaults": {
                "delimiter": ",",
                "quote_char": '"',
            },
        },
        "attributes": [
            {"attribute_name": "CLIENT_ID", "input_type": "CharField", "label": "Client ID", "required": True, "is_visible": True, "read_only": False},
            {"attribute_name": "CLIENT_SECRET", "input_type": "PasswordInput", "label": "Client Secret", "widget": "PasswordInput", "required": True, "is_visible": True, "read_only": False},
            {"attribute_name": "folder_name", "input_type": "CharField", "label": "Folder Name", "required": True, "is_visible": True, "read_only": False},
            {"attribute_name": "folder_id", "input_type": "CharField", "label": "Folder ID", "required": False, "is_visible": True, "read_only": False},
        ]
    },
    {
        "name": "AmazonS3",
        "model_defaults": {
            "is_file_based": True,
            "supports_notification_groups": True,
            "path_label": "S3 Folder Path",
            "min_count": 10000,
            "max_count": 100000,
            "supported_formats": ["csv","excel", "json", "parquet" ],
            "default_format": "csv",
            "csv_defaults": {
                "delimiter": ",",
                "quote_char": '"',
            },
        },
        "attributes": [
            {"attribute_name": "s3_bucket", "input_type": "CharField", "label": "S3 Bucket", "required": True, "is_visible": True, "read_only": False},
            {"attribute_name": "aws_access_key_id", "input_type": "CharField", "label": "AWS Access Key ID", "required": True, "is_visible": True, "read_only": False},
            {"attribute_name": "aws_secret_access_key", "input_type": "PasswordInput", "label": "AWS Secret Access Key", "widget": "PasswordInput", "required": True, "is_visible": True, "read_only": False},
            {"attribute_name": "base_folder_path", "input_type": "CharField", "label": "Base Folder Path", "required": False, "is_visible": True, "read_only": False},
        ]
    },
    # Snowflake (Source) -> Azure Data Lake Storage Gen2 (Destination): export Snowflake data to files and upload to ADLS.
    {
        "name": "AzureDataLakeStorage",
        "model_defaults": {
            "is_file_based": True,
            "supports_notification_groups": True,
            "path_label": "Folder Path",
            "min_count": 10000,
            "max_count": 100000,
            "supported_formats": ["csv", "excel","json", "parquet"],
            "default_format": "csv",
            "csv_defaults": {
                "delimiter": ",",
                "quote_char": '"',
            },
        },
        "attributes": [
            {"attribute_name": "tenant_id", "input_type": "CharField", "label": "Tenant ID", "required": True, "is_visible": True, "read_only": False},
            {"attribute_name": "client_id", "input_type": "CharField", "label": "Client ID", "required": True, "is_visible": True, "read_only": False},
            {"attribute_name": "client_secret", "input_type": "PasswordInput", "label": "Client Secret", "widget": "PasswordInput", "required": True, "is_visible": True, "read_only": False},
            {"attribute_name": "storage_account_name", "input_type": "CharField", "label": "Storage Account Name", "required": True, "is_visible": True, "read_only": False},
            {"attribute_name": "container_name", "input_type": "CharField", "label": "Container Name", "required": True, "is_visible": True, "read_only": False},
        ]
    },
]


class Command(BaseCommand):
    help = 'Populate DestinationAttribute (name-based, ordered, clear-safe)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear-existing',
            action='store_true',
            help='Delete all existing DestinationAttributes before insert'
        )
        parser.add_argument(
            '--destination',
            action='append',
            default=None,
            help='Only populate the given destination name (repeatable). Example: --destination SFTP'
        )
        parser.add_argument(
            '--only-sftp',
            action='store_true',
            help='Shortcut for --destination SFTP'
        )
        parser.add_argument(
            '--only-sharepoint',
            action='store_true',
            help='Shortcut for --destination SharePoint'
        )
        parser.add_argument(
            '--only-googledrive',
            action='store_true',
            help='Shortcut for --destination GoogleDrive'
        )
        parser.add_argument(
            '--only-amazons3',
            action='store_true',
            help='Shortcut for --destination AmazonS3'
        )
        parser.add_argument(
            '--only-adls',
            action='store_true',
            help='Shortcut for --destination AzureDataLakeStorage'
        )
        parser.add_argument(
            '--only-databricks',
            action='store_true',
            help='Shortcut for --destination Databricks'
        )
        parser.add_argument(
            '--clear-selected-existing',
            action='store_true',
            help='Delete DestinationAttributes for the selected destination(s) before insert'
        )
    def handle(self, *args, **options):
        clear_existing = options.get('clear_existing')
        only_sftp = options.get('only_sftp')
        only_sharepoint = options.get('only_sharepoint')
        only_googledrive = options.get('only_googledrive')
        only_amazons3 = options.get('only_amazons3')
        only_adls = options.get('only_adls')
        only_databricks = options.get('only_databricks')
        selected = options.get('destination') or []
        clear_selected_existing = options.get('clear_selected_existing')
        if only_sftp:
            selected = ["SFTP"]
        elif only_sharepoint:
            selected = ["SharePoint"]
        elif only_googledrive:
            selected = ["GoogleDrive"]
        elif only_amazons3:
            selected = ["AmazonS3"]
        elif only_adls:
            selected = ["AzureDataLakeStorage"]
        elif only_databricks:
            selected = ["Databricks"]
        destination_aliases = {
            "google_drive": "GoogleDrive",
            "google drive": "GoogleDrive",
            "googledrive": "GoogleDrive",
            "adls": "AzureDataLakeStorage",
            "azure_data_lake_storage": "AzureDataLakeStorage",
            "azure data lake storage": "AzureDataLakeStorage",
            "azuredatalakestorage": "AzureDataLakeStorage",
        }
        selected_set = {
            destination_aliases.get(str(name).strip().lower(), str(name).strip())
            for name in selected
            if str(name).strip()
        }
        if clear_existing:
            count = DestinationAttribute.objects.count()
            DestinationAttribute.objects.all().delete()
            self.stdout.write(self.style.WARNING(f'Cleared {count} attributes'))

        for destination_data in destinations_data:
            destination_name = destination_data["name"]

            if selected_set and destination_name not in selected_set:
                continue

            destination_obj, _ = DestinationModel.objects.get_or_create(name=destination_name)
            model_defaults = destination_data.get("model_defaults") or {}
            model_update_fields = []
            for field_name, value in model_defaults.items():
                if getattr(destination_obj, field_name, None) != value:
                    setattr(destination_obj, field_name, value)
                    model_update_fields.append(field_name)
            if model_update_fields:
                destination_obj.save(update_fields=model_update_fields)
            existing_attr_count = DestinationAttribute.objects.filter(dst=destination_obj).count()
            if clear_selected_existing and existing_attr_count:
                self.stdout.write(self.style.WARNING(f'Clearing {existing_attr_count} attributes for {destination_name}'))
            elif existing_attr_count:
                self.stdout.write(
                    self.style.WARNING(
                        f'Resyncing {existing_attr_count} existing attributes for {destination_name} to match file order'
                    )
                )
            if existing_attr_count:
                DestinationAttribute.objects.filter(dst=destination_obj).delete()

            self.stdout.write(f'\n🔹 Processing {destination_name}')

            next_dst_attr_id = (DestinationAttribute.objects.aggregate(max_id=Max('dst_attr_id')).get('max_id') or 0) + 1

            for order, attr in enumerate(destination_data["attributes"], start=1):

                input_type = attr["input_type"]
                if input_type == "PasswordInput":
                    input_type = DestinationAttribute.PASSWORD_FIELD
                elif input_type == "TextField":
                    input_type = DestinationAttribute.TEXTAREA_FIELD
                attribute_name = attr["attribute_name"]
                defaults = {
                    "input_type": input_type,
                    "label": attr["label"],
                    "required": attr.get("required", True),
                    "is_visible": attr.get("is_visible", True),
                    "depend_on": attr.get("depend_on"),
                    "dependency": attr.get("dependency"),
                    "widget": attr.get("widget"),
                    "choices": _attribute_choices_payload(attr),
                    "read_only": attr.get("read_only", False),
                }
                DestinationAttribute.objects.create(
                    dst_attr_id=next_dst_attr_id,
                    dst=destination_obj,
                    attribute_name=attribute_name,
                    **defaults,
                )
                next_dst_attr_id += 1

                self.stdout.write(f'  ✅ ({order}) {attr["attribute_name"]}')

        self.stdout.write(self.style.SUCCESS("\n🎉 Done: Destination attributes loaded"))
