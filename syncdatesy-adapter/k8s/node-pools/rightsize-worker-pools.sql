-- Right-size worker pools: node machine ≈ plan tier (2/4/8 vCPU, tight memory fit).
-- Run against the app DB (same as DATABASE_URL). Safe to re-run (idempotent).

UPDATE worker_node_pool_config
SET machine_type = 'e2-standard-2',
    allocatable_memory = '6Gi',
    allocatable_cpu = '1900m',
    overhead_memory = '1.5Gi',
    overhead_cpu = '150m',
    max_pods_per_node = 1,
    node_selector = '{"pool": "worker-standard"}'::jsonb,
    taint_key = NULL,
    taint_value = NULL,
    taint_effect = 'NoSchedule'
WHERE pool_name = 'worker-standard';

UPDATE worker_node_pool_config
SET machine_type = 'e2-standard-4',
    allocatable_memory = '13Gi',
    allocatable_cpu = '3800m',
    overhead_memory = '2Gi',
    overhead_cpu = '200m',
    max_pods_per_node = 1,
    node_selector = '{"pool": "worker-premium"}'::jsonb,
    taint_key = 'pool',
    taint_value = 'worker-premium',
    taint_effect = 'NoSchedule'
WHERE pool_name = 'worker-premium';

UPDATE worker_node_pool_config
SET machine_type = 'e2-standard-8',
    allocatable_memory = '26Gi',
    allocatable_cpu = '7800m',
    overhead_memory = '2Gi',
    overhead_cpu = '200m',
    max_pods_per_node = 1,
    node_selector = '{"pool": "worker-enterprise"}'::jsonb,
    taint_key = 'pool',
    taint_value = 'worker-enterprise',
    taint_effect = 'NoSchedule'
WHERE pool_name = 'worker-enterprise';

UPDATE plan_resource_config
SET cpu_request = '1500m',
    cpu_limit = '2',
    memory_request = '4Gi',
    memory_limit = '6Gi'
WHERE plan_name IN ('free', 'standard', 'discounted');

UPDATE plan_resource_config
SET cpu_request = '3',
    cpu_limit = '4',
    memory_request = '10Gi',
    memory_limit = '12Gi'
WHERE plan_name = 'premium';

UPDATE plan_resource_config
SET cpu_request = '7',
    cpu_limit = '8',
    memory_request = '22Gi',
    memory_limit = '24Gi',
    max_pods = 6,
    concurrency = 6,
    max_chunk_size = 175000,
    chunk_size_pct = 100.0,
    max_concurrency = 24,
    max_inflight_tables = 36
WHERE plan_name = 'enterprise';
