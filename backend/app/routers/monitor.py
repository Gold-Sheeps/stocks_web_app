from fastapi import APIRouter
from app.models.schemas import MonitorResponse
from app.services.monitor_service import MonitorService

router = APIRouter(prefix="/monitor", tags=["monitor"])


@router.get("/", response_model=MonitorResponse)
def get_monitor_data():
    """
    Monitor画面用のデータを一括取得
    - 市場指数
    - ウォッチリスト（RSI/出来高倍率等を含む）
    - アラート（±3%以上の変動銘柄）
    - 資産サマリー（プレースホルダ）
    """
    service = MonitorService()
    # 新しく実装したダッシュボード用メソッドを呼び出す
    return service.get_dashboard_data()
