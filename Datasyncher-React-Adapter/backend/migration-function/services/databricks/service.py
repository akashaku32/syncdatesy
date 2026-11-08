"""Databricks destination authorization / connection validation."""

from __future__ import annotations

import logging

from fastapi import HTTPException

logger = logging.getLogger(__name__)


class DatabricksService:
    def __init__(
        self,
        databricks_details,
        destination_schema,
        validate_destination_schema=True,
        source_schema=None,
        validate_source_schema=False,
        validate_sql_warehouse=None,
    ):
        if databricks_details is None:
            raise ValueError("databricks_details cannot be None")
        if not isinstance(databricks_details, dict):
            raise ValueError("databricks_details must be a dictionary")

        self.databricks_details = dict(databricks_details)
        self.destination_schema = destination_schema
        self.validate_destination_schema = bool(validate_destination_schema)
        self.source_schema = source_schema
        self.validate_source_schema = bool(validate_source_schema)
        if validate_sql_warehouse is None:
            validate_sql_warehouse = validate_destination_schema
        self.validate_sql_warehouse = bool(validate_sql_warehouse)

    def _missing_fields(self, details: dict, *, api_only: bool) -> list[str]:
        required = ("server_hostname", "access_token", "catalog") if api_only else (
            "server_hostname",
            "http_path",
            "access_token",
            "catalog",
        )
        missing = [key for key in required if not str(details.get(key) or "").strip()]
        if "server_hostname" in missing and str(details.get("host") or "").strip():
            missing.remove("server_hostname")
        if "access_token" in missing and str(details.get("token") or "").strip():
            missing.remove("access_token")
        return missing

    def engine_creation(self):
        from datasyncher_connections.databricks.connector import (
            create_databricks_sqlalchemy_like_engine,
            normalize_databricks_config,
            validate_databricks_api_connection,
            validate_databricks_sql_warehouse,
        )

        details = normalize_databricks_config(self.databricks_details)
        if self.destination_schema:
            details["schema"] = str(self.destination_schema).strip()

        api_only = not self.validate_destination_schema
        missing = self._missing_fields(details, api_only=api_only)
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Missing required Databricks fields: {', '.join(missing)}",
            )

        try:
            # Destination-config create/update/test: REST API auth; if http_path is
            # configured also ping the SQL warehouse so UI tests match migration reality.
            if api_only:
                logger.info(
                    "Databricks destination auth via REST API "
                    "(validate_destination_schema=False; SQL warehouse not required)"
                )
                api_result = validate_databricks_api_connection(details)
                validation_mode = api_result.get("mode", "api_only")
                sql_result = None
                if self.validate_sql_warehouse and str(details.get("http_path") or "").strip():
                    logger.info(
                        "Databricks destination also validating SQL warehouse (validate_sql_warehouse=True)"
                    )
                    sql_result = validate_databricks_sql_warehouse(details)
                    validation_mode = "api_and_sql_warehouse"
                payload = {
                    "body": "Databricks connection successful",
                    "status_code": 200,
                    "status": "success",
                    "message": "Databricks connection successful",
                    "schema": details.get("schema") or None,
                    "catalog": details.get("catalog"),
                    "validation_mode": validation_mode,
                }
                if sql_result:
                    sql_mode = str(sql_result.get("mode") or "").strip()
                    if sql_mode == "sql_cluster_compute":
                        validation_mode = "api_and_sql_cluster_compute"
                    elif sql_mode == "sql_warehouse":
                        validation_mode = "api_and_sql_warehouse"
                if sql_result and sql_result.get("context"):
                    payload["sql_context"] = sql_result.get("context")
                return payload

            if not details.get("schema"):
                raise HTTPException(
                    status_code=400,
                    detail="destination_schema is required for Databricks authorization",
                )

            # Connection save/test + migrations: SQL warehouse must be reachable.
            engine, schema = create_databricks_sqlalchemy_like_engine(
                details,
                ensure_schema=True,
            )
            with engine.connect() as conn:
                result = conn.execute("SELECT 1")
                row = result.fetchone()
                if not row or row[0] != 1:
                    raise RuntimeError("Databricks connectivity check failed")
            return {
                "body": "Databricks connection successful",
                "status_code": 200,
                "status": "success",
                "message": "Databricks connection successful",
                "schema": schema,
                "catalog": details.get("catalog"),
                "validation_mode": "sql_warehouse",
            }
        except HTTPException:
            raise
        except ValueError as exc:
            logger.warning("Databricks authorization failed: %s", exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            from datasyncher_connections.databricks.connector import DatabricksWarehouseUnavailable

            if isinstance(exc, DatabricksWarehouseUnavailable):
                logger.warning("Databricks warehouse unavailable: %s", exc)
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            logger.exception("Databricks authorization failed")
            raise HTTPException(status_code=400, detail=f"Databricks connection failed: {exc}") from exc

    def get_tables_and_columns(self, connection_id, job_name, cmp_id=None, user_id=None):
        from datasyncher_connections.databricks.connector import (
            create_databricks_sqlalchemy_like_engine,
            normalize_databricks_config,
        )
        from services.databricks.listing import (
            build_databricks_list_columns_sql,
            build_databricks_list_primary_keys_sql,
            build_databricks_list_tables_sql,
            is_internal_listing_table,
        )
        from services.snowflake.service import refresh_and_update_tables_in_database

        details = normalize_databricks_config(dict(self.databricks_details))
        schema = str(
            details.get("schema") or self.source_schema or self.destination_schema or ""
        ).strip()
        if schema:
            details["schema"] = schema
        if not schema:
            raise HTTPException(
                status_code=400,
                detail="Databricks source configuration missing required 'schema' field.",
            )
        catalog = (details.get("catalog") or "").strip() or None

        engine, _schema = create_databricks_sqlalchemy_like_engine(
            details,
            ensure_schema=False,
        )
        all_objects = {}
        columns_info = {}
        primary_keys = {}
        table_lookup: dict[str, str] = {}
        try:
            with engine.connect() as conn:
                table_rows = conn.execute(
                    build_databricks_list_tables_sql(catalog, schema)
                ).fetchall()
                for row in table_rows:
                    table_name = str(row[0] or "").strip()
                    table_type = str(row[1] or "table").strip().lower()
                    if is_internal_listing_table(table_name):
                        continue
                    if "view" in table_type:
                        kind = "view"
                    else:
                        kind = "table"
                    all_objects[table_name] = kind
                    table_lookup[table_name.lower()] = table_name

                try:
                    pk_rows = conn.execute(
                        build_databricks_list_primary_keys_sql(catalog, schema)
                    ).fetchall()
                    for table_name, column_name in pk_rows:
                        table_name = str(table_name or "").strip()
                        if is_internal_listing_table(table_name):
                            continue
                        canonical_table = table_lookup.get(table_name.lower(), table_name)
                        primary_keys.setdefault(canonical_table, []).append(str(column_name))
                except Exception as pk_err:
                    logger.warning("Databricks PK listing skipped: %s", pk_err)

                col_rows = conn.execute(
                    build_databricks_list_columns_sql(catalog, schema)
                ).fetchall()
                for table_name, column_name, data_type, ordinal in col_rows:
                    table_name = str(table_name or "").strip()
                    canonical_table = table_lookup.get(table_name.lower())
                    if not canonical_table:
                        continue
                    columns_info.setdefault(canonical_table, {})
                    column_name = str(column_name)
                    pk_names = {
                        str(pk).lower()
                        for pk in primary_keys.get(canonical_table, [])
                    }
                    columns_info[canonical_table][column_name] = {
                        "data_type": str(data_type or "STRING"),
                        "is_primary_key": column_name.lower() in pk_names,
                        "ordinal_position": int(ordinal or 0) or None,
                    }
        finally:
            try:
                engine.dispose()
            except Exception:
                pass

        refresh_and_update_tables_in_database(
            connection_id, all_objects, columns_info, job_name
        )
        return {
            "status_code": 200,
            "updated_tables": all_objects,
            "columns": columns_info,
            "body": "Tables fetched successfully",
        }
