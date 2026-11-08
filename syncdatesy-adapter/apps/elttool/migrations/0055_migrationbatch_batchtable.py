"""
Migration 0055 — Schema migration

Creates:
  - migration_batch  (MigrationBatch)
  - batch_table      (BatchTable) with UniqueConstraint on (connection, table_name)
  - adds nullable batch FK to scheduled_job
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0054_company_updated_at_backfill_and_state'),
    ]

    operations = [
        # ------------------------------------------------------------------ #
        # 1. MigrationBatch table
        # ------------------------------------------------------------------ #
        migrations.CreateModel(
            name='MigrationBatch',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(default='Batch 1', max_length=255)),
                ('time_frequency', models.CharField(default='15', max_length=100)),
                ('execution_order', models.CharField(
                    choices=[('parallel', 'parallel'), ('sequential', 'sequential')],
                    default='parallel',
                    max_length=100,
                )),
                ('sync_start_date', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(
                    choices=[('active', 'Active'), ('paused', 'Paused')],
                    default='active',
                    max_length=10,
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('connection', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='batches',
                    to='elttool.connection',
                )),
            ],
            options={
                'db_table': 'migration_batch',
                'indexes': [
                    models.Index(fields=['connection', 'status'], name='migbatch_conn_status_idx'),
                    models.Index(fields=['connection', 'updated_at'], name='migbatch_conn_updated_idx'),
                ],
                'unique_together': {('connection', 'name')},
            },
        ),

        # ------------------------------------------------------------------ #
        # 2. BatchTable table
        # ------------------------------------------------------------------ #
        migrations.CreateModel(
            name='BatchTable',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('table_name', models.CharField(max_length=500)),
                ('sequence', models.PositiveIntegerField(default=0)),
                ('batch', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='tables',
                    to='elttool.migrationbatch',
                )),
                ('connection', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    to='elttool.connection',
                )),
            ],
            options={
                'db_table': 'batch_table',
                'indexes': [
                    models.Index(fields=['batch'], name='batchtable_batch_idx'),
                    models.Index(fields=['connection', 'table_name'], name='batchtable_conn_table_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(
                        fields=['connection', 'table_name'],
                        name='uniq_batchtable_conn_table',
                    )
                ],
            },
        ),

        # ------------------------------------------------------------------ #
        # 3. Add nullable batch FK to ScheduledJob
        # ------------------------------------------------------------------ #
        migrations.AddField(
            model_name='scheduledjob',
            name='batch',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='scheduled_jobs',
                to='elttool.migrationbatch',
            ),
        ),
        migrations.AddIndex(
            model_name='scheduledjob',
            index=models.Index(fields=['batch', 'status'], name='schedjob_batch_status_idx'),
        ),
    ]
