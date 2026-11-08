from django.db import models, transaction
from django.db.models import IntegerField
from django.conf import settings
from django.db.models.functions import Cast, Substr
from django.contrib.auth.models import AbstractUser,Group,Permission
import logging
logger = logging.getLogger(__name__)


from django.db import models
from apps.utils.encryption import (
    decrypt_field,
    decrypt_sensitive,
    encrypt_field,
    encrypt_sensitive,
    parse_encrypted_payload,
)
import json
from django.utils.timezone import make_aware
from datetime import datetime

from django.db.models.signals import post_save
from django.dispatch import receiver

from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
import json
from datetime import datetime


class Plan(models.Model):
    """Subscription plan catalog (pod limits for Celery/KEDA scaling)."""
    plan_id = models.PositiveSmallIntegerField(primary_key=True)
    name = models.CharField(max_length=32, unique=True)
    pod_count = models.PositiveSmallIntegerField()

    class Meta:
        db_table = 'plans'
        verbose_name = 'plan'
        verbose_name_plural = 'plans'

    def __str__(self):
        return f"{self.name} ({self.pod_count} pods)"


# Maps company.plan (string) -> plans.plan_id
# Keep pod_count on plans rows synced with plan_resource_config.max_pods.
PLAN_ID_BY_COMPANY_PLAN_NAME = {
    'free': 1,
    'discounted': 2,
    'standard': 2,
    'enterprise': 3,
    'premium': 4,
}


class Company(models.Model):
    PLAN_CHOICES = [
        ('Free', 'Free'),
        ('Discounted', 'Discounted'),
        ('Standard', 'Standard'),
        ('Premium', 'Premium'),
        ('Enterprise', 'Enterprise'),
    ]
    ACCOUNT_STATUS_CHOICES = [
        ('Active', 'Active'),
        ('Inactive', 'Inactive'),
        ('Temporarily Hold', 'Temporarily Hold'),
    ]

    cmp_id = models.AutoField(primary_key=True)
    cmp_name = models.TextField(null=True)
    employee_range = models.CharField(max_length=100, null=True)
    pricing = models.CharField(max_length=10, default="0.02")
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    is_trial = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    billing_start_date = models.DateTimeField(null=True)
    billing_end_date = models.DateTimeField(null=True)
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default='Free')
    pod_count = models.IntegerField(
        default=0,
        help_text='Extra pods above plan catalog cap; used only when greater than plan.pod_count.',
    )
    plan_ref = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        to_field='plan_id',
        db_column='plan_id',
        related_name='companies',
    )
    account_status = models.CharField(max_length=20, default='active')
    billed_to = models.JSONField(null=True, blank=True)
    billing_address = models.JSONField(null=True, blank=True)
    shipping_address = models.JSONField(null=True, blank=True)
    stripe_customer_id = models.JSONField(null=True, blank=True)
    stripe_default_payment_method_id = models.JSONField(null=True, blank=True)
    stripe_card_last4 = models.JSONField(null=True, blank=True)
    stripe_card_brand = models.JSONField(null=True, blank=True)
    stripe_card_exp_month = models.JSONField(null=True, blank=True)
    stripe_card_exp_year = models.JSONField(null=True, blank=True)
    stripe_card_holder_name = models.JSONField(null=True, blank=True)
    card_expiry_email_sent_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        'User', null=True, blank=True, on_delete=models.SET_NULL, related_name='companies_created'
    )
    updated_by = models.ForeignKey(
        'User', null=True, blank=True, on_delete=models.SET_NULL, related_name='companies_updated'
    )
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'company'

    def resolve_plan_id_from_plan_field(self) -> int:
        """Return plans.plan_id for the current company.plan string."""
        plan_key = (self.plan or 'Free').strip().lower()
        return PLAN_ID_BY_COMPANY_PLAN_NAME.get(plan_key, 1)

    def sync_plan_ref_from_plan_field(self) -> None:
        """Assign plan_id FK from company.plan (Free/Standard/Enterprise/...)."""
        self.plan_ref_id = self.resolve_plan_id_from_plan_field()

    def get_plan_pod_count(self) -> int:
        """Pod cap from plans catalog for company.plan."""
        plan_id = self.plan_ref_id or self.resolve_plan_id_from_plan_field()
        if self.plan_ref_id == plan_id:
            plan_row = self.plan_ref
            if plan_row:
                return int(plan_row.pod_count)
        try:
            return int(Plan.objects.values_list('pod_count', flat=True).get(pk=plan_id))
        except Plan.DoesNotExist:
            # Mirrors plan_resource_config.max_pods when plans row is missing.
            fallback = {1: 2, 2: 2, 3: 6, 4: 4}
            return fallback.get(int(plan_id), 2)

    def get_effective_pod_count(self) -> int:
        """
        Celery/KEDA per-job pod cap:
        - Base = plan catalog pods for company.plan
        - If company.pod_count > plan pods, use company.pod_count
        """
        plan_pods = self.get_plan_pod_count()
        company_pods = int(self.pod_count or 0)
        if company_pods > plan_pods:
            return company_pods
        return plan_pods

    @property
    def data(self):
        """Decrypt and return the company name."""
        return self._decrypt_field(self.cmp_name)

    @data.setter
    def data(self, value):
        """Temporarily store `cmp_name` for encryption during save."""
        if value is not None:
            self._data_to_encrypt = value  # Store the raw value temporarily
        else:
            self._data_to_encrypt = None
            self.cmp_name = None
    
    def _decrypt_field(self, encrypted_value):
        if encrypted_value:
            try:
                return decrypt_sensitive(
                    encrypted_value,
                    self._get_encryption_key(),
                    legacy_fallback=self.cmp_id,
                )
            except (ValueError, KeyError, TypeError) as e:
                logger.warning("Decryption error: %s", e)
                return None
        return None

    def _get_encryption_key(self):
        # Legacy material only — new writes prefer FIELD_ENCRYPTION_KEY via encrypt_sensitive.
        created_day = self.created_at.day if self.created_at else 1
        cmp_id = self.cmp_id if self.cmp_id else 0
        return cmp_id + created_day

    def _encrypt_field(self, value):
        if value is None:
            return None
        return json.dumps(encrypt_sensitive(value, self._get_encryption_key()))

    def _is_encrypted_payload(self, value):
        return parse_encrypted_payload(value) is not None

    def _decrypt_sensitive_value(self, value):
        if value is None:
            return None
        raw = parse_encrypted_payload(value)
        if raw is None:
            return value
        if not self.cmp_id and len(raw) < 7:
            return None
        try:
            return decrypt_sensitive(
                raw, self._get_encryption_key(), legacy_fallback=self.cmp_id
            )
        except Exception:
            return None

    def _encrypt_sensitive_value(self, value):
        if value is None:
            return None
        if not self.cmp_id:
            return value
        return encrypt_sensitive(value, self._get_encryption_key())

    def get_decrypted_field(self, field_name: str):
        return self._decrypt_sensitive_value(getattr(self, field_name))

    def set_encrypted_field(self, field_name: str, value):
        setattr(self, field_name, self._encrypt_sensitive_value(value))

    def _collect_sensitive_updates(self):
        sensitive_fields = {
            "billing_address",
            "shipping_address",
            "stripe_customer_id",
            "stripe_default_payment_method_id",
            "stripe_card_last4",
            "stripe_card_brand",
            "stripe_card_exp_month",
            "stripe_card_exp_year",
            "stripe_card_holder_name",
        }
        updates = {}
        for field_name in sensitive_fields:
            value = getattr(self, field_name)
            raw = value
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except Exception:
                    pass
            if self._is_encrypted_payload(raw) or raw is None:
                continue
            updates[field_name] = self._encrypt_sensitive_value(raw)
        return updates

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        if not is_new and self.cmp_id:
            updates = self._collect_sensitive_updates()
            for field_name, encrypted_value in updates.items():
                setattr(self, field_name, encrypted_value)
        super().save(*args, **kwargs)
        if is_new and self.cmp_id:
            updates = self._collect_sensitive_updates()
            if updates:
                Company.objects.filter(pk=self.pk).update(**updates)
                for field_name, encrypted_value in updates.items():
                    setattr(self, field_name, encrypted_value)

    def __str__(self):
        return f"Company: {self.cmp_name or 'N/A'}"
    
    def create_subscription(self, is_trial=False, plan='Free'):
        """
        Create or update subscription details.
        
        Args:
            is_trial (bool): Whether this is a trial subscription
            plan (str): Plan type ('Free', 'Discounted', 'Standard')
        
        Returns:
            Company: Updated company instance
        """
        from django.utils import timezone
        from datetime import timedelta
        
        print("=" * 80)
        print("💳 [Company.create_subscription] Creating/updating subscription")
        print(f"   Company ID: {self.cmp_id}")
        print(f"   Is Trial: {is_trial}")
        print(f"   Plan: {plan}")
        print(f"   Current is_trial: {self.is_trial}")
        print(f"   Current plan: {self.plan}")
        
        if is_trial:
            self.is_trial = True
            self.start_date = timezone.now()
            self.end_date = timezone.now() + timedelta(days=14)
            self.plan = plan
            print(f"   ✅ Setting up trial subscription")
            print(f"   Start Date: {self.start_date}")
            print(f"   End Date: {self.end_date}")
        else:
            self.is_trial = False
            self.billing_start_date = timezone.now()
            # Set billing_end_date to 1 month from now (can be customized)
            self.billing_end_date = timezone.now() + timedelta(days=30)
            self.plan = plan
            print(f"   ✅ Setting up paid subscription")
            print(f"   Billing Start Date: {self.billing_start_date}")
            print(f"   Billing End Date: {self.billing_end_date}")
        
        self.save()
        
        # ✅ OPTIMIZATION: Invalidate trial check cache for all users in this company
        # This ensures permission checks reflect the updated subscription status
        from django.core.cache import cache
        from apps.elttool.models import User
        users = User.objects.filter(cmp=self).values_list('user_id', flat=True)
        for user_id in users:
            cache.delete(f'trial_check_{user_id}')
        print(f"   ✅ Cache invalidated for {len(users)} users")
        
        print(f"   ✅ Subscription saved successfully")
        print("=" * 80)
        return self
    
    def is_trial_expired(self):
        """
        Check if trial period has expired.
        
        Returns:
            bool: True if trial expired, False otherwise
        """
        from django.utils import timezone
        result = self.is_trial and self.end_date < timezone.now()
        print(f"🔍 [Company.is_trial_expired] Company {self.cmp_id}: {result}")
        return result
    
    def is_trial_active(self):
        """
        Check if trial period is currently active.
        
        Returns:
            bool: True if trial is active, False otherwise
        """
        from django.utils import timezone
        result = self.is_trial and self.end_date > timezone.now()
        print(f"🔍 [Company.is_trial_active] Company {self.cmp_id}: {result}")
        return result
    
    def get_trial_days_remaining(self):
        """
        Get number of days remaining in trial.
        
        Returns:
            int: Days remaining (0 if expired or not on trial)
        """
        from django.utils import timezone
        if self.is_trial and self.end_date > timezone.now():
            days = max(0, (self.end_date - timezone.now()).days)
            print(f"🔍 [Company.get_trial_days_remaining] Company {self.cmp_id}: {days} days")
            return days
        print(f"🔍 [Company.get_trial_days_remaining] Company {self.cmp_id}: 0 days (expired or not trial)")
        return 0


