from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone
 
from .ticket_support_serializers import (
    SupportTicketSerializer, TicketCategorySerializer,
    TicketIssueTypeSerializer, TicketReplySerializer,
)
from ..models import (
    Connection,
    SourceModel,
    TicketSupport,
    TicketCategory,
    TicketIssueType,
    TicketReply,
    TicketAttachment,
)
from .views import format_error_message
 

def _source_type_names_from_db():
    return list(
        SourceModel.objects.exclude(name__iexact="Other")
        .values_list("name", flat=True)
        .order_by("name")
    )


def _resolve_connector_name(source_type):
    if not source_type:
        return None
    raw = str(source_type).strip()
    if not raw:
        return None
    row = (
        SourceModel.objects.exclude(name__iexact="Other")
        .filter(name__iexact=raw)
        .values("name")
        .first()
    )
    return row.get("name") if row else None

 
class SupportTicketChoicesAPIView(APIView):
    permission_classes = [IsAuthenticated]
 
    def get(self, request):
        source_type = request.query_params.get('source_type')
        category_id = request.query_params.get('category_id')
        cmp_id = getattr(getattr(request.user, 'cmp', None), 'cmp_id', None)
        if not cmp_id:
            return Response({'message': format_error_message('Company not found')}, status=status.HTTP_400_BAD_REQUEST)
 
        try:
            source_types = _source_type_names_from_db()
        except (OperationalError, ProgrammingError):
            return Response(
                {'message': format_error_message('Source types are not configured')},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
 
        categories_qs = TicketCategory.objects.all()
        categories = TicketCategorySerializer(categories_qs, many=True).data
 
        issue_types_qs = TicketIssueType.objects.all()
        if category_id:
            issue_types_qs = issue_types_qs.filter(category_id=category_id)
        issue_types = TicketIssueTypeSerializer(issue_types_qs, many=True).data
 
        connections_qs = Connection.objects.select_related('src_config', 'src_config__src').filter(cmp_id=cmp_id)
        if source_type:
            try:
                connector_name = _resolve_connector_name(source_type)
            except (OperationalError, ProgrammingError):
                connector_name = None
            if connector_name:
                connections_qs = connections_qs.filter(src_config__src__name=connector_name)
            else:
                connections_qs = connections_qs.none()
 
        connections = [
            {
                'connection_id': item['connection_id'],
                'connection_name': item['src_config__name'],
                'connector_type': item['src_config__src__name'],
            }
            for item in connections_qs.values(
                'connection_id', 'src_config__name', 'src_config__src__name',
            ).order_by('src_config__name')
        ]
 
        serializer = SupportTicketSerializer()
        fields = {name: field for name, field in serializer.fields.items() if not field.read_only}
        editable_fields = []
        for name, field in fields.items():
            field_info = {'name': name, 'required': field.required, 'type': field.__class__.__name__}
            if getattr(field, "choices", None):
                field_info['choices'] = [choice for choice in field.choices.keys() if choice != '']
            editable_fields.append(field_info)
        editable_fields.append({'name': 'attachments', 'required': False, 'type': 'FileField', 'multiple': True})
        return Response({
            'source_type': source_types,
            'categories': categories,
            'issue_types': issue_types,
            'connections': connections,
            'editable_fields': editable_fields,
        }, status=status.HTTP_200_OK)
 
 
class SupportTicketListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
 
    def get(self, request):
        cmp_id = getattr(getattr(request.user, 'cmp', None), 'cmp_id', None)
        if not cmp_id:
            return Response({'message': format_error_message('Company not found')}, status=status.HTTP_400_BAD_REQUEST)
        tickets = TicketSupport.objects.filter(cmp_id=cmp_id).order_by('-created_at')
        data = SupportTicketSerializer(tickets, many=True).data
        return Response(data, status=status.HTTP_200_OK)
 
    def post(self, request):
        cmp = getattr(request.user, 'cmp', None)
        if not cmp:
            return Response({'message': format_error_message('Company not found')}, status=status.HTTP_400_BAD_REQUEST)
 
        # Determine if this is a Billing category ticket (accepts name or ID)
        category_val = request.data.get('category')
        is_billing = False
        if category_val:
            try:
                is_billing = TicketCategory.objects.filter(pk=int(category_val), name='Billing').exists()
            except (ValueError, TypeError):
                is_billing = str(category_val).strip().lower() == 'billing'
 
        # Resolve connection — not required for Billing tickets
        connection_obj = None
        if not is_billing:
            connection_name = request.data.get('connection_name')
            source_type = request.data.get('source_type')
            if not connection_name:
                return Response(
                    {'message': format_error_message('connection_name is required')},
                    status=status.HTTP_400_BAD_REQUEST
                )
            try:
                connector_name = _resolve_connector_name(source_type)
            except (OperationalError, ProgrammingError):
                return Response(
                    {'message': format_error_message('Source types are not configured')},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            if source_type and not connector_name:
                return Response(
                    {'message': format_error_message('Invalid source_type')},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            conn_qs = Connection.objects.select_related('src_config', 'src_config__src').filter(
                cmp=cmp, src_config__name=connection_name,
            )
            if connector_name:
                conn_qs = conn_qs.filter(src_config__src__name=connector_name)
            connection_obj = conn_qs.first()
            if not connection_obj:
                return Response(
                    {'message': format_error_message('Connection not found for the selected source type')},
                    status=status.HTTP_400_BAD_REQUEST
                )
 
        serializer = SupportTicketSerializer(data=request.data)
        if not serializer.is_valid():
            errors = serializer.errors
            first_error = None
            if isinstance(errors, dict) and errors:
                try:
                    first_field = next(iter(errors.keys()))
                    first_val = errors.get(first_field)
                    if isinstance(first_val, (list, tuple)) and first_val:
                        first_error = first_val[0]
                    else:
                        first_error = first_val
                except Exception:
                    first_error = None
            return Response(
                {
                    'message': format_error_message(str(first_error or 'Validation error')),
                    'errors': errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
 
        ticket = serializer.save(
            cmp=cmp,
            connection=connection_obj,
            created_by=request.user,
            updated_by=request.user,
            has_user_reply=True,
        )
        for f in request.FILES.getlist('attachments'):
            TicketAttachment.objects.create(ticket=ticket, file=f)
        return Response(SupportTicketSerializer(ticket).data, status=status.HTTP_201_CREATED)
 
 
class SupportTicketDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]
 
    def _get_ticket(self, ticket_id, cmp_id):
        try:
            return TicketSupport.objects.get(ticket_id=ticket_id, cmp_id=cmp_id)
        except TicketSupport.DoesNotExist:
            return None
 
    def get(self, request, ticket_id):
        cmp_id = getattr(getattr(request.user, 'cmp', None), 'cmp_id', None)
        if not cmp_id:
            return Response({'message': format_error_message('Company not found')}, status=status.HTTP_400_BAD_REQUEST)
        ticket = self._get_ticket(ticket_id, cmp_id)
        if not ticket:
            return Response({'message': format_error_message('Ticket not found')}, status=status.HTTP_404_NOT_FOUND)
        serializer = SupportTicketSerializer(ticket)
        return Response(serializer.data, status=status.HTTP_200_OK)
 
    def patch(self, request, ticket_id):
        """Close a ticket — sets status to Closed."""
        cmp_id = getattr(getattr(request.user, 'cmp', None), 'cmp_id', None)
        if not cmp_id:
            return Response({'message': format_error_message('Company not found')}, status=status.HTTP_400_BAD_REQUEST)
        ticket = self._get_ticket(ticket_id, cmp_id)
        if not ticket:
            return Response({'message': format_error_message('Ticket not found')}, status=status.HTTP_404_NOT_FOUND)
        if ticket.status == 'Closed':
            return Response({'message': format_error_message('Ticket is already closed')}, status=status.HTTP_400_BAD_REQUEST)
        ticket.status = 'Closed'
        ticket.closed_by = request.user
        ticket.closed_at = timezone.now()
        ticket.updated_by = request.user
        ticket.save(update_fields=['status', 'closed_by', 'closed_at', 'updated_by', 'updated_at'])
        return Response(SupportTicketSerializer(ticket).data, status=status.HTTP_200_OK)
 
 
class TicketReplyAPIView(APIView):
    """GET and POST replies for a ticket."""
    permission_classes = [IsAuthenticated]
 
    def _get_ticket(self, ticket_id, cmp_id):
        try:
            return TicketSupport.objects.get(ticket_id=ticket_id, cmp_id=cmp_id)
        except TicketSupport.DoesNotExist:
            return None
 
    def get(self, request, ticket_id):
        cmp_id = getattr(getattr(request.user, 'cmp', None), 'cmp_id', None)
        if not cmp_id:
            return Response({'message': format_error_message('Company not found')}, status=status.HTTP_400_BAD_REQUEST)
        ticket = self._get_ticket(ticket_id, cmp_id)
        if not ticket:
            return Response({'message': format_error_message('Ticket not found')}, status=status.HTTP_404_NOT_FOUND)

        if ticket.has_new_response:
            ticket.has_new_response = False
            ticket.save(update_fields=['has_new_response'])

        replies = TicketReply.objects.filter(ticket=ticket)
        serializer = TicketReplySerializer(replies, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
 
    def post(self, request, ticket_id):
        cmp = getattr(request.user, 'cmp', None)
        if not cmp:
            return Response({'message': format_error_message('Company not found')}, status=status.HTTP_400_BAD_REQUEST)
        cmp_id = cmp.cmp_id
        ticket = self._get_ticket(ticket_id, cmp_id)
        if not ticket:
            return Response({'message': format_error_message('Ticket not found')}, status=status.HTTP_404_NOT_FOUND)
        if ticket.status == 'Closed':
            return Response({'message': format_error_message('Cannot reply to a closed ticket')}, status=status.HTTP_400_BAD_REQUEST)
 
        message = request.data.get('message', '').strip()
        if not message:
            return Response({'message': format_error_message('message is required')}, status=status.HTTP_400_BAD_REQUEST)
 
        reply = TicketReply.objects.create(
            ticket=ticket,
            cmp=cmp,
            message=message,
            created_by=request.user,
            is_support_team=False,
        )
        for f in request.FILES.getlist('attachments'):
            TicketAttachment.objects.create(reply=reply, file=f)
 
        # notify admin that user has replied
        ticket.has_user_reply = True
        ticket.updated_by = request.user
        ticket.save(update_fields=['has_user_reply', 'updated_by', 'updated_at'])
 
        return Response(TicketReplySerializer(reply).data, status=status.HTTP_201_CREATED)
 
