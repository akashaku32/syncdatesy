# Create or update a platform superuser on the QA GKE django pod.

param(
    [string]$Email = "qa-platform-admin@datasyncher.com",
    [string]$Password = "QaK8sAdmin!2026",
    [string]$Namespace = "default"
)

$ErrorActionPreference = "Stop"

$py = "from apps.elttool.models import User; email='$Email'; password='$Password'; user, created = User.objects.get_or_create(company_email=email, defaults=dict(first_name='QA', last_name='PlatformAdmin', company_name='Datasyncher Platform', country='US', is_active=True, is_staff=True, is_superuser=True)); user.is_active=True; user.is_staff=True; user.is_superuser=True; user.set_password(password); user.save(); print(('CREATED' if created else 'UPDATED'), email, 'user_id=', user.user_id)"

Write-Host "Creating platform admin: $Email" -ForegroundColor Cyan
kubectl exec -n $Namespace deploy/django -- python manage.py shell -c $py
