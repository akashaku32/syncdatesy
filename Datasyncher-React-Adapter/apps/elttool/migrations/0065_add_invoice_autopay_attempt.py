from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0064_encrypt_invoice_receipt_failure"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="autopay_attempt",
            field=models.IntegerField(default=0),
        ),
    ]
