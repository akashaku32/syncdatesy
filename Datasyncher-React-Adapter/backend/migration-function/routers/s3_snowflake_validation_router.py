from fastapi import APIRouter, HTTPException
from models.snowflake_models import ListingTables
from services.snowflake.service import SnowflakeService

router = APIRouter()


@router.post("/Snowflake-listing-tables-readonly")
async def snowflake_tables_listing_readonly(data: ListingTables):
    """
    Read-only Snowflake schema listing endpoint.

    This endpoint is intended for S3 pre-save/pre-validation flows where we need
    to check existing table names in a destination Snowflake schema without
    updating SourceTableSelection or any mapping tables.
    """
    try:
        snowflake_details = data.Snowflake or {}
        destination_schema = data.destination_schema

        # Listing schema is derived from Snowflake payload first, fallback to destination_schema.
        schema_name = str(snowflake_details.get("schema") or destination_schema or "").strip()
        database_name = str(snowflake_details.get("database") or "").strip()
        if not schema_name or not database_name:
            raise HTTPException(
                status_code=422,
                detail="Missing required Snowflake fields: database and schema.",
            )

        service = SnowflakeService(snowflake_details, destination_schema)
        conn = service.create_connection()
        cur = conn.cursor()
        try:
            safe_db = '"' + database_name.replace('"', '""') + '"'
            safe_schema = '"' + schema_name.replace('"', '""') + '"'
            safe_schema_like = schema_name.replace("'", "''")

            # Check schema existence first. If missing, return success with empty tables.
            cur.execute(f"SHOW SCHEMAS LIKE '{safe_schema_like}' IN DATABASE {safe_db}")
            schema_rows = cur.fetchall() or []
            schema_exists = any(
                isinstance(row, (list, tuple))
                and len(row) > 1
                and str(row[1]).strip().upper() == schema_name.upper()
                for row in schema_rows
            )
            if not schema_exists:
                return {
                    "status_code": 200,
                    "schema_exists": False,
                    "existing_tables": [],
                    "existing_tables_upper": [],
                    "table_count": 0,
                    "body": "Snowflake schema not found (read-only listing).",
                }

            cur.execute(f"SHOW TABLES IN SCHEMA {safe_db}.{safe_schema}")
            table_rows = cur.fetchall() or []
            table_names = sorted({
                str(row[1]).strip()
                for row in table_rows
                if isinstance(row, (list, tuple)) and len(row) > 1 and str(row[1]).strip()
            })
        finally:
            try:
                cur.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

        return {
            "status_code": 200,
            "schema_exists": True,
            "existing_tables": table_names,
            "existing_tables_upper": [t.upper() for t in table_names],
            "table_count": len(table_names),
            "body": "Read-only Snowflake table listing fetched successfully",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=422,
            detail="Failed to fetch read-only Snowflake table listing.",
        )
