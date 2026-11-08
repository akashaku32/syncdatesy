from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0074_source_table_selection_updated_by_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourcetableselection",
            name="sftp_output_file_name",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="sourcetableselection",
            name="sftp_target_folder",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="sourcetableselection",
            name="sftp_file_format",
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.AddField(
            model_name="sourcetableselection",
            name="sftp_csv_delimiter",
            field=models.CharField(blank=True, max_length=5, null=True),
        ),
        migrations.AddField(
            model_name="sourcetableselection",
            name="sftp_csv_quote_char",
            field=models.CharField(blank=True, max_length=5, null=True),
        ),
        migrations.AddField(
            model_name="sourcetableselection",
            name="sftp_add_utc_timestamp",
            field=models.BooleanField(default=True),
        ),
    ]
