"""
Migration 0056 — Data migration

For every Connection that:
  - has at least one SourceTableSelection row with selected=True
  - has time_frequency set and not equal to "None" / "" / None

Creates:
  1. A MigrationBatch named "Batch 1" with the connection's existing
     time_frequency, execution_order and sync_start_date.
  2. A BatchTable row for each selected source table (in sequence order).
  3. Re-points the existing ScheduledJob row(s) for that connection at
     the new batch.

Connections with no selected tables, or no frequency, are skipped —
their tables remain unassigned (no BatchTable rows).
"""

from django.db import migrations


def migrate_tables_to_batch1(apps, schema_editor):
    Connection = apps.get_model('elttool', 'Connection')
    SourceTableSelection = apps.get_model('elttool', 'SourceTableSelection')
    MigrationBatch = apps.get_model('elttool', 'MigrationBatch')
    BatchTable = apps.get_model('elttool', 'BatchTable')
    ScheduledJob = apps.get_model('elttool', 'ScheduledJob')

    db_alias = schema_editor.connection.alias

    invalid_freqs = {None, 'None', '', 'none'}

    for conn in Connection.objects.using(db_alias).iterator():
        freq = conn.time_frequency
        if freq in invalid_freqs:
            continue

        selected_qs = (
            SourceTableSelection.objects
            .using(db_alias)
            .filter(connection=conn, selected=True)
            .order_by('sequence')
        )
        if not selected_qs.exists():
            continue

        # Create one "Batch 1" for this connection
        batch = MigrationBatch.objects.using(db_alias).create(
            connection=conn,
            name='Batch 1',
            time_frequency=freq,
            execution_order=conn.execution_order or 'parallel',
            sync_start_date=conn.sync_start_date,
            status='active',
        )

        # Bulk-create BatchTable rows
        batch_tables = []
        for row in selected_qs:
            batch_tables.append(
                BatchTable(
                    batch=batch,
                    connection=conn,
                    table_name=row.table,
                    sequence=row.sequence or 0,
                )
            )
        BatchTable.objects.using(db_alias).bulk_create(
            batch_tables,
            ignore_conflicts=True,  # safety: skip any accidental duplicate
        )

        # Re-point existing ScheduledJob rows at the new batch
        ScheduledJob.objects.using(db_alias).filter(
            connection=conn
        ).update(batch=batch)


def reverse_migrate(apps, schema_editor):
    """
    Reverse: delete all MigrationBatch / BatchTable rows and clear the
    ScheduledJob.batch FK.  The ScheduledJob rows themselves are kept.
    """
    MigrationBatch = apps.get_model('elttool', 'MigrationBatch')
    ScheduledJob = apps.get_model('elttool', 'ScheduledJob')

    db_alias = schema_editor.connection.alias

    ScheduledJob.objects.using(db_alias).update(batch=None)
    MigrationBatch.objects.using(db_alias).all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0055_migrationbatch_batchtable'),
    ]

    operations = [
        migrations.RunPython(
            migrate_tables_to_batch1,
            reverse_code=reverse_migrate,
        ),
    ]
