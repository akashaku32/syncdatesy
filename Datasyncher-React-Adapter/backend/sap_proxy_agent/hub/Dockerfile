FROM python:3.11-slim

WORKDIR /app

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

COPY sap_proxy_agent/hub/requirements.txt /tmp/hub-requirements.txt
RUN pip install --no-cache-dir -r /tmp/hub-requirements.txt

COPY sap_proxy_agent/generated /app/sap_proxy_agent/generated
COPY sap_proxy_agent/hub /app/sap_proxy_agent/hub

ENV HTTP_HEALTH_PORT=8081
ENV GRPC_HUB_PORT=50051
ENV GRPC_BOOTSTRAP_PORT=50052
ENV HUB_MTLS_DIR=/etc/sap-proxy-agent-hub/mtls

EXPOSE 50051 50052 8081

CMD ["python", "-m", "sap_proxy_agent.hub"]
