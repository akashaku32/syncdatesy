from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("elttool", "0053_company_account_status_state_only"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
                        ALTER TABLE company
                            ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone;
                        UPDATE company SET updated_at = COALESCE(created_at, NOW()) WHERE updated_at IS NULL;
                        ALTER TABLE company ALTER COLUMN updated_at SET DEFAULT NOW();
                    """,
                    reverse_sql="""
                        ALTER TABLE company DROP COLUMN IF EXISTS updated_at;
                    """,
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="company",
                    name="updated_at",
                    field=models.DateTimeField(auto_now=True),
                ),
            ],
        ),
    ]
