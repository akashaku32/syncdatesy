from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0053_add_invoice_payment_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="stripe_customer_id",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name="company",
            name="stripe_default_payment_method_id",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name="company",
            name="stripe_card_last4",
            field=models.CharField(blank=True, max_length=10, null=True),
        ),
        migrations.AddField(
            model_name="company",
            name="stripe_card_brand",
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.AddField(
            model_name="company",
            name="stripe_card_exp_month",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="company",
            name="stripe_card_exp_year",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="company",
            name="stripe_card_holder_name",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
    ]
