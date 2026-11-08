from fastapi import APIRouter, HTTPException

from services.snowflake.service import SnowflakeService

router = APIRouter()

# @router.post("/Snowflake-Authorization")
async def create_snowflake_engine(request: dict):
    try:
        snowflake_details = request.get('Snowflake')
        destination_schema = request.get('destination_schema')

        # Call SnowflakeService to create the engine
        print("snowflake details",snowflake_details)
        print("destination schema",destination_schema)
        service = SnowflakeService(snowflake_details, destination_schema)
        return service.engine_creation()
    except Exception as e:
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=e,
            context={
                'job_name': 'Snowflake Authorization',
                'service_name': 'Snowflake',
                'is_terminal_failure': False
            }
        )
        raise e

