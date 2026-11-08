from fastapi import APIRouter, HTTPException

from services.databricks.service import DatabricksService

router = APIRouter()


def _format_s3_staging_provision_response(result: dict) -> dict:
    if result.get("skipped_reason") and not result.get("provisioned"):
        return {
            "status_code": 200,
            "status": "skipped",
            "message": result.get("skipped_reason"),
            **result,
        }
    if result.get("provisioned") and not result.get("s3_prefix_readable"):
        return {
            "status_code": 200,
            "status": "warning",
            "message": (
                "S3 IAM trust updated but Databricks cannot LIST the staging prefix yet. "
                "First migration may retry automatically."
            ),
            **result,
        }
    return {
        "status_code": 200,
        "status": "success",
        "message": "Databricks S3 staging provisioned",
        **result,
    }


@router.post("/Databricks-Authorization")
async def create_databricks_engine(request: dict):
    try:
        databricks_details = request.get("Databricks") or request.get("databricks")
        destination_schema = request.get("destination_schema")
        validate_destination_schema = request.get("validate_destination_schema", True)
        source_schema = request.get("source_schema")
        validate_source_schema = request.get("validate_source_schema", False)
        validate_sql_warehouse = request.get("validate_sql_warehouse")
        if isinstance(validate_destination_schema, str):
            validate_destination_schema = validate_destination_schema.strip().lower() not in (
                "false",
                "0",
                "no",
            )
        if isinstance(validate_source_schema, str):
            validate_source_schema = validate_source_schema.strip().lower() not in (
                "false",
                "0",
                "no",
            )
        if isinstance(validate_sql_warehouse, str):
            validate_sql_warehouse = validate_sql_warehouse.strip().lower() not in (
                "false",
                "0",
                "no",
            )

        service = DatabricksService(
            databricks_details,
            destination_schema,
            validate_destination_schema=validate_destination_schema,
            source_schema=source_schema,
            validate_source_schema=validate_source_schema,
            validate_sql_warehouse=validate_sql_warehouse,
        )
        return service.engine_creation()
    except HTTPException:
        raise
    except Exception as e:
        from utils.error_utils import handle_service_error

        cmp_id = request.get("cmp_id")
        connection_id = request.get("connection_id")
        user_id = request.get("user_id")
        if cmp_id is not None and user_id is not None and connection_id is not None:
            handle_service_error(
                e,
                cmp_id=cmp_id,
                connection_id=connection_id,
                user_id=user_id,
                function_name="Databricks-Authorization",
            )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/Databricks-Provision-S3-Staging")
async def provision_databricks_s3_staging(request: dict):
    """Pre-provision UC S3 external location + IAM trust when a connection is saved."""
    try:
        connection_id = int(request.get("connection_id") or 0)
        if connection_id <= 0:
            raise HTTPException(status_code=400, detail="connection_id is required")

        databricks_details = request.get("Databricks") or request.get("databricks") or {}
        if not isinstance(databricks_details, dict):
            raise HTTPException(status_code=400, detail="Databricks config must be a dictionary")

        from services.databricks.load_helpers import (
            provision_databricks_s3_staging_for_connection,
            should_provision_databricks_s3_staging_at_connection,
        )

        if not should_provision_databricks_s3_staging_at_connection(databricks_details):
            return {
                "status_code": 200,
                "status": "skipped",
                "message": "S3 staging auto-provision not applicable for this connection",
                "skipped_reason": "s3_auto_provision_not_applicable",
            }

        result = provision_databricks_s3_staging_for_connection(
            connection_id,
            databricks_details,
            wait_for_iam_propagation=bool(request.get("wait_for_iam_propagation", True)),
        )
        return _format_s3_staging_provision_response(result)
    except HTTPException:
        raise
    except Exception as e:
        from utils.error_utils import handle_service_error

        cmp_id = request.get("cmp_id")
        connection_id = request.get("connection_id")
        user_id = request.get("user_id")
        if cmp_id is not None and user_id is not None and connection_id is not None:
            handle_service_error(
                e,
                cmp_id=cmp_id,
                connection_id=connection_id,
                user_id=user_id,
                function_name="Databricks-Provision-S3-Staging",
            )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/Databricks-Provision-Dynamics-Spark-Job")
