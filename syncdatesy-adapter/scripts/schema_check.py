import os
import django
from django.apps import apps
from django.db import connection

# Allow running as a standalone script or inside manage.py shell
if not os.environ.get("DJANGO_SETTINGS_MODULE"):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "datasyncher.settings.developer")
    django.setup()

SYSTEM_TABLES = {
    "django_migrations",
    "django_content_type",
    "django_session",
    "auth_group_permissions",
    "auth_permission",
    "user_groups",
    "user_permissions",
}


def get_db_schema():
    schema = {}
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
            """
        )
        for table, column in cursor.fetchall():
            if table in SYSTEM_TABLES:
                continue
            schema.setdefault(table, set()).add(column)
    return schema


def get_django_schema():
    schema = {}
    for model in apps.get_models():
        table = model._meta.db_table
        columns = set()

        for field in model._meta.get_fields():
            # Skip M2M fields; they live in separate tables
            if field.many_to_many:
                continue
            if hasattr(field, "column") and field.column:
                columns.add(field.column)

        schema[table] = columns
    return schema


def compare_schema():
    db_schema = get_db_schema()
    django_schema = get_django_schema()

    print("\n===== SCHEMA COMPARISON =====\n")

    missing_tables = set(django_schema.keys()) - set(db_schema.keys())
    print("Missing Tables:", missing_tables if missing_tables else "None")

    extra_tables = set(db_schema.keys()) - set(django_schema.keys())
    print("Extra Tables:", extra_tables if extra_tables else "None")

    print("\nColumn Differences:\n")

    for table in django_schema:
        if table not in db_schema:
            continue

        model_cols = django_schema[table]
        db_cols = db_schema[table]

        missing_cols = model_cols - db_cols
        extra_cols = db_cols - model_cols

        if missing_cols or extra_cols:
            print(f"TABLE: {table}")
            if missing_cols:
                print("   Missing:", missing_cols)
            if extra_cols:
                print("   Extra:", extra_cols)

    print("\nDone\n")


compare_schema()
