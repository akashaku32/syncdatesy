from django.db import migrations


class Migration(migrations.Migration):
    """
    Rename remaining prefixed tables (non-destination, non-source that are now unprefixed in models):
    company, communication_support, data_transfer, date_directory,
    error_log_entry, existing_tbl_hdr, existing_tbl_itm, invoice, log.
    """

    dependencies = [
        ("elttool", "0022_rename_source_tables"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "ALTER TABLE IF EXISTS elttool_company RENAME TO company;",
                "ALTER TABLE IF EXISTS elttool_communicationsupport RENAME TO communication_support;",
                "ALTER TABLE IF EXISTS elttool_data_transfer RENAME TO data_transfer;",
                "ALTER TABLE IF EXISTS elttool_datedirectory RENAME TO date_directory;",
                "ALTER TABLE IF EXISTS elttool_errorlogentry RENAME TO error_log_entry;",
                "ALTER TABLE IF EXISTS elttool_existing_tbl_hdr RENAME TO existing_tbl_hdr;",
                "ALTER TABLE IF EXISTS elttool_existing_tbl_itm RENAME TO existing_tbl_itm;",
                "ALTER TABLE IF EXISTS elttool_invoice RENAME TO invoice;",
                "ALTER TABLE IF EXISTS elttool_log RENAME TO log;",
            ],
            reverse_sql=[
                "ALTER TABLE IF EXISTS company RENAME TO elttool_company;",
                "ALTER TABLE IF EXISTS communication_support RENAME TO elttool_communicationsupport;",
                "ALTER TABLE IF EXISTS data_transfer RENAME TO elttool_data_transfer;",
                "ALTER TABLE IF EXISTS date_directory RENAME TO elttool_datedirectory;",
                "ALTER TABLE IF EXISTS error_log_entry RENAME TO elttool_errorlogentry;",
                "ALTER TABLE IF EXISTS existing_tbl_hdr RENAME TO elttool_existing_tbl_hdr;",
                "ALTER TABLE IF EXISTS existing_tbl_itm RENAME TO elttool_existing_tbl_itm;",
                "ALTER TABLE IF EXISTS invoice RENAME TO elttool_invoice;",
                "ALTER TABLE IF EXISTS log RENAME TO elttool_log;",
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterModelTable(name="company", table="company"),
                migrations.AlterModelTable(name="communicationsupport", table="communication_support"),
                migrations.AlterModelTable(name="data_transfer", table="data_transfer"),
                migrations.AlterModelTable(name="datedirectory", table="date_directory"),
                migrations.AlterModelTable(name="errorlogentry", table="error_log_entry"),
                migrations.AlterModelTable(name="existing_tbl_hdr", table="existing_tbl_hdr"),
                migrations.AlterModelTable(name="existing_tbl_itm", table="existing_tbl_itm"),
                migrations.AlterModelTable(name="invoice", table="invoice"),
                migrations.AlterModelTable(name="log", table="log"),
            ],
        ),
    ]

