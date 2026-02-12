from fastapi import APIRouter
from app.models.schemas import MonitorResponse
from app.services.monitor_service import MonitorService

router = APIRouter(prefix="/monitor", tags=["monitor"])


@router.get("/", response_model=MonitorResponse)
def get_monitor_data():
    """
    Monitor画面用のデータを取得
    - 資産サマリー（取得来/YTD損益）
    - 市場指数（VIX、ダウ、日経等）
    - 為替レート
    - メタル価格
    """
    service = MonitorService()
    return service.get_monitor_data()
