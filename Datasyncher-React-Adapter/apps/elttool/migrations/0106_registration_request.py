from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0105_remove_staging_table_details'),
    ]

    operations = [
        migrations.CreateModel(
            name='RegistrationRequest',
            fields=[
                (
                    'registration_request_id',
                    models.AutoField(primary_key=True, serialize=False),
                ),
                ('first_name', models.CharField(max_length=100)),
                ('last_name', models.CharField(max_length=100)),
                ('email', models.EmailField(max_length=254)),
                ('phone_number', models.CharField(max_length=16)),
                ('company_name', models.CharField(max_length=100)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'registration_request',
                'ordering': ['-created_at'],
            },
        ),
    ]
