from django.db import migrations


class Migration(migrations.Migration):
    """
    Rename remaining prefixed tables to unprefixed names:
    role, table_mapping, migration_record,
    scheduled_job, pricing, s3_mapping.
    """

    dependencies = [
        ("elttool", "0023_rename_misc_tables"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "ALTER TABLE IF EXISTS elttool_role RENAME TO role;",
                "ALTER TABLE IF EXISTS elttool_tablemapping RENAME TO table_mapping;",
                "ALTER TABLE IF EXISTS elttool_migrationrecord RENAME TO migration_record;",
                "ALTER TABLE IF EXISTS elttool_scheduledjob RENAME TO scheduled_job;",
                "ALTER TABLE IF EXISTS elttool_pricing RENAME TO pricing;",
                "ALTER TABLE IF EXISTS elttool_s3_mapping RENAME TO s3_mapping;",
            ],
            reverse_sql=[
                "ALTER TABLE IF EXISTS role RENAME TO elttool_role;",
                "ALTER TABLE IF EXISTS table_mapping RENAME TO elttool_tablemapping;",
                "ALTER TABLE IF EXISTS migration_record RENAME TO elttool_migrationrecord;",
                "ALTER TABLE IF EXISTS scheduled_job RENAME TO elttool_scheduledjob;",
                "ALTER TABLE IF EXISTS pricing RENAME TO elttool_pricing;",
                "ALTER TABLE IF EXISTS s3_mapping RENAME TO elttool_s3_mapping;",
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterModelTable(name="role", table="role"),
                migrations.AlterModelTable(name="tablemapping", table="table_mapping"),
                migrations.AlterModelTable(name="migrationrecord", table="migration_record"),
                migrations.AlterModelTable(name="scheduledjob", table="scheduled_job"),
                migrations.AlterModelTable(name="pricing", table="pricing"),
                migrations.AlterModelTable(name="s3mapping", table="s3_mapping"),
            ],
        ),
    ]

