"""Proxy routes for entity transform validate/dry-run."""

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

FAST_API_DIR = Path(__file__).resolve().parents[1]
if str(FAST_API_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_API_DIR))


def test_entity_transform_proxy_routes_registered():
    app_py = Path(__file__).resolve().parents[1] / "app.py"
    text = app_py.read_text(encoding="utf-8")
    assert "@app.post('/entity-transform/validate')" in text
    assert "@app.post('/entity-transform/dry-run')" in text
    assert "_forward_json_to_migration_function(request, 'entity-transform/validate')" in text
    assert (
        "_forward_json_to_migration_function(request, 'entity-transform/dry-run', timeout=300)"
        in text
    )
