from fastapi import APIRouter
from app.models.schemas import RotationResponse
from app.services.rotation_service import RotationService

router = APIRouter(prefix="/rotation", tags=["rotation"])


@router.get("/sectors", response_model=RotationResponse)
def get_rotation_sectors():
    """Get sector rotation data"""
    service = RotationService()
    return service.get_sector_rotation()
