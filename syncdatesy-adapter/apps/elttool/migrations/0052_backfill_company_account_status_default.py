from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("elttool", "0051_sourcetableselection_is_mapping_checked"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_name = 'company'
                          AND column_name = 'account_status'
                    ) THEN
                        EXECUTE 'UPDATE company SET account_status = ''active'' WHERE account_status IS NULL';
                        EXECUTE 'ALTER TABLE company ALTER COLUMN account_status SET DEFAULT ''active''';
                    END IF;
                END $$;
            """,
            reverse_sql="""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_name = 'company'
                          AND column_name = 'account_status'
                    ) THEN
                        EXECUTE 'ALTER TABLE company ALTER COLUMN account_status DROP DEFAULT';
                    END IF;
                END $$;
            """,
        )
    ]

