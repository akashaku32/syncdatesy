"""Audit plan/company/connection chunk alignment (run via manage.py shell)."""
from apps.elttool.models import (
    PlanResourceConfig,
    Company,
    CompanyResourceOverride,
    Connection,
    Plan,
    SourceModel,
    DestinationModel,
)
from apps.elttool.services.chunk_limit_resolver import (
    resolve_default_connection_chunk_count,
    resolve_chunk_size_django,
    normalize_plan_name,
)

EXPECTED_PLANS = {
    "free": {"max_chunk_size": 50000, "chunk_size_pct": 25.0, "max_concurrency": 8, "concurrency": 3},
    "standard": {"max_chunk_size": 50000, "chunk_size_pct": 25.0, "max_concurrency": 10, "concurrency": 3},
    "discounted": {"max_chunk_size": 50000, "chunk_size_pct": 25.0, "max_concurrency": 10, "concurrency": 3},
    "premium": {"max_chunk_size": 100000, "chunk_size_pct": 50.0, "max_concurrency": 16, "concurrency": 4},
    "enterprise": {"max_chunk_size": 200000, "chunk_size_pct": 100.0, "max_concurrency": 24, "concurrency": 6},
}

print("=== PLAN_RESOURCE_CONFIG ===")
plan_issues = []
for p in PlanResourceConfig.objects.all().order_by("plan_name"):
    exp = EXPECTED_PLANS.get(p.plan_name, {})
    pct = float(p.chunk_size_pct) if p.chunk_size_pct is not None else None
    flags = []
    for k, v in exp.items():
        got = getattr(p, k)
        if k == "chunk_size_pct":
            got = pct
        if got != v:
            flags.append(f"{k}: got {got} expected {v}")
    status = "OK" if not flags else "MISMATCH"
    if flags:
        plan_issues.append((p.plan_name, flags))
    print(
        f"{p.plan_name:12} max_chunk={p.max_chunk_size:6} pct={pct} "
        f"max_conc={p.max_concurrency} conc={p.concurrency} pods={p.max_pods} "
        f"mem={p.memory_limit} [{status}]"
    )
    for f in flags:
        print(f"    ! {f}")

print("\n=== PLANS CATALOG (legacy) ===")
for pl in Plan.objects.all().order_by("plan_id"):
    print(f"plan_id={pl.plan_id} name={pl.name!r} pod_count={pl.pod_count}")

print("\n=== COMPANIES ===")
for c in Company.objects.all().order_by("cmp_id"):
    try:
        override = c.resource_override
        ov_chunk = override.max_chunk_size
        ov_pods = override.max_pods
    except CompanyResourceOverride.DoesNotExist:
        ov_chunk = ov_pods = None
    print(
        f"cmp_id={c.cmp_id:3} plan={c.plan!r:12} plan_ref_id={c.plan_ref_id} "
        f"pod_count={c.pod_count} override_pods={ov_pods} override_chunk={ov_chunk}"
    )

print("\n=== COMPANY RESOURCE OVERRIDES ===")
ovs = CompanyResourceOverride.objects.select_related("company").all()
if not ovs:
    print("(none)")
for ov in ovs:
    print(
        f"cmp_id={ov.company_id} ({ov.company.cmp_name}) max_pods={ov.max_pods} "
        f"max_chunk={ov.max_chunk_size} conc={ov.concurrency} reason={ov.reason!r}"
    )

print("\n=== CONNECTIONS (chunk vs expected) ===")
conn_issues = []
for conn in Connection.objects.select_related("cmp", "src", "dest").filter(is_delete=False).order_by(
    "cmp_id", "connection_id"
):
    company = conn.cmp
    src = conn.src
    dst = conn.dest
    if not src or not dst:
        continue
    expected = resolve_default_connection_chunk_count(company, src, dst)
    plan_row = PlanResourceConfig.objects.filter(plan_name=normalize_plan_name(company.plan)).first()
    override = None
    try:
        override = company.resource_override
    except CompanyResourceOverride.DoesNotExist:
        pass
    override_dict = None
    if override:
        override_dict = {
            k: getattr(override, k)
            for k in ("max_chunk_size", "max_pods", "concurrency")
            if getattr(override, k) is not None
        }
    resolved = (
        resolve_chunk_size_django(
            connection_chunk_count=conn.chunk_count,
            plan_config=plan_row,
            src_min=getattr(src, "min_count", None),
            src_max=getattr(src, "max_count", None),
            dst_min=getattr(dst, "min_count", None),
            dst_max=getattr(dst, "max_count", None),
            is_reverse_etl=bool(getattr(src, "is_rtl", False)),
            override=override_dict,
        )
        if plan_row
        else None
    )
    eff = resolved.effective_chunk if resolved else None
    flags = []
    if conn.chunk_count != expected:
        flags.append(f"stored={conn.chunk_count} default={expected}")
    if eff and conn.chunk_count > eff:
        flags.append(f"over_cap effective={eff}")
    if flags:
        conn_issues.append((conn.connection_id, flags))
    print(
        f"conn={conn.connection_id:3} cmp={conn.cmp_id} plan={normalize_plan_name(company.plan):10} "
        f"src={str(src.name)[:18]:18} chunk={conn.chunk_count:6} default={expected:6} "
        f"effective={eff} {'ISSUE: ' + '; '.join(flags) if flags else 'OK'}"
    )

print("\n=== SOURCE/DEST LIMITS (key connectors) ===")
for name in ["Microsoft Dynamics", "Snowflake", "Salesforce", "Amazon S3", "Microsoft_Dynamics"]:
    sm = SourceModel.objects.filter(name=name).first()
    dm = DestinationModel.objects.filter(name=name).first()
    if sm:
        print(f"SRC {name:22} min={sm.min_count} max={sm.max_count}")
    if dm:
        print(f"DST {name:22} min={dm.min_count} max={dm.max_count}")

print("\n=== SUMMARY ===")
print(f"Plan mismatches: {len(plan_issues)}")
print(f"Connection issues: {len(conn_issues)}")
if plan_issues:
    print("Plan issues:", plan_issues)
if conn_issues:
    print("Connection issues:", conn_issues)
