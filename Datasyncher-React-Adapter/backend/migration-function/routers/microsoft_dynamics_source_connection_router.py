from fastapi import APIRouter, HTTPException
from services.dynamics.source import MicrosoftDynamics365Source
from models.microsoft_dynamics_models import MicrosoftDynamicsSourceConnection

router = APIRouter()

@router.post("/MicrosoftDynamics365_FO-Authorization")
async def source_connection(data: MicrosoftDynamicsSourceConnection):
    """
    POST endpoint to validate Microsoft Dynamics 365 source connection.
    """
    return await MicrosoftDynamics365Source.validate_connection(data)
