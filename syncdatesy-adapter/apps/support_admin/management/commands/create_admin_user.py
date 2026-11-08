from django.core.management.base import BaseCommand
from apps.support_admin.models import AdminUser


class Command(BaseCommand):
    help = 'Create a support admin user'

    def add_arguments(self, parser):
        parser.add_argument('--email', required=True)
        parser.add_argument('--password', required=True)
        parser.add_argument('--name', required=True)

    def handle(self, *args, **options):
        email = options['email']
        if AdminUser.objects.filter(email=email).exists():
            self.stdout.write(self.style.ERROR(f'Admin with email {email} already exists.'))
            return
        admin = AdminUser(email=email, full_name=options['name'])
        admin.set_password(options['password'])
        admin.save()
        self.stdout.write(self.style.SUCCESS(f'Admin user {email} created successfully.'))
