"""Recompute connection.chunk_count for all companies from current plan + connectors."""
from apps.elttool.models import Company, Connection
from apps.elttool.services.chunk_limit_resolver import resolve_default_connection_chunk_count

updated = 0
for company in Company.objects.all():
    for conn in Connection.objects.filter(cmp=company).select_related(
        "src_config__src", "dst_config__dst"
    ):
        src = conn.src_config.src if conn.src_config else None
        dst = conn.dst_config.dst if conn.dst_config else None
        if not src or not dst:
            continue
        new_chunk = resolve_default_connection_chunk_count(company, src, dst)
        if conn.chunk_count != new_chunk:
            print(
                f"cmp={company.cmp_id} conn={conn.connection_id} "
                f"{src.name}->{dst.name}: {conn.chunk_count} -> {new_chunk}"
            )
            Connection.objects.filter(pk=conn.pk).update(chunk_count=new_chunk)
            updated += 1

print(f"Done. Updated {updated} connection(s).")
