from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0062_auto_20260402_1729"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
                    ALTER TABLE company
                        ALTER COLUMN stripe_customer_id TYPE jsonb USING to_jsonb(stripe_customer_id),
                        ALTER COLUMN stripe_default_payment_method_id TYPE jsonb USING to_jsonb(stripe_default_payment_method_id),
                        ALTER COLUMN stripe_card_last4 TYPE jsonb USING to_jsonb(stripe_card_last4),
                        ALTER COLUMN stripe_card_brand TYPE jsonb USING to_jsonb(stripe_card_brand),
                        ALTER COLUMN stripe_card_exp_month TYPE jsonb USING to_jsonb(stripe_card_exp_month),
                        ALTER COLUMN stripe_card_exp_year TYPE jsonb USING to_jsonb(stripe_card_exp_year),
                        ALTER COLUMN stripe_card_holder_name TYPE jsonb USING to_jsonb(stripe_card_holder_name);
                    ALTER TABLE invoice
                        ALTER COLUMN payment_reference TYPE jsonb USING to_jsonb(payment_reference),
                        ALTER COLUMN payment_method_last4 TYPE jsonb USING to_jsonb(payment_method_last4),
                        ALTER COLUMN payment_method_brand TYPE jsonb USING to_jsonb(payment_method_brand),
                        ALTER COLUMN payment_method_exp_month TYPE jsonb USING to_jsonb(payment_method_exp_month),
                        ALTER COLUMN payment_method_exp_year TYPE jsonb USING to_jsonb(payment_method_exp_year),
                        ALTER COLUMN card_holder_name TYPE jsonb USING to_jsonb(card_holder_name);
                    """,
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name="company",
                    name="stripe_customer_id",
                    field=models.JSONField(blank=True, null=True),
                ),
                migrations.AlterField(
                    model_name="company",
                    name="stripe_default_payment_method_id",
                    field=models.JSONField(blank=True, null=True),
                ),
                migrations.AlterField(
                    model_name="company",
                    name="stripe_card_last4",
                    field=models.JSONField(blank=True, null=True),
                ),
                migrations.AlterField(
                    model_name="company",
                    name="stripe_card_brand",
                    field=models.JSONField(blank=True, null=True),
                ),
                migrations.AlterField(
                    model_name="company",
                    name="stripe_card_exp_month",
                    field=models.JSONField(blank=True, null=True),
                ),
                migrations.AlterField(
                    model_name="company",
                    name="stripe_card_exp_year",
                    field=models.JSONField(blank=True, null=True),
                ),
                migrations.AlterField(
                    model_name="company",
                    name="stripe_card_holder_name",
                    field=models.JSONField(blank=True, null=True),
                ),
                migrations.AlterField(
                    model_name="invoice",
                    name="payment_reference",
                    field=models.JSONField(blank=True, null=True),
                ),
                migrations.AlterField(
                    model_name="invoice",
                    name="payment_method_last4",
                    field=models.JSONField(blank=True, null=True),
                ),
                migrations.AlterField(
                    model_name="invoice",
                    name="payment_method_brand",
                    field=models.JSONField(blank=True, null=True),
                ),
                migrations.AlterField(
                    model_name="invoice",
                    name="payment_method_exp_month",
                    field=models.JSONField(blank=True, null=True),
                ),
                migrations.AlterField(
                    model_name="invoice",
                    name="payment_method_exp_year",
                    field=models.JSONField(blank=True, null=True),
                ),
                migrations.AlterField(
                    model_name="invoice",
                    name="card_holder_name",
                    field=models.JSONField(blank=True, null=True),
                ),
            ],
        ),
    ]
