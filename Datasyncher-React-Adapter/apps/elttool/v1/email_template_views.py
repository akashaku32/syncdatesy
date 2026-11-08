from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404

from ..models import EmailTemplate
from .serializers import EmailTemplateSerializer


class EmailTemplateListDetailAPIView(APIView):
    """Company-scoped CRUD API for custom notification email templates."""

    permission_classes = [IsAuthenticated]

    def get_queryset(self, request):
        return EmailTemplate.objects.filter(cmp=request.user.cmp).select_related(
            "created_by", "updated_by"
        )

    def get(self, request, template_id=None):
        if template_id is not None:
            template = get_object_or_404(self.get_queryset(request), id=template_id)
            return Response(EmailTemplateSerializer(template).data)
        return Response(EmailTemplateSerializer(self.get_queryset(request), many=True).data)

    def post(self, request, template_id=None):
        serializer = EmailTemplateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        template = serializer.save(
            cmp=request.user.cmp,
            created_by=request.user,
            updated_by=request.user,
        )
        return Response(EmailTemplateSerializer(template).data, status=status.HTTP_201_CREATED)

    def _update(self, request, template_id):
        template = get_object_or_404(self.get_queryset(request), id=template_id)
        serializer = EmailTemplateSerializer(template, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        template = serializer.save(updated_by=request.user)
        return Response(EmailTemplateSerializer(template).data)

    def put(self, request, template_id):
        return self._update(request, template_id)

    def patch(self, request, template_id):
        return self._update(request, template_id)

    def delete(self, request, template_id):
        template = get_object_or_404(self.get_queryset(request), id=template_id)
        template.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
