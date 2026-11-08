import logging
import requests
import json
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404

from apps.elttool.models import Connection, Log

logger = logging.getLogger(__name__)
FASTAPI_DOMAIN = getattr(settings, 'FASTAPI_DOMAIN', 'fastapi-svc:9090')
FASTAPI_SCHEME = getattr(settings, 'FASTAPI_SCHEME', 'http')


def _fastapi_url(path: str) -> str:
    """Build an internal FastAPI URL from the configured service settings."""
    return f"{FASTAPI_SCHEME}://{FASTAPI_DOMAIN}/{str(path).lstrip('/')}"


def _inject_proxy_agent(credentials: dict, cmp_id) -> dict:
    """
    If connection_method is 'proxy', look up the company's single ProxyAgent
    record and inject its agent_uid into credentials as 'proxy_agent'.

    This replaces the old approach of storing proxy_agent inside SourceConfig.
    The agent is resolved at call time from the ProxyAgent table.
    """
    if not isinstance(credentials, dict):
        return credentials
    if str(credentials.get("connection_method") or "").strip().lower() != "proxy":
        return credentials
    try:
        from apps.elttool.models import ProxyAgent
        agent = ProxyAgent.objects.filter(cmp_id=cmp_id).values_list("agent_uid", flat=True).first()
        if agent:
            credentials = dict(credentials)  # copy — don't mutate caller's dict
            credentials["proxy_agent"] = agent
            logger.info(
                "_inject_proxy_agent: injected proxy_agent='%s' for cmp_id=%s",
                agent, cmp_id,
            )
        else:
            logger.warning(
                "_inject_proxy_agent: no ProxyAgent found for cmp_id=%s — proxy_agent not injected",
                cmp_id,
            )
    except Exception as exc:
        logger.warning(
            "_inject_proxy_agent: failed to resolve proxy agent for cmp_id=%s: %s",
            cmp_id, exc,
        )
    return credentials


class SAPODataServiceEntitiesAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        connection_id = request.query_params.get("connection_id")
        service_name = request.query_params.get("service_name")

        if not connection_id or not service_name:
            try:
                body = json.loads(request.body.decode('utf-8'))
                connection_id = connection_id or body.get("connection_id")
                service_name = service_name or body.get("service_name")
            except Exception:
                pass

        if not connection_id or not service_name:
            return Response({"message": "Missing connection_id or service_name parameter."}, status=status.HTTP_400_BAD_REQUEST)

        connection = get_object_or_404(Connection.objects.select_related('src_config'), pk=connection_id)
        credentials = connection.src_config.data
        if not isinstance(credentials, dict):
            credentials = json.loads(credentials or "{}")

        # Inject proxy_agent from company ProxyAgent record (not stored in SourceConfig)
        credentials = _inject_proxy_agent(credentials, connection.cmp_id)

        try:
            api_url = _fastapi_url('/sap-odata/service-entities')
            from apps.elttool.v1.utils import internal_service_headers
            response = requests.post(
                api_url,
                json={"credentials": credentials, "service_name": service_name},
                headers=internal_service_headers(),
                verify=False,
            )
            if response.status_code != 200:
                try:
                    error_detail = response.json().get("detail")
                    if not isinstance(error_detail, str):
                        error_detail = None
                except Exception:
                    error_detail = None
                error_message = error_detail or f"Failed to fetch entities for service {service_name}."
                return Response(
                    {"message": error_message, "detail": response.text},
                    status=response.status_code
                )
            
            entities = response.json()
            output = []
            for ent in entities:
                output.append({
                    "entity_name": ent.get("entity_name"),
                    "is_delta": ent.get("is_delta", False),
                    "delta_name": ent.get("delta_name")
                })
            return Response(output, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({"message": f"Failed to fetch service entities: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class SAPODataSaveEntitiesAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        connection_id = request.data.get("connection_id")
        service_name = request.data.get("service_name")
        selected_entities = request.data.get("entities") or []

        if not connection_id or not service_name:
            return Response(
                {"message": "Missing connection_id or service_name parameter."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        connection = get_object_or_404(
            Connection.objects.select_related("src_config"), pk=connection_id
        )
        credentials = connection.src_config.data
        if not isinstance(credentials, dict):
            credentials = json.loads(credentials or "{}")
        credentials = _inject_proxy_agent(credentials, connection.cmp_id)

        try:
            api_url = f"http://{FASTAPI_DOMAIN}/sap-odata/service-entities"
            from apps.elttool.v1.utils import internal_service_headers
            response = requests.post(
                api_url,
                json={
                    "credentials": credentials,
                    "service_name": service_name,
                    "connection_id": connection_id,
                    "entities": selected_entities,
                },
                headers=internal_service_headers(),
                verify=False,
            )
            if response.status_code != 200:
                return Response(
                    {
                        "message": f"Failed to save entities for service {service_name}.",
                        "detail": response.text,
                    },
                    status=response.status_code,
                )

            save_result = response.json()
            saved_count = save_result.get("saved_count", 0)

            if saved_count > 0:
                Log.objects.create(
                    message=f"Service {service_name} added successfully",
                    cmp=connection.cmp,
                    user=request.user,
                    connection=connection,
                    status="S",
                    session=1,
                )

            return Response(
                {
                    "success": True,
                    "message": f"Successfully registered and saved {saved_count} entities.",
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            return Response(
                {"message": f"Failed to save service entities: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ---------------------------------------------------------------------------
# Proxy Agent Config Generation
# ---------------------------------------------------------------------------

# Rate limit: max 5 proxy agent config generations per company per hour.
_PROXY_AGENT_RATE_LIMIT = 5
_PROXY_AGENT_RATE_WINDOW_SECONDS = 3600  # 1 hour


class ProxyAgentGenerateAPIView(APIView):
    """
    POST /api/v1/proxy-agent/generate/

    Generate and download a unique config.json for a new Proxy Agent.

    Request body:
        { "agent_name": "<human-readable name>" }

    Response:
        Content-Type: application/json
        Content-Disposition: attachment; filename="config.json"
        Body: config.json with agent_id, server_host, server_port,
              trusted_ca_cert, client_cert, client_private_key, bootstrap_token.

    Security:
        - Requires authenticated session.
        - Company is derived from request.user.cmp_id (never from request body).
        - Rate-limited to 5 generations per company per hour.
        - The client private key and raw bootstrap token appear only in this response;
          they are never stored in the database.
        - config_downloaded_at is stamped on the ProxyAgent record at creation.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        from apps.elttool.services.proxy_agent_service import create_proxy_agent
        from django.core.exceptions import ImproperlyConfigured

        # --- Company scoping (always from the authenticated user) ---
        cmp_id = getattr(request.user, "cmp_id", None)
        if not cmp_id:
            return Response(
                {"message": "Unable to determine company for the current user."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # --- Rate limit check ---
        rate_key = f"proxy_agent_gen_rate:{cmp_id}"
        current_count = cache.get(rate_key, 0)
        if current_count >= _PROXY_AGENT_RATE_LIMIT:
            logger.warning(
                "ProxyAgentGenerateAPIView: rate limit hit for cmp_id=%s (count=%s)",
                cmp_id, current_count,
            )
            return Response(
                {
                    "message": (
                        f"Rate limit exceeded. You can generate at most "
                        f"{_PROXY_AGENT_RATE_LIMIT} proxy agent configs per hour. "
                        f"Please try again later."
                    )
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        # agent_name is always "Datasyncher Agent" — no validation needed
        agent_name = "Datasyncher Agent"

        # --- Create agent and generate config ---
        try:
            agent, config = create_proxy_agent(
                cmp_id=cmp_id,
                agent_name=agent_name,
                user=request.user,
            )
        except ImproperlyConfigured as exc:
            logger.error(
                "ProxyAgentGenerateAPIView: CA configuration error for cmp_id=%s: %s",
                cmp_id, exc,
            )
            return Response(
                {"message": f"Proxy agent CA configuration error: {str(exc)}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except RuntimeError as exc:
            logger.error(
                "ProxyAgentGenerateAPIView: agent_uid generation failed for cmp_id=%s: %s",
                cmp_id, exc,
            )
            return Response(
                {"message": "Failed to generate a unique agent ID. Please try again."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except Exception as exc:
            logger.exception(
                "ProxyAgentGenerateAPIView: unexpected error for cmp_id=%s: %s",
                cmp_id, exc,
            )
            return Response(
                {"message": "An unexpected error occurred while generating the agent config."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # --- Increment rate limit counter (only on success) ---
        if current_count == 0:
            cache.set(rate_key, 1, timeout=_PROXY_AGENT_RATE_WINDOW_SECONDS)
        else:
            cache.incr(rate_key)

        # --- Return config.json as a file download ---
        # IMPORTANT: Do NOT log the response body — it contains a private key.
        config_bytes = json.dumps(config, indent=2).encode("utf-8")
        filename = f"config.json" #-{agent.agent_uid}

        response = HttpResponse(config_bytes, content_type="application/json")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["X-Agent-UID"] = agent.agent_uid  # safe to expose — public identifier

        logger.info(
            "ProxyAgentGenerateAPIView: config.json issued for agent_uid=%s cmp_id=%s",
            agent.agent_uid, cmp_id,
        )
        return response


class ProxyAgentStatusAPIView(APIView):
    """
    GET /api/v1/proxy-agent/status/

    Returns the current company's proxy agent status:
    - has_agent: boolean
    - agent_uuid: string (or null)
    - agent_name: string (or null)
    - created_at: string ISO timestamp (or null)
    - connected_connectors: list of {"id": src_config_id, "name": name}
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.elttool.models import ProxyAgent, SourceConfig

        cmp_id = getattr(request.user, "cmp_id", None)
        if not cmp_id:
            return Response(
                {"message": "Unable to determine company for the current user."},
                status=status.HTTP_403_FORBIDDEN,
            )

        agent = ProxyAgent.objects.filter(cmp_id=cmp_id).first()
        if not agent:
            return Response({
                "has_agent": False,
                "agent_uuid": None,
                "agent_name": None,
                "created_at": None,
                "connected_connectors": [],
            })

        connected_connectors = []
        active_configs = (
            SourceConfig.objects
            .filter(cmp_id=cmp_id, is_active=True)
            .select_related("cmp")
        )
        for sc in active_configs:
            try:
                data = sc.data  # decrypts config_data
                if isinstance(data, dict) and str(data.get("connection_method") or "").strip().lower() == "proxy":
                    connected_connectors.append({
                        "id": sc.src_config_id,
                        "name": sc.name,
                    })
            except Exception as exc:
                logger.warning(
                    "ProxyAgentStatusAPIView: failed to parse SourceConfig id=%s: %s",
                    sc.src_config_id, exc,
                )

        return Response({
            "has_agent": True,
            "agent_uuid": agent.agent_uid,
            "agent_name": agent.agent_name,
            "created_at": agent.created_at.isoformat() if agent.created_at else None,
            "connected_connectors": connected_connectors,
        })
