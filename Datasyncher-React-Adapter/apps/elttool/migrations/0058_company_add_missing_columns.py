"""
Repair DBs where 0053/0054 only updated migration state but never added columns.

0053 used SeparateDatabaseAndState with empty database_operations for account_status.
0054 only ran UPDATE on updated_at if the column already existed — it never ADDed it.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0057_alter_sourcefilemapping_options_and_more"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                ALTER TABLE company
                    ADD COLUMN IF NOT EXISTS account_status varchar(20) DEFAULT 'active' NOT NULL;
                ALTER TABLE company
                    ALTER COLUMN account_status SET DEFAULT 'active';
                UPDATE company SET account_status = 'active' WHERE account_status IS NULL;

                ALTER TABLE company
                    ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone;
                UPDATE company SET updated_at = COALESCE(created_at, NOW()) WHERE updated_at IS NULL;
                ALTER TABLE company ALTER COLUMN updated_at SET DEFAULT NOW();
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
