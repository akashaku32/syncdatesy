from datetime import timedelta
from django.utils import timezone
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.contrib.auth.tokens import default_token_generator
import json
import logging
from rest_framework import serializers

logger = logging.getLogger(__name__)
from rest_framework.validators import UniqueValidator
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from ..models import Company, Role, Invoice, DestinationConfig, SourceConfig, Connection, DestinationModel, SourceModel, SourceTableSelection, TableMapping, DestinationTableSelection, CommunicationSupport, Country, RegistrationRequest , EmailTemplate  # Add DestinationConfig
 
User = get_user_model()


class RegistrationRequestSerializer(serializers.ModelSerializer):
    first_name = serializers.CharField(max_length=100, trim_whitespace=True)
    last_name = serializers.CharField(max_length=100, trim_whitespace=True)
    email = serializers.EmailField()
    phone_number = serializers.CharField(
        max_length=16,
        trim_whitespace=True,
        required=False,
        allow_blank=True,
        default='',
    )
    company_name = serializers.CharField(max_length=100, trim_whitespace=True)

    class Meta:
        model = RegistrationRequest
        fields = [
            'registration_request_id',
            'first_name',
            'last_name',
            'email',
            'phone_number',
            'company_name',
            'created_at',
        ]
        read_only_fields = ['registration_request_id', 'created_at']


class SignUpSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(write_only=True)
    employee_range = serializers.CharField(write_only=True)
    company_email = serializers.EmailField(
        write_only=True,
        validators=[
            UniqueValidator(
                queryset=User.objects.all(),
                message="A user with that email already exists."
            )
        ]
    )

    password = serializers.CharField(
        write_only=True,
        style={'input_type': 'password'}
    )

    class Meta:
        model = User
        fields = [
            'first_name', 'last_name',
            'company_name', 'employee_range',
            'company_email', 'password'
        ]

    # ✅ FIX: enforce Django password validation properly
    def validate(self, attrs):
        user = User(email=attrs.get("company_email"))
        validate_password(attrs.get("password"), user)
        return attrs

    def create(self, validated_data):
        company_name = validated_data.pop('company_name')
        employee_range = validated_data.pop('employee_range')
        company_email = validated_data.pop('company_email')
        password = validated_data.pop('password')

        user = User(
            first_name=validated_data.get('first_name'),
            last_name=validated_data.get('last_name'),
            email=company_email,
        )

        user.company_email = company_email
        user.company_name = company_name
        user.set_password(password)

        request = self.context.get('request')
        if request and request.user.is_authenticated:
            company = Company.objects.filter(cmp_id=request.user.cmp.cmp_id).first()
        else:
            start_date = timezone.now()
            end_date = start_date + timedelta(days=14)
            company = Company.objects.create(
                data=company_name,
                employee_range=employee_range,
                start_date=start_date,
                end_date=end_date,
                is_trial=True
            )
            user.is_superuser = True

        role = Role.objects.filter(role_name='Administrator').first()
        user.cmp = company
        user.roles = role

        user.save()
        if company and not company.created_by:
            company.created_by = user
            company.updated_by = user
            company.save(update_fields=['created_by', 'updated_by'])
        return user

class VerifyOTPSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(min_length=6, max_length=6)


class ResendOTPSerializer(serializers.Serializer):
    email = serializers.EmailField()


def send_otp_email(email, otp_code):
    from django.conf import settings
    from urllib.parse import quote_plus
    frontend_url = getattr(settings, 'FRONTEND_URL', 'https://qa.datasyncher.com')
    verify_url = f"{frontend_url}/auth/verify-email?email={quote_plus(email)}"
  
    subject = 'Verify your Datasyncher account'
    text_content = f"""Hello,  from django.conf import settings
    from urllib.parse import quote_plus
    frontend_url = getattr(settings, 'FRONTEND_URL', 'https://qa.datasyncher.com')
    verify_url = f"{frontend_url}/auth/verify-email?email={quote_plus(email)}"

Your verification code is: {otp_code}

Or click the link below to verify directly:
{verify_url}

This code expires in 10 minutes. Do not share it with anyone.

Thanks,
Datasyncher Team
"""
    html_content = f"""
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#ffffff;font-family:Arial,sans-serif;color:#111111;">
<table width="100%" cellspacing="0" cellpadding="0" style="padding:24px;">
<tr><td align="center">
<div style="margin-bottom:10px;font-size:13px;color:#52525B;">
Click the link below to open the verification page and enter the OTP code.
</div>
<div style="margin-bottom:18px;font-size:14px;font-weight:600;">
<a href="{verify_url}" style="color:#2563EB;text-decoration:underline;">{verify_url}</a>
</div>
<table width="480" cellspacing="0" cellpadding="0" style="background:#ffffff;border:1px solid #E4E4E7;border-radius:10px;padding:28px;">
<tr><td style="font-size:18px;font-weight:600;text-align:center;">Verify your email</td></tr>
<tr><td height="14"></td></tr>
<tr><td style="font-size:14px;line-height:1.6;">Use the code below to verify your Datasyncher account.</td></tr>
<tr><td height="22"></td></tr>
<tr><td align="center" style="font-size:32px;font-weight:700;letter-spacing:8px;color:#2563EB;">{otp_code}</td></tr>
<tr><td height="22"></td></tr>
<tr><td style="font-size:13px;color:#52525B;line-height:1.6;">This code expires in <strong>10 minutes</strong>.<br>Do not share this code with anyone.</td></tr>
<tr><td height="24"></td></tr>
<tr><td style="font-size:12px;color:#9CA3AF;text-align:center;border-top:1px solid #E4E4E7;padding-top:14px;">Thanks,<br>Datasyncher Support Team</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""
    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )
    msg.attach_alternative(html_content, 'text/html')
    msg.send()


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)
 
 
class PasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField()
 
    def validate_email(self, value):
        if not User.objects.filter(email=value).exists():
            raise serializers.ValidationError("User with this email does not exist.")
        return value
 
    def save(self):
        user = User.objects.get(email=self.validated_data['email'])
        token = default_token_generator.make_token(user)
        uid = urlsafe_base64_encode(force_bytes(user.pk))

        frontend_url = getattr(
            settings,
            'FRONTEND_URL',
            'https://datasyncher.com',
        ).rstrip('/')
        reset_url = f"{frontend_url}/auth/reset-password?token={token}&uid={uid}"

        send_password_reset_email(user, reset_url)


def send_password_reset_email(user, reset_url):
    subject = "Reset your Datasyncher password"

    text_content = f"""
Hello,

We received a request to reset your Datasyncher password.

Use the link below to set a new password:
{reset_url}

This link expires in 30 minutes and can only be used once.

If you did not request this, you can safely ignore this email.

Datasyncher Security Team
support1@datasyncher.com
"""

    html_content = f"""
<!DOCTYPE html>
<html>
<body style="
    margin:0;
    padding:0;
    background:#ffffff;
    font-family:'Plus Jakarta Sans', Arial, sans-serif;
    color:#09090B;
">

<table width="100%" cellspacing="0" cellpadding="0" style="padding:24px;">
<tr>
<td align="center">

<table width="480" cellspacing="0" cellpadding="0"
       style="
           background:#ffffff;
           border:1px solid #E4E4E7;
           border-radius:10px;
           padding:28px;
       ">

<tr>
<td style="font-size:18px; font-weight:600; text-align:center;">
Reset Your Password
</td>
</tr>

<tr><td height="14"></td></tr>

<tr>
<td style="font-size:14px; line-height:1.6;">
We received a request to reset the password for your Datasyncher account.
</td>
</tr>

<tr><td height="22"></td></tr>

<tr>
<td align="center">
<a href="{reset_url}"
   style="
       background:#2563EB;
       color:#ffffff;
       padding:12px 22px;
       border-radius:6px;
       text-decoration:none;
       font-size:14px;
       font-weight:600;
       display:inline-block;
   ">
Reset Password
</a>
</td>
</tr>

<tr><td height="22"></td></tr>

<tr>
<td style="font-size:13px; color:#52525B; line-height:1.6;">
This link will expire in <strong>30 minutes</strong> and can only be used once.
<br><br>
If you did not request this password reset, you can safely ignore this email.
Your password will remain unchanged.
<br><br>
For security reasons, do not share this link with anyone.
</td>
</tr>

<tr><td height="26"></td></tr>

<tr>
<td style="
    font-size:12px;
    color:#71717A;
    text-align:center;
    border-top:1px solid #E4E4E7;
    padding-top:14px;
">
Datasyncher Security Team<br>
support1@datasyncher.com
</td>
</tr>

</table>
</td>
</tr>
</table>

