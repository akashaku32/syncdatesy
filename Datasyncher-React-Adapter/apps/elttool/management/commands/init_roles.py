"""
Management command to initialize required roles in the database.

Usage:
    python manage.py init_roles

This command creates the three required roles if they don't exist:
- Administrator (role_id=1)
- Analyst (role_id=2)
- Billing (role_id=3)
"""

from django.core.management.base import BaseCommand
from apps.elttool.models import Role


class Command(BaseCommand):
    help = 'Initialize required roles (Administrator, Analyst, Billing)'

    def handle(self, *args, **options):
        """
        Create the three required roles if they don't exist.
        """
        roles_to_create = [
            {'role_id': 1, 'role_name': 'Administrator'},
            {'role_id': 2, 'role_name': 'Analyst'},
            {'role_id': 3, 'role_name': 'Billing'},
        ]

        created_count = 0
        existing_count = 0

        for role_data in roles_to_create:
            role, created = Role.objects.get_or_create(
                role_id=role_data['role_id'],
                defaults={'role_name': role_data['role_name']}
            )
            
            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✅ Created role: {role_data['role_name']} (ID: {role_data['role_id']})"
                    )
                )
            else:
                existing_count += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"⚠️  Role already exists: {role_data['role_name']} (ID: {role_data['role_id']})"
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\n✅ Role initialization complete: {created_count} created, {existing_count} already existed"
            )
        )
