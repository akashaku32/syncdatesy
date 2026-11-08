from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0042_remove_existing_tbl_itm_tbl_delete_existing_tbl_hdr_and_more"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "ALTER TABLE log DROP COLUMN IF EXISTS status_code;",
                "ALTER TABLE migration_record DROP COLUMN IF EXISTS status_code;",
            ],
            reverse_sql=[
                "ALTER TABLE log ADD COLUMN IF NOT EXISTS status_code INTEGER;",
                "ALTER TABLE migration_record ADD COLUMN IF NOT EXISTS status_code INTEGER;",
            ],
        ),
    ]