@receiver(models.signals.pre_save, sender=Company)
def sync_company_plan_ref_before_save(sender, instance, **kwargs):
    """Keep company.plan_id aligned with company.plan before every save."""
    instance.sync_plan_ref_from_plan_field()


# Signal to handle encryption after save
@receiver(post_save, sender=Company)
def encrypt_company_data(sender, instance, created, **kwargs):
    if created and hasattr(instance, '_data_to_encrypt'):
        # Calculate created_day and incremented_value for encryption
        created_day = instance.created_at.day  # Extract the day from the created_at timestamp
        incremented_value = instance.cmp_id + created_day  # Combine cmp_id with created day

        # Encrypt the company name using the incremented_value
        encrypted_data = encrypt_field(instance._data_to_encrypt, incremented_value)
        instance.cmp_name = json.dumps(encrypted_data)
        instance.save()  # Save again to persist the encrypted value


# Signal to invalidate trial check cache when Company subscription changes
@receiver(post_save, sender=Company)
def invalidate_trial_check_cache(sender, instance, **kwargs):
    """
    Invalidate trial check cache for all users in the company when subscription changes.
    This ensures permission checks reflect the updated subscription status immediately.
    """
    from django.core.cache import cache
    from apps.elttool.models import User
    
    # Only invalidate if trial/subscription-related fields might have changed
    # This avoids unnecessary cache clearing on unrelated updates
    update_fields = kwargs.get('update_fields')
    if update_fields is not None:
        # Only invalidate if trial/subscription fields were updated
        trial_fields = {
            'is_trial', 'end_date', 'start_date', 'plan', 'plan_ref', 'pod_count',
            'billing_start_date', 'billing_end_date',
        }
        if not any(field in update_fields for field in trial_fields):
            return
    
    # Invalidate cache for all users in this company
    try:
        users = User.objects.filter(cmp=instance).values_list('user_id', flat=True)
        for user_id in users:
            cache.delete(f'trial_check_{user_id}')
    except Exception:
        # Silently fail if cache deletion fails (e.g., cache backend not configured)
        pass




class WorkerNodePoolConfig(models.Model):
    """GKE worker node pool profile for per-job Celery worker scheduling."""
    pool_name = models.CharField(max_length=64, primary_key=True)
    machine_type = models.CharField(max_length=64)
    allocatable_memory = models.CharField(max_length=16)
    allocatable_cpu = models.CharField(max_length=16)
    overhead_memory = models.CharField(max_length=16, default='2Gi')
    overhead_cpu = models.CharField(max_length=16, default='200m')
    max_pods_per_node = models.IntegerField(default=1)
    node_selector = models.JSONField(default=dict)
    taint_key = models.CharField(max_length=128, null=True, blank=True)
    taint_value = models.CharField(max_length=128, null=True, blank=True)
    taint_effect = models.CharField(max_length=32, default='NoSchedule')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'worker_node_pool_config'

    def __str__(self):
        return f"{self.pool_name} ({self.machine_type})"


class PlanResourceConfig(models.Model):
    """Per-plan Celery/K8s resource profile (RFC-001 §6)."""
    plan_name = models.CharField(max_length=32, primary_key=True)
    max_pods = models.PositiveSmallIntegerField()
    cpu_request = models.CharField(max_length=16)
    cpu_limit = models.CharField(max_length=16)
    memory_request = models.CharField(max_length=16)
    memory_limit = models.CharField(max_length=16)
    concurrency = models.PositiveSmallIntegerField()
    max_chunk_size = models.PositiveIntegerField()
    chunk_size_pct = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
    )
    max_concurrency = models.IntegerField(null=True, blank=True)
    max_concurrent_jobs = models.PositiveSmallIntegerField(default=1)
    max_inflight_tables = models.PositiveSmallIntegerField()
    priority = models.SmallIntegerField(default=50)
    extra_config = models.JSONField(default=dict, blank=True)
    worker_pool_name = models.ForeignKey(
        'WorkerNodePoolConfig',
        db_column='worker_pool_name',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        to_field='pool_name',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'plan_resource_config'
        verbose_name = 'plan resource config'
        verbose_name_plural = 'plan resource configs'

    def __str__(self):
        return f"{self.plan_name} ({self.max_pods} pods, concurrency={self.concurrency})"


class CompanyResourceOverride(models.Model):
    """Per-company resource overrides (RFC-001 §7). Nullable fields override plan defaults only."""
    company = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        primary_key=True,
        db_column='company_id',
        related_name='resource_override',
    )
    max_pods = models.PositiveSmallIntegerField(null=True, blank=True)
    cpu_request = models.CharField(max_length=16, null=True, blank=True)
    cpu_limit = models.CharField(max_length=16, null=True, blank=True)
    memory_request = models.CharField(max_length=16, null=True, blank=True)
    memory_limit = models.CharField(max_length=16, null=True, blank=True)
    concurrency = models.PositiveSmallIntegerField(null=True, blank=True)
    max_chunk_size = models.PositiveIntegerField(null=True, blank=True)
    max_concurrent_jobs = models.PositiveSmallIntegerField(null=True, blank=True)
    priority = models.SmallIntegerField(null=True, blank=True)
    extra_config = models.JSONField(null=True, blank=True)
    reason = models.TextField(null=True, blank=True)
    worker_pool_name = models.ForeignKey(
        'WorkerNodePoolConfig',
        db_column='worker_pool_name',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        to_field='pool_name',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'company_resource_override'
        verbose_name = 'company resource override'
        verbose_name_plural = 'company resource overrides'

    def __str__(self):
        return f"override for company {self.company_id}"


class Role(models.Model):
    role_id = models.PositiveIntegerField(primary_key=True)
    role_name = models.CharField(max_length=100, unique=True)

    class Meta:
        db_table = 'role'

    def save(self, *args, **kwargs):
        if not self.role_id:
            last_role_id = Role.objects.aggregate(max_role_id=models.Max('role_id'))['max_role_id']
            if last_role_id is not None:
                self.role_id = last_role_id + 1
            else:
                self.role_id = 1
        super().save(*args, **kwargs)


class RoleAccess(models.Model):
    role_access_id = models.PositiveIntegerField(primary_key=True)  # Not auto-incremented
    type = models.CharField(max_length=100)
    role = models.ForeignKey(Role, on_delete=models.CASCADE)

    class Meta:
        db_table = 'role_access'

    def save(self, *args, **kwargs):
        if not self.role_access_id:
            last_role_access_id = RoleAccess.objects.aggregate(max_role_access_id=models.Max('role_access_id'))['max_role_access_id']
            if last_role_access_id is not None:
                self.role_access_id = last_role_access_id + 1
            else:
                self.role_access_id = 1
        super().save(*args, **kwargs)

class User(AbstractUser):
    username = None
    user_id = models.PositiveIntegerField(primary_key=True)
    cmp = models.ForeignKey(Company,on_delete=models.CASCADE,null=True)
    roles = models.ForeignKey(Role,on_delete=models.CASCADE,null=True)
    first_name=models.CharField(max_length=100)
    last_name=models.CharField(max_length=100)
    company_name=models.CharField(max_length=100)
    options=(
        ("1-10","1-10"),
        ("11-75","11-75"),
        ("76-199","76-199"),
        ("199-2000","199-2000"),
        ("2000+","2000+")
    )
    employee_range=models.CharField(max_length=100,choices=options,default="select")
    company_email=models.EmailField(unique=True)
    
    YOUR_COUNTRY_CHOICES = [
    ('US', 'United States'),
    ('CA', 'Canada'),
    ('MX', 'Mexico'),
    ('GB', 'United Kingdom'),
    ('FR', 'France'),
    ('DE', 'Germany'),
    ('IT', 'Italy'),
    ('IN', 'India'),
    ('JP', 'Japan'),
    ('CN', 'China'),
    ('AU', 'Australia'),
    ('ZA', 'South Africa'),
    ('BR', 'Brazil'),
    ('AR', 'Argentina'),
    ('NG', 'Nigeria'),
    ('EG', 'Egypt'),
    ('KE', 'Kenya'),
    ('SA', 'Saudi Arabia'),
    ('RU', 'Russia'),
    ('KR', 'South Korea'),
]
    country = models.CharField(max_length=100, choices=YOUR_COUNTRY_CHOICES)
    phone= models.CharField(max_length=15, blank=True, null=True)
    is_dark_mode = models.BooleanField(default=False)
    profile_image = models.ImageField(upload_to='profile_images/', null=True, blank=True)
    password_reset_required = models.BooleanField(default=False)
    USERNAME_FIELD = 'company_email'
     #🔑 OVERRIDE THESE TWO
    groups = models.ManyToManyField(
        Group,
        through='UserGroup',
        related_name='users',
        blank=True
    )

    user_permissions = models.ManyToManyField(
        Permission,
        through='UserPermission',
        related_name='users',
        blank=True
    )
    class Meta:
        db_table = 'user'
        indexes = [
            models.Index(fields=['cmp']),
            models.Index(fields=['cmp', 'date_joined']),
            models.Index(fields=['company_email']),  # Already unique, but index helps lookups
        ]
    
    def save(self, *args, **kwargs):
        if not self.user_id:
            last_user_id = User.objects.aggregate(max_user_id=models.Max('user_id'))['max_user_id']
            if last_user_id is not None:
                self.user_id = last_user_id + 1
            else:
                self.user_id = 1
        super().save(*args, **kwargs)
class UserGroup(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    group = models.ForeignKey(Group, on_delete=models.CASCADE)

    class Meta:
        db_table = 'user_groups'
        unique_together = ('user', 'group')


class UserPermission(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE)

    class Meta:
        db_table = 'user_permissions'
        unique_together = ('user', 'permission')

class SourceModel(models.Model):
    src_id = models.PositiveIntegerField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)
    dst_schema=models.CharField(max_length=100)
    deflt_interval = models.CharField(max_length=100, default="15")  # Default interval in minutes
    is_rtl = models.BooleanField(default=False)  # Flag to identify reverse ETL sources (e.g., Snowflake)
    disable_update_schema = models.BooleanField(default=False)
    display_name = models.CharField(max_length=150,null=True,blank=True)
    min_count = models.PositiveIntegerField(default=1)
    max_count = models.PositiveIntegerField(null=True, blank=True)
    compatible_destinations = models.ManyToManyField(
        'DestinationModel',
        blank=True,
        related_name='compatible_sources',
        db_table='source_destination_compatibility',
    )

    class Meta:
        db_table = 'source_model'
    
    def save(self, *args, **kwargs):
        if self.pk is None:
            last_src_id = SourceModel.objects.aggregate(max_src_id=models.Max('src_id'))['max_src_id']
            if last_src_id is not None:
                self.src_id = last_src_id + 1
            else:
                self.src_id = 1
        super().save(*args, **kwargs)



from django.db import models

class DestinationModel(models.Model):
    dst_id = models.PositiveIntegerField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)
    min_count = models.PositiveIntegerField(default=0)  # Default value can be adjusted as needed
    max_count = models.PositiveIntegerField(default=500)  # Default value can be adjusted as needed
    is_file_based = models.BooleanField(default=False, blank=True)
    supports_notification_groups = models.BooleanField(default=False, blank=True)
    path_label = models.CharField(max_length=100, blank=True, null=True)
    supported_formats = models.JSONField(blank=True, null=True)
    default_format = models.CharField(max_length=20, blank=True, null=True)
    csv_defaults = models.JSONField(blank=True, null=True)

    class Meta:
        db_table = 'destination_model'

    def save(self, *args, **kwargs):
        # Only assign dst_id on create. Never rewrite an existing PK (that used
        # to force dst_id=1 and attach attributes to the wrong destination).
        if not self.dst_id:
            last_dst_id = DestinationModel.objects.aggregate(
                max_dst_id=models.Max('dst_id')
            )['max_dst_id']
            self.dst_id = (last_dst_id + 1) if last_dst_id is not None else 1
        super().save(*args, **kwargs)
    
