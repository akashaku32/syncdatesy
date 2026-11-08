# Generated manually to resolve parallel migration leaves on QA.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0064_free_standard_chunk_limit_50k"),
        ("elttool", "0078_data_transfer_connection_alter_data_transfer_log_and_more"),
        ("elttool", "0088_admin_ops_beat_schedule"),
    ]

    operations = []
