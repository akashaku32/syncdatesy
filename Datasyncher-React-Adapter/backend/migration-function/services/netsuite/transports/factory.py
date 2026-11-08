from __future__ import annotations

from typing import Any, Dict

from services.netsuite.config import METHOD_SUITEANALYTICS, METHOD_SUITEQL
from services.netsuite.transports.suiteanalytics import SuiteAnalyticsTransport
from services.netsuite.transports.suiteql_rest import SuiteQLRestTransport


def build_transport(config: Dict[str, Any]):
    method = config["connection_method"]
    if method == METHOD_SUITEQL:
        return SuiteQLRestTransport(config)
    if method == METHOD_SUITEANALYTICS:
        return SuiteAnalyticsTransport(config)
    raise ValueError(f"Unsupported connection_method: {method}")
