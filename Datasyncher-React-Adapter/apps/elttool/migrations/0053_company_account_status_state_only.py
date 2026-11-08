from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("elttool", "0052_backfill_company_account_status_default"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
                        ALTER TABLE company
                            ADD COLUMN IF NOT EXISTS account_status varchar(20) DEFAULT 'active' NOT NULL;
                        ALTER TABLE company
                            ALTER COLUMN account_status SET DEFAULT 'active';
                        UPDATE company SET account_status = 'active' WHERE account_status IS NULL;
                    """,
                    reverse_sql="""
                        ALTER TABLE company DROP COLUMN IF EXISTS account_status;
                    """,
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="company",
                    name="account_status",
                    field=models.CharField(default="active", max_length=20),
                ),
            ],
        ),
    ]
