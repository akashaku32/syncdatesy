from rest_framework import serializers
from django.utils import timezone

from apps.elttool.models import (
    Company,
    Connection,
    DestinationAttribute,
    DestinationConfig,
    DestinationModel,
    Invoice,
    RegistrationRequest,
    SourceTableSelection,
    TicketSupport,
    TicketReply,
    TicketAttachment,
)
from .models import AdminUser


class AdminTicketAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = TicketAttachment
        fields = ['attachment_id', 'file', 'uploaded_at']


class AdminRegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    full_name = serializers.CharField(max_length=200)

    def validate_email(self, value):
        if AdminUser.objects.filter(email=value).exists():
            raise serializers.ValidationError('An admin with this email already exists.')
        return value


class AdminLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class AdminForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()


class AdminResetPasswordSerializer(serializers.Serializer):
    token = serializers.CharField()
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True, min_length=8)

    def validate(self, data):
        if data.get('password') != data.get('confirm_password'):
            raise serializers.ValidationError({'confirm_password': 'Passwords do not match.'})
        return data


class AdminUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdminUser
        fields = ['admin_id', 'email', 'full_name', 'is_active', 'created_at']


class AdminRegistrationRequestSerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField()

    class Meta:
        model = RegistrationRequest
        fields = [
            'registration_request_id',
            'company_name',
            'first_name',
            'last_name',
            'email',
            'phone_number',
            'status',
            'access_provided_at',
            'created_at',
        ]

        read_only_fields = fields

    def get_status(self, obj):
        """Return a live expiration status for provisioned trial accounts."""
        if obj.status != RegistrationRequest.Status.ACCESS_PROVIDED:
            return obj.status

        company = obj.company
        if (
            company is not None
            and company.is_trial
            and company.end_date <= timezone.now()
        ):
            return RegistrationRequest.Status.EXPIRED

        return obj.status


class AdminRegistrationRequestActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(
        choices=['provide_access', 'reject_access']
    )
    password = serializers.CharField(
        write_only=True,
        required=False,
        trim_whitespace=False,
    )

    def validate(self, attrs):
        if (
            attrs['action'] == 'provide_access'
            and not attrs.get('password')
        ):
            raise serializers.ValidationError({
                'password': 'Password is required when providing access.'
            })

        return attrs

class AdminRegistrationRequestExtendEndDateSerializer(serializers.Serializer):
    days = serializers.IntegerField(min_value=1)


class AdminTicketListSerializer(serializers.ModelSerializer):
    company_name = serializers.SerializerMethodField()
    category_name = serializers.CharField(source='category.name', default=None)
    issue_type_name = serializers.CharField(source='issue_type.name', default=None)
    created_by_email = serializers.CharField(source='created_by.company_email', default=None)
    reply_count = serializers.SerializerMethodField()

    class Meta:
        model = TicketSupport
        fields = [
            'ticket_id', 'company_name', 'created_by_email',
            'category_name', 'issue_type_name', 'source_type',
            'connection_name', 'subject', 'status', 'has_user_reply',
            'reply_count', 'created_at', 'updated_at',
        ]

    def get_company_name(self, obj):
        try:
            return obj.cmp.data
        except Exception:
            return str(obj.cmp_id)

    def get_reply_count(self, obj):
        return obj.replies.count()