class SourceAttribute(models.Model):
    # Widget type choices
    TEXT_INPUT = 'TextInput'
    PASSWORD_INPUT = 'PasswordInput'
    SELECT = 'Select'
    TEXTAREA = 'Textarea'

    WIDGET_CHOICES = [
        (TEXT_INPUT, 'TextInput'),
        (PASSWORD_INPUT, 'PasswordInput'),
        (SELECT, 'Select'),
        (TEXTAREA, 'Textarea'),
    ]
    src_attr_id = models.IntegerField(primary_key=True)
    src = models.ForeignKey('SourceModel', on_delete=models.CASCADE)
    attribute_name = models.CharField(max_length=100)
    input_type = models.CharField(max_length=50)
    label = models.CharField(max_length=100)
    widget = models.CharField(max_length=50, choices=WIDGET_CHOICES, blank=True, null=True)
    required = models.BooleanField(default=True)
    is_visible = models.BooleanField(default=True)
    choices = models.JSONField(null=True, blank=True)
    depend_on = models.CharField(max_length=100, null=True, blank=True)
    dependency_value = models.CharField(max_length=100, null=True, blank=True)
    read_only = models.BooleanField(default=False)
    description = models.TextField(null=True, blank=True, help_text="Help text/description for the form field")
    placeholder = models.CharField(max_length=255, null=True, blank=True, help_text="Placeholder text for the input field")
    default_value = models.CharField(max_length=500, null=True, blank=True, help_text="Default value for the form field")
    
    class Meta:
        db_table = 'source_attribute'
    
    def save(self, *args, **kwargs):
        if self.pk is None:
            last_src_attr_id = SourceAttribute.objects.aggregate(max_src_attr_id=models.Max('src_attr_id'))['max_src_attr_id']
            if last_src_attr_id is not None:
                self.src_attr_id = last_src_attr_id + 1
            else:
                self.src_attr_id = 1
        super().save(*args, **kwargs)


from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
import json

class SourceConfig(models.Model):
    src_config_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100)
    src = models.ForeignKey('SourceModel', on_delete=models.CASCADE)
    cmp = models.ForeignKey('Company', on_delete=models.SET_NULL, null=True)
    config_data = models.TextField(null=True)  # Encrypted field stored in the database
    is_active = models.BooleanField(default=False)
    last_synced = models.DateTimeField(auto_now_add=True)
    response_message = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'source_config'
        indexes = [
            models.Index(fields=['cmp', 'is_active']),
            models.Index(fields=['last_synced']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['cmp', 'name'],
                condition=models.Q(is_active=True),
                name='uniq_active_source_config_name_per_cmp',
            ),
        ]

    @property
    def data(self):
        """Decrypt and return the config data."""
        return self._decrypt_field(self.config_data)

    @data.setter
    def data(self, value):
        """Temporarily store `data` for encryption during save."""
        if value is not None:
            self._data_to_encrypt = value  # Store raw value temporarily
        else:
            self._data_to_encrypt = None
            self.config_data = None

    def _decrypt_field(self, encrypted_value):
        """Decrypt the encrypted field."""
        # print(f"\n🔍 [DECRYPTION DEBUG] _decrypt_field called")
        # print(f"   - encrypted_value type: {type(encrypted_value)}")
        # print(f"   - encrypted_value is None: {encrypted_value is None}")
        # print(f"   - encrypted_value is empty: {encrypted_value == ''}")
        
        if encrypted_value:
            try:
                print(f"   - Attempting to parse JSON...")
                encrypted_value = json.loads(encrypted_value)
                print(f"   - JSON parsed successfully")
                print(f"   - Unpacking encrypted data...")
                (encrypted_data, nonce, tag, salt, original_type, iterations) = encrypted_value
                print(f"   - Unpacked successfully")

                if self.cmp is None:
                    print(f"   ❌ ERROR: cmp is None, cannot decrypt!")
                    return None
                
                created_day = self.cmp.created_at.day  # Extract the day from the created_at timestamp
                incremented_value = self.cmp.cmp_id + created_day    # Combine cmp_id with created day
                print(f"   - created_day: {created_day}")
                print(f"   - incremented_value: {incremented_value}")

                print(f"   - Calling decrypt_field...")
                result = decrypt_field(
                    encrypted_data, incremented_value, nonce, tag, salt, original_type
                )
                print(f"   - Decryption result type: {type(result)}")
                print(f"   - Decryption result is None: {result is None}")
                return result
            except (ValueError, KeyError, TypeError) as e:
                print(f"   ❌ Decryption error: {e}")
                print(f"   ❌ Error type: {type(e).__name__}")
                import traceback
                print(f"   ❌ Traceback: {traceback.format_exc()}")
                return None
        else:
            print(f"   ⚠️  encrypted_value is falsy, returning None")
        return None

@receiver(post_save, sender=SourceConfig)
def encrypt_source_config_data(sender, instance, created, **kwargs):
    """
    Encrypt the `data` property of SourceConfig after saving.
    Handles both create and update if `_data_to_encrypt` is set.
    """
    if hasattr(instance, '_data_to_encrypt') and instance._data_to_encrypt is not None:
        if instance.cmp is None:
            logger.error(
                "SourceConfig encryption skipped: cmp is None (src_config_id=%s)",
                instance.src_config_id,
            )
            return

        created_day = instance.cmp.created_at.day
        incremented_value = instance.cmp.cmp_id + created_day
        encrypted_data = encrypt_field(instance._data_to_encrypt, incremented_value)
        SourceConfig.objects.filter(pk=instance.pk).update(
            config_data=json.dumps(encrypted_data)
        )
        instance._data_to_encrypt = None


    
class DestinationAttribute(models.Model):
    dst_attr_id = models.IntegerField(primary_key=True)

    # Field type choices
    CHAR_FIELD = 'CharField'
    BOOLEAN_FIELD = 'BooleanField'
    DATETIME_FIELD = 'DateTimeField'
    DURATION_FIELD = 'DurationField'
    CHOICE_FIELD = 'ChoiceField'
    PASSWORD_FIELD = 'PasswordField'
    TEXTAREA_FIELD = 'TextAreaField'

    INPUT_TYPE_CHOICES = [
        (CHAR_FIELD, 'CharField'),
        (BOOLEAN_FIELD, 'BooleanField'),
        (DATETIME_FIELD, 'DateTimeField'),
        (DURATION_FIELD, 'DurationField'),
        (CHOICE_FIELD, 'ChoiceField'),
        (PASSWORD_FIELD, 'PasswordField'),
        (TEXTAREA_FIELD, 'TextAreaField'),
    ]

    # Widget type choices
    TEXT_INPUT = 'TextInput'
    PASSWORD_INPUT = 'PasswordInput'
    SELECT = 'Select'
    TEXTAREA = 'Textarea'

    WIDGET_CHOICES = [
        (TEXT_INPUT, 'TextInput'),
        (PASSWORD_INPUT, 'PasswordInput'),
        (SELECT, 'Select'),
        (TEXTAREA, 'Textarea'),
    ]

    dst = models.ForeignKey('DestinationModel', on_delete=models.CASCADE)
    attribute_name = models.CharField(max_length=255)
    input_type = models.CharField(max_length=100, choices=INPUT_TYPE_CHOICES)
    label = models.CharField(max_length=255)
    choices = models.JSONField(blank=True, null=True)
    widget = models.CharField(max_length=50, choices=WIDGET_CHOICES, blank=True, null=True)
    required = models.BooleanField(default=True)
    is_visible = models.BooleanField(default=True)

    # Simple dependency fields
    depend_on = models.CharField(max_length=255, blank=True, null=True)   # e.g., "authentication_type"
    dependency = models.CharField(max_length=255, blank=True, null=True)  # e.g., "key_pair"
    read_only = models.BooleanField(default=False)

    class Meta:
        db_table = 'destination_attribute'

    def save(self, *args, **kwargs):
        if self.pk is None:
            last_dst_attr_id = DestinationAttribute.objects.aggregate(
                max_dst_attr_id=models.Max('dst_attr_id')
            )['max_dst_attr_id']
            self.dst_attr_id = (last_dst_attr_id + 1) if last_dst_attr_id is not None else 1
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.attribute_name} ({self.label})"



