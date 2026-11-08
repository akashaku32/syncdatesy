FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Build context: backend/ (see scripts/build-push-k8s.ps1)
COPY datasyncher_connections /datasyncher_connections
RUN pip install --no-cache-dir /datasyncher_connections

COPY migration-function/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --default-timeout=300 --retries=5 -r requirements.txt

RUN pip install uvicorn

ARG INSTALL_NETSUITE_ODBC=0
COPY scripts/install-netsuite-odbc.sh /build/scripts/install-netsuite-odbc.sh
COPY vendor/netsuite-odbc/ /build/vendor/netsuite-odbc/
RUN sed -i 's/\r$//' /build/scripts/install-netsuite-odbc.sh \
    && INSTALL_NETSUITE_ODBC="${INSTALL_NETSUITE_ODBC}" \
       NETSUITE_ODBC_VENDOR_DIR=/build/vendor/netsuite-odbc \
       bash /build/scripts/install-netsuite-odbc.sh

ENV LD_LIBRARY_PATH=/opt/netsuite/odbcclient/lib64:${LD_LIBRARY_PATH}
ENV NETSUITE_ODBC_TRUSTSTORE=/opt/netsuite/odbcclient/cert/ca4.cer
ENV NETSUITE_ODBC_DRIVER="{NetSuite ODBC Drivers}"
# ARG must be re-declared before ENV so build-arg survives into the image (Docker scoping).
ARG INSTALL_NETSUITE_ODBC
ENV NETSUITE_ODBC_INSTALLED=${INSTALL_NETSUITE_ODBC}

RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    apt-transport-https \
    ca-certificates && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
        | gpg --dearmor -o /etc/apt/keyrings/cloud.google.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
        > /etc/apt/sources.list.d/google-cloud-sdk.list && \
    apt-get update && apt-get install -y google-cloud-cli

COPY migration-function/ /app/
COPY sap_proxy_agent/ /app/sap_proxy_agent/

RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --create-home --shell /usr/sbin/nologin app \
    && chown -R app:app /app

USER app

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "5"]
