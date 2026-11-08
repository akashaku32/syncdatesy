"""Recompute chunk_count using directional ceiling (source vs dest by is_rtl)."""
import math
from decimal import Decimal

from apps.elttool.models import Company, Connection, PlanResourceConfig


def connector_ceiling(src, dst):
    if bool(getattr(src, "is_rtl", False)):
        return getattr(dst, "max_count", None)
    return getattr(src, "max_count", None)


def resolve_chunk(company, src, dst):
    plan_name = (getattr(company, "plan", None) or "free").strip().lower()
    try:
        plan_row = PlanResourceConfig.objects.get(plan_name=plan_name)
    except PlanResourceConfig.DoesNotExist:
        plan_row = PlanResourceConfig.objects.filter(plan_name="free").first()
        if not plan_row:
            return 100

    ceiling = connector_ceiling(src, dst)
    pct = getattr(plan_row, "chunk_size_pct", None)
    if ceiling is not None and pct is not None:
        natural = math.floor(float(ceiling) * float(pct) / 100)
    else:
        natural = int(getattr(plan_row, "max_chunk_size", 0) or 0)

    override = getattr(company, "resource_override", None)
    if override and getattr(override, "max_chunk_size", None):
        natural = min(natural, int(override.max_chunk_size))

    min_floor = int(getattr(plan_row, "min_chunk_size", None) or 100)
    effective = max(natural, min_floor)
    if ceiling is not None:
        effective = min(effective, int(ceiling))
    return effective


updated = 0
for company in Company.objects.all():
    for conn in Connection.objects.filter(cmp=company).select_related(
        "src_config__src", "dst_config__dst"
    ):
        src = conn.src_config.src if conn.src_config else None
        dst = conn.dst_config.dst if conn.dst_config else None
        if not src or not dst:
            continue
        new_chunk = resolve_chunk(company, src, dst)
        if conn.chunk_count != new_chunk:
            rtl = "rev_etl" if getattr(src, "is_rtl", False) else "normal"
            print(
                f"cmp={company.cmp_id} conn={conn.connection_id} {rtl} "
                f"{src.name}->{dst.name}: {conn.chunk_count} -> {new_chunk} "
                f"(ceiling={connector_ceiling(src, dst)})"
            )
            Connection.objects.filter(pk=conn.pk).update(chunk_count=new_chunk)
            updated += 1

print(f"Done. Updated {updated} connection(s).")
