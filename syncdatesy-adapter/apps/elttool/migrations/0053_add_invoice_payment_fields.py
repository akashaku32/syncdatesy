from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0052_auto_20260319_1007"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="payment_reference",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name="invoice",
            name="failure_reason",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="invoice",
            name="payment_method_last4",
            field=models.CharField(blank=True, max_length=10, null=True),
        ),
        migrations.AddField(
            model_name="invoice",
            name="payment_method_brand",
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.AddField(
            model_name="invoice",
            name="payment_method_exp_month",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="invoice",
            name="payment_method_exp_year",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="invoice",
            name="card_holder_name",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
    ]
