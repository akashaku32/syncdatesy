from django.db import migrations


class Migration(migrations.Migration):
    """
    Strip leading 'elttool_' from index names in public schema.
    Reverse restores the prefix.
    """

    dependencies = [
        ("elttool", "0025_rename_auth_tables"),
    ]

    operations = [
        migrations.RunSQL(
            sql=r"""
            DO $$
            DECLARE
                rec record;
                newname text;
            BEGIN
                FOR rec IN
                    SELECT indexname
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND indexname LIKE 'elttool_%'
                LOOP
                    newname := regexp_replace(rec.indexname, '^elttool_', '');
                    EXECUTE format('ALTER INDEX %I RENAME TO %I', rec.indexname, newname);
                END LOOP;
            END$$;
            """,
            reverse_sql=r"""
            DO $$
            DECLARE
                rec record;
                newname text;
            BEGIN
                FOR rec IN
                    SELECT indexname
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND indexname NOT LIKE 'elttool_%'
                      AND indexname NOT LIKE 'pg_%'
                LOOP
                    newname := 'elttool_' || rec.indexname;
                    EXECUTE format('ALTER INDEX %I RENAME TO %I', rec.indexname, newname);
                END LOOP;
            END$$;
            """,
        ),
    ]

