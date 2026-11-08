from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0067_ticket_id_start_20000'),
    ]

    operations = [
        migrations.RunSQL(
            "ALTER SEQUENCE ticket_support_ticket_id_seq RESTART WITH 20000;",
        ),
        migrations.RunSQL(
            "ALTER SEQUENCE ticket_category_category_id_seq RESTART WITH 9000;",
        ),
        migrations.RunSQL(
            "ALTER SEQUENCE ticket_issue_type_issue_type_id_seq RESTART WITH 10000;",
        ),
    ]
