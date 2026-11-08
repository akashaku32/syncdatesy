from rest_framework import serializers
from django.db.utils import OperationalError, ProgrammingError

from apps.elttool.models import (
    SourceModel,
    TicketSupport,
    TicketCategory,
    TicketIssueType,
    TicketReply,
    TicketAttachment,
)
 
 
class TicketCategorySerializer(serializers.ModelSerializer):
    """Serializer for TicketCategory model"""
    class Meta:
        model = TicketCategory
        fields = ['category_id', 'name', 'description']
 
 
class TicketIssueTypeSerializer(serializers.ModelSerializer):
    """Serializer for TicketIssueType model"""
    category_name = serializers.ReadOnlyField(source='category.name')
 
    class Meta:
        model = TicketIssueType
        fields = ['issue_type_id', 'category', 'category_name', 'name', 'description']
 
 
class TicketAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = TicketAttachment
        fields = ['attachment_id', 'file', 'uploaded_at']
 
 
class SupportTicketSerializer(serializers.ModelSerializer):
    """Serializer for TicketSupport model"""
 
    source_type = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    category_detail = TicketCategorySerializer(source='category', read_only=True)
    issue_type_detail = TicketIssueTypeSerializer(source='issue_type', read_only=True)
    attachments = TicketAttachmentSerializer(many=True, read_only=True)
    closed_by_name = serializers.SerializerMethodField()
 
    def to_internal_value(self, data):
        data = data.copy()
 
        # Validate category is provided
        category_val = data.get('category')
        if not category_val:
            raise serializers.ValidationError({'category': 'This field is required.'})
 
        # Resolve category by name if a string is provided
        if not str(category_val).isdigit():
            cat = TicketCategory.objects.filter(name__iexact=str(category_val).strip()).first()
            if not cat:
                raise serializers.ValidationError(
                    {'category': f'"{category_val}" is not a valid category.'}
                )
            data['category'] = cat.category_id
 
        # Validate issue_type is provided
        issue_val = data.get('issue_type')
        if not issue_val:
            raise serializers.ValidationError({'issue_type': 'This field is required.'})
 
        # Resolve issue_type by name if a string is provided
        if not str(issue_val).isdigit():
            category_id = data.get('category')
            qs = TicketIssueType.objects.filter(name__iexact=str(issue_val).strip())
            if category_id:
                qs = qs.filter(category_id=category_id)
            issue = qs.first()
            if not issue:
                raise serializers.ValidationError(
                    {'issue_type': f'"{issue_val}" is not a valid issue type.'}
                )
            data['issue_type'] = issue.issue_type_id
 
        return super().to_internal_value(data)
 
    def get_closed_by_name(self, obj):
        if obj.closed_by:
            return f"{obj.closed_by.first_name} {obj.closed_by.last_name}".strip()
        return None
 
    def validate_source_type(self, value):
        if not value:
            return value
        raw = str(value).strip()
        if not raw:
            return value

        try:
            row = (
                SourceModel.objects.exclude(name__iexact="Other")
                .filter(name__iexact=raw)
                .values("name")
                .first()
            )
        except (OperationalError, ProgrammingError):
            raise serializers.ValidationError('Source types are not configured.')

        if not row or not row.get("name"):
            raise serializers.ValidationError(f'"{value}" is not a valid choice.')

        return row["name"]
 
    class Meta:
        model = TicketSupport
        fields = [
            'cmp',
            'connection',
            'ticket_id',
            'category',
            'category_detail',
            'issue_type',
            'issue_type_detail',
            'source_type',
            'connection_name',
            'subject',
            'description',
            'attachments',
            'status',
            'created_at',
            'updated_at',
            'closed_at',
            'created_by',
            'updated_by',
            'closed_by',
            'closed_by_name',
            'has_new_response',
        ]
        read_only_fields = ['ticket_id', 'cmp', 'created_at', 'updated_at', 'connection', 'created_by', 'updated_by', 'closed_at', 'closed_by', 'has_new_response']
 
 
class TicketReplySerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    attachments = TicketAttachmentSerializer(many=True, read_only=True)
 
    class Meta:
        model = TicketReply
        fields = ['reply_id', 'ticket', 'cmp', 'message', 'attachments', 'is_support_team', 'created_by', 'created_by_name', 'created_at']
        read_only_fields = ['reply_id', 'ticket', 'cmp', 'is_support_team', 'created_by', 'created_at']
 
    def get_created_by_name(self, obj):
        if obj.created_by:
            return f"{obj.created_by.first_name} {obj.created_by.last_name}".strip()
        return "Support Team"
 