class AdminTicketDetailSerializer(serializers.ModelSerializer):
    company_name = serializers.SerializerMethodField()
    category_name = serializers.CharField(source='category.name', default=None)
    issue_type_name = serializers.CharField(source='issue_type.name', default=None)
    created_by_email = serializers.CharField(source='created_by.company_email', default=None)
    created_by_name = serializers.SerializerMethodField()
    closed_by_name = serializers.SerializerMethodField()

    attachments = AdminTicketAttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = TicketSupport
        fields = [
            'ticket_id', 'company_name', 'created_by_email', 'created_by_name',
            'category_name', 'issue_type_name', 'source_type', 'connection_name',
            'subject', 'description', 'status', 'attachments',
            'created_at', 'updated_at', 'closed_at', 'closed_by_name',
        ]

    def get_company_name(self, obj):
        try:
            return obj.cmp.data
        except Exception:
            return str(obj.cmp_id)

    def get_created_by_name(self, obj):
        if obj.created_by:
            return f"{obj.created_by.first_name} {obj.created_by.last_name}".strip()
        return None
 
    def get_closed_by_name(self, obj):
        if obj.closed_by:
            return f"{obj.closed_by.first_name} {obj.closed_by.last_name}".strip()
        if obj.status == 'Closed':
            return 'Support Admin'
        return None


class AdminTicketReplySerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()
    sender_email = serializers.SerializerMethodField()
    attachments = AdminTicketAttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = TicketReply
        fields = [
            'reply_id', 'ticket', 'message',
            'is_support_team', 'sender_name', 'sender_email', 'attachments', 'created_at',
        ]

    def get_sender_name(self, obj):
        if obj.is_support_team:
            return 'Support Team'
        if obj.created_by:
            return f"{obj.created_by.first_name} {obj.created_by.last_name}".strip()
        return 'Unknown'

    def get_sender_email(self, obj):
        if obj.created_by:
            return obj.created_by.company_email
        return None


class AdminTicketStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['Open', 'Closed'])


class AdminDestinationListSerializer(serializers.ModelSerializer):
    dst = serializers.CharField(source='dst.name', read_only=True)
    created_by_name = serializers.SerializerMethodField()
    updated_by_name = serializers.SerializerMethodField()

    class Meta:
        model = DestinationConfig
        fields = [
            'dst_config_id',
            'name',
            'dst',
            'created_by_name',
            'updated_by_name',
            'created_at',
            'updated_at',
            'cmp',
            'is_active',
        ]

    def _get_user_name(self, user):
        if not user:
            return None
        full_name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
        return full_name or getattr(user, 'company_email', None)

    def get_created_by_name(self, obj):
        user = (
            getattr(obj, 'created_by', None)
            or getattr(getattr(obj, 'cmp', None), 'created_by', None)
        )
        return self._get_user_name(user) or 'Support Admin'

    def get_updated_by_name(self, obj):
        user = (
            getattr(obj, 'updated_by', None)
            or getattr(obj, 'created_by', None)
            or getattr(getattr(obj, 'cmp', None), 'updated_by', None)
            or getattr(getattr(obj, 'cmp', None), 'created_by', None)
        )
        return self._get_user_name(user) or 'Support Admin'


class AdminCompanyListSerializer(serializers.ModelSerializer):
    cmp_name = serializers.SerializerMethodField()

    class Meta:
        model = Company
        fields = [
            'cmp_id',
            'cmp_name',
            'employee_range',
            'pricing',
            'start_date',
            'end_date',
            'is_trial',
            'created_at',
            'billing_start_date',
            'billing_end_date',
            'plan',
            'account_status',
        ]

    def get_cmp_name(self, obj):
        return obj.data


