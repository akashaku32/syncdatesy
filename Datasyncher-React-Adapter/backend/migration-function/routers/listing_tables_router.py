# routers/listing_tables_router.py
from fastapi import APIRouter, HTTPException
from models.microsoft_dynamics_models import ListingTables
from services.listing_tables.service import authenticate_and_list_tables

router = APIRouter()

@router.post("/MicrosoftDynamics365_FO-listing-tables")
async def list_tables(data: ListingTables):
    """
    List new tables from Azure Blob Storage.
    
    Args:
        data (ListingTables): Input data with connection details and last checked date.
    
    Returns:
        dict: New tables, attributes, and the most recent directory processed.
    """
    import time
    start_time = time.time()
    try:
        print("Start: Parsing input data")
        microsoft_dynamics = data.MicrosoftDynamics365_FO
        container_name = microsoft_dynamics.get("container_name")
        connection_string = microsoft_dynamics.get("connection_string")
        last_checked_date = data.last_checked_date
        connection_id = data.connection_id
        job_name=data.job_name
        cmp_id = data.cmp_id
        user_id=data.user_id
        # Validate inputs
        if not container_name or not connection_string:
            raise HTTPException(status_code=400, detail="Missing required parameters: container_name and connection_string")

        print("Start: Authenticating and listing tables")
        result = authenticate_and_list_tables(connection_id,container_name, connection_string, cmp_id,user_id,last_checked_date,job_name)
        print("Completed: Listing tables")

        return result

    except Exception as e:
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=e,
            context={
                'job_name': data.job_name,
                'log_id': data.log_id,
                'cmp_id': data.cmp_id,
                'connection_id': data.connection_id,
                'user_id': data.user_id,
                'operation': 'UPDATE_SCHEMA',
                'is_terminal_failure': True,
                'execution_time': time.time() - start_time,
                'total_tables': 0,
                'successful_tables': 0,
                'total_rows': 0,
                'service_name': 'Microsoft Dynamics'
            }
        )
        raise e

from models.snowflake_models import SnowflakeDetails



@router.get("/get-details", response_model=SnowflakeDetails)
def get_details():
    details = SnowflakeDetails(schema_="public")  # internal field name
    return details.dict(by_alias=True)  # ensures "schema" is used in output
