from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0048_sourcemodel_disable_update_schema"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterModelTable(
                    name="s3mapping",
                    table="source_file_mapping",
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="""
                    DO $body$
                    BEGIN
                        IF to_regclass('public.s3_mapping') IS NOT NULL
                           AND to_regclass('public.source_file_mapping') IS NULL
                        THEN
                            ALTER TABLE s3_mapping RENAME TO source_file_mapping;
                        END IF;
                    END
                    $body$;
                    """,
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="s3mapping",
                    name="basefolder_path",
                    field=models.CharField(blank=True, max_length=300, null=True),
                ),
                migrations.AddField(
                    model_name="s3mapping",
                    name="cmp_id",
                    field=models.IntegerField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="s3mapping",
                    name="created_by",
                    field=models.IntegerField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="s3mapping",
                    name="source_id",
                    field=models.IntegerField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="s3mapping",
                    name="updated_by",
                    field=models.IntegerField(blank=True, null=True),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="""
                    ALTER TABLE source_file_mapping
                        ADD COLUMN IF NOT EXISTS basefolder_path varchar(300);
                    ALTER TABLE source_file_mapping
                        ADD COLUMN IF NOT EXISTS cmp_id integer;
                    ALTER TABLE source_file_mapping
                        ADD COLUMN IF NOT EXISTS created_by integer;
                    ALTER TABLE source_file_mapping
                        ADD COLUMN IF NOT EXISTS source_id integer;
                    ALTER TABLE source_file_mapping
                        ADD COLUMN IF NOT EXISTS updated_by integer;
                    """,
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
        ),
    ]
