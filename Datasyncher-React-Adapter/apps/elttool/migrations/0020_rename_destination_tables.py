from django.db import migrations


class Migration(migrations.Migration):
    """
    Incremental rename: destination tables only.
    Keeps reverse_sql for safe rollback and uses IF EXISTS guards.
    """

    dependencies = [
        ("elttool", "0017_alter_user_options_and_more"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                # Rename tables using the actual current names in Postgres
                "ALTER TABLE IF EXISTS elttool_destinationmodel RENAME TO destination_model;",
                "ALTER TABLE IF EXISTS elttool_destinationattribute RENAME TO destination_attribute;",
                "ALTER TABLE IF EXISTS elttool_destinationconfig RENAME TO destination_config;",
                "ALTER TABLE IF EXISTS elttool_destinationtableselection RENAME TO destination_table_selection;",
            ],
            reverse_sql=[
                "ALTER TABLE IF EXISTS destination_model RENAME TO elttool_destinationmodel;",
                "ALTER TABLE IF EXISTS destination_attribute RENAME TO elttool_destinationattribute;",
                "ALTER TABLE IF EXISTS destination_config RENAME TO elttool_destinationconfig;",
                "ALTER TABLE IF EXISTS destination_table_selection RENAME TO elttool_destinationtableselection;",
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterModelTable(
                    name="destinationmodel", table="destination_model"
                ),
                migrations.AlterModelTable(
                    name="destinationattribute", table="destination_attribute"
                ),
                migrations.AlterModelTable(
                    name="destinationconfig", table="destination_config"
                ),
                migrations.AlterModelTable(
                    name="destinationtableselection", table="destination_table_selection"
                ),
            ],
        ),
        migrations.RunSQL(
            # destination_config indexes (rename all to drop prefix)
            sql=[
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'elttool_destinationconfig_pkey') THEN
                        ALTER INDEX elttool_destinationconfig_pkey RENAME TO destinationconfig_pkey;
                    END IF;
                END $$;
                """,
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'elttool_destinationconfig_cmp_id_name_0ca9ccb8_uniq') THEN
                        ALTER INDEX elttool_destinationconfig_cmp_id_name_0ca9ccb8_uniq RENAME TO destinationconfig_cmp_id_name_0ca9ccb8_uniq;
                    END IF;
                END $$;
                """,
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'elttool_destinationconfig_cmp_id_33000a08') THEN
                        ALTER INDEX elttool_destinationconfig_cmp_id_33000a08 RENAME TO destinationconfig_cmp_id_33000a08;
                    END IF;
                END $$;
                """,
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'elttool_destinationconfig_dst_id_a2d46850') THEN
                        ALTER INDEX elttool_destinationconfig_dst_id_a2d46850 RENAME TO destinationconfig_dst_id_a2d46850;
                    END IF;
                END $$;
                """,
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'elttool_des_cmp_id_ece110_idx') THEN
                        ALTER INDEX elttool_des_cmp_id_ece110_idx RENAME TO destination_cmp_id_0281f5_idx;
                    END IF;
                END $$;
                """,
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'elttool_des_updated_ad7913_idx') THEN
                        ALTER INDEX elttool_des_updated_ad7913_idx RENAME TO destination_updated_bc348d_idx;
                    END IF;
                END $$;
                """,
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'elttool_des_cmp_id_9a1832_idx') THEN
                        ALTER INDEX elttool_des_cmp_id_9a1832_idx RENAME TO destination_cmp_id_560861_idx;
                    END IF;
                END $$;
                """,
            ],
            reverse_sql=[
                "ALTER INDEX IF EXISTS destinationconfig_pkey RENAME TO elttool_destinationconfig_pkey;",
                "ALTER INDEX IF EXISTS destinationconfig_cmp_id_name_0ca9ccb8_uniq RENAME TO elttool_destinationconfig_cmp_id_name_0ca9ccb8_uniq;",
                "ALTER INDEX IF EXISTS destinationconfig_cmp_id_33000a08 RENAME TO elttool_destinationconfig_cmp_id_33000a08;",
                "ALTER INDEX IF EXISTS destinationconfig_dst_id_a2d46850 RENAME TO elttool_destinationconfig_dst_id_a2d46850;",
                "ALTER INDEX IF EXISTS destination_cmp_id_0281f5_idx RENAME TO elttool_des_cmp_id_ece110_idx;",
                "ALTER INDEX IF EXISTS destination_updated_bc348d_idx RENAME TO elttool_des_updated_ad7913_idx;",
                "ALTER INDEX IF EXISTS destination_cmp_id_560861_idx RENAME TO elttool_des_cmp_id_9a1832_idx;",
            ],
        ),
        migrations.RunSQL(
            # destination_table_selection indexes (rename all to drop prefix)
            sql=[
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'elttool_destinationtableselection_pkey') THEN
                        ALTER INDEX elttool_destinationtableselection_pkey RENAME TO destinationtableselection_pkey;
                    END IF;
                END $$;
                """,
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'elttool_destinationtable_table_connection_id_c835e24c_uniq') THEN
                        ALTER INDEX elttool_destinationtable_table_connection_id_c835e24c_uniq RENAME TO destinationtable_table_connection_id_c835e24c_uniq;
                    END IF;
                END $$;
                """,
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'elttool_destinationtableselection_connection_id_656e1897') THEN
                        ALTER INDEX elttool_destinationtableselection_connection_id_656e1897 RENAME TO destinationtableselection_connection_id_656e1897;
                    END IF;
                END $$;
                """,
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'elttool_des_connect_7641b6_idx') THEN
                        ALTER INDEX elttool_des_connect_7641b6_idx RENAME TO destination_connect_24a03a_idx;
                    END IF;
                END $$;
                """,
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'elttool_des_connect_6f4638_idx') THEN
                        ALTER INDEX elttool_des_connect_6f4638_idx RENAME TO destination_connect_a25535_idx;
                    END IF;
                END $$;
                """,
            ],
            reverse_sql=[
                "ALTER INDEX IF EXISTS destinationtableselection_pkey RENAME TO elttool_destinationtableselection_pkey;",
                "ALTER INDEX IF EXISTS destinationtable_table_connection_id_c835e24c_uniq RENAME TO elttool_destinationtable_table_connection_id_c835e24c_uniq;",
                "ALTER INDEX IF EXISTS destinationtableselection_connection_id_656e1897 RENAME TO elttool_destinationtableselection_connection_id_656e1897;",
                "ALTER INDEX IF EXISTS destination_connect_24a03a_idx RENAME TO elttool_des_connect_7641b6_idx;",
                "ALTER INDEX IF EXISTS destination_connect_a25535_idx RENAME TO elttool_des_connect_6f4638_idx;",
            ],
        ),
        migrations.RunSQL(
            # destination_model indexes
            sql=[
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'elttool_destinationmodel_pkey') THEN
                        ALTER INDEX elttool_destinationmodel_pkey RENAME TO destinationmodel_pkey;
                    END IF;
                END $$;
                """,
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'elttool_destinationmodel_name_key') THEN
                        ALTER INDEX elttool_destinationmodel_name_key RENAME TO destinationmodel_name_key;
                    END IF;
                END $$;
                """,
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'elttool_destinationmodel_name_2a33e73f_like') THEN
                        ALTER INDEX elttool_destinationmodel_name_2a33e73f_like RENAME TO destinationmodel_name_2a33e73f_like;
                    END IF;
                END $$;
                """,
            ],
            reverse_sql=[
                "ALTER INDEX IF EXISTS destinationmodel_pkey RENAME TO elttool_destinationmodel_pkey;",
                "ALTER INDEX IF EXISTS destinationmodel_name_key RENAME TO elttool_destinationmodel_name_key;",
                "ALTER INDEX IF EXISTS destinationmodel_name_2a33e73f_like RENAME TO elttool_destinationmodel_name_2a33e73f_like;",
            ],
        ),
        migrations.RunSQL(
            # destination_attribute indexes
            sql=[
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'elttool_destinationattribute_pkey') THEN
                        ALTER INDEX elttool_destinationattribute_pkey RENAME TO destinationattribute_pkey;
                    END IF;
                END $$;
                """,
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'elttool_destinationattribute_dst_id_2c74bfcd') THEN
                        ALTER INDEX elttool_destinationattribute_dst_id_2c74bfcd RENAME TO destinationattribute_dst_id_2c74bfcd;
                    END IF;
                END $$;
                """,
            ],
            reverse_sql=[
                "ALTER INDEX IF EXISTS destinationattribute_pkey RENAME TO elttool_destinationattribute_pkey;",
                "ALTER INDEX IF EXISTS destinationattribute_dst_id_2c74bfcd RENAME TO elttool_destinationattribute_dst_id_2c74bfcd;",
            ],
        ),
    ]

