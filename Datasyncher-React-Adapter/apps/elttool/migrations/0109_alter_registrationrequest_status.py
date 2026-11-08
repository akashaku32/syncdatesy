from django.db import migrations, models
import django.db.models.deletion


def link_existing_registration_requests(apps, schema_editor):
    RegistrationRequest = apps.get_model('elttool', 'RegistrationRequest')
    User = apps.get_model('elttool', 'User')

    company_by_email = {
        email.strip().lower(): company_id
        for email, company_id in User.objects.exclude(cmp_id=None).values_list(
            'company_email',
            'cmp_id',
        )
        if email
    }

    for registration_request in RegistrationRequest.objects.filter(
        status='access_provided',
        company_id=None,
    ).iterator():
        company_id = company_by_email.get(
            registration_request.email.strip().lower(),
        )
        if company_id is not None:
            registration_request.company_id = company_id
            registration_request.save(update_fields=['company'])


class Migration(migrations.Migration):

    dependencies = [
        ('elttool', '0108_user_password_reset_required'),
    ]

    operations = [
        migrations.AddField(
            model_name='registrationrequest',
            name='company',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='registration_requests',
                to='elttool.company',
            ),
        ),
        migrations.RunPython(
            link_existing_registration_requests,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='registrationrequest',
            name='status',
            field=models.CharField(
                blank=True,
                choices=[
                    ('access_provided', 'Access Provided'),
                    ('access_rejected', 'Access Rejected'),
                    ('expired', 'Expired'),
                ],
                max_length=20,
                null=True,
            ),
        ),
    ]