class DestinationConfig(models.Model):
    dst_config_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100)
    dst = models.ForeignKey('DestinationModel', on_delete=models.CASCADE)
    cmp = models.ForeignKey('Company', on_delete=models.SET_NULL, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='destination_configs_created'
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='destination_configs_updated'
    )
    config_data = models.TextField(null=True)  # Encrypted field stored in the database
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    response_message = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'destination_config'
        indexes = [
            models.Index(fields=['cmp', 'is_active']),
            models.Index(fields=['updated_at']),
            models.Index(fields=['cmp', 'is_active', 'updated_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['cmp', 'name'],
                condition=models.Q(is_active=True),
                name='uniq_active_destination_config_name_per_cmp',
            ),
        ]

    @property
    def data(self):
        """Decrypt and return the config data."""
        return self._decrypt_field(self.config_data)

    @data.setter
    def data(self, value):
        """Temporarily store `data` for encryption."""
        if value is not None:
            self._data_to_encrypt = value  # Store raw value temporarily
        else:
            self._data_to_encrypt = None
            self.config_data = None

    def _decrypt_field(self, encrypted_value):
        """Decrypt the encrypted field."""
        # print("decryption for",encrypted_value)
        if encrypted_value:
            try:
                encrypted_value = json.loads(encrypted_value)
                (
                    encrypted_data,
                    nonce,
                    tag,
                    salt,
                    original_type,
                    iterations
                ) = encrypted_value


                created_day = self.cmp.created_at.day  # Extract the day from the created_at timestamp
                incremented_value = self.cmp.cmp_id + created_day   # Combine cmp_id with created day

                # Use incremented_value for decryption
                return decrypt_field(
                    encrypted_data, incremented_value, nonce, tag, salt, original_type
                )
            except (ValueError, KeyError, TypeError) as e:
                print(f"Decryption error: {e}")
                return None
        return None

# Signal to handle encryption after save
@receiver(post_save, sender=DestinationConfig)
def encrypt_destination_config_data(sender, instance, created, **kwargs):
    # ✅ FIX: Handle both creation and updates
    if hasattr(instance, '_data_to_encrypt') and instance._data_to_encrypt is not None:
        print(f"🔐 Encryption signal triggered for DestinationConfig (ID: {instance.dst_config_id})")
        print(f"   - Created: {created}")
        print(f"   - Has data to encrypt: {bool(instance._data_to_encrypt)}")
        
        created_day = instance.cmp.created_at.day  # Extract the day from the created_at timestamp
        incremented_value = instance.cmp.cmp_id + created_day   # Combine cmp_id with created day

        # Encrypt the data using the incremented_value
        encrypted_data = encrypt_field(instance._data_to_encrypt, incremented_value)
        print(f"   ✅ Data encrypted successfully")
        
        # queryset.update does not refresh the instance. Keep config_data on the
        # object so callers can decrypt `.data` in the same request.
        encrypted_payload = json.dumps(encrypted_data)
        DestinationConfig.objects.filter(pk=instance.pk).update(
            config_data=encrypted_payload
        )
        instance.config_data = encrypted_payload
        print(f"   ✅ Encrypted data saved to database")
        
        # Clear the temporary attribute
        instance._data_to_encrypt = None
    else:
        if created:
            print(f"⚠️ Encryption signal: No data to encrypt for new DestinationConfig (ID: {instance.dst_config_id})")
        else:
            print(f"ℹ️ Encryption signal: No data to encrypt for update (ID: {instance.dst_config_id})")


#optional



    
class Connection(models.Model):
    connection_id = models.IntegerField(primary_key=True)
    cmp = models.ForeignKey(Company, on_delete=models.SET_NULL, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='connections_created'
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='connections_updated'
    )
    dst_config = models.ForeignKey(DestinationConfig, on_delete=models.CASCADE)
    src_config = models.ForeignKey(SourceConfig, on_delete=models.CASCADE)
    dst_schema = models.CharField(max_length=50, null=True)
    choice = [
        ('A', 'A'),
        ('B', 'B'),
        ('P', 'P'),
        ('D', 'D'),
    ]
    DELETED = models.CharField(max_length=20, default="_SPECT_DELETED")
    SYNCED = models.CharField(max_length=20, default="_SPECT_SYNCED")
    status = models.CharField(max_length=100, choices=choice)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    time_frequency = models.CharField(max_length=100, default="None")
    safety_interval = models.CharField(max_length=100, default="None")  # Set from source model's deflt_interval in save()
    custom_structure = models.PositiveIntegerField(default=0)
    order = [
        ('parallel', 'parallel'),
        ('sequential', 'sequential'),
    ]
    execution_order = models.CharField(max_length=100, choices=order, default="parallel")
    sync_start_date = models.DateTimeField(null=True, blank=True)
    last_synced = models.CharField(max_length=100, null=True)
    retrieval_progress = models.CharField(
        max_length=32,
        null=True,
        blank=True,
        default=None,
        help_text='Dynamics retrieval ratio e.g. 50/500; cleared when listing/migration completes.',
    )

    # New field for chunk count
    chunk_count = models.PositiveIntegerField()
    logs_creation=models.BooleanField(default=False)

    class Meta:
        db_table = 'connection'
        indexes = [
            models.Index(fields=['cmp', 'status']),
            models.Index(fields=['updated_at']),
            models.Index(fields=['status', 'updated_at']),
            models.Index(fields=['time_frequency']),
            models.Index(fields=['cmp', 'status', 'updated_at']),
        ]

    def save(self, *args, **kwargs):
        # Set default chunk_count based on source and destination
        if self.pk is None:  # If the object is being created
            # Set default safety_interval from source model's deflt_interval if not already set or is "None"
            if not self.safety_interval or self.safety_interval == "None":
                # Get default interval from source model, fallback to "15" if not available
                source_default_interval = getattr(self.src_config.src, 'deflt_interval', '15')
                self.safety_interval = source_default_interval
            
            from apps.elttool.services.chunk_limit_resolver import resolve_default_connection_chunk_count

            self.chunk_count = resolve_default_connection_chunk_count(
                self.cmp,
                self.src_config.src,
                self.dst_config.dst,
            )

            # Set connection_id if not already set
            last_connection_id = Connection.objects.aggregate(max_connection_id=models.Max('connection_id'))['max_connection_id']
            if last_connection_id is not None:
                self.connection_id = last_connection_id + 1
            else:
                self.connection_id = 1

        super().save(*args, **kwargs)


@receiver(post_save, sender=Company)
def sync_connection_chunks_on_plan_change(sender, instance, created, **kwargs):
    """Recompute connection.chunk_count when company.plan changes."""
    if kwargs.get("raw") or created:
        return
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and "plan" not in update_fields:
        return
    from apps.elttool.services.chunk_limit_resolver import resolve_default_connection_chunk_count

    for conn in Connection.objects.filter(cmp=instance).select_related(
        "src_config__src", "dst_config__dst"
    ):
        src = conn.src_config.src if conn.src_config else None
        dst = conn.dst_config.dst if conn.dst_config else None
        if not src or not dst:
            continue
        new_chunk = resolve_default_connection_chunk_count(instance, src, dst)
        if conn.chunk_count != new_chunk:
            Connection.objects.filter(pk=conn.pk).update(chunk_count=new_chunk)


import json

from django.core.serializers.json import DjangoJSONEncoder

from django.db import models, transaction, IntegrityError

class SourceTableSelection(models.Model):
    tbl_id = models.AutoField(primary_key=True)  # Auto-incrementing primary key
    connection = models.ForeignKey(Connection, on_delete=models.DO_NOTHING, null=True)
    sequence = models.IntegerField(null=True)
    selected = models.BooleanField(default=False)
    is_mapping_checked = models.BooleanField(default=False)
    last_synced = models.CharField(max_length=100, null=True)
    account_type = models.CharField(
        max_length=10,
        null=True,
        blank=True,
        choices=[
            ('admin', 'Admin'),
            ('user', 'User'),
        ],
    )
    updated_by_id = models.IntegerField(null=True, blank=True)
    table = models.CharField(max_length=100)
    folder_path = models.CharField(max_length=300, null=True)
    json_path = models.CharField(max_length=300, null=True)
    table_fields = models.JSONField()
    primary_keys = models.JSONField(default=list, blank=True)
    # Generic file-export settings for file-based reverse ETL destinations.
    output_file_name = models.CharField(max_length=255, null=True, blank=True)
    target_folder = models.CharField(max_length=255, null=True, blank=True)
    file_format = models.CharField(max_length=20, null=True, blank=True)  # csv/json/parquet/excel (validated in API)
    csv_delimiter = models.CharField(max_length=5, null=True, blank=True)
    csv_quote_char = models.CharField(max_length=5, null=True, blank=True)
    excel_sheet_name = models.CharField(max_length=100, null=True, blank=True)
    excel_options = models.JSONField(null=True, blank=True)
    excel_conditional_formats = models.JSONField(default=list, blank=True, null=True)
    add_utc_timestamp = models.BooleanField(default=True, null=True, blank=True)
    filename_date_format = models.CharField(max_length=20, null=True, blank=True)
    selected_fields = models.JSONField(null=True, blank=True)  # File-based destinations: ordered export field list; null = all fields
    notification_email_group_ids = models.JSONField(default=list, blank=True, null=True)
    email_custom_fields = models.JSONField(default=dict, blank=True, null=True)
    email_template = models.ForeignKey(
        "EmailTemplate", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="export_settings",
    )
    added_on = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, null=True)
    is_history = models.BooleanField(default=False)  # <-- New field to flag history tables
    delta_timestamp_field = models.CharField(max_length=150, blank=True,null=True)
    combination_formula = models.CharField(max_length=500, null=True, blank=True)
    truncate_data = models.BooleanField(default=False,null=True,blank=True)
    is_delta = models.BooleanField(default=False, null=True, blank=True) # <-- New field to flag delta load
    service_name = models.CharField(max_length=150, null=True, blank=True)
    delta_name = models.CharField(max_length=150, null=True, blank=True)
    delta_token = models.TextField(null=True, blank=True)
    load_method = models.CharField(max_length=50, null=True, blank=True, default='initial')
    partition_delta_by_date = models.BooleanField(default=False, null=True, blank=True)
    compression_method = models.CharField(max_length=50, null=True, blank=True, default='none')

    # ── ETL lifecycle control fields (SAP OData → ADLS Gen2) ──────────────────
    # Datetime of the first successful load run (initial snapshot or delta init).
    # Null until the very first run completes for any load method.
    first_sync_timestamp = models.DateTimeField(null=True, blank=True)
    # True once the initial snapshot (or delta-only initialisation) has completed.
    # Controls whether subsequent runs skip the initial phase.
    initial_completed_flag = models.BooleanField(default=False, null=True, blank=True)
    # True for INITIAL_DELTA and DELTA_ONLY after the first successful run.
    # Prevents the user from changing load_method once delta tracking has started.
    load_method_locked = models.BooleanField(default=False, null=True, blank=True)
    # Datetime of the most recent successful delta extraction run.
    # Null for INITIAL method; updated after every successful delta pull.
    last_delta_run_timestamp = models.DateTimeField(null=True, blank=True)
    delete_and_load = models.BooleanField(blank=True, null=True, default=None)


    # ── Row filtering control fields (SAP OData → ADLS Gen2) ──────────────────
    row_filter_config = models.JSONField(blank=True, null=True, default=None)
    locked_row_filter_config = models.JSONField(blank=True, null=True, default=None)


    class Meta:
        db_table = 'source_table_selection'
        unique_together = ('table', 'connection')
        indexes = [
            models.Index(fields=['connection', 'selected']),
            models.Index(fields=['connection', 'status']),
            models.Index(fields=['connection', 'sequence']),
            models.Index(fields=['connection', 'table']),
            models.Index(fields=['connection', 'selected', 'status']),
        ]
        

from django.db import models

