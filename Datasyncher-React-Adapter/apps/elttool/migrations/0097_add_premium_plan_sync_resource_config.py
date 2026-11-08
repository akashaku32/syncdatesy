from django.db import migrations, models


# plans.plan_id catalog aligned with plan_resource_config.plan_name / max_pods.
PLAN_CATALOG = [
    (1, "Free", "free"),
    (2, "Standard", "standard"),
    (3, "Enterprise", "enterprise"),
    (4, "Premium", "premium"),
]

PLAN_ID_BY_COMPANY_PLAN_NAME = {
    "free": 1,
    "discounted": 2,
    "standard": 2,
    "enterprise": 3,
    "premium": 4,
}


def add_premium_and_sync_pod_counts(apps, schema_editor):
    Plan = apps.get_model("elttool", "Plan")
    PlanResourceConfig = apps.get_model("elttool", "PlanResourceConfig")
    Company = apps.get_model("elttool", "Company")

    prc_by_name = {
        str(row.plan_name).strip().lower(): int(row.max_pods)
        for row in PlanResourceConfig.objects.all()
    }

    for plan_id, name, prc_key in PLAN_CATALOG:
        pod_count = prc_by_name.get(prc_key)
        if pod_count is None:
            # Fallback if plan_resource_config row is missing.
            pod_count = {1: 2, 2: 2, 3: 6, 4: 4}[plan_id]
        Plan.objects.update_or_create(
            plan_id=plan_id,
            defaults={"name": name, "pod_count": pod_count},
        )

    for company in Company.objects.all().iterator():
        key = (company.plan or "Free").strip().lower()
        plan_id = PLAN_ID_BY_COMPANY_PLAN_NAME.get(key, 1)
        if company.plan_ref_id != plan_id:
            company.plan_ref_id = plan_id
            company.save(update_fields=["plan_ref_id"])


def noop_reverse(apps, schema_editor):
    # Keep Premium row; do not delete in reverse (companies may reference it).
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0096_plan_resource_config_flow_pod_caps"),
    ]

    operations = [
        migrations.AlterField(
            model_name="company",
            name="plan",
            field=models.CharField(
                choices=[
                    ("Free", "Free"),
                    ("Discounted", "Discounted"),
                    ("Standard", "Standard"),
                    ("Premium", "Premium"),
                    ("Enterprise", "Enterprise"),
                ],
                default="Free",
                max_length=20,
            ),
        ),
        migrations.RunPython(add_premium_and_sync_pod_counts, noop_reverse),
    ]
