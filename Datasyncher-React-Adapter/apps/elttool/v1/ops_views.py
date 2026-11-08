"""Platform admin ops monitoring REST endpoints (superuser only)."""
from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.elttool.services.ops_monitoring_service import (
    build_ops_job_detail,
    build_ops_jobs_list,
    build_ops_overview,
    build_ops_snapshot,
    build_ops_workers,
)
from apps.elttool.v1.permissions import IsInternalService, IsPlatformSuperuser
from apps.elttool.websocket.ws_utils import push_admin_ops_snapshot

logger = logging.getLogger(__name__)

OPS_AUTH = [TokenAuthentication, JWTAuthentication, SessionAuthentication]


class OpsOverviewAPIView(APIView):
    authentication_classes = OPS_AUTH
    permission_classes = [IsPlatformSuperuser]

    def get(self, request):
        return Response(build_ops_overview())


class OpsJobsListAPIView(APIView):
    authentication_classes = OPS_AUTH
    permission_classes = [IsPlatformSuperuser]

    def get(self, request):
        company_id = request.query_params.get("company_id")
        plan = request.query_params.get("plan")
        try:
            limit = int(request.query_params.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))
        cid = int(company_id) if company_id else None
        return Response(build_ops_jobs_list(company_id=cid, plan=plan, limit=limit))


class OpsJobDetailAPIView(APIView):
    authentication_classes = OPS_AUTH
    permission_classes = [IsPlatformSuperuser]

    def get(self, request, job_name):
        detail = build_ops_job_detail(job_name)
        if detail is None:
            return Response({"error": "Job not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(detail)


class OpsWorkersAPIView(APIView):
    authentication_classes = OPS_AUTH
    permission_classes = [IsPlatformSuperuser]

    def get(self, request):
        return Response(build_ops_workers())


class InternalAdminOpsPushAPIView(APIView):
    """Cluster-internal hook to broadcast a fresh ops snapshot to WS clients."""

    authentication_classes = []
    permission_classes = [IsInternalService]

    def post(self, request):
        try:
            payload = build_ops_snapshot()
            push_admin_ops_snapshot(payload)
            logger.info("Admin ops WS snapshot pushed via internal endpoint")
            return Response(
                {"message": "Admin ops WebSocket update pushed successfully"},
                status=status.HTTP_200_OK,
            )
        except Exception as exc:
            logger.exception("InternalAdminOpsPushAPIView error")
            return Response({"error": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
