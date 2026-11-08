from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0063_encrypt_billing_stripe_fields"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
                    ALTER TABLE invoice
                        ALTER COLUMN receipt_url TYPE jsonb USING to_jsonb(receipt_url),
                        ALTER COLUMN failure_reason TYPE jsonb USING to_jsonb(failure_reason);
                    """,
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name="invoice",
                    name="receipt_url",
                    field=models.JSONField(blank=True, null=True),
                ),
                migrations.AlterField(
                    model_name="invoice",
                    name="failure_reason",
                    field=models.JSONField(blank=True, null=True),
                ),
            ],
        ),
    ]
