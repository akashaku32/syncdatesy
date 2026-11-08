# Use the official Python image
FROM python:3.11-slim

# Invalidate COPY layer when rollout changes (ARG must precede COPY that uses it).
ARG CACHEBUST=20260908-retl-omit-id-name-datetime-fields

# Set the working directory
WORKDIR /app

# Single requirements file includes migration-function runtime deps (see requirements.txt header).
# Build context is expected to be `backend/` (see docker build command).
COPY datasyncher_connections/ /datasyncher_connections/
RUN pip install --no-cache-dir /datasyncher_connections

COPY fast-api/requirements.txt .

RUN pip install --no-cache-dir --default-timeout=300 --retries=5 -r requirements.txt

RUN pip install flower

ARG INSTALL_NETSUITE_ODBC=0
COPY scripts/install-netsuite-odbc.sh /build/scripts/install-netsuite-odbc.sh
COPY vendor/netsuite-odbc/ /build/vendor/netsuite-odbc/
RUN sed -i 's/\r$//' /build/scripts/install-netsuite-odbc.sh \
    && chmod +x /build/scripts/install-netsuite-odbc.sh \
    && INSTALL_NETSUITE_ODBC="${INSTALL_NETSUITE_ODBC}" \
       NETSUITE_ODBC_VENDOR_DIR=/build/vendor/netsuite-odbc \
       bash /build/scripts/install-netsuite-odbc.sh

ENV LD_LIBRARY_PATH=/opt/netsuite/odbcclient/lib64:${LD_LIBRARY_PATH}
ENV NETSUITE_ODBC_TRUSTSTORE=/opt/netsuite/odbcclient/cert/ca4.cer
ENV NETSUITE_ODBC_DRIVER="{NetSuite ODBC Drivers}"
ARG INSTALL_NETSUITE_ODBC
ENV NETSUITE_ODBC_INSTALLED=${INSTALL_NETSUITE_ODBC}

# Copy the FastAPI app and celery modules
COPY fast-api/ .

# Celery direct connector dispatch imports services/models from migration-function.
COPY migration-function/ /migration-function/

RUN echo "cachebust=${CACHEBUST}"

RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --create-home --shell /usr/sbin/nologin app \
    && chown -R app:app /app /migration-function /datasyncher_connections

USER app

# Expose the port FastAPI will run on
EXPOSE 9090

# Command to run the application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9090"]
