import math
import os
import re
import subprocess

from sqlalchemy import create_engine, text

JOB = "D365_PROD_TEST_manual_30"
CONN = "D365_PROD_TEST"

e = create_engine(
    f"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
    f"@{os.environ['DB_HOST']}/{os.environ['DB_NAME']}"
)
with e.connect() as c:
    row = c.execute(
        text(
            """
            SELECT mr.job_name,
                   COUNT(*) AS in_progress,
                   COALESCE(MAX(c.plan), '') AS company_plan,
                   GREATEST(
                       COALESCE(MAX(p.pod_count), 1),
                       CASE
                           WHEN COALESCE(MAX(c.pod_count), 0) > COALESCE(MAX(p.pod_count), 1)
                           THEN COALESCE(MAX(c.pod_count), 0)
                           ELSE 0
                       END
                   ) AS pod_cap
            FROM migration_record mr
            JOIN connection cn ON cn.connection_id = mr.connection_id
            LEFT JOIN company c ON c.cmp_id = cn.cmp_id
            LEFT JOIN plans p ON p.plan_id = c.plan_id
            WHERE mr.status = 'in_progress'
              AND mr.job_name LIKE :jobpat
            GROUP BY mr.job_name
            LIMIT 1
            """
        ),
        {"jobpat": f"{JOB}%"},
    ).fetchone()

plan_caps = {"free": 1, "standard": 2, "enterprise": 3, "discounted": 2}
concurrency = int(os.environ.get("CELERY_CHILD_CONCURRENCY", "3") or 3)
if row:
    job_name, in_progress, company_plan, pod_cap = row
    plan_key = (company_plan or "").strip().lower()
    plan_cap = plan_caps.get(plan_key, 3)
    effective_cap = max(1, min(int(pod_cap or 1), plan_cap))
    base_pods = max(1, math.ceil(int(in_progress or 0) / concurrency))
    keda_pods_for_job = min(base_pods, effective_cap)
    print("=== D365_PROD_TEST KEDA allocation ===")
    print(f"job: {job_name[:80]}")
    print(f"company.plan: {company_plan or '(empty)'}")
    print(f"pod_cap (DB): {pod_cap}  plan_cap: {plan_cap}  effective_cap: {effective_cap}")
    print(f"in_progress tables: {in_progress}")
    print(f"base_pods = ceil({in_progress}/{concurrency}) = {base_pods}")
    print(f"KEDA pods reserved for this job: {keda_pods_for_job}")
else:
    print("Connection not found")

out = subprocess.check_output(
    [
        "celery", "-A", "celery_worker.celery_app", "inspect", "active", "--timeout", "10",
    ],
    text=True,
    stderr=subprocess.STDOUT,
)
pods: dict[str, int] = {}
for m in re.finditer(r"celery@(\S+).*?(?=celery@|\Z)", out, re.DOTALL):
    pod = m.group(1)
    section = m.group(0)
    if "D365_PROD_TEST_manual" in section:
        pods[pod] = section.count("celery_worker.call_function_child")

print("\n=== Pods with active D365_PROD_TEST tasks right now ===")
print(f"pods: {len(pods)}  active tasks: {sum(pods.values())}")
for pod, cnt in sorted(pods.items()):
    print(f"  {pod}: {cnt}")