class DestinationTableSelection(models.Model):
    tbl_id = models.AutoField(primary_key=True)  # Auto-incrementing primary key
    connection = models.ForeignKey(Connection, on_delete=models.DO_NOTHING, null=True)
    # sequence = models.IntegerField(null=True)
    selected = models.BooleanField(default=False)
    table = models.CharField(max_length=100)  # Destination table name
    table_type=models.CharField(max_length=100)
    table_fields = models.JSONField()
    added_on = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, null=True)

    class Meta:
        db_table = 'destination_table_selection'
        unique_together = ('table', 'connection')
        indexes = [
            models.Index(fields=['connection', 'table']),
            models.Index(fields=['connection', 'status']),
        ]

    def __str__(self):
        return f"{self.table} ({self.connection})"
    


class TableMapping(models.Model):
    mapping_id = models.AutoField(primary_key=True)
    connection = models.ForeignKey(Connection, on_delete=models.DO_NOTHING, null=True)
    destination_table = models.ForeignKey(DestinationTableSelection, on_delete=models.CASCADE)  # One Destination
    source_table = models.ForeignKey(SourceTableSelection, on_delete=models.CASCADE)  # Multiple Sources
    added_on = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'table_mapping_retl'
        unique_together = ('destination_table', 'source_table')  # Prevent duplicate mappings
        indexes = [
            models.Index(fields=['connection', 'destination_table']),
            models.Index(fields=['connection', 'source_table']),
        ]

    def __str__(self):
        return f"{self.destination_table.table} -> {self.source_table.table}"


class Log(models.Model):
    CHOICES = (
        ('S', 'Success'),
        ('E', 'Error'),
        ('I', 'In Progress'),
        ('P', 'Partial')
    )
    log_id = models.AutoField(primary_key=True)
    time = models.DateTimeField(auto_now_add=True)
    message = models.TextField()
    cmp = models.ForeignKey(Company, on_delete=models.DO_NOTHING)
    connection = models.ForeignKey(Connection, on_delete=models.DO_NOTHING, null=True)
    user = models.ForeignKey(User, on_delete=models.DO_NOTHING)
    status = models.CharField(max_length=1, choices=CHOICES)
    session = models.IntegerField(default=1,null=True)
      # Renamed session_number to session
    job_name = models.TextField(blank=True, null=True)  # Link to migration jobs
    migration_record = models.ForeignKey(
        'MigrationRecord',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='logs'
    )  # Direct link to table-level record

    class Meta:
        db_table = 'log'
        indexes = [
            models.Index(fields=['connection', 'time']),
            models.Index(fields=['session', 'connection']),
            models.Index(fields=['time']),
            models.Index(fields=['connection', 'status', 'time']),
            models.Index(fields=['cmp', 'time']),
        ]
    @classmethod
    def get_logs_with_session_info(cls, connection_id, time_filter=None, limit=10):
        """
        Get logs with session information included
        Returns logs categorized by whether they're part of a migration session
        """
        logs_qs = cls.objects.filter(connection_id=connection_id)
        if time_filter:
            logs_qs = logs_qs.filter(time__gte=time_filter)
        
        logs = logs_qs.order_by('-time')[:limit]
        
        result = []
        for log in logs:
            log_data = {
                'log_id': log.log_id,
                'message': log.message,
                'time': log.time,
                'status': log.status,
                'user': log.user,
                'session_id': log.session,
                'is_session_log': cls._is_session_log(log)
            }
            result.append(log_data)
        
        return result
    
    @classmethod
    def _is_session_log(cls, log):
        """
        Determine if a log is part of a migration session
        Only logs with a session ID > 1 are considered session logs
        """
        # Check if log has a valid session ID
        has_valid_session = log.session is not None and log.session > 1
        
        # A log is session-related ONLY if it has a valid session ID
        return has_valid_session
    
    @classmethod
    def _is_summary_log(cls, log):
        """
        Determine if a log is a session summary (start/complete) rather than detailed table log
        Only session summaries should appear in main activity feed
        """
        message_lower = log.message.lower()
        
        # EXCLUDE individual table migration logs first (these should be hidden)
        # Examples:
        # - "Successfully migrated/updated 'custgroup' table, containing data with 34 rows and 34 columns."
        # - "accounts migration: Success - 1250, Failure - 0"
        table_detail_patterns = [
            'successfully migrated/updated',  # From Microsoft Dynamics
            'migration: success -',           # From Salesforce
            'migration: failure -',
            ': success -',
            ': failure -',
            'containing data with',
            'rows and',
            'columns.'
        ]
        
        # If it has a table name AND matches table detail patterns, hide it
        # if log.table is not None and log.table != "Migration":
        #     for pattern in table_detail_patterns:
        #         if pattern in message_lower:
        #             return False  # Hide table-specific logs
        
        # Also hide if message contains table detail patterns even without table field
        for pattern in table_detail_patterns:
            if pattern in message_lower and 'table' in message_lower:
                return False
        
        # Session summary keywords (these SHOULD be shown)
        summary_keywords = [
            'migration session started',    # "Migration session started"
            'migration completed successfully in',  # "Migration completed successfully in X seconds..." (detailed)
            'migration completed',          # "Migration completed successfully" / "Migration completed with errors"
            'migration partially completed',
            'migration in progress',        # Initial migration message  
            'schema refresh',               # Schema operations
            'update schema in progress',    # Initial listing/update-schema message
            'update schema completed',      # Listing/update-schema completion
            'update schema failed',         # Listing/update-schema failure
            'tables listed and updated',    # Table listing operations
            'warning: another migration',   # Migration warnings
            'drop and reload initiated',    # Single table drop and reload
            'delta refresh initiated',      # Single table delta refresh
            'full initial load initiated',  # Single table full initial load
        ]
        
        # EXCLUDE old-style completion messages
        if 'all tables migrated successfully' in message_lower and log.session == 0:
            return False  # Hide old completion logs
        
        # This is a summary log if it contains summary keywords
        is_summary = any(keyword in message_lower for keyword in summary_keywords)
        
        # Return True for summary logs
        return is_summary
    
    
    @classmethod
    def get_unique_sessions(cls, connection_id, time_filter=None):
        """
        Get list of unique session IDs for a connection
        """
        logs_qs = cls.objects.filter(
            connection_id=connection_id,
            session__isnull=False,
            session__gt=1  # Exclude default session
        )
        if time_filter:
            logs_qs = logs_qs.filter(time__gte=time_filter)
        
        # Get unique session IDs ordered by most recent
        session_ids = logs_qs.values_list('session', flat=True).distinct().order_by('-session')
        
        return list(session_ids)
# ✅ Signal to trigger WebSocket update after Log creation or job-level log update
@receiver(post_save, sender=Log)
def trigger_log_websocket_update(sender, instance, created, **kwargs):
    """
    Trigger WebSocket update for Activity Log after a job-level log is created
    or updated (e.g. Migration in progress → Migration completed).
    Uses transaction.on_commit to ensure the log is visible before triggering the update.
    """
    if not instance.connection_id:
        return
    if instance.migration_record_id is not None:
        return

    connection_id = instance.connection_id
    log_message = instance.message[:50] if instance.message else ""

    def trigger_ws():
        try:
            from apps.elttool.websocket.ws_utils import trigger_connection_activity_log_update
            action = "created" if created else "updated"
            logger.info(
                f"🔔 Log {action}, triggering WS update for connection {connection_id} | message: {log_message}..."
            )
            trigger_connection_activity_log_update(connection_id)
        except Exception as e:
            logger.error(f"❌ Failed to trigger WS update for log: {e}")

    def trigger_company_ws():
        try:
            cmp_id = instance.cmp_id
            if cmp_id:
                from apps.elttool.websocket.ws_utils import push_company_connections_status_update
                push_company_connections_status_update(
                    cmp_id,
                    {
                        "connection_id": connection_id,
                        "error_message": instance.message if instance.status == 'E' else None
                    }
                )
        except Exception as e:
            logger.error(f"❌ Failed to trigger company WS update for log: {e}")

    transaction.on_commit(trigger_ws)
    transaction.on_commit(trigger_company_ws)

class Data_Transfer(models.Model):
    tr_id = models.AutoField(primary_key=True)
    # ✅ Direct connection FK — billing/usage queries use this directly.
    # Survives log + migration_record cleanup after 92 days.
    connection = models.ForeignKey(
        'Connection',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='connection_transfers'
    )
    # ✅ SET_NULL: deleting a Log row will NULL this FK, not delete the data_transfer row
    log = models.ForeignKey(Log, on_delete=models.SET_NULL, null=True, blank=True)
    # ✅ SET_NULL + nullable: deleting a MigrationRecord will NULL this FK, not delete the row
    migration_record = models.ForeignKey(
        'MigrationRecord',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='data_transfers'
    )
    table_name = models.CharField(max_length=255)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()

    new_rec = models.PositiveIntegerField()
    total_rec = models.PositiveIntegerField()
    mod_rec = models.PositiveIntegerField()
    del_rec = models.PositiveIntegerField()

    class Meta:
        db_table = 'data_transfer'
        indexes = [
            models.Index(fields=['start_time']),
            models.Index(fields=['connection']),
        ]


class MigrationRecord(models.Model):
    STATUS_CHOICES = [
        ('completed', 'Completed'),
        ('in_progress', 'In Progress'),
        ('failed', 'Failed'),
        ('skipped', 'Skipped'),
        ('deleted', 'Deleted'),
    ]
    
    connection = models.ForeignKey('Connection', on_delete=models.CASCADE)  # Assuming 'Connection' model exists
    migration_session_id = models.PositiveIntegerField()  # Integer to identify the migration session
    table_name = models.CharField(max_length=255,null=True)
    job_name = models.TextField()   # New field for job name
    task_id = models.CharField(max_length=255, null=True, blank=True)  # New task_id field, allowing null values
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='in_progress')
    start_time = models.DateTimeField(blank=True, null=True, help_text='Timestamp when migration actually started (status changed to in_progress)')
    staging_records_count = models.PositiveIntegerField(default=0, help_text='Cumulative rows merged to base (Databricks per-chunk load)')
    transform_version = models.PositiveIntegerField(null=True, blank=True, help_text='EntityTransform version used for this run')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'migration_record'
        unique_together = ('migration_session_id', 'table_name', 'job_name')  # Ensure uniqueness of job per session/table
        indexes = [
            models.Index(fields=['connection', 'created_at']),
            models.Index(fields=['connection', 'status']),
            models.Index(fields=['migration_session_id']),
            models.Index(fields=['connection', 'status', 'created_at']),
            models.Index(fields=['task_id']),
             models.Index(fields=['job_name'], name='idx_migrec_job_name'),
            models.Index(fields=['job_name', 'table_name'], name='idx_migrec_job_table'),
        ]

    def __str__(self):
        return f"{self.connection} - Session: {self.migration_session_id} - Table: {self.table_name} - Job: {self.job_name} - Status: {self.status}"


