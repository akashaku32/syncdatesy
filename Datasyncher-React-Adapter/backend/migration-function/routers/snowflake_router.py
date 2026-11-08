from fastapi import APIRouter
from fastapi import HTTPException
from models.snowflake_models import ListingTables
from services.snowflake.service import SnowflakeService
import time
from datetime import datetime
import logging


router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/Snowflake-Authorization")
async def create_snowflake_engine(request: dict):
    try:
        snowflake_details = request.get('Snowflake')
        destination_schema = request.get('destination_schema')
        validate_destination_schema = request.get('validate_destination_schema', True)
        source_schema = request.get('source_schema')
        validate_source_schema = request.get('validate_source_schema', False)
        if isinstance(validate_destination_schema, str):
            validate_destination_schema = validate_destination_schema.strip().lower() not in ("false", "0", "no")
        if isinstance(validate_source_schema, str):
            validate_source_schema = validate_source_schema.strip().lower() not in ("false", "0", "no")
        print("snowflake details",snowflake_details)
        print("destination schema", destination_schema)
        print("validate destination schema", validate_destination_schema)
        print("source schema", source_schema)
        print("validate source schema", validate_source_schema)
        # Call SnowflakeService to create the engine
        service = SnowflakeService(
            snowflake_details,
            destination_schema,
            validate_destination_schema=validate_destination_schema,
            source_schema=source_schema,
            validate_source_schema=validate_source_schema,
        )
        return service.engine_creation()
    except HTTPException:
        # Expected auth/validation failures should pass through directly.
        raise
    except Exception as e:
        from utils.error_utils import handle_service_error
        cmp_id = request.get("cmp_id")
        connection_id = request.get("connection_id")
        user_id = request.get("user_id")

        # Only attempt DB-backed service logging when required context exists.
        if cmp_id is not None and user_id is not None and connection_id is not None:
            handle_service_error(
                error=e,
                context={
                    'job_name': 'Snowflake Connection Creation',
                    'service_name': 'Snowflake',
                    'is_terminal_failure': False,
                    'cmp_id': cmp_id,
                    'connection_id': connection_id,
                    'user_id': user_id,
                    'session': request.get('session'),
                }
            )
        else:
            logger.warning(
                "Skipping handle_service_error DB logging for Snowflake-Authorization due to missing context: "
                "cmp_id=%s, connection_id=%s, user_id=%s",
                cmp_id,
                connection_id,
                user_id,
            )
        raise e

@router.post("/Snowflake-listing-tables")
async def Snowflake_tables_listing(data: ListingTables):
    import time
    start_time = time.time()
    try:
        print("Start: Parsing input data")
        snowflake = data.Snowflake
        
        destination_schema=data.destination_schema
        connection_id = data.connection_id
        job_name=data.job_name
        service = SnowflakeService(snowflake,destination_schema)
        result = service.get_tables_and_columns(connection_id,job_name,destination_schema)  # Calling the function from SnowflakeService
        return result
    except Exception as e:
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=e,
            context={
                'job_name': data.job_name,
                'log_id': data.log_id,
                # Note: Snowflake Listing model is missing cmp_id and user_id fields
                'cmp_id': getattr(data, 'cmp_id', None),
                'connection_id': data.connection_id,
                'user_id': getattr(data, 'user_id', None),
                'operation': 'UPDATE_SCHEMA',
                'is_terminal_failure': True,
                'execution_time': time.time() - start_time,
                'total_tables': 0,
                'successful_tables': 0,
                'total_rows': 0,
                'service_name': 'Snowflake'
            }
        )
        raise


from models.snowflake_models import SnowflakeDetails,SnowflakeMigration
from services.snowflake.as_source.to_snowflake import migrate
@router.post("/Snowflake-migrate")
async def migrate_data(request: SnowflakeMigration):
    """
    Handles the migration of data based on the provided SnowflakeMigration request.
    """
    start_time = time.time()
    try:
        result = migrate(request)
        return result
    except Exception as e:
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=e,
            context={
                'job_name': getattr(request, 'job_name', 'Snowflake Migration'),
                'log_id': getattr(request, 'log_id', None),
                'cmp_id': getattr(request, 'cmp_id', None),
                'connection_id': getattr(request, 'connection_id', None),
                'user_id': getattr(request, 'user_id', None),
                'operation': 'MIGRATION',
                'is_terminal_failure': True,
                'successful_tables': 0,
                'total_tables': len(request.selected_tables) if request.selected_tables else 0,
                'total_rows': 0,
                'service_name': 'Snowflake'
            }
        )
        raise e

@router.get("/get-details", response_model=SnowflakeDetails)
def get_details():
    details = SnowflakeDetails(schema_="public")  # internal field name
    return details.dict(by_alias=True)  # ensures "schema" is used in output

