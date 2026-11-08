"""Hub process entry: HTTP :8081 health + gRPC :50051/:50052 load server."""

from __future__ import annotations

import asyncio
import logging
import os
import threading

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def _run_health_server() -> None:
    import uvicorn

    from sap_proxy_agent.hub.health import app

    port = int(os.getenv("HTTP_HEALTH_PORT", "8081"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


def main() -> None:
    health_thread = threading.Thread(
        target=_run_health_server,
        name="hub-health",
        daemon=True,
    )
    health_thread.start()

    from sap_proxy_agent.hub.hub_grpc_server import serve

    asyncio.run(serve())


if __name__ == "__main__":
    main()
