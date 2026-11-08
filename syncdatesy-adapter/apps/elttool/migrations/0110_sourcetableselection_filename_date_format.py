from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0109_alter_registrationrequest_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourcetableselection",
            name="filename_date_format",
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
    ]
