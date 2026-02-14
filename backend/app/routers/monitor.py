from fastapi import APIRouter
from app.models.schemas import MonitorResponse
from app.services.monitor_service import MonitorService

router = APIRouter(prefix="/monitor", tags=["monitor"])


@router.get("/", response_model=MonitorResponse)
def get_monitor_data():
    """
    Monitor画面用のデータを一括取得
    """
    service = MonitorService()
    return service.get_monitor_data()
