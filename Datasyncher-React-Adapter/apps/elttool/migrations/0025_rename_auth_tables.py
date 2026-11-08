from django.db import migrations


class Migration(migrations.Migration):
    """
    Rename auth-related tables to unprefixed names:
    - elttool_user -> "user" (quoted because of reserved word)
    - elttool_user_groups -> user_groups
    - elttool_user_user_permissions -> user_permissions
    - elttool_roleaccess -> role_access
    """

    dependencies = [
        ("elttool", "0024_rename_roles_pricing_mapping"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                'ALTER TABLE IF EXISTS elttool_user RENAME TO "user";',
                "ALTER TABLE IF EXISTS elttool_user_groups RENAME TO user_groups;",
                "ALTER TABLE IF EXISTS elttool_user_user_permissions RENAME TO user_permissions;",
                "ALTER TABLE IF EXISTS elttool_roleaccess RENAME TO role_access;",
            ],
            reverse_sql=[
                'ALTER TABLE IF EXISTS "user" RENAME TO elttool_user;',
                "ALTER TABLE IF EXISTS user_groups RENAME TO elttool_user_groups;",
                "ALTER TABLE IF EXISTS user_permissions RENAME TO elttool_user_permissions;",
                "ALTER TABLE IF EXISTS role_access RENAME TO elttool_roleaccess;",
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterModelTable(name="user", table="user"),
                migrations.AlterModelTable(name="roleaccess", table="role_access"),
            ],
        ),
    ]

