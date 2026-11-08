"""Tenant-scoped lookups for request handlers (fail closed on cross-company access)."""

from __future__ import annotations

from typing import Optional, Sequence, Union

from django.http import Http404
from django.shortcuts import get_object_or_404

from apps.elttool.models import Connection

SelectRelated = Union[str, Sequence[str]]


def get_connection_for_user(
    user,
    connection_id: int,
    *,
    select_related: Optional[SelectRelated] = None,
) -> Connection:
    """
    Load a Connection that belongs to the authenticated user's company.

    Raises Http404 when the connection is missing or not in the user's tenant.
    """
    cmp = getattr(user, "cmp", None)
    if cmp is None:
        raise Http404("Connection not found.")

    qs = Connection.objects.filter(cmp=cmp)
    if select_related:
        if isinstance(select_related, str):
            qs = qs.select_related(select_related)
        else:
            qs = qs.select_related(*select_related)
    return get_object_or_404(qs, connection_id=connection_id)