class MigrationBatch(models.Model):
    """
    A named schedule group that owns a subset of a connection's tables.
    Each batch has its own APScheduler job so tables can run on independent
    frequencies.  The internal job-id is keyed on the PK (never the name),
    so renaming a batch is safe.
    """
    STATUS_CHOICES = [('active', 'Active'), ('paused', 'Paused')]
    EXEC_CHOICES = [('parallel', 'parallel'), ('sequential', 'sequential')]
    SCHEDULE_TYPE_CHOICES = [('interval', 'interval'), ('cron', 'cron')]

    connection = models.ForeignKey(
        'Connection', on_delete=models.CASCADE, related_name='batches'
    )
    name = models.CharField(max_length=255, default='Batch 1')
    time_frequency = models.CharField(max_length=100, default='15')
    execution_order = models.CharField(
        max_length=100, choices=EXEC_CHOICES, default='parallel'
    )
    schedule_type = models.CharField(
        max_length=20, choices=SCHEDULE_TYPE_CHOICES, default='interval'
    )
    schedule_config = models.JSONField(default=dict, blank=True)
    sync_start_date = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default='active'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'migration_batch'
        unique_together = (('connection', 'name'),)
        indexes = [
            models.Index(fields=['connection', 'status']),
            models.Index(fields=['connection', 'updated_at']),
        ]

    def __str__(self):
        return f"Batch '{self.name}' (conn {self.connection_id}) — {self.time_frequency} min"


class BatchTable(models.Model):
    # batch image verify marker: ensure BatchTable ships in django :batch image
    """
    Associates one source table with one MigrationBatch.
    The UniqueConstraint on (connection, table_name) enforces the invariant
    that a table belongs to at most one batch per connection.
    """
    batch = models.ForeignKey(
        MigrationBatch, on_delete=models.CASCADE, related_name='tables'
    )
    connection = models.ForeignKey('Connection', on_delete=models.CASCADE)
    table_name = models.CharField(max_length=500)
    sequence = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'batch_table'
        constraints = [
            models.UniqueConstraint(
                fields=['connection', 'table_name'],
                name='uniq_batchtable_conn_table'
            )
        ]
        indexes = [
            models.Index(fields=['batch']),
            models.Index(fields=['connection', 'table_name']),
        ]

    def __str__(self):
        return f"{self.table_name} → {self.batch}"


class ScheduledJob(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
    ]
    
    connection = models.ForeignKey('Connection', on_delete=models.CASCADE)
    batch = models.ForeignKey(
        MigrationBatch,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='scheduled_jobs',
    )
    job_name = models.CharField(max_length=255)
    frequency_minutes = models.PositiveIntegerField()  # Sync frequency in minutes
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    last_run = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'scheduled_job'
        indexes = [
            models.Index(fields=['connection', 'status']),
            models.Index(fields=['connection', 'updated_at']),
            models.Index(fields=['job_name']),
            models.Index(fields=['connection', 'status', 'updated_at']),
            models.Index(fields=['batch', 'status']),
        ]

    def __str__(self):
        return f"{self.job_name} - Frequency: {self.frequency_minutes} mins - Status: {self.status}"


class Pipeline(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('paused', 'Paused'),
    ]

    cmp = models.ForeignKey('Company', on_delete=models.CASCADE, related_name='pipelines')
    name = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    schedule_paused = models.BooleanField(
        default=False,
        help_text='When true, automatic schedule is paused without changing flow status.',
    )
    schedule_type = models.CharField(
        max_length=20,
        choices=[('manual', 'manual'), ('interval', 'interval'), ('cron', 'cron')],
        default='interval',
    )
    time_frequency = models.CharField(max_length=100, default='15')
    schedule_config = models.JSONField(default=dict, blank=True)
    sync_start_date = models.DateTimeField(null=True, blank=True)
    sync_end_date = models.DateTimeField(null=True, blank=True)
    notify_on_flow_finish = models.BooleanField(default=True)
    notification_email_group_ids = models.JSONField(default=list, blank=True)
    notification_emails = models.JSONField(default=list, blank=True)
    validated_graph = models.JSONField(null=True, blank=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    validated_by = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='validated_pipelines',
    )
    created_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'pipeline'
        unique_together = (('cmp', 'name'),)
        indexes = [
            models.Index(fields=['cmp', 'status']),
            models.Index(fields=['cmp', 'updated_at']),
        ]

    def __str__(self):
        return f"Pipeline {self.name} ({self.cmp_id})"


class PipelineNode(models.Model):
    NODE_KIND_CHOICES = [('start', 'start'), ('batch', 'batch')]
    SCHEDULE_TYPE_CHOICES = [('interval', 'interval'), ('cron', 'cron')]

    pipeline = models.ForeignKey(Pipeline, on_delete=models.CASCADE, related_name='nodes')
    node_kind = models.CharField(max_length=20, choices=NODE_KIND_CHOICES, default='batch')
    connection = models.ForeignKey(
        'Connection', on_delete=models.CASCADE, related_name='pipeline_nodes',
        null=True, blank=True,
    )
    batch = models.ForeignKey(
        MigrationBatch, on_delete=models.CASCADE, related_name='pipeline_nodes',
        null=True, blank=True,
    )
    node_label = models.CharField(max_length=255, blank=True, default='')
    x = models.IntegerField(default=0)
    y = models.IntegerField(default=0)
    order_index = models.PositiveIntegerField(default=0)
    execution_order = models.CharField(max_length=100, choices=MigrationBatch.EXEC_CHOICES, default='parallel')
    schedule_type = models.CharField(max_length=20, choices=SCHEDULE_TYPE_CHOICES, default='interval')
    time_frequency = models.CharField(max_length=100, default='15')
    schedule_config = models.JSONField(default=dict, blank=True)
    sync_start_date = models.DateTimeField(null=True, blank=True)
    notify_on_completion = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'pipeline_node'
        constraints = [
            # A migration batch may appear on at most one pipeline canvas.
            models.UniqueConstraint(
                fields=['batch'],
                condition=models.Q(node_kind='batch'),
                name='uniq_pipeline_node_batch_global',
            ),
            models.UniqueConstraint(
                fields=['pipeline'],
                condition=models.Q(node_kind='start'),
                name='uniq_pipeline_start_node_per_pipeline',
            ),
        ]
        indexes = [
            models.Index(fields=['pipeline', 'order_index']),
            models.Index(fields=['connection']),
            models.Index(fields=['batch']),
        ]

    def __str__(self):
        return f"PipelineNode {self.id} ({self.pipeline_id}:{self.batch_id})"


class PipelineEdge(models.Model):
    pipeline = models.ForeignKey(Pipeline, on_delete=models.CASCADE, related_name='edges')
    from_node = models.ForeignKey(PipelineNode, on_delete=models.CASCADE, related_name='outgoing_edges')
    to_node = models.ForeignKey(PipelineNode, on_delete=models.CASCADE, related_name='incoming_edges')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'pipeline_edge'
        constraints = [
            models.UniqueConstraint(
                fields=['pipeline', 'from_node', 'to_node'],
                name='uniq_pipeline_edge'
            )
        ]
        indexes = [
            models.Index(fields=['pipeline']),
            models.Index(fields=['from_node']),
            models.Index(fields=['to_node']),
        ]

    def __str__(self):
        return f"{self.from_node_id} -> {self.to_node_id}"


class PipelineRun(models.Model):
    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('deleted', 'Deleted'),
    ]

    pipeline = models.ForeignKey(Pipeline, on_delete=models.CASCADE, related_name='runs')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='running')
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    triggered_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'pipeline_run'
        indexes = [
            models.Index(fields=['pipeline', 'status']),
            models.Index(fields=['pipeline', 'started_at']),
        ]

    def __str__(self):
        return f"PipelineRun {self.id} ({self.pipeline_id}) - {self.status}"


from django.db import models

from django.db import models
from django.utils import timezone

# models.py
from django.db import models
from django.utils import timezone

from django.db import models
from django.utils import timezone
from django.db import models
from django.utils import timezone

