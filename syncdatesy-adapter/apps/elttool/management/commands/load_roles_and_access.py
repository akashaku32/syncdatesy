# your_app/management/commands/load_roles_and_access.py

from django.core.management.base import BaseCommand
from ...models import Role, RoleAccess

class Command(BaseCommand):
    help = 'Loads default Role and RoleAccess entries'

    def handle(self, *args, **kwargs):
        # List of roles
        roles = [
            'Administrator',
            'Creator',
            'Billing',
            'Analyst',
            'Reviewer',
        ]

        role_objects = {}

        for role_name in roles:
            role, created = Role.objects.get_or_create(role_name=role_name)
            role_objects[role_name] = role
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created role: {role_name}"))
            else:
                self.stdout.write(self.style.WARNING(f"Role already exists: {role_name}"))

        # List of accesses (type + role_name)
        accesses = [
            ('Create', 'Administrator'),
            ('Table', 'Administrator'),
            ('Create', 'Creator'),
            ('Table', 'Creator'),
            ('Billing', 'Billing'),
            ('Create', 'Analyst'),
            ('Table', 'Analyst'),
            ('Table', 'Reviewer'),
        ]

        for access_type, role_name in accesses:
            role = role_objects.get(role_name)
            if not RoleAccess.objects.filter(type=access_type, role=role).exists():
                access = RoleAccess(type=access_type, role=role)
                access.save()  # auto-generates `role_access_id`
                self.stdout.write(self.style.SUCCESS(
                    f"Created RoleAccess: {role_name} - {access_type}"))
            else:
                self.stdout.write(self.style.WARNING(
                    f"RoleAccess already exists: {role_name} - {access_type}"))
