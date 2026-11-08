from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0073_source_table_selection_account_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourcetableselection",
            name="updated_by_id",
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
