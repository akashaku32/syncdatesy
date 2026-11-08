from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0107_registrationrequest_access_provided_at_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='password_reset_required',
            field=models.BooleanField(default=False),
        ),
    ]
