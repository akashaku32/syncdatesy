"""Canonical NetSuite SourceAttribute definitions (form + API schema)."""

NETSUITE_SOURCE_NAME = "NetSuite"

def _connect_tba_visibility():
    return {
        "_visibility_all_of": [
            {"depend_on": "connection_method", "dependency": "suiteanalytics"},
            {"depend_on": "connect_auth_type", "dependency": "tba"},
        ],
    }


def _connect_oauth_visibility():
    return {
        "_visibility_all_of": [
            {"depend_on": "connection_method", "dependency": "suiteanalytics"},
            {"depend_on": "connect_auth_type", "dependency": "oauth2_m2m"},
        ],
    }


def _connect_email_visibility():
    return {
        "_visibility_all_of": [
            {"depend_on": "connection_method", "dependency": "suiteanalytics"},
            {"depend_on": "connect_auth_type", "dependency": "email_password"},
        ],
    }


NETSUITE_SOURCE_ATTRIBUTES = [
    {
        "attribute_name": "destination_schema",
        "input_type": "CharField",
        "label": "Destination Schema",
        "required": True,
        "is_visible": True,
        "read_only": True,
    },
    {
        "attribute_name": "connection_method",
        "input_type": "ChoiceField",
        "label": "Connection Method",
        "required": True,
        "is_visible": True,
        "read_only": False,
        "choices": [
            {"value": "suiteql_rest", "display": "SuiteQL (REST API)"},
            {"value": "suiteanalytics", "display": "SuiteAnalytics Connect (ODBC)"},
        ],
    },
    {
        "attribute_name": "account_id",
        "input_type": "CharField",
        "label": "Account ID",
        "placeholder": "e.g. 1234567 or 1234567_SB1",
        "required": True,
        "is_visible": True,
        "read_only": False,
        "description": "Company ID from Setup → Company → Company Information (same for both methods).",
    },
    {
        "attribute_name": "oauth_scope",
        "input_type": "CharField",
        "label": "OAuth 2.0 Scope",
        "default_value": "rest_webservices",
        "placeholder": "rest_webservices",
        "required": True,
        "is_visible": False,
        "read_only": False,
        "depend_on": "connection_method",
        "dependency_value": "suiteql_rest",
        "description": "Must match scopes enabled on the integration record (typically rest_webservices).",
    },
    {
        "attribute_name": "client_id",
        "input_type": "CharField",
        "label": "Client ID (Integration)",
        "required": True,
        "is_visible": False,
        "read_only": False,
        "depend_on": "connection_method",
        "dependency_value": "suiteql_rest",
    },
    {
        "attribute_name": "certificate_id",
        "input_type": "CharField",
        "label": "Certificate ID (M2M)",
        "required": True,
        "is_visible": False,
        "read_only": False,
        "depend_on": "connection_method",
        "dependency_value": "suiteql_rest",
    },
    {
        "attribute_name": "private_key",
        "input_type": "TextField",
        "label": "Private Key (PEM)",
        "required": True,
        "is_visible": False,
        "read_only": False,
        "depend_on": "connection_method",
        "dependency_value": "suiteql_rest",
    },
    {
        "attribute_name": "connect_auth_type",
        "input_type": "ChoiceField",
        "label": "Connect Authentication",
        "required": True,
        "is_visible": False,
        "read_only": False,
        "default_value": "tba",
        "depend_on": "connection_method",
        "dependency_value": "suiteanalytics",
        "choices": [
            {"value": "tba", "display": "Token-based (Integration tokens)"},
            {"value": "oauth2_m2m", "display": "OAuth 2.0 M2M (Certificate)"},
            {"value": "email_password", "display": "NetSuite email & password"},
        ],
        "description": "SuiteAnalytics Connect (NetSuite2.com ODBC) auth; different from SuiteQL REST credentials.",
    },
    {
        "attribute_name": "role_id",
        "input_type": "CharField",
        "label": "Role ID",
        "placeholder": "e.g. 3",
        "required": True,
        "is_visible": False,
        "read_only": False,
        "depend_on": "connection_method",
        "dependency_value": "suiteanalytics",
        "description": "Internal role ID from SuiteAnalytics Connect driver download / Your Configuration.",
    },
    {
        "attribute_name": "consumer_key",
        "input_type": "CharField",
        "label": "Consumer Key",
        "required": True,
        "is_visible": False,
        "read_only": False,
        "choices": _connect_tba_visibility(),
    },
    {
        "attribute_name": "consumer_secret",
        "input_type": "PasswordInput",
        "label": "Consumer Secret",
        "required": True,
        "is_visible": False,
        "read_only": False,
        "widget": "PasswordInput",
        "choices": _connect_tba_visibility(),
    },
    {
        "attribute_name": "token_id",
        "input_type": "CharField",
        "label": "Token ID",
        "required": True,
        "is_visible": False,
        "read_only": False,
        "choices": _connect_tba_visibility(),
    },
    {
        "attribute_name": "token_secret",
        "input_type": "PasswordInput",
        "label": "Token Secret",
        "required": True,
        "is_visible": False,
        "read_only": False,
        "widget": "PasswordInput",
        "choices": _connect_tba_visibility(),
    },
    {
        "attribute_name": "connect_client_id",
        "input_type": "CharField",
        "label": "Client ID (Connect OAuth 2.0)",
        "required": True,
        "is_visible": False,
        "read_only": False,
        "choices": _connect_oauth_visibility(),
    },
    {
        "attribute_name": "connect_certificate_id",
        "input_type": "CharField",
        "label": "Certificate ID (Connect OAuth 2.0)",
        "required": True,
        "is_visible": False,
        "read_only": False,
        "choices": _connect_oauth_visibility(),
    },
    {
        "attribute_name": "connect_private_key",
        "input_type": "TextField",
        "label": "Private Key (Connect OAuth 2.0 PEM)",
        "required": True,
        "is_visible": False,
        "read_only": False,
        "choices": _connect_oauth_visibility(),
    },
    {
        "attribute_name": "logon_email",
        "input_type": "CharField",
        "label": "NetSuite Login Email",
        "required": True,
        "is_visible": False,
        "read_only": False,
        "choices": _connect_email_visibility(),
    },
    {
        "attribute_name": "connect_password",
        "input_type": "PasswordInput",
        "label": "NetSuite Password",
        "required": True,
        "is_visible": False,
        "read_only": False,
        "widget": "PasswordInput",
        "choices": _connect_email_visibility(),
    },
]
