from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='AdminUser',
            fields=[
                ('admin_id', models.AutoField(primary_key=True, serialize=False)),
                ('email', models.EmailField(unique=True)),
                ('password', models.CharField(max_length=255)),
                ('full_name', models.CharField(max_length=200)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'support_admin_user',
            },
        ),
    ]
