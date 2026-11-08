import os
from sqlalchemy import create_engine, text
import celery_resource_config as rc

db = create_engine(os.environ["DATABASE_URL"])
with db.connect() as conn:
    row = conn.execute(
        text(
            "SELECT max_pods, concurrency, memory_limit, max_chunk_size, max_inflight_tables "
            "FROM plan_resource_config WHERE plan_name='enterprise'"
        )
    ).mappings().first()
print("db_row", dict(row))

cfg = rc.load_plan_resource_config(db, "enterprise")
print("loaded", cfg.max_pods, cfg.concurrency, cfg.memory_limit, cfg.max_chunk_size, cfg.max_inflight_tables)

conn = rc.ConnectorLimits(chunk_count=200000, src_max_count=200000)
chunk, ceiling, natural = rc.resolve_chunk_size(conn, cfg)
print("chunk", chunk, ceiling, natural)
