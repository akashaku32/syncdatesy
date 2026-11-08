from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0077_sourcetableselection_selected_fields"),
    ]

    operations = [
        migrations.RenameField(
            model_name="sourcetableselection",
            old_name="sftp_output_file_name",
            new_name="output_file_name",
        ),
        migrations.RenameField(
            model_name="sourcetableselection",
            old_name="sftp_target_folder",
            new_name="target_folder",
        ),
        migrations.RenameField(
            model_name="sourcetableselection",
            old_name="sftp_file_format",
            new_name="file_format",
        ),
        migrations.RenameField(
            model_name="sourcetableselection",
            old_name="sftp_csv_delimiter",
            new_name="csv_delimiter",
        ),
        migrations.RenameField(
            model_name="sourcetableselection",
            old_name="sftp_csv_quote_char",
            new_name="csv_quote_char",
        ),
        migrations.RenameField(
            model_name="sourcetableselection",
            old_name="sftp_add_utc_timestamp",
            new_name="add_utc_timestamp",
        ),
        migrations.RunSQL(
            sql=(
                'ALTER TABLE "source_table_selection" '
                'DROP COLUMN IF EXISTS "file_export_settings";'
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
