# Stub kept for migration graph compatibility on core/batch_technique.
# Batch models are retained; this migration is a no-op (0058+ depend on this node name).
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0056_migrate_connection_tables_to_batch1'),
    ]

    operations = []