class Invoice(models.Model):
    
    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('GENERATED', 'Generated'),
        ('PAID', 'Paid'),
        ('FAILED', 'Failed')
    )

    PAYMENT_STATUS_CHOICES = (
        ('UNPAID', 'Unpaid'),
        ('PROCESSING', 'Processing'),
        ('PAID', 'Paid'),
        ('FAILED', 'Failed'),
        ('REFUNDED', 'Refunded'),
    )

    cmp = models.ForeignKey('Company', on_delete=models.PROTECT)
    invoice_number = models.CharField(max_length=50, unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    billing_start_date = models.DateTimeField(null=True)
    billing_end_date = models.DateTimeField(null=True)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    total_records = models.IntegerField(default=0)
    units = models.DecimalField(max_digits=10, decimal_places=0, default=0)

    pdf_content = models.BinaryField(null=True)
    is_generated = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')

    # ✅ New Fields
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='UNPAID')
    payment_date = models.DateTimeField(null=True, blank=True)
    payment_reference = models.JSONField(null=True, blank=True)
    receipt_url = models.JSONField(null=True, blank=True)
    failure_reason = models.JSONField(null=True, blank=True)
    payment_method_last4 = models.JSONField(null=True, blank=True)
    payment_method_brand = models.JSONField(null=True, blank=True)
    payment_method_exp_month = models.JSONField(null=True, blank=True)
    payment_method_exp_year = models.JSONField(null=True, blank=True)
    card_holder_name = models.JSONField(null=True, blank=True)
    payment_details = models.JSONField(null=True, blank=True)
    autopay_attempt = models.IntegerField(default=0)

    class Meta:
        db_table = 'invoice'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['cmp', 'created_at']),
            models.Index(fields=['invoice_number']),
            models.Index(fields=['status']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['cmp', 'billing_start_date', 'billing_end_date'],
                name='unique_invoice_per_period'
            )
        ]


    def __str__(self):
        return f"Invoice {self.invoice_number} - {self.cmp.data}"

    def _get_encryption_key(self):
        cmp_id = self.cmp_id or (self.cmp.cmp_id if self.cmp else None)
        if not cmp_id:
            return None
        created_day = None
        if self.cmp and self.cmp.created_at:
            created_day = self.cmp.created_at.day
        return cmp_id + (created_day if created_day is not None else 1)

    def _encrypt_payment_details(self, value):
        if value is None:
            return None
        key = self._get_encryption_key()
        if not key:
            return None
        return encrypt_field(value, key)

    def _decrypt_payment_details(self, encrypted_value):
        if not encrypted_value:
            return None
        try:
            encrypted_data, nonce, tag, salt, original_type, iterations = encrypted_value
            key = self._get_encryption_key()
            if key:
                try:
                    return decrypt_field(encrypted_data, key, nonce, tag, salt, original_type, iterations)
                except Exception:
                    pass
            cmp_id = self.cmp_id or (self.cmp.cmp_id if self.cmp else None)
            if not cmp_id:
                return None
            return decrypt_field(encrypted_data, cmp_id, nonce, tag, salt, original_type, iterations)
        except Exception:
            return None

    def _is_encrypted_payload(self, value):
        return isinstance(value, (list, tuple)) and len(value) == 6

    def _decrypt_sensitive_value(self, value):
        if value is None:
            return None
        raw = value
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                return value
        if not self._is_encrypted_payload(raw):
            return raw
        encrypted_data, nonce, tag, salt, original_type, iterations = raw
        key = self._get_encryption_key()
        if not key:
            return None
        try:
            return decrypt_field(encrypted_data, key, nonce, tag, salt, original_type, iterations)
        except Exception:
            cmp_id = self.cmp_id or (self.cmp.cmp_id if self.cmp else None)
            if not cmp_id:
                return None
            try:
                return decrypt_field(encrypted_data, cmp_id, nonce, tag, salt, original_type, iterations)
            except Exception:
                return None

    def _encrypt_sensitive_value(self, value):
        if value is None:
            return None
        key = self._get_encryption_key()
        if not key:
            return value
        return encrypt_field(value, key)

    @property
    def decrypted_payment_details(self):
        return self._decrypt_payment_details(self.payment_details)

    @decrypted_payment_details.setter
    def decrypted_payment_details(self, value):
        encrypted_value = self._encrypt_payment_details(value)
        if encrypted_value is None:
            self.payment_details = None
            return
        self.payment_details = encrypted_value

    def get_decrypted_field(self, field_name: str):
        return self._decrypt_sensitive_value(getattr(self, field_name))

    def set_encrypted_field(self, field_name: str, value):
        setattr(self, field_name, self._encrypt_sensitive_value(value))

    def _collect_sensitive_updates(self):
        sensitive_fields = {
            "payment_reference",
            "receipt_url",
            "failure_reason",
            "payment_method_last4",
            "payment_method_brand",
            "payment_method_exp_month",
            "payment_method_exp_year",
            "card_holder_name",
        }
        updates = {}
        for field_name in sensitive_fields:
            value = getattr(self, field_name)
            raw = value
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except Exception:
                    pass
            if self._is_encrypted_payload(raw) or raw is None:
                continue
            updates[field_name] = self._encrypt_sensitive_value(raw)
        return updates

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        if not is_new:
            updates = self._collect_sensitive_updates()
            for field_name, encrypted_value in updates.items():
                setattr(self, field_name, encrypted_value)
        super().save(*args, **kwargs)
        if is_new:
            updates = self._collect_sensitive_updates()
            if updates:
                Invoice.objects.filter(pk=self.pk).update(**updates)
                for field_name, encrypted_value in updates.items():
                    setattr(self, field_name, encrypted_value)

    @classmethod
    def generate_invoice_number(cls, company, date):
        """
        Generates an invoice number like: INVyyMM-200001, INVyyMM-200002, etc.
        """
        # Format year/month
        yy = str(date.year)[-2:]
        mm = f"{date.month:02d}"

        # Sequence logic (starts at 200001) based on numeric suffix, not lexicographic order
        last_seq = (
            cls.objects
            .filter(invoice_number__regex=r"^INV\d{4}-\d{6}$")
            .annotate(seq=Cast(Substr("invoice_number", 9), IntegerField()))
            .order_by("-seq")
            .values_list("seq", flat=True)
            .first()
        ) or 199999

        new_seq = last_seq + 1
        return f"INV{yy}{mm}-{new_seq:06d}"

class StripeWebhookEvent(models.Model):
    STATUS_CHOICES = [
        ("received", "Received"),
        ("processed", "Processed"),
        ("skipped", "Skipped"),
        ("failed", "Failed"),
    ]

    event_id = models.CharField(max_length=100, unique=True)
    event_type = models.CharField(max_length=100, null=True, blank=True)
    invoice = models.ForeignKey(
        Invoice, on_delete=models.SET_NULL, null=True, blank=True, related_name="stripe_events"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="received")
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "stripe_webhook_event"
        indexes = [
            models.Index(fields=["event_type"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.event_id} ({self.event_type or 'unknown'})"

    @property
    def billing_period_display(self):
        return f"{self.billing_start_date.strftime('%Y-%m-%d')} to {self.billing_end_date.strftime('%Y-%m-%d')}"

class DateDirectory(models.Model):
    connection = models.ForeignKey(Connection, on_delete=models.CASCADE, related_name="date_directories")
    directories = models.JSONField()  # Stores list of date directories
    created_at = models.DateTimeField(auto_now_add=True)  # Timestamp for creation
    updated_at = models.DateTimeField(auto_now=True)  # Auto-updated on modification

    class Meta:
        db_table = 'date_directory'

    def __str__(self):
        return f"{self.connection.name} - {self.created_at.strftime('%Y-%m-%d %H:%M:%S')}"



class ErrorLogEntry(models.Model):
    cmp = models.ForeignKey('Company', on_delete=models.CASCADE)  # Company relation
    connection = models.ForeignKey('Connection', on_delete=models.CASCADE)  # Related connection ID
    message = models.TextField()  # Log message
    created_on = models.DateTimeField(auto_now_add=True)  # Automatically stores creation timestamp
    function_name = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = 'error_log_entry'
        ordering = ["-created_on"]

    def __str__(self):
        return f"Log {self.id}: {self.message[:50]} ({self.created_on})"

from django.db import models; 
from django.contrib.postgres.fields import IntegerRangeField

class Pricing(models.Model):
    effective_start_date = models.DateField(); 
    effective_end_date = models.DateField()
    
    unit_volume_range = IntegerRangeField(null=True, blank=True)
    price_per_unit = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    fixed_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    
    pricing_type = models.CharField(max_length=20, choices=[('fixed', 'Fixed Amount'), ('variable', 'Variable Rate')], default='variable')

    class Meta:
        db_table = 'pricing'
        verbose_name = "Pricing Tier"
        verbose_name_plural = "Pricing Tiers"

    def __str__(self): return f"{self.unit_volume_range} | {self.effective_start_date} - {self.effective_end_date}"
class CommunicationSupport(models.Model):
    """Model to store communication support email addresses for companies"""
    
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='communication_support')
    email_addresses = models.JSONField(default=list, help_text="List of email addresses for communication support")
    email_groups = models.JSONField(default=list, blank=True, help_text="List of notification email groups")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    updated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='communication_support_updated',
    )
    
    class Meta:
        db_table = 'communication_support'
        verbose_name = "Communication Support"
        verbose_name_plural = "Communication Support"
        unique_together = ['company']  # One communication support record per company
    
    def __str__(self):
        return f"Communication Support for {self.company.cmp_name}"
    
    def get_email_list(self):
        """Return email addresses as a comma-separated string"""
        if isinstance(self.email_addresses, list):
            return ', '.join(self.email_addresses)
        return ''
    
    def set_email_list(self, email_string):
        """Set email addresses from a comma-separated string"""
        if email_string:
            # Split by comma, strip whitespace, and filter out empty strings
            emails = [email.strip() for email in email_string.split(',') if email.strip()]
            self.email_addresses = emails
        else:
            self.email_addresses = []
class SourceDeltaTable(models.Model):
    source = models.OneToOneField(SourceModel, on_delete=models.CASCADE, related_name="delta_config")
    delta_fields = models.JSONField(default=list)

    class Meta:
        db_table = 'source_delta_table'

class SourceFileMapping(models.Model):
    """
    Stores S3 → Snowflake mapping per connection.

    - One row per connection.
    - Mapping is locked (not editable) after initial sync.
    """
    connection = models.OneToOneField(
        Connection,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="source_file_mapping",
    )
    file_mapping_mode = models.CharField(
        max_length=64,
        help_text="single_table_per_file or multi_files_single_table",
    )
    single_file_table_mapping = models.JSONField(
        default=dict,
        blank=True,
        help_text="file_key -> table_name mapping for single_table_per_file",
    )
    table_to_files_mapping = models.JSONField(
        default=dict,
        blank=True,
        help_text="table_name -> [file_key,...] mapping for multi_files_single_table",
    )
    cmp_id = models.IntegerField(null=True, blank=True)
    source_id = models.IntegerField(null=True, blank=True)
    basefolder_path = models.CharField(max_length=300, null=True, blank=True)
    created_by = models.IntegerField(null=True, blank=True)
    updated_by = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "source_file_mapping"
        verbose_name = "Source File Mapping"
        verbose_name_plural = "Source File Mappings"


# Backward compatibility alias for existing imports/tests.
S3Mapping = SourceFileMapping


class TableSelectionAudit(models.Model):
    """
    Audit trail for table selection changes.
    Tracks detailed information about what tables were added, removed, or reordered.
    """
    ACTION_CHOICES = [
        ('ADD', 'Table Added'),
        ('REMOVE', 'Table Removed'),
        ('REORDER', 'Table Reordered'),
        ('BULK_UPDATE', 'Bulk Selection Update')
    ]
    
    audit_id = models.AutoField(primary_key=True)
    connection = models.ForeignKey(Connection, on_delete=models.CASCADE)
    log = models.ForeignKey('Log', on_delete=models.CASCADE, null=True, blank=True)
    action_type = models.CharField(max_length=20, choices=ACTION_CHOICES)
    table_name = models.CharField(max_length=100)
    old_sequence = models.IntegerField(null=True, blank=True)
    new_sequence = models.IntegerField(null=True, blank=True)
    old_selected = models.BooleanField(null=True, blank=True)
    new_selected = models.BooleanField(null=True, blank=True)
    changed_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True)
    changed_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'table_selection_audit'
        indexes = [
            models.Index(fields=['connection', 'changed_at']),
            models.Index(fields=['log']),
            models.Index(fields=['connection', 'action_type']),
        ]
    
    def __str__(self):
        return f"{self.action_type} - {self.table_name} ({self.connection.connection_id})"
    

class TicketCategory(models.Model):
    category_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
 
    class Meta:
        db_table = 'ticket_category'
        verbose_name = "Ticket Category"
        verbose_name_plural = "Ticket Categories"
 
    def __str__(self):
        return self.name
 
class TicketIssueType(models.Model):
    issue_type_id = models.AutoField(primary_key=True)
    category = models.ForeignKey(TicketCategory, on_delete=models.CASCADE, related_name='issue_types')
    name = models.CharField(max_length=150)
    description = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
 
    class Meta:
        db_table = 'ticket_issue_type'
        verbose_name = "Ticket Issue Type"
        verbose_name_plural = "Ticket Issue Types"
        unique_together = ('category', 'name')
 
    def __str__(self):
        return f"{self.category.name} - {self.name}"
 
