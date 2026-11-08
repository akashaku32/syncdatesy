from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0110_sourcetableselection_filename_date_format"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                ALTER TABLE source_file_mapping
                    ALTER COLUMN created_at SET DEFAULT NOW(),
                    ALTER COLUMN updated_at SET DEFAULT NOW();
            """,
            reverse_sql="""
                ALTER TABLE source_file_mapping
                    ALTER COLUMN created_at DROP DEFAULT,
                    ALTER COLUMN updated_at DROP DEFAULT;
            """,
        ),
    ]
