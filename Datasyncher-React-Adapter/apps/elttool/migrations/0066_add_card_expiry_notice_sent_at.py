from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("elttool", "0065_add_invoice_autopay_attempt"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="card_expiry_email_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