class AdminCompanyBillingSerializer(serializers.ModelSerializer):
    cmp_name = serializers.SerializerMethodField()
    billing_address = serializers.SerializerMethodField()
    shipping_address = serializers.SerializerMethodField()
    stripe_customer_id = serializers.SerializerMethodField()
    stripe_default_payment_method_id = serializers.SerializerMethodField()
    stripe_card_last4 = serializers.SerializerMethodField()
    stripe_card_brand = serializers.SerializerMethodField()
    stripe_card_exp_month = serializers.SerializerMethodField()
    stripe_card_exp_year = serializers.SerializerMethodField()
    stripe_card_holder_name = serializers.SerializerMethodField()

    class Meta:
        model = Company
        fields = [
            'cmp_id',
            'cmp_name',
            'billed_to',
            'billing_address',
            'shipping_address',
            'plan',
            'account_status',
            'is_trial',
            'billing_start_date',
            'billing_end_date',
            'stripe_customer_id',
            'stripe_default_payment_method_id',
            'stripe_card_last4',
            'stripe_card_brand',
            'stripe_card_exp_month',
            'stripe_card_exp_year',
            'stripe_card_holder_name',
        ]

    def get_cmp_name(self, obj):
        return obj.data

    def get_billing_address(self, obj):
        return obj.get_decrypted_field("billing_address")

    def get_shipping_address(self, obj):
        return obj.get_decrypted_field("shipping_address")

    def get_stripe_customer_id(self, obj):
        return obj.get_decrypted_field("stripe_customer_id")

    def get_stripe_default_payment_method_id(self, obj):
        return obj.get_decrypted_field("stripe_default_payment_method_id")

    def get_stripe_card_last4(self, obj):
        return obj.get_decrypted_field("stripe_card_last4")

    def get_stripe_card_brand(self, obj):
        return obj.get_decrypted_field("stripe_card_brand")

    def get_stripe_card_exp_month(self, obj):
        return obj.get_decrypted_field("stripe_card_exp_month")

    def get_stripe_card_exp_year(self, obj):
        return obj.get_decrypted_field("stripe_card_exp_year")

    def get_stripe_card_holder_name(self, obj):
        return obj.get_decrypted_field("stripe_card_holder_name")


class AdminInvoiceSerializer(serializers.ModelSerializer):
    payment_reference = serializers.SerializerMethodField()
    receipt_url = serializers.SerializerMethodField()
    failure_reason = serializers.SerializerMethodField()
    payment_method_last4 = serializers.SerializerMethodField()
    payment_method_brand = serializers.SerializerMethodField()
    payment_method_exp_month = serializers.SerializerMethodField()
    payment_method_exp_year = serializers.SerializerMethodField()
    card_holder_name = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            'id',
            'invoice_number',
            'created_at',
            'billing_start_date',
            'billing_end_date',
            'total_amount',
            'payment_status',
            'payment_date',
            'payment_reference',
            'receipt_url',
            'failure_reason',
            'payment_method_last4',
            'payment_method_brand',
            'payment_method_exp_month',
            'payment_method_exp_year',
            'card_holder_name',
        ]

    def get_payment_reference(self, obj):
        return obj.get_decrypted_field("payment_reference")

    def get_receipt_url(self, obj):
        return obj.get_decrypted_field("receipt_url")

    def get_failure_reason(self, obj):
        return obj.get_decrypted_field("failure_reason")

    def get_payment_method_last4(self, obj):
        value = obj.get_decrypted_field("payment_method_last4")
        return "**** **** **** ****" if value else None

    def get_payment_method_brand(self, obj):
        return obj.get_decrypted_field("payment_method_brand") or None

    def get_payment_method_exp_month(self, obj):
        return "**" if obj.get_decrypted_field("payment_method_exp_month") else None

    def get_payment_method_exp_year(self, obj):
        return "**" if obj.get_decrypted_field("payment_method_exp_year") else None

    def get_card_holder_name(self, obj):
        decrypted = obj.get_decrypted_field("card_holder_name")
        if not decrypted:
            return None
        return "".join("*" if ch != " " else " " for ch in str(decrypted))


class AdminConnectionSchemaSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceTableSelection
        fields = [
            'tbl_id',
            'table',
            'last_synced',
            'account_type',
            'updated_by_id',
        ]


