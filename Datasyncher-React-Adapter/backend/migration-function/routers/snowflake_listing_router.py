from fastapi import APIRouter, HTTPException
from models.snowflake_models import ListingTables
from services.snowflake.service import SnowflakeService

router = APIRouter()


from models.snowflake_models import SnowflakeDetails

@router.get("/get-details", response_model=SnowflakeDetails)
def get_details():
    details = SnowflakeDetails(schema_="public")  # internal field name
    return details.dict(by_alias=True)  # ensures "schema" is used in output
@router.post("/Snowflake-listing-tables")
async def snowflake_tables_listing(data: ListingTables):
    import time
    import traceback
    start_time = time.time()
    try:
        print("✅ Start: Parsing input data")
        snowflake_details = data.Snowflake
        
        # ============================================================
        # CRITICAL DEBUG: Check what FastAPI received
        # ============================================================
        print("=" * 80)
        print("🔍 [CRITICAL DEBUG] FastAPI received data:")
        print(f"   - snowflake_details type: {type(snowflake_details)}")
        print(f"   - Has 'passphrase' key: {'passphrase' in snowflake_details}")
        print(f"   - Has 'private_key' key: {'private_key' in snowflake_details}")
        if 'passphrase' in snowflake_details:
            passphrase_val = snowflake_details.get('passphrase')
            print(f"   - Passphrase value: '{passphrase_val}' (type: {type(passphrase_val)}, length: {len(str(passphrase_val)) if passphrase_val else 0})")
        else:
            print(f"   - ⚠️ PASSPHRASE IS MISSING FROM snowflake_details!")
        if 'private_key' in snowflake_details:
            private_key_val = snowflake_details.get('private_key')
            print(f"   - Private key length: {len(str(private_key_val)) if private_key_val else 0}")
            print(f"   - Private key starts with: {str(private_key_val)[:50] if private_key_val else 'None'}")
            print(f"   - Private key ends with: {str(private_key_val)[-50:] if private_key_val else 'None'}")
        else:
            print(f"   - ⚠️ PRIVATE_KEY IS MISSING FROM snowflake_details!")
        print(f"   - All keys in snowflake_details: {list(snowflake_details.keys())}")
        print(f"   - Authentication type: {snowflake_details.get('authentication_type', 'NOT SET')}")
        print("=" * 80)
        
        destination_schema = snowflake_details.get('schema')
        connection_id = data.connection_id
        job_name = data.job_name
        print(snowflake_details)
        print(destination_schema)
        service = SnowflakeService(snowflake_details, destination_schema)
        print("after service",service)
        result = service.get_tables_and_columns(connection_id, job_name,destination_schema)
        
        return result
    except Exception as e:
        from utils.error_utils import handle_service_error
        handle_service_error(
            error=e,
            context={
                'job_name': data.job_name,
                'log_id': data.log_id,
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
        raise e
