"""
Bootstrap token validation for SAP proxy agent Enroll (TokenValidationService / unary_rpc :50053).
"""

from __future__ import annotations

import hmac
import logging

from apps.elttool.models import ProxyAgent
from apps.elttool.services.proxy_agent_service import generate_bootstrap_token
from apps.utils.encryption import decrypt_sensitive

logger = logging.getLogger(__name__)


def validate_and_rotate_bootstrap_token(
    agent_uid: str,
    presented_token: str,
) -> tuple[bool, str | None]:
    """
    Verify the presented bootstrap token against the encrypted DB value for agent_uid.
    On success, rotate token (store new encrypted value) and return the new plaintext token.
    """
    agent_uid = (agent_uid or "").strip()
    presented_token = (presented_token or "").strip()
    if not agent_uid or not presented_token:
        return False, None

    agent = (
        ProxyAgent.objects.filter(agent_uid=agent_uid)
        .select_related("cmp")
        .first()
    )
    if agent is None or not agent.bootstrap_token:
        logger.warning("bootstrap: unknown agent or missing token agent_uid=%s", agent_uid)
        return False, None

    try:
        stored_raw = decrypt_sensitive(agent.bootstrap_token, agent.cmp_id)
    except Exception:
        logger.exception("bootstrap: decrypt failed agent_uid=%s", agent_uid)
        return False, None

    if not hmac.compare_digest(str(stored_raw), presented_token):
        logger.warning("bootstrap: token mismatch agent_uid=%s", agent_uid)
        return False, None

    raw_new, encrypted_new = generate_bootstrap_token(agent.cmp_id)
    agent.bootstrap_token = encrypted_new
    agent.save(update_fields=["bootstrap_token", "updated_at"])
    logger.info("bootstrap: rotated token for agent_uid=%s", agent_uid)
    return True, raw_new
