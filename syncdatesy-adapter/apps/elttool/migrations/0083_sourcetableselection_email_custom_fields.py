from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("elttool", "0082_sourcetableselection_excel_export_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourcetableselection",
            name="email_custom_fields",
            field=models.JSONField(blank=True, default=dict, null=True),
        ),
    ]
