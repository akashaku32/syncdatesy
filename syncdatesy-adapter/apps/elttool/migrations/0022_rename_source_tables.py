from django.db import migrations


class Migration(migrations.Migration):
    """
    Rename source-related tables to unprefixed names (round 2).
    State-only update is paired with SQL renames to avoid recreating tables.
    """

    dependencies = [
        ("elttool", "0021_prefix_non_destination_tables"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "ALTER TABLE IF EXISTS elttool_sourcemodel RENAME TO source_model;",
                "ALTER TABLE IF EXISTS elttool_sourceattribute RENAME TO source_attribute;",
                "ALTER TABLE IF EXISTS elttool_sourceconfig RENAME TO source_config;",
                "ALTER TABLE IF EXISTS elttool_sourcetableselection RENAME TO source_table_selection;",
                "ALTER TABLE IF EXISTS elttool_connection RENAME TO connection;",
                "ALTER TABLE IF EXISTS elttool_sourcedeltatable RENAME TO source_delta_table;",
            ],
            reverse_sql=[
                "ALTER TABLE IF EXISTS source_model RENAME TO elttool_sourcemodel;",
                "ALTER TABLE IF EXISTS source_attribute RENAME TO elttool_sourceattribute;",
                "ALTER TABLE IF EXISTS source_config RENAME TO elttool_sourceconfig;",
                "ALTER TABLE IF EXISTS source_table_selection RENAME TO elttool_sourcetableselection;",
                "ALTER TABLE IF EXISTS connection RENAME TO elttool_connection;",
                "ALTER TABLE IF EXISTS source_delta_table RENAME TO elttool_sourcedeltatable;",
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterModelTable(name="sourcemodel", table="source_model"),
                migrations.AlterModelTable(name="sourceattribute", table="source_attribute"),
                migrations.AlterModelTable(name="sourceconfig", table="source_config"),
                migrations.AlterModelTable(name="sourcetableselection", table="source_table_selection"),
                migrations.AlterModelTable(name="connection", table="connection"),
                migrations.AlterModelTable(name="sourcedeltatable", table="source_delta_table"),
            ],
        ),
    ]

