# 株式市場チェッカー（Trading System）

総合トレード運用システム - US株のスクリーニング、モニタリング、ポートフォリオ管理

## 📋 目次

- [概要](#概要)
- [システム構成](#システム構成)
- [セットアップ](#セットアップ)
- [毎日の運用](#毎日の運用)
- [開発](#開発)

---

## 概要

このシステムは以下の機能を提供します：

- **Monitor**: 市場全体の監視
- **Rotation**: セクターローテーション分析
- **Screener**: CANSLIM戦略ベースの銘柄スクリーニング
- **Watchlist**: 注目銘柄のトラッキング
- **Stock Detail**: 個別銘柄の詳細分析
- **Portfolio**: ポートフォリオ管理

---

## システム構成

### バックエンド
- **言語**: Python 3.11+
- **フレームワーク**: FastAPI
- **データベース**: PostgreSQL
- **API**: RESTful API (OpenAPI/Swagger)

### フロントエンド
- **言語**: HTML/JavaScript (Vanilla)
- **サーバー**: Python SimpleHTTPServer
- **スタイル**: CSS (カスタム)

### データソース
- **価格データ**: yfinance (Yahoo Finance)
- **インジケーター**: 自動計算（SMA/EMA/RSI/MACD/RS Score等）

---

## セットアップ

### 1. データベース起動

```bash
# PostgreSQL起動（Dockerの場合）
docker start postgres_container
```

### 2. バックエンド起動

```bash
cd backend
uv run python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

APIドキュメント: http://localhost:8000/docs

### 3. フロントエンド起動

```bash
python frontend/server.py
```

フロントエンド: http://localhost:3000

---

## 毎日の運用

### 🔄 毎日実行すべきスクリプト

システムを最新に保つため、以下のスクリプトを **毎日** 実行してください。  
**Rundeck** または **cron** でスケジュール設定を推奨します。

#### 1. 価格データ更新（必須）

過去2週間の価格データを更新します。

```bash
cd c:\Users\o_van\Desktop\stocks_market_checker
python src/update_recent_prices.py
```

**オプション:**

```bash
# 特定期間の更新
python src/update_recent_prices.py --start 2026-02-01 --end 2026-02-09

# API遅延時間を調整（デフォルト: 0.5秒）
python src/update_recent_prices.py --delay 1.0

# ヘルプ表示
python src/update_recent_prices.py --help
```

**推奨実行時間:** 毎日 **朝6:00** （米国市場クローズ後）  
**所要時間:** 約20〜30分（5,000銘柄）

---

#### 2. インジケーター再計算（必須）

価格データ更新後、テクニカル指標を再計算します。

```bash
cd c:\Users\o_van\Desktop\stocks_market_checker
python src/calculate_indicators_batch.py
```

**計算される指標:**
- SMA (20, 50, 200)
- EMA (21, 50)
- RSI (14)
- MACD (12, 26, 9)
- ボリンジャーバンド
- ATR (14)
- RS Score（相対強度スコア）
- 52週高値距離
- 出来高移動平均

**推奨実行時間:** 価格データ更新の **直後**  
**所要時間:** 約10〜15分（5,000銘柄）

---

### 📅 Rundeck/Cron設定例

#### Rundeckジョブ設定

1. **ジョブ1: 価格データ更新**
   - 名前: `update_us_prices`
   - スケジュール: `0 6 * * *` （毎日6:00）
   - コマンド:
     ```bash
     cd c:\Users\o_van\Desktop\stocks_market_checker && python src/update_recent_prices.py
     ```

2. **ジョブ2: インジケーター計算**
   - 名前: `calculate_indicators`
   - スケジュール: `30 6 * * *` （毎日6:30）
   - コマンド:
     ```bash
     cd c:\Users\o_van\Desktop\stocks_market_checker && python src/calculate_indicators_batch.py
     ```

#### Linux Cron設定

```bash
# crontab -e で編集
0 6 * * * cd /path/to/stocks_market_checker && python src/update_recent_prices.py >> logs/update_prices.log 2>&1
30 6 * * * cd /path/to/stocks_market_checker && python src/calculate_indicators_batch.py >> logs/calculate_indicators.log 2>&1
```

---

## 初回セットアップ: データインポート

初めてシステムを構築する場合、以下を実行：

### 1. データベーステーブル作成

```bash
cd c:\Users\o_van\Desktop\stocks_market_checker
python src/stocks_price_get_update.py
```

### 2. US株の初回インポート（10年分）

```bash
cd c:\Users\o_van\Desktop\stocks_market_checker
python src/bulk_import_us_stocks.py
```

**所要時間:** 約1.5〜2時間（5,000銘柄 × 10年）  
**注意:** このスクリプトは初回のみ実行。日次更新は `update_recent_prices.py` を使用。

### 3. インジケーター初回計算

```bash
python src/calculate_indicators_batch.py
```

---

## 開発

### API仕様

- **BASE URL**: `http://localhost:8000/api/v1`
- **認証**: なし（開発環境）
- **ドキュメント**: http://localhost:8000/docs

### 主要エンドポイント

| エンドポイント | メソッド | 説明 |
|---------------|---------|------|
| `/monitor` | GET | 市場全体のモニタリングデータ |
| `/rotation/sectors` | GET | セクターローテーションデータ |
| `/screener/scan` | GET | スクリーニング結果（スコア順） |
| `/stock/{symbol}` | GET | 個別銘柄詳細 |
| `/portfolio/holdings` | GET | ポートフォリオ保有銘柄 |

### ディレクトリ構造

```
stocks_market_checker/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPIアプリケーション
│   │   ├── routers/             # APIルーター
│   │   ├── services/            # ビジネスロジック
│   │   └── models/              # データモデル
│   └── requirements.txt
├── frontend/
│   ├── monitor.html
│   ├── rotation.html
│   ├── screener.html
│   ├── watchlist.html
│   ├── stock_detail.html
│   ├── portfolio.html
│   └── server.py
├── src/
│   ├── bulk_import_us_stocks.py        # 初回インポート（10年分）
│   ├── update_recent_prices.py         # 日次価格更新
│   ├── calculate_indicators_batch.py   # インジケーター計算
│   └── postgresql_connect.py           # DB接続
└── README.md
```

---

## トラブルシューティング

### データが古い

毎日のスクリプト実行を忘れていないか確認：

```bash
# 最新データの日付を確認
python -c "import sys; sys.path.insert(0, 'src'); import postgresql_connect; db = postgresql_connect.PostgreSQLConnect(); db.connect(); result = db.execute('SELECT MAX(trading_date) FROM price_daily'); print(f'Latest date: {result[0][0]}'); db.disconnect()"
```

### インジケーターが計算されない

```bash
# indicator_dailyテーブルのレコード数確認
python -c "import sys; sys.path.insert(0, 'src'); import postgresql_connect; db = postgresql_connect.PostgreSQLConnect(); db.connect(); result = db.execute('SELECT COUNT(*) FROM indicator_daily'); print(f'{result[0][0]} indicator records'); db.disconnect()"
```

### Screenerに結果が表示されない

1. インジケーターが計算されているか確認
2. バックエンドが起動しているか確認（http://localhost:8000/docs）
3. ブラウザのコンソールでエラーを確認

---

## ライセンス

Private Project

---

## PowerShell Quick Start (Windows)

### 0. Working Directory

```powershell
cd C:\Users\o_van\Desktop\stocks_web_app
```

### 1. Stop All Services

```powershell
.\stop_services.cmd
```

### 2. Start All Services (Visible Windows)

```powershell
.\start_services_visible.cmd
```

This opens 3 separate windows and keeps them visible:
- `FRONTEND-3000`
- `BACKEND-8000`
- `API-8010`

### 3. Health Checks

```powershell
netstat -ano | findstr :3000
netstat -ano | findstr :8000
netstat -ano | findstr :8010
```

If each port shows `LISTENING`, services are up.

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:3000/screener.html
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8010/health
```

Expected: StatusCode `200`.

### 4. Open in Browser

- Screener: `http://127.0.0.1:3000/screener.html`
- Stock Detail: `http://127.0.0.1:3000/stock_detail.html`

If UI looks stale, run hard reload: `Ctrl+F5`.

### 5. Manual Start (without scripts)

Open 3 PowerShell windows and run:

Window 1 (Frontend):
```powershell
cd C:\Users\o_van\Desktop\stocks_web_app\frontend
C:\Users\o_van\AppData\Local\Programs\Python\Python312\python.exe -m http.server 3000
```

Window 2 (Backend 8000):
```powershell
cd C:\Users\o_van\Desktop\stocks_web_app\backend
..\ .venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Window 3 (API 8010):
```powershell
cd C:\Users\o_van\Desktop\stocks_web_app\backend
..\ .venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8010
```

Note: in the two commands above, use `..\.venv\Scripts\python.exe` (no space).

PowerSHellで以下を実行
cd C:\Users\o_van\Desktop\stocks_web_app                                                                      
.\start_services_visible.cmd 
---

## NVDA Backtest (DB-only)

Run from repository root:

```powershell
python backend/scripts/run_backtest_nvda.py --asof 2026-02-16 --start 2019-01-01 --end 2026-02-16 --horizon 15 --flat_band 2.0 --calibration sigmoid
```

Notes:
- Backtest/prediction features read market data from DB only (`price_daily`).
- The script runs a DB freshness check (`trade_date <= as_of`) for `US:NVDA`, `US:QQQ`, `US:^SOX`, `US:SMH`.
- If `max_date` is older than `--asof`, it prints warnings but still runs.

Output:
- Console summary (accuracy, macro_f1, logloss, brier, ece, interval_coverage).
- JSON file: `backend/ml_predictor_data/backtest_nvda_YYYYMMDD.json`.

---

## Market Data Refresh (External -> DB, then DB-only Inference)

Update DB incrementally from external source (yfinance):

```powershell
python backend/scripts/refresh_market_data.py --symbols "US:NVDA,US:QQQ,US:^SOX,US:SMH" --source yfinance
```

Optional:

```powershell
python backend/scripts/refresh_market_data.py --symbols "US:NVDA,US:QQQ,US:^SOX,US:SMH" --source yfinance --db-only-check
python backend/scripts/refresh_market_data.py --symbols "US:NVDA,US:QQQ,US:^SOX,US:SMH" --source yfinance --max-age-days 1
```

Run backtest (DB-only):

```powershell
python backend/scripts/run_backtest_nvda.py --asof 2026-02-16 --start 2019-01-01 --end 2026-02-16 --horizon 15 --flat_band 2.0 --calibration sigmoid --db-check
```

Policy:
- External API is used only by `backend/scripts/refresh_market_data.py` for ETL/refresh.
- Prediction/training/backtest (`prediction_service.py`, `run_backtest_nvda.py`) use DB data only.
