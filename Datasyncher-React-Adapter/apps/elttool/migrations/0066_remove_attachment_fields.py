from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0065_add_invoice_autopay_attempt'),
    ]

    operations = [
        # attachment column already removed from model, just drop from DB
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL("ALTER TABLE ticket_support DROP COLUMN IF EXISTS attachment;"),
                migrations.RunSQL("ALTER TABLE ticket_reply DROP COLUMN IF EXISTS attachment;"),
            ],
            state_operations=[],
        ),
    ]
