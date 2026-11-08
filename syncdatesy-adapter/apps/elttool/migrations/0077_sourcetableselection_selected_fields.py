from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0076_auto_20260507_1245"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourcetableselection",
            name="selected_fields",
            field=models.JSONField(
                blank=True,
                null=True,
            ),
        ),
    ]
