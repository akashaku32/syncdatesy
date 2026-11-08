from django.db import migrations


class Migration(migrations.Migration):
    """
    Align Django state to use prefixed table names for all non-destination models
    so Django queries existing prefixed tables while destination tables remain unprefixed.
    No database operations are performed; tables are already prefixed in DB.
    """

    dependencies = [
        ("elttool", "0020_rename_destination_tables"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterModelTable(name="company", table="elttool_company"),
                migrations.AlterModelTable(name="role", table="elttool_role"),
                migrations.AlterModelTable(name="roleaccess", table="elttool_role_access"),
                migrations.AlterModelTable(name="user", table="elttool_user"),
                migrations.AlterModelTable(name="sourcemodel", table="elttool_sourcemodel"),
                migrations.AlterModelTable(name="sourceattribute", table="elttool_sourceattribute"),
                migrations.AlterModelTable(name="sourceconfig", table="elttool_sourceconfig"),
                migrations.AlterModelTable(name="connection", table="elttool_connection"),
                migrations.AlterModelTable(name="sourcetableselection", table="elttool_sourcetableselection"),
                migrations.AlterModelTable(name="tablemapping", table="elttool_tablemapping"),
                migrations.AlterModelTable(name="log", table="elttool_log"),
                migrations.AlterModelTable(name="data_transfer", table="elttool_data_transfer"),
                migrations.AlterModelTable(name="existing_tbl_hdr", table="elttool_existing_tbl_hdr"),
                migrations.AlterModelTable(name="existing_tbl_itm", table="elttool_existing_tbl_itm"),
                migrations.AlterModelTable(name="migrationrecord", table="elttool_migrationrecord"),
                migrations.AlterModelTable(name="scheduledjob", table="elttool_scheduledjob"),
                migrations.AlterModelTable(name="invoice", table="elttool_invoice"),
                migrations.AlterModelTable(name="datedirectory", table="elttool_datedirectory"),
                migrations.AlterModelTable(name="errorlogentry", table="elttool_errorlogentry"),
                migrations.AlterModelTable(name="pricing", table="elttool_pricing"),
                migrations.AlterModelTable(name="communicationsupport", table="elttool_communicationsupport"),
                migrations.AlterModelTable(name="sourcedeltatable", table="elttool_sourcedeltatable"),
                migrations.AlterModelTable(name="s3mapping", table="elttool_s3_mapping"),
            ],
        ),
    ]

