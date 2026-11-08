"""One-off QA audit: company plan vs connection chunk_count."""
from apps.elttool.models import Company, Connection, PlanResourceConfig
from apps.elttool.services.chunk_limit_resolver import resolve_default_connection_chunk_count

companies = Company.objects.all().order_by("cmp_id")
print("=== Company connection chunk audit ===")
print(f"Total companies: {companies.count()}\n")

for c in companies:
    plan = (c.plan or "free").strip().lower()
    try:
        prc = PlanResourceConfig.objects.get(plan_name=plan)
        pct = prc.chunk_size_pct
        max_chunk = prc.max_chunk_size
    except PlanResourceConfig.DoesNotExist:
        pct = None
        max_chunk = None

    conns = Connection.objects.filter(cmp=c).select_related(
        "src_config__src", "dst_config__dst"
    )
    if not conns.exists():
        continue

    name = getattr(c, "cmp_name", None) or getattr(c, "name", str(c.cmp_id))
    print(
        f"Company {c.cmp_id} | {name} | plan={plan} | "
        f"plan_max_chunk={max_chunk} | chunk_size_pct={pct} | connections={conns.count()}"
    )
    for conn in conns:
        src = conn.src_config.src if conn.src_config else None
        dst = conn.dst_config.dst if conn.dst_config else None
        if not src or not dst:
            print(f"  conn {conn.connection_id} | missing src/dst | chunk_count={conn.chunk_count}")
            continue
        expected = resolve_default_connection_chunk_count(c, src, dst)
        status = "OK" if conn.chunk_count == expected else "MISMATCH"
        print(
            f"  conn {conn.connection_id} | {src.name}->{dst.name} | "
            f"chunk_count={conn.chunk_count} | expected={expected} | {status}"
        )
    print()
