"""NetSuite Connect TBA token password format."""

import sys
from pathlib import Path

MIGRATION_ROOT = Path(__file__).resolve().parents[1]
if str(MIGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(MIGRATION_ROOT))

from services.netsuite.tba_token_password import build_connect_tba_token_password


def test_token_password_contains_hmac_suffix():
    pwd = build_connect_tba_token_password(
        "1234567",
        "consumer_key",
        "token_id",
        "consumer_secret",
        "token_secret",
    )
    assert "&HMAC-SHA256" in pwd
    assert pwd.startswith("1234567&consumer_key&token_id&")