class AdminCompanyConnectionSerializer(serializers.ModelSerializer):
    connector_name = serializers.CharField(source='src_config.name', read_only=True)
    source_name = serializers.SerializerMethodField()
    destination_name = serializers.CharField(source='dst_config.name', read_only=True)
    destination = serializers.SerializerMethodField()
    migration_status = serializers.SerializerMethodField()
    connection_status = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    updated_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Connection
        fields = [
            'connection_id',
            'connector_name',
            'source_name',
            'destination_name',
            'destination',
            'created_by_name',
            'updated_by_name',
            'migration_status',
            'connection_status',
        ]

    def get_source_name(self, obj):
        try:
            return obj.src_config.src.display_name or obj.src_config.src.name
        except Exception:
            return None

    def get_destination(self, obj):
        try:
            return obj.dst_config.dst.name
        except Exception:
            return obj.dst_config.name if getattr(obj, 'dst_config', None) else None

    def get_migration_status(self, obj):
        return self.context.get('migration_statuses', {}).get(obj.connection_id)

    def get_connection_status(self, obj):
        status_map = {
            'A': 'Active',
            'P': 'Paused',
            'D': 'Deleted',
        }
        return status_map.get(obj.status, obj.status or None)

    def _get_user_name(self, user):
        if not user:
            return None
        full_name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
        return full_name or getattr(user, 'company_email', None)

    def get_created_by_name(self, obj):
        user = (
            getattr(obj, 'created_by', None)
            or getattr(getattr(obj, 'dst_config', None), 'created_by', None)
            or getattr(getattr(obj, 'cmp', None), 'created_by', None)
        )
        return self._get_user_name(user) or 'Support Admin'

    def get_updated_by_name(self, obj):
        user = (
            getattr(obj, 'updated_by', None)
            or getattr(getattr(obj, 'dst_config', None), 'updated_by', None)
            or getattr(obj, 'created_by', None)
            or getattr(getattr(obj, 'dst_config', None), 'created_by', None)
            or getattr(getattr(obj, 'cmp', None), 'updated_by', None)
            or getattr(getattr(obj, 'cmp', None), 'created_by', None)
        )
        return self._get_user_name(user) or 'Support Admin'


class AdminDestinationAttributeSerializer(serializers.ModelSerializer):
    class Meta:
        model = DestinationAttribute
        fields = [
            'dst_attr_id',
            'attribute_name',
            'input_type',
            'label',
            'widget',
            'required',
            'is_visible',
            'choices',
            'depend_on',
            'dependency_value',
            'read_only',
            'description',
            'placeholder',
            'default_value',
        ]


class AdminDestinationDetailSerializer(serializers.ModelSerializer):
    dst = serializers.CharField(source='dst.name', read_only=True)
    config_data = serializers.SerializerMethodField()
    destination_fields = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    updated_by_name = serializers.SerializerMethodField()

    class Meta:
        model = DestinationConfig
        fields = [
            'dst_config_id',
            'name',
            'dst',
            'config_data',
            'destination_fields',
            'cmp',
            'created_by_name',
            'updated_by_name',
            'is_active',
            'created_at',
            'updated_at',
        ]

    def get_config_data(self, obj):
        return obj.data or {}

    def get_destination_fields(self, obj):
        try:
            destination = obj.dst
            attrs = DestinationAttribute.objects.filter(dst=destination).order_by('dst_attr_id')
            return AdminDestinationAttributeSerializer(attrs, many=True).data
        except Exception:
            return []

    def _get_user_name(self, user):
        if not user:
            return None
        full_name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
        return full_name or getattr(user, 'company_email', None)

    def get_created_by_name(self, obj):
        user = (
            getattr(obj, 'created_by', None)
            or getattr(getattr(obj, 'cmp', None), 'created_by', None)
        )
        return self._get_user_name(user) or 'Support Admin'

    def get_updated_by_name(self, obj):
        user = (
            getattr(obj, 'updated_by', None)
            or getattr(obj, 'created_by', None)
            or getattr(getattr(obj, 'cmp', None), 'updated_by', None)
            or getattr(getattr(obj, 'cmp', None), 'created_by', None)
        )
        return self._get_user_name(user) or 'Support Admin'
