from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0125_add_email_templates"),
    ]

    operations = [
        migrations.AddField(
            model_name="connection",
            name="create_dedicated_dbx_serverless_job",
            field=models.BooleanField(
                default=False,
                help_text="When true, provision a per-connector Databricks Lakeflow Job with serverless tasks for Dynamics Spark MERGE.",
            ),
        ),
        migrations.AddField(
            model_name="connection",
            name="databricks_spark_job_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="connection",
            name="databricks_spark_job_name",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
