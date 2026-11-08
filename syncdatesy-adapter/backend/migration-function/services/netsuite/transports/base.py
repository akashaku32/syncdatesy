from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol


class QueryTransport(Protocol):
    """Run SuiteQL/SQL against NetSuite."""

    def run_query(
        self,
        sql: str,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        ...

    def close(self) -> None:
        ...
