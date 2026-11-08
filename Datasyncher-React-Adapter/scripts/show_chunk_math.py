from apps.elttool.models import PlanResourceConfig, SourceModel, DestinationModel
from apps.elttool.services.chunk_limit_resolver import resolve_default_connection_chunk_count
from apps.elttool.models import Company

print("=== Plan configs ===")
for p in ["free", "standard", "premium", "enterprise"]:
    try:
        r = PlanResourceConfig.objects.get(plan_name=p)
        print(f"  {p}: max_chunk_size={r.max_chunk_size} chunk_size_pct={r.chunk_size_pct}")
    except PlanResourceConfig.DoesNotExist:
        print(f"  {p}: MISSING")

print("\n=== Connector ceilings ===")
for name in ["MicrosoftDynamics365_FO", "Snowflake", "Salesforce"]:
    s = SourceModel.objects.filter(name=name).first()
    d = DestinationModel.objects.filter(name=name).first()
    if s:
        print(f"  Source {name}: max_count={s.max_count}")
    if d:
        print(f"  Dest {name}: max_count={d.max_count}")

src = SourceModel.objects.filter(name="MicrosoftDynamics365_FO").first()
dst = DestinationModel.objects.filter(name="Snowflake").first()
print("\n=== Dynamics->Snowflake chunk by plan ===")
for plan in ["free", "premium", "enterprise"]:
    c = Company(plan=plan)
    val = resolve_default_connection_chunk_count(c, src, dst)
    print(f"  plan={plan} -> chunk_count={val}")
