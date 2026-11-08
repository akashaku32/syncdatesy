from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0070_remove_inprogress_resolved_add_has_user_reply"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            -- Create source_file_mapping if it was missed due to faked migrations
            CREATE TABLE IF NOT EXISTS source_file_mapping (
                connection_id integer PRIMARY KEY REFERENCES connection(connection_id) ON DELETE CASCADE,
                file_mapping_mode varchar(64) NOT NULL,
                single_file_table_mapping jsonb NOT NULL DEFAULT '{}'::jsonb,
                table_to_files_mapping jsonb NOT NULL DEFAULT '{}'::jsonb,
                cmp_id integer NULL,
                source_id integer NULL,
                basefolder_path varchar(300) NULL,
                created_by integer NULL,
                updated_by integer NULL,
                created_at timestamptz NOT NULL DEFAULT NOW(),
                updated_at timestamptz NOT NULL DEFAULT NOW()
            );

            -- Ensure expected columns exist on source_file_mapping
            ALTER TABLE source_file_mapping ADD COLUMN IF NOT EXISTS file_mapping_mode varchar(64);
            ALTER TABLE source_file_mapping ADD COLUMN IF NOT EXISTS single_file_table_mapping jsonb NOT NULL DEFAULT '{}'::jsonb;
            ALTER TABLE source_file_mapping ADD COLUMN IF NOT EXISTS table_to_files_mapping jsonb NOT NULL DEFAULT '{}'::jsonb;
            ALTER TABLE source_file_mapping ADD COLUMN IF NOT EXISTS cmp_id integer;
            ALTER TABLE source_file_mapping ADD COLUMN IF NOT EXISTS source_id integer;
            ALTER TABLE source_file_mapping ADD COLUMN IF NOT EXISTS basefolder_path varchar(300);
            ALTER TABLE source_file_mapping ADD COLUMN IF NOT EXISTS created_by integer;
            ALTER TABLE source_file_mapping ADD COLUMN IF NOT EXISTS updated_by integer;
            ALTER TABLE source_file_mapping ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT NOW();
            ALTER TABLE source_file_mapping ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT NOW();

            -- Missing columns reported by schema check
            ALTER TABLE source_model ADD COLUMN IF NOT EXISTS deflt_interval varchar(100) NOT NULL DEFAULT '15';
            ALTER TABLE source_model ADD COLUMN IF NOT EXISTS is_rtl boolean NOT NULL DEFAULT FALSE;

            ALTER TABLE source_attribute ADD COLUMN IF NOT EXISTS read_only boolean NOT NULL DEFAULT FALSE;
            ALTER TABLE source_attribute ADD COLUMN IF NOT EXISTS description text NULL;
            ALTER TABLE source_attribute ADD COLUMN IF NOT EXISTS placeholder varchar(255) NULL;
            ALTER TABLE source_attribute ADD COLUMN IF NOT EXISTS default_value varchar(500) NULL;

            ALTER TABLE destination_attribute ADD COLUMN IF NOT EXISTS read_only boolean NOT NULL DEFAULT FALSE;

            ALTER TABLE source_table_selection ADD COLUMN IF NOT EXISTS primary_keys jsonb NOT NULL DEFAULT '[]'::jsonb;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
