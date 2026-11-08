"""Small helpers for normalizing connector config payloads."""

from typing import Any, Dict, Mapping, MutableMapping, Optional, Tuple


def drop_none_config_values(data: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Remove keys whose values are None (common after OAuth token merge)."""
    if not data:
        return {}
    return {key: value for key, value in data.items() if value is not None}


def sanitize_listing_payload(
    payload: MutableMapping[str, Any],
    config_keys: Tuple[str, ...],
) -> MutableMapping[str, Any]:
    """Strip nulls from nested connector config dicts inside listing payloads."""
    for key in config_keys:
        if key in payload and isinstance(payload[key], dict):
            payload[key] = drop_none_config_values(payload[key])
    return payload