</body>
</html>
"""

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )

    email.attach_alternative(html_content, "text/html")
    email.send()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(min_length=8, write_only=True)
    confirm_password = serializers.CharField(min_length=8, write_only=True)
 
    def validate(self, data):
        if data['new_password'] != data['confirm_password']:
            raise serializers.ValidationError({
                'confirm_password': 'Passwords do not match.'
            })

        try:
            uid = force_str(urlsafe_base64_decode(data['uid']))
            user = User.objects.get(pk=uid)
        except Exception:
            raise serializers.ValidationError("Invalid user.")

        if not default_token_generator.check_token(user, data['token']):
            raise serializers.ValidationError("Invalid or expired token.")

        try:
            validate_password(data['new_password'], user)
        except DjangoValidationError as error:
            raise serializers.ValidationError({
                'new_password': error.messages,
            })

        data['user'] = user
        return data
 
    def save(self):
        user = self.validated_data['user']
        user.set_password(self.validated_data['new_password'])
        user.password_reset_required = False
        user.save(update_fields=['password', 'password_reset_required'])
 
 
# NEW: DestinationConfigSerializer
class DestinationConfigSerializer(serializers.ModelSerializer):
    dst = serializers.CharField(source='dst.name') 
    created_by = serializers.SerializerMethodField()
    updated_by = serializers.SerializerMethodField()
    modified_by = serializers.SerializerMethodField()
    modified_at = serializers.DateTimeField(source='updated_at', read_only=True)
 
    class Meta:
        model = DestinationConfig
        fields = [
            'dst_config_id', 'name', 'dst', 'created_at', 'updated_at', 'modified_at',
            'created_by', 'updated_by', 'modified_by',
            'cmp', 'is_active'
        ]
 
    def _serialize_user(self, user):
        if not user:
            return None
        return {
            "user_id": getattr(user, "user_id", None),
            "email": getattr(user, "company_email", None) or getattr(user, "email", None),
            "first_name": getattr(user, "first_name", None),
            "last_name": getattr(user, "last_name", None),
        }
 
    def get_created_by(self, obj):
        return self._serialize_user(
            getattr(obj, "created_by", None)
            or getattr(getattr(obj, "cmp", None), "created_by", None)
        )

    def get_updated_by(self, obj):
        return self._serialize_user(
            getattr(obj, "updated_by", None)
            or getattr(obj, "created_by", None)
            or getattr(getattr(obj, "cmp", None), "updated_by", None)
            or getattr(getattr(obj, "cmp", None), "created_by", None)
        )

    def get_modified_by(self, obj):
        return self.get_updated_by(obj)


def _is_adls_destination_name(destination_name):
    normalized = ''.join(ch for ch in str(destination_name or '').strip().lower() if ch.isalnum())
    return normalized in {'azuredatalakestorage', 'adls'}


class DestinationModelSerializer(serializers.ModelSerializer):
    """
    Serializer for DestinationModel capability metadata.
    """
    supports_notification_groups = serializers.SerializerMethodField()

    class Meta:
        model = DestinationModel
        fields = [
            'dst_id',
            'name',
            'min_count',
            'max_count',
            'is_file_based',
            'supports_notification_groups',
            'path_label',
            'supported_formats',
            'default_format',
            'csv_defaults',
        ]

    def get_supports_notification_groups(self, obj):
        if _is_adls_destination_name(getattr(obj, 'name', None)):
            return True
        return bool(getattr(obj, 'supports_notification_groups', False))


class SourceModelSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    source_name = serializers.CharField(source='name', read_only=True)

    class Meta:
        model = SourceModel
        fields = ['src_id', 'name', 'source_name', 'disable_update_schema']

    def get_name(self, obj):
        display_names = {
            'MicrosoftDynamics365_FO': 'Microsoft Dynamics 365 F&O',
            'NetSuite': 'NetSuite',
        }
        return display_names.get(obj.name, obj.display_name or obj.name)




class DestinationConfigCreateSerializer(serializers.Serializer):

    dst = serializers.SlugRelatedField(slug_field='name', queryset=DestinationModel.objects.all(),required=False)
    name = serializers.CharField(required=False)
    config_data = serializers.JSONField()


    def validate_config_data(self, value):
        if not isinstance(value, dict) or not value:
            raise serializers.ValidationError("config_data must not be empty.")
        return value

    def _normalize_nested_config_payload(self, data):
        """
        Support multipart submissions that place the full destination payload
        inside the `config_data` JSON field alongside uploaded files.
        """
        config_data = data.get('config_data')
        if not isinstance(config_data, dict):
            return data

        nested_config = config_data.get('config_data')
        if not isinstance(nested_config, dict):
            return data

        normalized = dict(data)
        if not normalized.get('dst') and config_data.get('dst'):
            normalized['dst'] = config_data.get('dst')
        if not normalized.get('name') and config_data.get('name'):
            normalized['name'] = config_data.get('name')
        normalized['config_data'] = nested_config
        return normalized

    def _resolve_destination_instance(self, data):
        dst_value = data.get('dst')
        if not isinstance(dst_value, str):
            return data

        destination_name = dst_value.strip()
        if not destination_name:
            return data

        normalized = dict(data)
        try:
            normalized['dst'] = DestinationModel.objects.get(name=destination_name)
        except DestinationModel.DoesNotExist:
            raise serializers.ValidationError({
                'dst': f"Destination '{destination_name}' does not exist."
            })
        return normalized

    def validate(self, data):
        """
        Validate and auto-populate dst and name fields if missing.
        
        Priority for dst (destination type):
        1. From request data (if provided)
        2. From session (stored when schema was loaded) - REQUIRED
        
        Priority for name:
        1. From request data (if provided)
        2. From config_data.destination_name
        """
        data = self._normalize_nested_config_payload(data)
        data = self._resolve_destination_instance(data)
        if not self.context.get('is_update', False):
            print("\n" + "=" * 80)
            print("🔍 DESTINATION CONFIG VALIDATION")
            print(f"   Initial dst: {data.get('dst')}")
            print(f"   Initial name: {data.get('name')}")
            print(f"   Config data keys: {list(data.get('config_data', {}).keys())}")
            
            # ==========================================
            # STEP 1: Extract 'name' from config_data.destination_name if missing
            # ==========================================
            print("\n📝 STEP 1: Extracting destination name...")
            if not data.get('name') and 'config_data' in data:
                destination_name = data['config_data'].get('destination_name')
                if destination_name:
                    data['name'] = destination_name
                    print(f"   ✅ Extracted name from config_data: '{destination_name}'")
                else:
                    print(f"   ⚠️ No 'destination_name' found in config_data")
            else:
                if data.get('name'):
                    print(f"   ℹ️ Name already provided: '{data.get('name')}'")
                else:
                    print(f"   ⚠️ No config_data available")
            
            # ==========================================
            # STEP 2: Get 'dst' from session (stored when schema was loaded)
            # ==========================================
            print("\n💾 STEP 2: Retrieving destination from session...")
            if not data.get('dst'):
                request = self.context.get('request')
                if request and hasattr(request, 'session'):
                    selected_destination = request.session.get('selected_destination_name')
                    print(f"   Session destination: {selected_destination}")
                    if selected_destination:
                        try:
                            data['dst'] = DestinationModel.objects.get(name=selected_destination)
                            print(f"   ✅ Retrieved destination from session: {selected_destination}")
                            print(f"      Destination object: {data['dst']}")
                        except DestinationModel.DoesNotExist:
                            print(f"   ❌ Destination '{selected_destination}' from session not found in DB")
                    else:
                        print(f"   ⚠️ No 'selected_destination_name' in session")
                        print(f"   ⚠️ User must click on a destination from the list to load the schema first")
                else:
                    print(f"   ⚠️ No request or session available in context")
            else:
                print(f"   ℹ️ Destination already provided: {data.get('dst')}")
            
            # ==========================================
            # STEP 3: Validate required fields
            # ==========================================
            print("\n✅ STEP 3: Final validation...")
            print(f"   Final dst: {data.get('dst')}")
            print(f"   Final name: {data.get('name')}")
            
            if not data.get('dst'):
                print(f"   ❌ VALIDATION FAILED: Destination type missing")
                print(f"   ℹ️ Hint: User must click on a destination (e.g., Snowflake) from the list")
                print(f"   ℹ️ This loads the schema AND stores the destination in session")
                print("=" * 80)
                raise serializers.ValidationError({
                    'dst': 'Destination type not found in session. Please go back and select a destination from the list first.'
                })
            if not data.get('name'):
                print(f"   ❌ VALIDATION FAILED: Destination name missing")
                print("=" * 80)
                raise serializers.ValidationError({
                    'name': 'Destination name is required. Please provide a name in the destination_name field.'
                })
            
            print(f"   ✅ Validation passed!")
            print(f"      - Destination: {data['dst'].name}")
            print(f"      - Name: {data['name']}")
            print("=" * 80 + "\n")
        
        return data

 # for getting configuration details of a destinationconfig    
class DestinationDetailSerializer(serializers.ModelSerializer):
    config_data = serializers.SerializerMethodField()
    dst = serializers.SerializerMethodField()
    class Meta:
        model = DestinationConfig
        fields = ['dst_config_id', 'name', 'dst', 'config_data']

    
    def get_dst(self, obj):
        try:
            return obj.dst.name
        except AttributeError:
            return None
        
    def get_config_data(self, obj):
        # Ensure proper parsing of JSON string or return as-is if already a dict
        if isinstance(obj.data, str):
            try:
                config_data = json.loads(obj.data)
            except json.JSONDecodeError:
                config_data = {}
        else:
            config_data = obj.data or {}
        
        # Ensure destination_name is included (use config name if not in data)
        if 'destination_name' not in config_data:
            config_data['destination_name'] = obj.name
        
        # Ensure authentication_type is included if it exists in the data
        # (This preserves the authentication type for editing)
        if not config_data.get('authentication_type'):
            # Try to infer from existing fields (password vs key-based)
            if config_data.get('password'):
                config_data['authentication_type'] = 'password'
            elif config_data.get('private_key'):
                config_data['authentication_type'] = 'key_pair'

        if getattr(getattr(obj, "dst", None), "name", None) == "SharePoint":
            certificate_filename = config_data.get("certificate_filename")
            certificate_password_present = config_data.get("certificate_password") not in ("", None)
            config_data.pop("client_certificate_content", None)
            config_data.pop("client_certificate", None)
            config_data["client_certificate_file"] = certificate_filename
            config_data["client_certificate_uploaded"] = bool(certificate_filename)
            config_data["certificate_password_set"] = bool(certificate_password_present)
            config_data["certificate_password"] = "********" if certificate_password_present else None

        return config_data

# for getting configuration details of a sourceconfig       
class SourceConfigDetailSerializer(serializers.ModelSerializer):

    config_data = serializers.SerializerMethodField()
    
    class Meta:

        model = SourceConfig

        fields = ['src_config_id','name','src','config_data']

    def get_config_data(self, obj):
      
        return obj.data or {}
    







class DynamicSourceConfigSerializer(serializers.Serializer):
    connection_name = serializers.CharField()

    destination_schema = serializers.CharField(required=False)

    form_data = serializers.DictField()  

    action = serializers.ChoiceField(choices=["authorize", "test"])




# serializers.py

import datetime
class ConnectionSerializer(serializers.ModelSerializer):
    readable_time_frequency = serializers.SerializerMethodField()
    last_synced_new = serializers.SerializerMethodField()
    next_sync_time = serializers.SerializerMethodField()
    created_by = serializers.SerializerMethodField()
    updated_by = serializers.SerializerMethodField()
    modified_by = serializers.SerializerMethodField()
    modified_at = serializers.DateTimeField(source='updated_at', read_only=True)
    source_name = serializers.SerializerMethodField()
    display_name = serializers.SerializerMethodField()  # ✅ Friendly name for UI
    destination_name = serializers.SerializerMethodField()
    is_file_based = serializers.SerializerMethodField()
    supports_notification_groups = serializers.SerializerMethodField()
    disable_update_schema = serializers.SerializerMethodField()
    connector_name = serializers.CharField(source='src_config.name', read_only=True)
    dst_config_name = serializers.CharField(source='dst_config.name', read_only=True)
    migration_status = serializers.SerializerMethodField()  # Runtime sync state: S/E/None
    created_by = serializers.SerializerMethodField()
    updated_by = serializers.SerializerMethodField()
    error_message = serializers.SerializerMethodField()

    class Meta:
        model = Connection
        fields = [
            'connection_id','connector_name','dst_config_name','source_name','display_name','destination_name','status',
            'created_at', 'modified_at', 'created_by', 'updated_by', 'modified_by',
            'is_file_based', 'supports_notification_groups',
            'migration_status', 'error_message',
            'connection_id','connector_name','source_name','display_name','destination_name','status',
            'migration_status', 'error_message', 'retrieval_progress',
            'readable_time_frequency', 'last_synced_new', 'next_sync_time',
            'disable_update_schema',
        ]
    def _serialize_user(self, user):
        if not user:
            return None

        return {
            "user_id": getattr(user, "user_id", None),
            "email": getattr(user, "company_email", None) or getattr(user, "email", None),
            "first_name": getattr(user, "first_name", None),
            "last_name": getattr(user, "last_name", None),
        }

    def get_created_by(self, obj):
        return self._serialize_user(obj.created_by)

    def get_updated_by(self, obj):
        return self._serialize_user(obj.updated_by)
    def get_migration_status(self, obj):
        """Return runtime sync state: S=Syncing, E=Error, None=idle.
        Computed in bulk by ConnectionListAPIView — no per-connection DB queries.
        """
        return self.context.get('migration_statuses', {}).get(obj.connection_id)

    def get_error_message(self, obj):
        return self.context.get('job_level_error_messages', {}).get(obj.connection_id)

    def _serialize_user(self, user):
        if not user:
            return None
        return {
            "user_id": getattr(user, "user_id", None),
            "email": getattr(user, "company_email", None) or getattr(user, "email", None),
            "first_name": getattr(user, "first_name", None),
            "last_name": getattr(user, "last_name", None),
        }

    def get_created_by(self, obj):
        created_by_users = self.context.get('created_by_users', {})
        user = created_by_users.get(obj.connection_id)
        if user:
            return self._serialize_user(user)
        return self._serialize_user(
            getattr(obj, "created_by", None)
            or getattr(getattr(obj, "dst_config", None), "created_by", None)
            or getattr(getattr(obj, "cmp", None), "created_by", None)
        )

    def get_updated_by(self, obj):
        updated_by_users = self.context.get('updated_by_users', {})
        user = updated_by_users.get(obj.connection_id)
        if user:
            return self._serialize_user(user)
        fallback_user = self.context.get('created_by_users', {}).get(obj.connection_id)
        if fallback_user:
            return self._serialize_user(fallback_user)
        return self._serialize_user(
            getattr(obj, "updated_by", None)
            or getattr(getattr(obj, "dst_config", None), "updated_by", None)
            or getattr(obj, "created_by", None)
            or getattr(getattr(obj, "dst_config", None), "created_by", None)
            or getattr(getattr(obj, "cmp", None), "updated_by", None)
            or getattr(getattr(obj, "cmp", None), "created_by", None)
        )

    def get_modified_by(self, obj):
        return self.get_updated_by(obj)

    def get_source_name(self, obj):
        """Return the raw source name for URL routing."""
        try:
            return obj.src_config.src.name
        except AttributeError:
            return None

    def get_display_name(self, obj):
        """Return the user-friendly display name for UI."""
        try:
            return obj.src_config.src.display_name
        except AttributeError:
            return None

    def get_destination_name(self, obj):
        try:
            return obj.dst_config.dst.name
        except AttributeError:
            return None

    def get_is_file_based(self, obj):
        try:
            return bool(obj.dst_config.dst.is_file_based)
        except AttributeError:
            return False

    def get_supports_notification_groups(self, obj):
        try:
            if _is_adls_destination_name(obj.dst_config.dst.name):
                return True
            return bool(obj.dst_config.dst.supports_notification_groups)
        except AttributeError:
            return False

    def get_disable_update_schema(self, obj):
        try:
            return bool(obj.src_config and obj.src_config.src and obj.src_config.src.disable_update_schema)
        except Exception:
            return False

    def get_readable_time_frequency(self, obj):
        freq_map = {
            'None': 'None', '1': '1 minute', '5': '5 minutes', '15': '15 minutes',
            '30': '30 minutes', '60': '1 hour', '120': '2 hours',
            '180': '3 hours', '360': '6 hours', '480': '8 hours',
            '720': '12 hours', '1440': '24 hours'
        }
        return freq_map.get(obj.time_frequency, 'Unknown')

    def get_last_synced_new(self, obj):
        sync_times = self.context.get('last_synced_times', {})
        return sync_times.get(obj.connection_id, "Never Synced")

    def get_next_sync_time(self, obj):
        """
        Calculate next sync time based on last sync and sync frequency.
        Returns datetime object if connection is active and has sync frequency, otherwise "None".
        """
        if obj.status == "A":
            try:
                job = obj.scheduledjob_set.order_by('-updated_at').first()
                sync_minutes = None
                if job and job.frequency_minutes:
                    sync_minutes = int(job.frequency_minutes)
                    logger.info("[next_sync] connection_id=%s using job.frequency_minutes=%s", obj.connection_id, sync_minutes)
                else:
                    if obj.time_frequency == "None":
                        logger.info("[next_sync] connection_id=%s no frequency set (time_frequency=None)", obj.connection_id)
                        return "None"
                    sync_minutes = int(obj.time_frequency)
                    logger.info("[next_sync] connection_id=%s using connection.time_frequency=%s", obj.connection_id, sync_minutes)
                
                if job:
                    last_synced = job.updated_at
                else:
                    # Fallback to current UTC time if no job exists
                    last_synced = timezone.now()
                
                # Handle datetime parsing if last_synced is a string
                if isinstance(last_synced, str):
                    try:
                        # Try parsing with timezone format
                        last_synced = datetime.strptime(last_synced, '%Y-%m-%d %H:%M:%S.%f+00:00')
                        # Make timezone-aware if naive
                        if last_synced.tzinfo is None:
                            last_synced = timezone.make_aware(last_synced)
                    except ValueError:
                        try:
                            # Try parsing without timezone seconds
                            last_synced = datetime.strptime(last_synced, '%Y-%m-%d %H:%M:%S.%f+00')
                            # Make timezone-aware if naive
                            if last_synced.tzinfo is None:
                                last_synced = timezone.make_aware(last_synced)
                        except ValueError:
                            # If parsing fails, use current time
                            last_synced = timezone.now()
                
                # Ensure last_synced is timezone-aware (in case job.updated_at is naive, though unlikely)
                if last_synced.tzinfo is None:
                    last_synced = timezone.make_aware(last_synced)
                
                # Calculate next sync time
                next_sync_time = last_synced + timedelta(minutes=sync_minutes)
                logger.info("[next_sync] connection_id=%s last_synced=%s next_sync_time=%s", obj.connection_id, last_synced, next_sync_time)
                return next_sync_time
            except Exception as e:
                print(f"Error calculating next sync time: {str(e)}")
                return "None"
        return "None"




class SourceConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceConfig
        fields = ['src_config_id', 'src', 'name']





# class ConnectionSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = Connection
#         fields = '__all__'

class SourceTableSelectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceTableSelection
        fields = [
            'tbl_id', 'connection', 'sequence', 'selected', 'is_mapping_checked',
            'last_synced', 'account_type', 'updated_by_id', 'table', 'folder_path',
            'json_path', 'table_fields', 'primary_keys', 'output_file_name',
            'target_folder', 'file_format', 'csv_delimiter', 'csv_quote_char',
            'excel_sheet_name', 'excel_options', 'excel_conditional_formats',
            'add_utc_timestamp', 'filename_date_format',
            'selected_fields', 'notification_email_group_ids', 'added_on', 'status', 'is_history',
            'email_custom_fields','email_template', 'delta_timestamp_field', 'combination_formula', 'truncate_data', 'is_delta',
        ]

class DestinationTableSelectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DestinationTableSelection
        fields = '__all__'

class TableMappingSerializer(serializers.ModelSerializer):
    class Meta:
        model = TableMapping
        fields = '__all__'






# serializers.py
from rest_framework import serializers

class ConnectionDetailSerializer(serializers.ModelSerializer):
    min_count = serializers.SerializerMethodField()
    max_count = serializers.SerializerMethodField()
    effective_max_chunk = serializers.SerializerMethodField()
    readable_time_frequency = serializers.SerializerMethodField()
    readable_safety_interval = serializers.SerializerMethodField()
    next_sync_time = serializers.SerializerMethodField()

    # ✅ platform names
    source_name = serializers.SerializerMethodField()
    display_name = serializers.SerializerMethodField()  # ✅ Friendly name for UI
    destination_name = serializers.SerializerMethodField()
    is_file_based = serializers.SerializerMethodField()
    supports_notification_groups = serializers.SerializerMethodField()

    # ✅ config names (renamed)
    source_title = serializers.SerializerMethodField()
    destination_title = serializers.SerializerMethodField()

    company_name = serializers.SerializerMethodField()
    target_schema = serializers.CharField(source="dst_schema", read_only=True)
    target_database = serializers.SerializerMethodField()
    
    # ✅ Reverse ETL flag
    is_reverse_etl = serializers.SerializerMethodField()
    disable_update_schema = serializers.SerializerMethodField()
    
    # ✅ Connection creation timestamp
    connected_on = serializers.DateTimeField(source='created_at', read_only=True)
    modified_at = serializers.DateTimeField(source='updated_at', read_only=True)
    created_by = serializers.SerializerMethodField()
    modified_by = serializers.SerializerMethodField()
    root_folder = serializers.SerializerMethodField()
    batches_in_pipeline = serializers.SerializerMethodField()
    has_batches_in_pipeline = serializers.SerializerMethodField()

    class Meta:
        model = Connection
        fields = [
            "connection_id", "time_frequency", "safety_interval", "execution_order",
            "sync_start_date", "chunk_count", "status",
            "readable_time_frequency", "readable_safety_interval",
            "next_sync_time",
            "min_count", "max_count",
            "effective_max_chunk",
            "source_name", "display_name", "destination_name",          # ✅ platform names
            "is_file_based", "supports_notification_groups",
            "source_title", "destination_title", # ✅ config names
            "company_name", "target_schema",
            "target_database",
            "is_reverse_etl",  # ✅ Reverse ETL flag
            "disable_update_schema",
            "connected_on",  # ✅ Connection creation timestamp
            "created_at",
            "created_by",
            "modified_at",
            "modified_by",
            "root_folder",
            "batches_in_pipeline",
            "has_batches_in_pipeline",
        ]

    def _serialize_user(self, user):
        if not user:
            return None
        return {
            "user_id": getattr(user, "user_id", None),
            "email": getattr(user, "company_email", None) or getattr(user, "email", None),
            "first_name": getattr(user, "first_name", None),
            "last_name": getattr(user, "last_name", None),
        }

    def get_created_by(self, obj):
        return self._serialize_user(
            getattr(obj, "created_by", None)
            or getattr(getattr(obj, "dst_config", None), "created_by", None)
            or getattr(getattr(obj, "cmp", None), "created_by", None)
        )

    def get_modified_by(self, obj):
        return self._serialize_user(
            getattr(obj, "updated_by", None)
            or getattr(getattr(obj, "dst_config", None), "updated_by", None)
            or getattr(obj, "created_by", None)
            or getattr(getattr(obj, "dst_config", None), "created_by", None)
            or getattr(getattr(obj, "cmp", None), "updated_by", None)
            or getattr(getattr(obj, "cmp", None), "created_by", None)
        )

    
    def get_source_name(self, obj):
        """Return the raw source name for URL routing."""
        try:
            return obj.src_config.src.name
        except AttributeError:
            return None
    def get_display_name(self, obj):
        """Return the user-friendly display name for UI."""
        try:
            return obj.src_config.src.display_name
        except AttributeError:
            return None

    # ✅ Platform → “Snowflake”
    def get_destination_name(self, obj):
        try:
            return obj.dst_config.dst.name
        except:
            return None

    def get_is_file_based(self, obj):
        try:
            return bool(obj.dst_config.dst.is_file_based)
        except AttributeError:
            return False

    def get_supports_notification_groups(self, obj):
        try:
            if _is_adls_destination_name(obj.dst_config.dst.name):
                return True
            return bool(obj.dst_config.dst.supports_notification_groups)
        except AttributeError:
            return False

    # ✅ Config Name → “SF_Prod_Config”
    def get_source_title(self, obj):
        return obj.src_config.name if obj.src_config else None

    # ✅ Config Name → “Snowflake_WH_QA”
    def get_destination_title(self, obj):
        return obj.dst_config.name if obj.dst_config else None

    def get_company_name(self, obj):
        return obj.cmp.data if obj.cmp else None

    def _connection_endpoints(self, obj):
        src = obj.src_config.src if obj.src_config else None
        dst = obj.dst_config.dst if obj.dst_config else None
        is_reverse_etl = bool(getattr(src, "is_rtl", False))
        return src, dst, is_reverse_etl

    def get_min_count(self, obj):
        from apps.elttool.services.chunk_limit_resolver import compute_connector_min

        src, dst, is_reverse_etl = self._connection_endpoints(obj)
        if not src and not dst:
            return None
        return compute_connector_min(
            getattr(src, "min_count", None),
            getattr(dst, "min_count", None),
            is_reverse_etl=is_reverse_etl,
        )

    def get_max_count(self, obj):
        from apps.elttool.services.chunk_limit_resolver import compute_connector_ceiling

        src, dst, is_reverse_etl = self._connection_endpoints(obj)
        if not src and not dst:
            return None
        return compute_connector_ceiling(
            getattr(src, "max_count", None),
            getattr(dst, "max_count", None),
            is_reverse_etl=is_reverse_etl,
        )

    def _plan_resource_row(self, obj):
        from apps.elttool.models import PlanResourceConfig
        from apps.elttool.services.chunk_limit_resolver import normalize_plan_name

        plan_name = normalize_plan_name(obj.cmp.plan if obj.cmp else "free")
        try:
            return PlanResourceConfig.objects.get(plan_name=plan_name)
        except PlanResourceConfig.DoesNotExist:
            return PlanResourceConfig.objects.filter(plan_name="free").first()

    def get_effective_max_chunk(self, obj):
        """Plan-adjusted chunk ceiling for chunk_count input (after % of connector max)."""
        from apps.elttool.services.chunk_limit_resolver import resolve_effective_max_chunk_for_plan

        plan_row = self._plan_resource_row(obj)
        if not plan_row:
            return None
        src, dst, is_reverse_etl = self._connection_endpoints(obj)
        override = getattr(obj.cmp, "resource_override", None) if obj.cmp else None
        return resolve_effective_max_chunk_for_plan(
            plan_row,
            getattr(src, "max_count", None),
            getattr(dst, "max_count", None),
            override=override,
            is_reverse_etl=is_reverse_etl,
        )

    def get_readable_time_frequency(self, obj):
        time_frequencies = {
            'None': 'None', '1': '1 minute', '5': '5 minutes', '15': '15 minutes',
            '30': '30 minutes', '60': '1 hour', '120': '2 hours', '180': '3 hours',
            '360': '6 hours', '480': '8 hours', '720': '12 hours', '1440': '24 hours'
        }
        return time_frequencies.get(obj.time_frequency, "Unknown")

    def get_readable_safety_interval(self, obj):
        time_frequencies = {
            'None': 'None', '1': '1 minute', '5': '5 minutes', '15': '15 minutes',
            '30': '30 minutes', '60': '1 hour', '120': '2 hours', '180': '3 hours',
            '360': '6 hours', '480': '8 hours', '720': '12 hours', '1440': '24 hours'
        }
        return time_frequencies.get(obj.safety_interval, "Unknown")

    def get_next_sync_time(self, obj):
        """
        Calculate next sync time based on last sync and sync frequency.
        Returns datetime object if connection is active and has sync frequency, otherwise "None".
        """
        if obj.status == "A":
            try:
                current_utc = timezone.now()
                job = obj.scheduledjob_set.order_by('-updated_at').first()
                sync_minutes = None
                if job and job.frequency_minutes:
                    sync_minutes = int(job.frequency_minutes)
                    logger.info("[next_sync] (detail) connection_id=%s using job.frequency_minutes=%s", obj.connection_id, sync_minutes)
                else:
                    if obj.time_frequency == "None":
                        logger.info("[next_sync] (detail) connection_id=%s no frequency set (time_frequency=None)", obj.connection_id)
                        return "None"
                    sync_minutes = int(obj.time_frequency)
                    logger.info("[next_sync] (detail) connection_id=%s using connection.time_frequency=%s", obj.connection_id, sync_minutes)
                
                if job:
                    last_synced = job.updated_at
                else:
                    # Fallback to current UTC time if no job exists
                    last_synced = timezone.now()
                
                # Handle datetime parsing if last_synced is a string
                if isinstance(last_synced, str):
                    try:
                        # Try parsing with timezone format
                        last_synced = datetime.strptime(last_synced, '%Y-%m-%d %H:%M:%S.%f+00:00')
                        # Make timezone-aware if naive
                        if last_synced.tzinfo is None:
                            last_synced = timezone.make_aware(last_synced)
                    except ValueError:
                        try:
                            # Try parsing without timezone seconds
                            last_synced = datetime.strptime(last_synced, '%Y-%m-%d %H:%M:%S.%f+00')
                            # Make timezone-aware if naive
                            if last_synced.tzinfo is None:
                                last_synced = timezone.make_aware(last_synced)
                        except ValueError:
                            # If parsing fails, use current time
                            last_synced = timezone.now()
                
                # Ensure last_synced is timezone-aware (in case job.updated_at is naive, though unlikely)
                if last_synced.tzinfo is None:
                    last_synced = timezone.make_aware(last_synced)
                
                # Calculate next sync time from job.updated_at
                next_sync_time = last_synced + timedelta(minutes=sync_minutes)
                logger.info("[next_sync] (detail) connection_id=%s last_synced=%s next_sync_time=%s", obj.connection_id, last_synced, next_sync_time)
                
                # If calculated time is in the past, use sync_start_date to calculate next upcoming interval
                if next_sync_time <= current_utc and obj.sync_start_date:
                    sync_start = obj.sync_start_date
                    if isinstance(sync_start, str):
                        # Frontend passes ISO format: "2025-12-20T13:35:44.004869Z"
                        try:
                            # Replace 'Z' with '+00:00' for fromisoformat compatibility
                            iso_string = sync_start.replace('Z', '+00:00') if 'Z' in sync_start else sync_start
                            sync_start = datetime.datetime.fromisoformat(iso_string)
                            # Make timezone-aware if naive
                            if sync_start.tzinfo is None:
                                sync_start = timezone.make_aware(sync_start)
                        except (ValueError, AttributeError):
                            sync_start = None
                    elif not isinstance(sync_start, datetime.datetime):
                        sync_start = None
                    
                    if sync_start and isinstance(sync_start, datetime.datetime):
                        # Ensure sync_start is timezone-aware
                        if sync_start.tzinfo is None:
                            sync_start = timezone.make_aware(sync_start)
                        # Calculate how many intervals have passed since sync_start_date
                        elapsed_minutes = (current_utc - sync_start).total_seconds() // 60
                        intervals_passed = int(elapsed_minutes // sync_minutes)
                        # Next sync is the upcoming interval from current time
                        next_sync_time = sync_start + timedelta(minutes=(intervals_passed + 1) * sync_minutes)
                        return next_sync_time
                
                return next_sync_time
            except Exception as e:
                print(f"Error calculating next sync time: {str(e)}")
                return "None"
        return "None"

    def get_target_database(self, obj):
        try:
            config_data = obj.dst_config.data
            return config_data.get("database") if config_data else None
        except:
            return None

    def get_root_folder(self, obj):
        try:
            config_data = obj.dst_config.data
            if not config_data:
                return None
            return config_data.get("root_folder") or config_data.get("folder_path")
        except Exception:
            return None

    # ✅ Reverse ETL check: Uses is_rtl flag from SourceModel
    def get_is_reverse_etl(self, obj):
        """
        Check if this connection is a reverse ETL connection using is_rtl flag.
        Returns True if source has is_rtl=True, False otherwise.
        """
        try:
            is_reverse = obj.src_config.src.is_rtl if obj.src_config and obj.src_config.src else False
            source_name = obj.src_config.src.name if obj.src_config and obj.src_config.src else None
            
            print(f"   🔍 Reverse ETL check:")
            print(f"      - Source: {source_name}")
            print(f"      - Is Reverse ETL (is_rtl): {is_reverse}")
            
            return is_reverse
        except Exception as e:
            print(f"   ⚠️  Error checking reverse ETL: {e}")
            return False

    def get_disable_update_schema(self, obj):
        try:
            return bool(obj.src_config and obj.src_config.src and obj.src_config.src.disable_update_schema)
        except Exception:
            return False

    def get_batches_in_pipeline(self, obj):
        from apps.elttool.services.pipeline_batch_guards import list_connection_pipeline_batch_usage
        return list_connection_pipeline_batch_usage(obj)

    def get_has_batches_in_pipeline(self, obj):
        from apps.elttool.services.pipeline_batch_guards import connection_has_batches_in_pipeline
        return connection_has_batches_in_pipeline(obj)


class ConnectionUpdateSerializer(serializers.Serializer):
    time_frequency = serializers.ChoiceField(choices=[
        ('None', 'None'), ('1', '1 minute'), ('5', '5 minutes'), ('15', '15 minutes'),
        ('30', '30 minutes'), ('60', '1 hour'), ('120', '2 hours'), ('180', '3 hours'),
        ('360', '6 hours'), ('480', '8 hours'), ('720', '12 hours'), ('1440', '24 hours')
    ])
    safety_interval = serializers.ChoiceField(choices=[
        ('None', 'None'), ('1', '1 minute'), ('5', '5 minutes'), ('15', '15 minutes'),
        ('30', '30 minutes'), ('60', '1 hour'), ('120', '2 hours'), ('180', '3 hours'),
        ('360', '6 hours'), ('480', '8 hours'), ('720', '12 hours'), ('1440', '24 hours')
    ])
    execution_order = serializers.ChoiceField(choices=[
        ('parallel', 'parallel'),
        ('sequential', 'sequential')
    ])
    sync_start_date = serializers.DateTimeField(required=False, allow_null=True)
    chunk_count = serializers.IntegerField()

    def to_internal_value(self, data):
        """
        Override to handle sync_start_date before DRF's DateTimeField tries to parse it.
        This intercepts empty strings and invalid formats before they cause validation errors.
        """
        # Get the raw value for sync_start_date
        sync_start_date_value = data.get('sync_start_date') if isinstance(data, dict) else None
        
        # If it's an empty string or invalid placeholder, convert to None
        if isinstance(sync_start_date_value, str):
            sync_start_date_value = sync_start_date_value.strip()
            if not sync_start_date_value or sync_start_date_value in ['0', 'None', 'null', '']:
                # Convert empty/invalid strings to None so DRF doesn't try to parse them
                if isinstance(data, dict):
                    data = data.copy()
                    data['sync_start_date'] = None
        
        # Call parent to_internal_value which will handle the rest
        return super().to_internal_value(data)

    def validate_sync_start_date(self, value):
        """
        Validate sync_start_date. If empty, "0", None, or invalid, use current UTC time + sync frequency.
        Frontend sends correct UTC ISO format (e.g., "2025-12-08T06:55:00Z") when set.
        """
        from datetime import datetime, timedelta
        import pytz
        
        print("=" * 80)
        print("🔍 [validate_sync_start_date] Validating sync_start_date")
        print(f"   Input value: {value}")
        print(f"   Input type: {type(value)}")
        
        # Get time_frequency from initial_data to calculate start date
        time_frequency = self.initial_data.get('time_frequency')
        print(f"   Time frequency from request: {time_frequency}")
        
        # Helper function to calculate start date with sync frequency
        def calculate_start_date_with_frequency():
            current_utc = datetime.now(pytz.utc)
            
            # If time_frequency is None or 'None', just use current UTC time
            if not time_frequency or time_frequency == 'None':
                print(f"   ⚠️ No sync frequency provided, using current UTC time")
                return current_utc
            
            # Convert time_frequency string to minutes (e.g., '30' -> 30 minutes)
            try:
                sync_minutes = int(time_frequency)
                calculated_start = current_utc + timedelta(minutes=sync_minutes)
                print(f"   ✅ Calculated start date: current UTC ({current_utc}) + {sync_minutes} minutes = {calculated_start}")
                return calculated_start
            except (ValueError, TypeError):
                print(f"   ⚠️ Invalid time_frequency '{time_frequency}', using current UTC time")
                return current_utc
        
        # Check if value is None
        if value is None:
            print("   ⚠️ sync_start_date is None, calculating with sync frequency")
            result = calculate_start_date_with_frequency()
            print(f"   ✅ Returning: {result}")
            print("=" * 80)
            return result
        
        # Handle string values
        if isinstance(value, str):
            value = value.strip()
            print(f"   📝 String value after strip: '{value}'")
            
            # Empty string, "0", "None", "null" means not set - calculate with sync frequency
            if not value or value in ['0', 'None', 'null', '']:
                print(f"   ⚠️ sync_start_date is empty/invalid ('{value}'), calculating with sync frequency")
                result = calculate_start_date_with_frequency()
                print(f"   ✅ Returning: {result}")
                print("=" * 80)
                return result
            
            # Frontend sends correct UTC ISO format (e.g., "2025-12-08T06:55:00Z")
            # Just parse it and ensure UTC timezone
            try:
                print(f"   📅 Parsing UTC ISO format: '{value}'")
                # Replace 'Z' with '+00:00' for fromisoformat compatibility
                iso_string = value.replace('Z', '+00:00')
                parsed = datetime.fromisoformat(iso_string)
                # Ensure it's UTC (should already be, but just in case)
                if parsed.tzinfo is None:
                    parsed = pytz.utc.localize(parsed)
                print(f"   ✅ Parsed successfully: {parsed}")
                print("=" * 80)
                return parsed
            except (ValueError, AttributeError) as e:
                print(f"   ❌ Could not parse sync_start_date '{value}': {e}")
                print(f"   ⚠️ Calculating with sync frequency instead")
                result = calculate_start_date_with_frequency()
                print(f"   ✅ Returning: {result}")
                print("=" * 80)
                return result
        
        # If it's already a datetime object, ensure it's UTC
        if isinstance(value, datetime):
            print(f"   📅 Value is already a datetime object: {value}")
            if value.tzinfo is None:
                print("   ⚠️ No timezone info, localizing to UTC")
                result = pytz.utc.localize(value)
            else:
                result = value
            print(f"   ✅ Returning: {result}")
            print("=" * 80)
            return result
        
        # Fallback: calculate with sync frequency
        print(f"   ⚠️ sync_start_date has unexpected type/format: {type(value)}")
        result = calculate_start_date_with_frequency()
        print(f"   ✅ Returning calculated start date: {result}")
        print("=" * 80)
        return result

    def validate_chunk_count(self, value):
        from apps.elttool.services.chunk_limit_resolver import resolve_effective_max_chunk_for_plan

        connection = self.context['connection']
        src = connection.src_config.src
        dst = connection.dst_config.dst
        from apps.elttool.services.chunk_limit_resolver import (
            compute_connector_ceiling,
            compute_connector_min,
        )

        is_reverse_etl = bool(getattr(src, "is_rtl", False))
        connector_min = compute_connector_min(
            getattr(src, "min_count", None),
            getattr(dst, "min_count", None),
            is_reverse_etl=is_reverse_etl,
        )
        if connector_min and value < connector_min:
            raise serializers.ValidationError(f"Minimum chunk count is {connector_min}")
        connector_max = compute_connector_ceiling(
            getattr(src, "max_count", None),
            getattr(dst, "max_count", None),
            is_reverse_etl=is_reverse_etl,
        )
        if connector_max is not None and value > connector_max:
            side = "destination" if is_reverse_etl else "source"
            raise serializers.ValidationError(
                f"Maximum chunk count ({side}) is {connector_max}"
            )

        plan_row = ConnectionDetailSerializer()._plan_resource_row(connection)
        if plan_row:
            override = getattr(connection.cmp, "resource_override", None) if connection.cmp else None
            plan_max = resolve_effective_max_chunk_for_plan(
                plan_row,
                getattr(src, "max_count", None),
                getattr(dst, "max_count", None),
                override=override,
                is_reverse_etl=is_reverse_etl,
            )
            if value > plan_max:
                raise serializers.ValidationError(
                    f"Maximum chunk count for your plan is {plan_max}"
                )
        return value
    def update(self, instance, validated_data):
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        request = self.context.get('request')
        if request and getattr(request, 'user', None) and request.user.is_authenticated:
            instance.updated_by = request.user
        instance.save()
        return instance


class ConnectionUsageSerializer(serializers.Serializer):
    daily_labels = serializers.ListField(child=serializers.CharField())
    new_rec_values_daily = serializers.ListField(child=serializers.IntegerField())
    mod_rec_values_daily = serializers.ListField(child=serializers.IntegerField())
    del_rec_values_daily = serializers.ListField(child=serializers.IntegerField())

    monthly_labels = serializers.ListField(child=serializers.CharField())
    new_rec_values_monthly = serializers.ListField(child=serializers.IntegerField())
    mod_rec_values_monthly = serializers.ListField(child=serializers.IntegerField())
    del_rec_values_monthly = serializers.ListField(child=serializers.IntegerField())

    readable_time_frequency = serializers.CharField()
    cmp_id = serializers.IntegerField()





# serializers.py
from rest_framework import serializers
from ..models import User, Role

class UserSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()
    company_email = serializers.EmailField(write_only=True)
    company_name = serializers.CharField(read_only=True)

    # ✅ Only for create - allow_blank=True allows empty strings during updates
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    
    # ✅ NEW: Accept both role (string) and role_id (integer) from frontend
    role_id = serializers.IntegerField(write_only=True, required=False)

    class Meta:
        model = User
        fields = [
            "user_id", "first_name", "last_name",
            "company_email", "company_name", "role", "role_id", "password"
        ]
        extra_kwargs = {
            "user_id": {"read_only": True},
        }

    def get_role(self, obj):
        return obj.roles.role_name if obj.roles else None

    def validate(self, attrs):
        print("\n" + "=" * 80)
        print("🔍 [UserSerializer.validate] Validating user data")
        print(f"   Validated attrs: {list(attrs.keys())}")
        print(f"   Initial data: {list(self.initial_data.keys())}")
        
        # ✅ NEW: Convert role name to role_id if role name is provided
        role_from_data = self.initial_data.get('role')
        role_id_from_data = self.initial_data.get('role_id') or attrs.get('role_id')
        
        print(f"   Role from initial_data: {role_from_data} (type: {type(role_from_data)})")
        print(f"   Role ID from data: {role_id_from_data} (type: {type(role_id_from_data)})")
        
        # If role name is provided but not role_id, convert it
        if role_from_data and not role_id_from_data:
            print(f"   🔄 Converting role name '{role_from_data}' to role_id...")
            try:
                role = Role.objects.get(role_name=role_from_data)
                attrs['role_id'] = role.role_id
                print(f"   ✅ Converted '{role_from_data}' to role_id={role.role_id}")
            except Role.DoesNotExist:
                print(f"   ❌ Role '{role_from_data}' does not exist")
                raise serializers.ValidationError(
                    {'role': f'Role "{role_from_data}" does not exist. Valid roles: Administrator, Analyst, Billing'}
                )
        elif role_id_from_data:
            print(f"   ℹ️  Role ID already provided: {role_id_from_data}")
            attrs['role_id'] = role_id_from_data
        
        # ✅ Only validate password during create, not update
        # self.instance is None during create, exists during update
        if self.instance is None and "password" in attrs:
            password = attrs.get("password")
            if password:  # Only validate if password is provided
                print(f"   🔒 Validating password...")
                company_email = attrs.get("company_email") or self.initial_data.get("company_email")
                user = User(email=company_email) if company_email else User()
                validate_password(password, user)
                print(f"   ✅ Password validation passed")
        
        print(f"   Final attrs keys: {list(attrs.keys())}")
        print("=" * 80 + "\n")
        return attrs

    def create(self, validated_data):
        print("\n" + "=" * 80)
        print("🔍 [UserSerializer.create] Creating new user")
        print(f"   Validated data keys: {list(validated_data.keys())}")
        
        # ✅ Get role_id from validated_data (already converted in validate())
        role_id = validated_data.pop('role_id', None)
        print(f"   Role ID: {role_id}")
        
        if not role_id:
            print(f"   ❌ No role_id provided")
            raise serializers.ValidationError({"role": "Role is required. Please provide either 'role' (name) or 'role_id' (integer)."})

        try:
            role = Role.objects.get(role_id=role_id)
            print(f"   ✅ Found role: {role.role_name} (ID: {role.role_id})")
        except Role.DoesNotExist:
            print(f"   ❌ Role with ID {role_id} does not exist")
            raise serializers.ValidationError({"role": f"Role with ID {role_id} does not exist."})

        # ✅ password validation happens in validate() method
        password = validated_data.pop("password", None)
        if not password:
            raise serializers.ValidationError({"password": "This field is required."})

        company_email = validated_data.pop("company_email")
        if User.objects.filter(company_email=company_email).exists():
            raise serializers.ValidationError({"company_email": "This company email already exists."})

        # ✅ Auto-assign company name from logged-in user
        request = self.context.get("request")
        if request:
            validated_data["company_name"] = request.user.cmp.data

        # ✅ Create user
        user = User(**validated_data)
        user.email = company_email
        user.company_email = company_email
        user.roles = role
        user.cmp = request.user.cmp

        # ✅ Set password (already validated in validate() method)
        user.set_password(password)

        user.save()
        return user

    def update(self, instance, validated_data):
        # ✅ NO PASSWORD SUPPORT HERE - explicitly remove password if present
        validated_data.pop("password", None)  # Remove password from validated_data
        
        roles_data = self.initial_data.get("role")
        if roles_data:
            try:
                instance.roles = Role.objects.get(role_name=roles_data)
            except Role.DoesNotExist:
                raise serializers.ValidationError({"role": f"Role '{roles_data}' does not exist."})

        company_email = validated_data.pop("company_email", None)
        if company_email:
            if User.objects.exclude(pk=instance.pk).filter(company_email=company_email).exists():
                raise serializers.ValidationError({"company_email": "This company email already exists."})
            instance.email = company_email
            instance.company_email = company_email

        request = self.context.get("request")
        if request:
            instance.company_name = request.user.cmp.data

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()
        return instance

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep["company_email"] = instance.company_email or instance.email
        return rep

class CompanyTrialSerializer(serializers.ModelSerializer):
    cmp_name = serializers.SerializerMethodField()
    valid_from = serializers.DateTimeField(source='start_date', read_only=True)
    valid_to = serializers.DateTimeField(source='end_date', read_only=True)
    class Meta:
        model = Company  
        fields = ['cmp_id','cmp_name','valid_from','valid_to']
    def get_cmp_name(self, obj):
        return obj.data


class UserProfileSerializer(serializers.ModelSerializer):
    company = CompanyTrialSerializer(source='cmp', read_only=True)
    role = serializers.SerializerMethodField()
    password = serializers.CharField(write_only=True, required=False)
    profile_image = serializers.SerializerMethodField()
    company_email = serializers.CharField(source="email")  # ✅ alias for email
    is_trial = serializers.SerializerMethodField()
    trial_days_remaining = serializers.SerializerMethodField()
    is_trial_expired = serializers.SerializerMethodField()
    message = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()  # ✅ NEW: Permissions object

    class Meta:
        model = User
        fields = [
            'user_id', 'first_name', 'last_name',
            'company_email', 'role', 'company',
            'password', 'profile_image',
            'is_trial', 'trial_days_remaining', 'is_trial_expired',
            'message', 'permissions'  # ✅ Added permissions
        ]
        extra_kwargs = {
            'user_id': {'read_only': True},      # 🔑 prevent "user_id required"
            'password': {'write_only': True}
        }
    
    def get_is_trial(self, obj):
        return obj.cmp.is_trial if obj.cmp else None
    
    def get_trial_days_remaining(self, obj):
        if obj.cmp:
            from django.utils import timezone
            if obj.cmp.is_trial and obj.cmp.end_date > timezone.now():
                return max(0, (obj.cmp.end_date - timezone.now()).days)
            return 0
        return None
    
    def get_is_trial_expired(self, obj):
        if obj.cmp:
            from django.utils import timezone
            return obj.cmp.is_trial and obj.cmp.end_date < timezone.now()
        return None
    
    def get_message(self, obj):
        if obj.cmp:
            from django.utils import timezone
            if obj.cmp.is_trial and obj.cmp.end_date < timezone.now():
                return "Your trial period has expired. Please select a subscription plan."
        return None

    def get_profile_image(self, obj):
        request = self.context.get('request')
        if obj.profile_image:
            return request.build_absolute_uri(obj.profile_image.url) if request else obj.profile_image.url
        return None

    def get_role(self, obj):
        return obj.roles.role_name if obj.roles else None
    
    def get_permissions(self, obj):
        """Get permissions object based on user role."""
        role_name = obj.roles.role_name if obj.roles else None
        
        # Define permissions for each role
        permissions_map = {
            'Administrator': {
                # Connectors & Destinations
                'can_view_connectors': True,
                'can_create_connectors': True,
                'can_edit_connectors': True,
                'can_delete_connectors': True,
                'can_view_destinations': True,
                'can_create_destinations': True,
                'can_edit_destinations': True,
                'can_delete_destinations': True,
                
                # Tables
                'can_view_tables': True,
                'can_select_tables': True,
                'can_edit_tables': True,
                
                # Connection Settings
                'can_view_connection_details': True,
                'can_edit_connection_settings': True,
                'can_toggle_connections': True,
                'can_update_sync_frequency': True,
                
                # Logs & Metrics
                'can_view_logs': True,
                'can_view_metrics': True,
                'can_view_migration_status': True,
                
                # User Management
                'can_view_users': True,
                'can_create_users': True,
                'can_edit_users': True,
                'can_delete_users': True,
                
                # Billing
                'can_access_billing': True,
                'can_view_billing_charts': True,
                'can_view_billing_plans': True,
                'can_update_billing_plan': True,
                
                # Settings
                'can_access_settings': True,
                'can_edit_company_settings': True,
            },
            'Analyst': {
                # Connectors & Destinations (View only)
                'can_view_connectors': True,
                'can_create_connectors': False,
                'can_edit_connectors': False,
                'can_delete_connectors': False,
                'can_view_destinations': True,
                'can_create_destinations': False,
                'can_edit_destinations': False,
                'can_delete_destinations': False,
                
                # Tables (Can view and edit)
                'can_view_tables': True,
                'can_select_tables': True,
                'can_edit_tables': True,
                
                # Connection Settings (View only)
                'can_view_connection_details': True,
                'can_edit_connection_settings': False,
                'can_toggle_connections': False,
                'can_update_sync_frequency': False,
                
                # Logs & Metrics (View only)
                'can_view_logs': True,
                'can_view_metrics': True,
                'can_view_migration_status': True,
                
                # User Management (No access)
                'can_view_users': False,
                'can_create_users': False,
                'can_edit_users': False,
                'can_delete_users': False,
                
                # Billing (No access)
                'can_access_billing': False,
                'can_view_billing_charts': False,
                'can_view_billing_plans': False,
                'can_update_billing_plan': False,
                
                # Settings (No access)
                'can_access_settings': False,
                'can_edit_company_settings': False,
            },
            'Billing': {
                # Connectors & Destinations (No access)
                'can_view_connectors': False,
                'can_create_connectors': False,
                'can_edit_connectors': False,
                'can_delete_connectors': False,
                'can_view_destinations': False,
                'can_create_destinations': False,
                'can_edit_destinations': False,
                'can_delete_destinations': False,
                
                # Tables (No access)
                'can_view_tables': False,
                'can_select_tables': False,
                'can_edit_tables': False,
                
                # Connection Settings (No access)
                'can_view_connection_details': False,
                'can_edit_connection_settings': False,
                'can_toggle_connections': False,
                'can_update_sync_frequency': False,
                
                # Logs & Metrics (No access)
                'can_view_logs': False,
                'can_view_metrics': False,
                'can_view_migration_status': False,
                
                # User Management (No access)
                'can_view_users': False,
                'can_create_users': False,
                'can_edit_users': False,
                'can_delete_users': False,
                
                # Billing (Full access)
                'can_access_billing': True,
                'can_view_billing_charts': True,
                'can_view_billing_plans': True,
                'can_update_billing_plan': True,
                
                # Settings (No access)
                'can_access_settings': False,
                'can_edit_company_settings': False,
            }
        }
        
        return permissions_map.get(role_name, {})

    def update(self, instance, validated_data):
        instance.first_name = validated_data.get('first_name', instance.first_name)
        instance.last_name = validated_data.get('last_name', instance.last_name)

        # company_email is mapped to `email` by source
        company_email = validated_data.get('email')
        if company_email:
            instance.email = company_email
            instance.company_email = company_email
            if instance.cmp:
                instance.cmp.company_email = company_email
                instance.cmp.save()

        profile_image = self.context['request'].FILES.get('profile_image')
        if profile_image:
            instance.profile_image = profile_image

        instance.save()
        return instance

class BillingChartDataSerializer(serializers.Serializer):
    labels = serializers.ListField(child=serializers.CharField())
    data = serializers.ListField(child=serializers.FloatField())

class BillingPeriodSerializer(serializers.Serializer):
    period = serializers.CharField()
    total_usage = serializers.FloatField()
    amount = serializers.FloatField()

class InvoiceSerializer(serializers.ModelSerializer):
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
        return "**** **** **** ****" if obj.get_decrypted_field("payment_method_last4") else None

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

class SaveDestinationConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = DestinationConfig
        fields = ['name', 'dst', 'cmp', 'is_active']



from rest_framework import serializers

class FullConnectionDetailsSerializer(serializers.ModelSerializer):

    src_config = SourceConfigSerializer()
    dst_config = DestinationConfigSerializer()
    source_name = serializers.SerializerMethodField()
    destination_name = serializers.SerializerMethodField()
    display_name = serializers.SerializerMethodField()  # ✅ Friendly name for UI
    class Meta:
        model = Connection
        fields = [
            'connection_id',
            'source_name',
            'destination_name',
            'status',
            'src_config',
            'dst_config',
            'dst_schema'
        ]

    def get_source_name(self, obj):
        """Return the raw source name for URL routing."""
        try:
            return obj.src_config.src.name
        except AttributeError:
            return None

    def get_display_name(self, obj):
        """Return the user-friendly display name for UI."""
        try:
            return obj.src_config.src.display_name
        except AttributeError:
            return None
    def get_destination_name(self, obj):
        try:
            return obj.dst_config.dst.name
        except AttributeError:
            return None



class CompanyDetailSerializer(serializers.ModelSerializer):

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
            'pod_count',
            'billed_to',
        ]

    def get_cmp_name(self, obj):
        return obj.data


class AccountProfileSerializer(serializers.ModelSerializer):
    cmp_name = serializers.SerializerMethodField()
    account_status = serializers.SerializerMethodField()
    trial_days_left = serializers.SerializerMethodField()
    created_by = serializers.SerializerMethodField()
    updated_by = serializers.SerializerMethodField()
    next_billing_date = serializers.SerializerMethodField()
    valid_from = serializers.DateTimeField(source='start_date')
    valid_to = serializers.DateTimeField(source='end_date')
    billing_address = serializers.SerializerMethodField()
    shipping_address = serializers.SerializerMethodField()
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
            'created_at',
            'valid_from',
            'valid_to',
            'created_by',
            'updated_by',
            'plan',
            'account_status',
            'is_trial',
            'trial_days_left',
            'billing_address',
            'shipping_address',
            'stripe_card_last4',
            'stripe_card_brand',
            'stripe_card_exp_month',
            'stripe_card_exp_year',
            'stripe_card_holder_name',
            'billing_start_date',
            'billing_end_date',
            'next_billing_date',
        ]

    def get_cmp_name(self, obj):
        return obj.data

    def get_account_status(self, obj):
        return getattr(obj, "account_status", None)

    def get_trial_days_left(self, obj):
        return obj.get_trial_days_remaining()

    def _serialize_user(self, user):
        if not user:
            return None
        return {
            "user_id": user.user_id,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
        }

    def get_created_by(self, obj):
        return self._serialize_user(obj.created_by)

    def get_updated_by(self, obj):
        return self._serialize_user(obj.updated_by)

    def get_next_billing_date(self, obj):
        if obj.is_trial:
            return None
        # Next billing date is the 1st day of the next month (00:00) in UTC
        from django.utils import timezone
        from datetime import datetime, timezone as dt_timezone

        base_dt = obj.billing_end_date or timezone.now()
        base_utc = base_dt.astimezone(dt_timezone.utc) if timezone.is_aware(base_dt) else base_dt
        year = base_utc.year + (1 if base_utc.month == 12 else 0)
        month = 1 if base_utc.month == 12 else base_utc.month + 1

        return datetime(year, month, 1, 0, 0, 0, tzinfo=dt_timezone.utc)

    def get_billing_address(self, obj):
        return obj.get_decrypted_field("billing_address")

    def get_shipping_address(self, obj):
        return obj.get_decrypted_field("shipping_address")

    def get_stripe_card_last4(self, obj):
        last4 = obj.get_decrypted_field("stripe_card_last4")
        if not last4:
            return None
        return f"**** {last4}"

    def get_stripe_card_brand(self, obj):
        brand = (obj.get_decrypted_field("stripe_card_brand") or "").strip().lower()
        brand_labels = {
            "visa": "Visa",
            "mastercard": "Mastercard",
            "amex": "American Express",
            "discover": "Discover",
            "diners": "Diners Club",
            "jcb": "JCB",
            "unionpay": "UnionPay",
            "eftpos_au": "Eftpos Australia",
            "interac": "Interac",
            "unknown": "Card",
        }
        if not brand:
            return None
        return brand_labels.get(brand, brand.title())

    def get_stripe_card_exp_month(self, obj):
        return obj.get_decrypted_field("stripe_card_exp_month") or None

    def get_stripe_card_exp_year(self, obj):
        return obj.get_decrypted_field("stripe_card_exp_year") or None

    def get_stripe_card_holder_name(self, obj):
        return obj.get_decrypted_field("stripe_card_holder_name") or None


class AccountProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = [
            'billing_address',
            'shipping_address',
        ]

    def update(self, instance, validated_data):
        if 'billing_address' in validated_data:
            instance.billing_address = validated_data.get('billing_address')
        if 'shipping_address' in validated_data:
            instance.shipping_address = validated_data.get('shipping_address')

        request = self.context.get('request')
        if request and getattr(request, 'user', None) and request.user.is_authenticated:
            instance.updated_by = request.user

        instance.save()
        return instance

class CountrySerializer(serializers.ModelSerializer):
    class Meta:
        model = Country
        fields = ['id', 'country_name', 'code']


class ProfileDetailsSerializer(serializers.ModelSerializer):
    company = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()
    profile_image = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'first_name', 'last_name', 'email',
            'role', 'company', 'profile_image'
        ]

    def get_role(self, obj):
        return obj.roles.role_name if obj.roles else None
    def get_company(self, obj):
        return obj.cmp.data if obj.cmp else None

    def get_profile_image(self, obj):
        request = self.context.get('request')
        if obj.profile_image:
            url = obj.profile_image.url
            if request:
                return request.build_absolute_uri(url)
            return url
    
class UserInfoSerializer(serializers.ModelSerializer):

    cmp_id = serializers.SerializerMethodField()
    cmp_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['user_id','email', 'cmp_id','cmp_name']

    def get_cmp_id(self, obj):
        if obj.cmp:
            return obj.cmp.cmp_id 
        return None
    def get_cmp_name(self,obj):
        if obj.cmp:
            return obj.cmp.data
        
class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ['role_id','role_name']

class SelectedSourceTableSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceTableSelection
        fields = [
            'tbl_id', 'table', 'sequence', 'status', 'last_synced', 'account_type', 'updated_by_id',
            'output_file_name', 'target_folder', 'file_format', 'csv_delimiter',
            'excel_sheet_name', 'excel_options', 'excel_conditional_formats',
            'csv_quote_char', 'add_utc_timestamp', 'filename_date_format', 'selected_fields', 'notification_email_group_ids',
            'email_custom_fields','email_template'
        ]

class ConnectionSchemaInfoSerializer(serializers.ModelSerializer):
    target_schema = serializers.CharField(source="dst_schema", read_only=True)
    target_database = serializers.SerializerMethodField()

    class Meta:
        model = Connection
        fields = ["connection_id", "target_schema", "target_database"]

    def get_target_database(self, obj):
        try:
            config_data = obj.dst_config.data  # decrypted JSON dict
            return config_data.get("database") if config_data else None
        except Exception:
            return None


class CommunicationSupportSerializer(serializers.ModelSerializer):
    """Serializer for CommunicationSupport model"""
    email_addresses_input = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        help_text='Enter multiple email addresses separated by commas'
    )
    email_addresses = serializers.SerializerMethodField()
    
    class Meta:
        model = CommunicationSupport
        fields = [
            'id', 'email_addresses', 'email_addresses_input',
            'is_active', 'created_at', 'updated_at', 'created_by', 'updated_by'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by', 'updated_by']
    
    def get_email_addresses(self, obj):
        """Return email addresses as a list"""
        if isinstance(obj.email_addresses, list):
            return obj.email_addresses
        return []
    
    def validate_email_addresses_input(self, value):
        """Validate email addresses from comma-separated string"""
        import re
        
        # Allow blank/empty values (matching Django form behavior)
        if not value or not value.strip():
            return value
        
        # Split by comma and validate each email
        emails = [email.strip() for email in value.split(',') if email.strip()]
        
        # If no valid emails after splitting, return empty string
        if not emails:
            return value
        
        # Email validation regex
        email_pattern = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
        
        invalid_emails = []
        for email in emails:
            if not email_pattern.match(email):
                invalid_emails.append(email)
        
        if invalid_emails:
            raise serializers.ValidationError(
                f"Invalid email addresses: {', '.join(invalid_emails)}"
            )
        
        # Check for duplicates
        unique_emails = list(set(emails))
        if len(unique_emails) != len(emails):
            raise serializers.ValidationError("Duplicate email addresses found. Please remove duplicates.")
        
        return value
    
    def create(self, validated_data):
        """Create a new CommunicationSupport instance"""
        email_string = validated_data.pop('email_addresses_input', '')
        instance = super().create(validated_data)
        
        # Set email addresses from the input field
        if email_string:
            instance.set_email_list(email_string)
            instance.save()
        
        return instance
    
    def update(self, instance, validated_data):
        """Update an existing CommunicationSupport instance"""
        email_string = validated_data.pop('email_addresses_input', None)
        instance = super().update(instance, validated_data)
        
        # Set email addresses from the input field if provided
        if email_string is not None:
            instance.set_email_list(email_string)
            instance.save()
        
        return instance

class EmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailTemplate
        fields = [
            "id", "name", "description", "subject", "header_subtitle", "header_bg_color",
            "primary_color", "greeting_name", "body_content", "callout_content", "callout_styles", "body_fields", "cta_button_text",
            "button_bg_color", "button_text_color", "button_align", "button_variant", "team_name",
            "cmp", "created_by", "updated_by", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "cmp", "created_by", "updated_by", "created_at", "updated_at"]