class TicketSupport(models.Model):
    TICKET_STATUS_CHOICES = [
        ('Open', 'Open'),
        ('Closed', 'Closed'),
    ]
    TICKET_SOURCE_CHOICES = [
        ('Salesforce', 'Salesforce'),
        ('MicrosoftDynamics', 'Microsoft Dynamics'),
        ('AmazonS3', 'Amazon S3'),
        ('GoogleReview', 'Google Review'),
        ('Other', 'Other'),
    ]
    ticket_id=models.AutoField(primary_key=True)
    cmp = models.ForeignKey(Company, on_delete=models.CASCADE)
    connection=models.ForeignKey(Connection, on_delete=models.CASCADE, null=True, blank=True)
    category=models.ForeignKey('TicketCategory', on_delete=models.CASCADE, null=True, blank=True)
    issue_type=models.ForeignKey('TicketIssueType', on_delete=models.CASCADE, null=True, blank=True)
    source_type=models.CharField(max_length=100, choices=TICKET_SOURCE_CHOICES, null=True, blank=True)
    connection_name=models.CharField(max_length=200,null=True,blank=True)
    subject=models.CharField(max_length=200)
    description=models.TextField()
    status=models.CharField(max_length=20, choices=TICKET_STATUS_CHOICES, default='Open')
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    created_by=models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_tickets')
    updated_by=models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='updated_tickets')
    closed_by=models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='closed_tickets')
    closed_at=models.DateTimeField(null=True, blank=True)
    has_new_response=models.BooleanField(default=False)
    has_user_reply=models.BooleanField(default=False)
 
    class Meta:
        db_table = 'ticket_support'
        indexes = [
            models.Index(fields=['cmp', 'status']),
            models.Index(fields=['connection'], name='ticket_supp_connection_idx'),
            models.Index(fields=['created_at']),
        ]
    def __str__(self):
        return f"Ticket {self.ticket_id} - {self.subject[:50]} ({self.status})"
 
 
class TicketReply(models.Model):
    reply_id = models.AutoField(primary_key=True)
    ticket = models.ForeignKey(TicketSupport, on_delete=models.CASCADE, related_name='replies')
    cmp = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True)
    message = models.TextField()
    is_support_team = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
 
    class Meta:
        db_table = 'ticket_reply'
        ordering = ['created_at']
 
    def __str__(self):
        return f"Reply {self.reply_id} on Ticket {self.ticket_id}"
 
 
class TicketAttachment(models.Model):
    attachment_id = models.AutoField(primary_key=True)
    ticket = models.ForeignKey(TicketSupport, on_delete=models.CASCADE, null=True, blank=True, related_name='attachments')
    reply = models.ForeignKey(TicketReply, on_delete=models.CASCADE, null=True, blank=True, related_name='attachments')
    file = models.FileField(upload_to='ticket_attachments/')
    uploaded_at = models.DateTimeField(auto_now_add=True)
 
    class Meta:
        db_table = 'ticket_attachment'
class RegistrationRequest(models.Model):
    class Status(models.TextChoices):
        ACCESS_PROVIDED = 'access_provided', 'Access Provided'
        ACCESS_REJECTED = 'access_rejected', 'Access Rejected'
        EXPIRED = 'expired', 'Expired'

    registration_request_id = models.AutoField(primary_key=True)
    company_name = models.CharField(max_length=100)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField()
    phone_number = models.CharField(max_length=16)
    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='registration_requests',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        null=True,
        blank=True,
    )

    access_provided_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        db_table = 'registration_request'
        ordering = ['-created_at']



    def __str__(self):
        return f"{self.first_name} {self.last_name} - {self.company_name}"


class EntityTransform(models.Model):
    """Versioned column-mapping JSON and Python transform script per SAP entity."""

    RUN_ORDER_FILTER_SCRIPT_MAPPING = "filter_script_mapping"
    RUN_ORDER_MAPPING_SCRIPT = "mapping_script"
    RUN_ORDER_CHOICES = [
        (RUN_ORDER_FILTER_SCRIPT_MAPPING, "SAP filter, script, mapping on leftover columns, load"),
        (RUN_ORDER_MAPPING_SCRIPT, "Mapping first, then script"),
    ]

    DESTINATION_NAME_DEFAULT = "DEFAULT"
    DESTINATION_NAME_JSON = "JSON"
    DESTINATION_NAME_MANUAL = "MANUAL"
    DESTINATION_NAME_SOURCE_CHOICES = [
        (DESTINATION_NAME_DEFAULT, "Default"),
        (DESTINATION_NAME_JSON, "From mapping JSON"),
        (DESTINATION_NAME_MANUAL, "Manual override"),
    ]

    RENAME_MODE_CREATE_NEW = "create_new"
    RENAME_MODE_RENAME_EXISTING = "rename_existing"
    RENAME_MODE_CHOICES = [
        (RENAME_MODE_CREATE_NEW, "Create new table and full reload"),
        (RENAME_MODE_RENAME_EXISTING, "Rename existing table"),
    ]

    STATUS_DRAFT = "draft"
    STATUS_VALIDATED = "validated"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_VALIDATED, "Validated"),
    ]

    id = models.AutoField(primary_key=True)
    connection = models.ForeignKey(
        Connection,
        on_delete=models.CASCADE,
        related_name="entity_transforms",
    )
    service_name = models.CharField(max_length=150)
    entity_name = models.CharField(max_length=150)
    table_name = models.CharField(max_length=255)
    version = models.PositiveIntegerField()
    is_active = models.BooleanField(default=False)
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
    )
    mapping_json = models.JSONField(null=True, blank=True)
    mapping_overrides = models.JSONField(default=dict, blank=True)
    script_text = models.TextField(null=True, blank=True)
    script_sha256 = models.CharField(max_length=64, blank=True, default="")
    entry_point = models.CharField(max_length=255, blank=True, default="")
    run_order = models.CharField(
        max_length=64,
        choices=RUN_ORDER_CHOICES,
        default=RUN_ORDER_FILTER_SCRIPT_MAPPING,
    )
    row_filter = models.JSONField(null=True, blank=True)
    # Snowflake destination naming (SAP OData → Snowflake)
    json_table_name = models.CharField(max_length=255, null=True, blank=True)
    destination_table_name = models.CharField(max_length=255, null=True, blank=True)
    destination_name_source = models.CharField(
        max_length=16,
        choices=DESTINATION_NAME_SOURCE_CHOICES,
        default=DESTINATION_NAME_DEFAULT,
        blank=True,
    )
    previous_destination_table_name = models.CharField(
        max_length=255, null=True, blank=True
    )
    # Company + Snowflake database + schema. Uniqueness is per destination, not per connector.
    destination_schema_key = models.CharField(max_length=255, null=True, blank=True)
    destination_rename_mode = models.CharField(
        max_length=32,
        choices=RENAME_MODE_CHOICES,
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="entity_transforms_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "entity_transform"
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "service_name", "entity_name", "version"],
                name="entity_transform_conn_svc_entity_ver_uniq",
            ),
            models.UniqueConstraint(
                fields=["connection", "service_name", "entity_name"],
                condition=models.Q(is_active=True),
                name="entity_transform_one_active_per_entity",
            ),
            # At most one open draft per entity (autosave upserts this row).
            models.UniqueConstraint(
                fields=["connection", "service_name", "entity_name"],
                condition=models.Q(status="draft"),
                name="entity_transform_one_draft_per_entity",
            ),
            # One active destination table name per company + Snowflake database + schema.
            models.UniqueConstraint(
                fields=["destination_schema_key", "destination_table_name"],
                condition=models.Q(is_active=True)
                & models.Q(destination_table_name__isnull=False)
                & ~models.Q(destination_table_name="")
                & models.Q(destination_schema_key__isnull=False)
                & ~models.Q(destination_schema_key=""),
                name="entity_transform_active_dest_name_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["connection", "table_name", "is_active"],
                name="entity_tran_connect_2c1de9_idx",
            ),
            models.Index(
                fields=["connection", "service_name", "entity_name"],
                name="entity_tran_connect_5d84ed_idx",
            ),
            models.Index(
                fields=["destination_schema_key", "destination_table_name", "is_active"],
                name="entity_tran_schema_dest_idx",
            ),
        ]

    def __str__(self):
        return f"{self.table_name} v{self.version} (active={self.is_active})"


class ProxyAgent(models.Model):
    """
    One on-prem SAP proxy agent per company.

    Client cert and bootstrap token are AES-GCM encrypted at rest (cmp_id key).
    progress_signing_secret_hash stores SHA-256 of the one-time HMAC secret
    issued in config.json for HTTPS migration-progress callbacks.
    """

    agent_id = models.AutoField(primary_key=True)
    agent_name = models.CharField(max_length=255)
    agent_uid = models.CharField(max_length=64, unique=True, db_index=True)
    cmp = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='proxy_agents',
    )
    bootstrap_token = models.JSONField(null=True, blank=True)
    client_cert_pem = models.JSONField(null=True, blank=True)
    client_cert_not_after = models.DateTimeField(null=True, blank=True)
    progress_signing_secret_hash = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text='SHA-256 hex digest of progress_signing_secret (raw secret never stored).',
    )
    # AES-GCM encrypted raw HMAC secret (decrypt with cmp_id for signature verify).
    progress_signing_secret = models.JSONField(null=True, blank=True)
    last_progress_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='proxy_agents_created',
    )
    config_downloaded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'proxy_agent'
        indexes = [
            models.Index(fields=['cmp', 'agent_uid']),
        ]

    def __str__(self):
        return f"{self.agent_uid} ({self.cmp_id})"


class Country(models.Model):
    country_name = models.CharField(max_length=100)
    code = models.CharField(max_length=15,null=True)
    def __str__(self):
        return self.country_name

    class Meta:
        db_table='country'


class EmailTemplate(models.Model):
    """Company-owned custom email template used by export notifications."""

    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    subject = models.CharField(max_length=500)
    header_subtitle = models.CharField(max_length=255, default="AUTOMATED REPORT")
    header_bg_color = models.CharField(max_length=50, default="#6E2FD5")
    primary_color = models.CharField(max_length=50, default="#6E2FD5")
    greeting_name = models.CharField(max_length=255, default="Hi Team,")
    body_content = models.TextField(default="", blank=True)
    callout_content = models.TextField(default="", blank=True)
    callout_styles = models.JSONField(default=dict, blank=True)
    body_fields = models.JSONField(default=list, blank=True)
    cta_button_text = models.CharField(max_length=255, default="View file on SharePoint")
    button_bg_color = models.CharField(max_length=50, default="#6E2FD5")
    button_text_color = models.CharField(max_length=50, default="#ffffff")
    button_align = models.CharField(max_length=20, default="center", choices=[("left", "left"), ("center", "center"), ("right", "right")])
    button_variant = models.CharField(max_length=20, default="solid", choices=[("solid", "solid"), ("outline", "outline"), ("subtle", "subtle")])
    team_name = models.TextField()
    cmp = models.ForeignKey("Company", on_delete=models.CASCADE, related_name="email_templates")
    created_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True, related_name="email_templates_created")
    updated_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True, related_name="email_templates_updated")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "email_templates"
        ordering = ["-updated_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["cmp", "name"], name="uniq_email_template_name_per_cmp")]
        indexes = [models.Index(fields=["cmp", "updated_at"])]

