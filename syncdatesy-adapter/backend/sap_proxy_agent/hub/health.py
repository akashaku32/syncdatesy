"""HTTP health for Kubernetes probes (port 8081)."""

from __future__ import annotations

import logging

from fastapi import FastAPI

logger = logging.getLogger("sap_proxy_agent_hub")

app = FastAPI(title="SAP Proxy Agent Hub Health", version="1.0.0")


@app.get("/healthz")
def healthz():
    return {"status": "ok", "role": "sap-proxy-agent-hub"}
