"""One-shot QA fix: restore worker_node_pool_config node_selector + taints."""
from apps.elttool.models import PlanResourceConfig, WorkerNodePoolConfig

POOLS = {
    "worker-standard": {
        "machine_type": "e2-standard-2",
        "allocatable_memory": "6Gi",
        "allocatable_cpu": "1900m",
        "overhead_memory": "1.5Gi",
        "overhead_cpu": "150m",
        "max_pods_per_node": 1,
        "node_selector": {"pool": "worker-standard"},
        "taint_key": None,
        "taint_value": None,
        "taint_effect": "NoSchedule",
    },
    "worker-premium": {
        "machine_type": "e2-standard-4",
        "allocatable_memory": "13Gi",
        "allocatable_cpu": "3800m",
        "overhead_memory": "2Gi",
        "overhead_cpu": "200m",
        "max_pods_per_node": 1,
        "node_selector": {"pool": "worker-premium"},
        "taint_key": "pool",
        "taint_value": "worker-premium",
        "taint_effect": "NoSchedule",
    },
    "worker-enterprise": {
        "machine_type": "e2-standard-8",
        "allocatable_memory": "26Gi",
        "allocatable_cpu": "7800m",
        "overhead_memory": "2Gi",
        "overhead_cpu": "200m",
        "max_pods_per_node": 1,
        "node_selector": {"pool": "worker-enterprise"},
        "taint_key": "pool",
        "taint_value": "worker-enterprise",
        "taint_effect": "NoSchedule",
    },
}

PLAN_RESOURCES = {
    "free": ("1500m", "2", "4Gi", "6Gi"),
    "standard": ("1500m", "2", "4Gi", "6Gi"),
    "discounted": ("1500m", "2", "4Gi", "6Gi"),
    "premium": ("3", "4", "10Gi", "12Gi"),
    "enterprise": ("7", "8", "22Gi", "24Gi"),
}


def main():
    for pool_name, fields in POOLS.items():
        WorkerNodePoolConfig.objects.filter(pool_name=pool_name).update(**fields)
        row = WorkerNodePoolConfig.objects.get(pool_name=pool_name)
        print(
            f"{row.pool_name}: node_selector={row.node_selector} "
            f"taint={row.taint_key}:{row.taint_value} machine={row.machine_type}"
        )

    for plan_name, (cpu_req, cpu_lim, mem_req, mem_lim) in PLAN_RESOURCES.items():
        PlanResourceConfig.objects.filter(plan_name=plan_name).update(
            cpu_request=cpu_req,
            cpu_limit=cpu_lim,
            memory_request=mem_req,
            memory_limit=mem_lim,
        )

    print("OK: worker pool routing restored")


if __name__ == "__main__":
    main()
