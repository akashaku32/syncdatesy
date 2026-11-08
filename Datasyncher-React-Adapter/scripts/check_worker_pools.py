import psycopg2

c = psycopg2.connect(
    host="136.115.40.171",
    port=5432,
    dbname="datasyncher_batch",
    user="postgres",
    password="DSQa_2026",
)
cur = c.cursor()
cur.execute(
    """
    SELECT pool_name, machine_type, allocatable_memory, allocatable_cpu, max_pods_per_node
    FROM worker_node_pool_config ORDER BY pool_name
    """
)
print("pools:", cur.fetchall())
cur.execute(
    """
    SELECT plan_name, max_pods, cpu_request, cpu_limit, memory_request, memory_limit, worker_pool_name
    FROM plan_resource_config
    WHERE plan_name IN ('enterprise', 'premium')
    ORDER BY plan_name
    """
)
print("plans:", cur.fetchall())
cur.close()
c.close()