async def provision_dynamics_dedicated_spark_job(request: dict):
    """Create or update a dedicated serverless Lakeflow Job on a Databricks destination."""
    try:
        dst_config_id = int(request.get("dst_config_id") or 0)
        if dst_config_id <= 0:
            raise HTTPException(status_code=400, detail="dst_config_id is required")

        databricks_details = request.get("Databricks") or request.get("databricks") or {}
        if not isinstance(databricks_details, dict):
            raise HTTPException(status_code=400, detail="Databricks config must be a dictionary")

        from services.databricks.load_helpers import (
            provision_dedicated_dynamics_spark_job_for_destination,
        )

        result = provision_dedicated_dynamics_spark_job_for_destination(
            dst_config_id,
            databricks_details,
        )
        if result.get("skipped_reason") and not result.get("provisioned"):
            return {
                "status_code": 200,
                "status": "skipped",
                "message": result.get("skipped_reason"),
                **result,
            }
        if result.get("provisioned") and result.get("compute_mode") == "classic":
            return {
                "status_code": 200,
                "status": "warning",
                "message": (
                    "Dedicated job created using classic compute (scoped-credential staging). "
                    "Serverless requires Unity Catalog external location access."
                ),
                **result,
            }
        return {
            "status_code": 200,
            "status": "success",
            "message": "Dedicated Dynamics Spark job provisioned",
            **result,
        }
    except HTTPException:
        raise
    except Exception as e:
        from utils.error_utils import handle_service_error

        cmp_id = request.get("cmp_id")
        user_id = request.get("user_id")
        if cmp_id is not None and user_id is not None:
            handle_service_error(
                e,
                cmp_id=cmp_id,
                connection_id=request.get("connection_id"),
                user_id=user_id,
                function_name="Databricks-Provision-Dynamics-Spark-Job",
            )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/Databricks-Provision-S3-Staging-Destination")
async def provision_databricks_s3_staging_destination(request: dict):
    """Pre-provision UC credential + IAM trust when a Databricks destination is saved."""
    try:
        dst_config_id = int(request.get("dst_config_id") or 0)
        if dst_config_id <= 0:
            raise HTTPException(status_code=400, detail="dst_config_id is required")

        databricks_details = request.get("Databricks") or request.get("databricks") or {}
        if not isinstance(databricks_details, dict):
            raise HTTPException(status_code=400, detail="Databricks config must be a dictionary")

        from services.databricks.load_helpers import (
            provision_databricks_s3_staging_for_destination,
            should_provision_databricks_s3_staging_at_destination,
        )

        if not should_provision_databricks_s3_staging_at_destination(databricks_details):
            return {
                "status_code": 200,
                "status": "skipped",
                "message": "S3 staging auto-provision not applicable for this destination",
                "skipped_reason": "s3_auto_provision_not_applicable",
            }

        result = provision_databricks_s3_staging_for_destination(
            dst_config_id,
            databricks_details,
            wait_for_iam_propagation=bool(request.get("wait_for_iam_propagation", True)),
        )
        return _format_s3_staging_provision_response(result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/Databricks-listing-tables")
async def list_databricks_tables(request: dict):
    try:
        databricks_details = request.get("Databricks") or request.get("databricks") or {}
        service = DatabricksService(
            databricks_details,
            destination_schema=databricks_details.get("schema"),
            validate_destination_schema=False,
            source_schema=databricks_details.get("schema"),
            validate_source_schema=True,
        )
        return service.get_tables_and_columns(
            request.get("connection_id"),
            request.get("job_name"),
            cmp_id=request.get("cmp_id"),
            user_id=request.get("user_id"),
        )
    except HTTPException:
        raise
    except Exception as e:
        from utils.error_utils import handle_service_error

        handle_service_error(
            error=e,
            context={
                "job_name": request.get("job_name"),
                "log_id": request.get("log_id"),
                "cmp_id": request.get("cmp_id"),
                "connection_id": request.get("connection_id"),
                "user_id": request.get("user_id"),
                "operation": "UPDATE_SCHEMA",
                "is_terminal_failure": True,
                "service_name": "Databricks",
            },
        )
        raise HTTPException(status_code=500, detail=str(e)) from e
