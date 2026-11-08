from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0054_add_company_stripe_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="receipt_url",
            field=models.TextField(blank=True, null=True),
        ),
    ]
