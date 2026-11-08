FROM python:3.11-slim

WORKDIR /app

COPY sap_proxy_agent/gateway/requirements.txt .
RUN pip install --no-cache-dir --default-timeout=300 --retries=5 -r requirements.txt

COPY sap_proxy_agent/gateway/ .

RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --create-home --shell /usr/sbin/nologin app \
    && chown -R app:app /app

USER app

EXPOSE 8080

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
