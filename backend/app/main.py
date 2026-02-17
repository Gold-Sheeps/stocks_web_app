from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings

from app.routers import (
    monitor,
    portfolio,
    prediction,
    rotation,
    sector,
    screener,
    stock_detail,
    watchlist,
    system,
    override,
)

app = FastAPI(
    title="Stock Trading System API",
    description="Comprehensive Trading System API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ... (omitted)

# ルーター登録
app.include_router(monitor.router, prefix=settings.api_v1_prefix)
app.include_router(prediction.router, prefix=settings.api_v1_prefix)
app.include_router(stock_detail.router, prefix=settings.api_v1_prefix)
app.include_router(portfolio.router, prefix=settings.api_v1_prefix)
app.include_router(screener.router, prefix=settings.api_v1_prefix)
app.include_router(rotation.router, prefix=settings.api_v1_prefix)
app.include_router(sector.router, prefix=settings.api_v1_prefix)
app.include_router(watchlist.router, prefix=settings.api_v1_prefix)
app.include_router(system.router, prefix=settings.api_v1_prefix)
app.include_router(override.router, prefix=settings.api_v1_prefix)


@app.on_event("startup")
async def startup_event():
    from app.core.config import settings

    print(
        f"Startup: Connecting to DB: {settings.postgres_db} at {settings.postgres_host}"
    )


from fastapi.staticfiles import StaticFiles
import os

# Mount frontend directory
frontend_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "frontend"
)
if os.path.exists(frontend_path):
    app.mount(
        "/frontend", StaticFiles(directory=frontend_path, html=True), name="frontend"
    )
else:
    print(f"Warning: Frontend directory not found at {frontend_path}")


@app.get("/")
def root():
    """ヘルスチェック"""
    return {
        "status": "ok",
        "message": "Stock Trading System API is running",
    }


@app.get("/health")
def health_check():
    """ヘルスチェック"""
    return {"status": "healthy"}
