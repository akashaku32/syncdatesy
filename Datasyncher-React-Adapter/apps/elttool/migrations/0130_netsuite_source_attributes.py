"""Backfill NetSuite SourceAttribute rows for the connector authorization form."""

from django.db import migrations

NETSUITE_SOURCE_NAME = "NetSuite"

NETSUITE_ATTRIBUTES = [
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
            {"value": "suiteql_rest", "display": "SuiteQL (REST)"},
            {"value": "suiteanalytics", "display": "SuiteAnalytics Connect"},
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
    },
    {
        "attribute_name": "client_id",
        "input_type": "CharField",
        "label": "Client ID (Integration)",
        "required": False,
        "is_visible": False,
        "read_only": False,
        "depend_on": "connection_method",
        "dependency_value": "suiteql_rest",
    },
    {
        "attribute_name": "certificate_id",
        "input_type": "CharField",
        "label": "Certificate ID",
        "required": False,
        "is_visible": False,
        "read_only": False,
        "depend_on": "connection_method",
        "dependency_value": "suiteql_rest",
    },
    {
        "attribute_name": "private_key",
        "input_type": "TextField",
        "label": "Private Key (PEM)",
        "required": False,
        "is_visible": False,
        "read_only": False,
        "depend_on": "connection_method",
        "dependency_value": "suiteql_rest",
    },
    {
        "attribute_name": "role_id",
        "input_type": "CharField",
        "label": "Role ID",
        "required": False,
        "is_visible": False,
        "read_only": False,
        "depend_on": "connection_method",
        "dependency_value": "suiteanalytics",
    },
    {
        "attribute_name": "consumer_key",
        "input_type": "CharField",
        "label": "Consumer Key",
        "required": False,
        "is_visible": False,
        "read_only": False,
        "depend_on": "connection_method",
        "dependency_value": "suiteanalytics",
    },
    {
        "attribute_name": "consumer_secret",
        "input_type": "PasswordInput",
        "label": "Consumer Secret",
        "required": False,
        "is_visible": False,
        "read_only": False,
        "widget": "PasswordInput",
        "depend_on": "connection_method",
        "dependency_value": "suiteanalytics",
    },
    {
        "attribute_name": "token_id",
        "input_type": "CharField",
        "label": "Token ID",
        "required": False,
        "is_visible": False,
        "read_only": False,
        "depend_on": "connection_method",
        "dependency_value": "suiteanalytics",
    },
    {
        "attribute_name": "token_secret",
        "input_type": "PasswordInput",
        "label": "Token Secret",
        "required": False,
        "is_visible": False,
        "read_only": False,
        "widget": "PasswordInput",
        "depend_on": "connection_method",
        "dependency_value": "suiteanalytics",
    },
]


def _create_netsuite_attributes(apps, schema_editor):
    SourceModel = apps.get_model("elttool", "SourceModel")
    SourceAttribute = apps.get_model("elttool", "SourceAttribute")

    try:
        source = SourceModel.objects.get(name=NETSUITE_SOURCE_NAME)
    except SourceModel.DoesNotExist:
        return

    max_id = (
        SourceAttribute.objects.order_by("-src_attr_id")
        .values_list("src_attr_id", flat=True)
        .first()
        or 0
    )
    next_id = max_id + 1

    for attribute_data in NETSUITE_ATTRIBUTES:
        if SourceAttribute.objects.filter(
            src=source, attribute_name=attribute_data["attribute_name"]
        ).exists():
            continue

        kwargs = {
            "src_attr_id": next_id,
            "src": source,
            "attribute_name": attribute_data["attribute_name"],
            "input_type": attribute_data["input_type"],
            "label": attribute_data["label"],
            "required": attribute_data.get("required", True),
            "is_visible": attribute_data.get("is_visible", True),
            "read_only": attribute_data.get("read_only", False),
            "depend_on": attribute_data.get("depend_on"),
            "dependency_value": attribute_data.get("dependency_value"),
            "choices": attribute_data.get("choices"),
            "widget": attribute_data.get("widget"),
            "placeholder": attribute_data.get("placeholder"),
        }
        SourceAttribute.objects.create(**kwargs)
        next_id += 1


def _remove_netsuite_attributes(apps, schema_editor):
    SourceModel = apps.get_model("elttool", "SourceModel")
    SourceAttribute = apps.get_model("elttool", "SourceAttribute")
    try:
        source = SourceModel.objects.get(name=NETSUITE_SOURCE_NAME)
    except SourceModel.DoesNotExist:
        return
    names = [a["attribute_name"] for a in NETSUITE_ATTRIBUTES]
    SourceAttribute.objects.filter(src=source, attribute_name__in=names).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0129_netsuite_snowflake_databricks_compatibility"),
    ]

    operations = [
        migrations.RunPython(_create_netsuite_attributes, _remove_netsuite_attributes),
    ]
