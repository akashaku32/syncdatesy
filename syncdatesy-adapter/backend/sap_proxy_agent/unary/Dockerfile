# Build from syncdatesy-adapter repo root:
#   docker build -f backend/sap_proxy_agent/unary/Dockerfile -t sap-proxy-agent-unary .
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/datasyncher_connections /app/backend/datasyncher_connections
COPY datasyncher/requirements.txt /app/datasyncher/requirements.txt
COPY backend/sap_proxy_agent/unary/requirements.txt /app/backend/sap_proxy_agent/unary/requirements.txt

RUN pip install --no-cache-dir /app/backend/datasyncher_connections && \
    pip install --no-cache-dir -r /app/datasyncher/requirements.txt && \
    pip install --no-cache-dir -r /app/backend/sap_proxy_agent/unary/requirements.txt && \
    pip install --no-cache-dir --upgrade 'protobuf>=7.35.1'

COPY . /app

ENV PYTHONUNBUFFERED=1 \
    UNARY_RPC_PORT=50053

EXPOSE 50053

CMD ["python", "backend/sap_proxy_agent/unary/unary_rpc.py"]
