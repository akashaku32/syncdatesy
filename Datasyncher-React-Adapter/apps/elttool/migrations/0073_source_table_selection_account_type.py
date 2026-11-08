from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0072_merge_20260417_1318"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourcetableselection",
            name="account_type",
            field=models.CharField(
                choices=[("admin", "Admin"), ("user", "User")],
                blank=True,
                max_length=10,
                null=True,
            ),
        ),
    ]
