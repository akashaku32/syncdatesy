from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0066_remove_attachment_fields'),
    ]

    operations = [
        migrations.RunSQL(
            "ALTER SEQUENCE ticket_support_ticket_id_seq RESTART WITH 20000;",
            "ALTER SEQUENCE ticket_support_ticket_id_seq RESTART WITH 1;"
        ),
    ]
