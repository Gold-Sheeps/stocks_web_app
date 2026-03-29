# stocks_web_app README

この README は、まず最初に「フロント / バックエンド / API を一気に起動する手順」をすぐ実行できるように整理しています。

## 最初にやること（PowerShell / 一括起動）

### 1. 作業フォルダへ移動

```powershell
cd C:\Users\o_van\Desktop\stocks_web_app
```

### 2. 既存プロセス停止（任意だが推奨）

```powershell
.\stop_services.cmd
```

### 3. フロント・バック・API を一括起動

```powershell
.\start_services_visible.cmd
```

このコマンドで以下 3 つのウィンドウが起動します。

- `FRONTEND-3000`
- `BACKEND-8000`
- `API-8010`

### 4. アクセス先

- Frontend: `http://127.0.0.1:3000/screener.html`
- JP Screener (preset): `http://127.0.0.1:3000/screener_jp.html`
- Backend docs (8000): `http://127.0.0.1:8000/docs`
- API docs (8010): `http://127.0.0.1:8010/docs`

### 5. 起動確認（任意）

```powershell
netstat -ano | findstr :3000
netstat -ano | findstr :8000
netstat -ano | findstr :8010
```

`LISTENING` が出れば起動できています。

---

## Data Update画面での一括更新（まずは運用で使う）

起動後、`Data Update` 画面から更新をまとめて実行できます。

- 画面: `http://127.0.0.1:3000/data_update.html`
- `全選択` で対象をまとめて選択
- `Execute Update` で一括実行

現在の `Execute Update` は、選択内容に応じて以下をまとめて実行できます。

- 市況データ更新（指数 / FX / Metals / Crypto）
- 個別株価格更新（Watchlist / Portfolio 系）
- 指標再計算（indicator）
- RS 更新
- Sector Rotation 更新
- Fundamentals 更新
- CANSLIM 更新
- AI Prediction Batch（チェック時のみ、時間がかかります）

注意:
- `AI Prediction Batch` は長時間（約 1〜1.5 時間）かかる場合があります。

---

## 20年分データをDBへ一括投入する（初回・再構築用）

20年分の価格データを取得し、必要な計算系テーブルもまとめて更新するコマンドです。

### 実行コマンド（まずはこれ）

```powershell
cd C:\Users\o_van\Desktop\stocks_web_app
uv run python backend/scripts/update_db_full_20y.py
```

このコマンドで、デフォルトでは以下を実行します。

- `price_daily` に20年分の価格データを一括更新（yfinanceベース）
- `indicator_daily` の再計算
- `rs_ratings` の更新
- `sector_rotation` の更新
- `scripts/full_db_refresh.py` 連携（シンボル系 / fundamentals / CANSLIM も含む）

### 重要ポイント

- 価格データの実取得は `backend/scripts/refresh_market_data.py` が担当
- 20年一括スクリプトは、銘柄をチャンク分割して順次実行
- バッチ失敗時の再試行制御あり（`--batch-retries`）
- 銘柄ごとの取得リトライは `refresh_market_data.py` 側で実施（`--max-retries`）

### よく使うオプション

```powershell
# 10年分だけ
uv run python backend/scripts/update_db_full_20y.py --years 10

# 対象銘柄を限定
uv run python backend/scripts/update_db_full_20y.py --symbols "US:NVDA,US:QQQ,US:^SOX,US:SMH"

# 日本株（4桁コード）はプレフィックス省略でもJPとして解釈
uv run python backend/scripts/update_db_full_20y.py --symbols "7203,6758,JP:9984"

# 日本株リストファイル（1行1銘柄）を使う
uv run python backend/scripts/update_db_full_20y.py --symbols-file backend/config/universe_jp.txt --default-market JP

# バッチ再試行回数を増やす（デフォルト 2）
uv run python backend/scripts/update_db_full_20y.py --batch-retries 5

# 価格取得側の銘柄リトライ回数を増やす（デフォルト 3）
uv run python backend/scripts/update_db_full_20y.py --max-retries 5

# CANSLIM をスキップ
uv run python backend/scripts/update_db_full_20y.py --skip-canslim

# Fundamentals をスキップ
uv run python backend/scripts/update_db_full_20y.py --skip-fundamentals

# full_db_refresh 連携自体を止める（価格20年+指標/RS/sectorのみ）
uv run python backend/scripts/update_db_full_20y.py --no-run-full-refresh
```

### 実行結果の出力先

- 20年一括更新サマリ: `backend/ml_predictor_data/update_db_full_20y_YYYYMMDD_HHMMSS.json`
- 価格更新サマリ（チャンク毎）: `backend/ml_predictor_data/refresh_market_data_YYYYMMDD_HHMMSS.json`

---

## 日次/通常の更新（DBの最新化）

### 1. Data Update画面から実行（推奨）

普段の更新は `Data Update` 画面の `Execute Update` を使うのが簡単です。

### 2. コマンドで統合更新（CLI）

```powershell
cd C:\Users\o_van\Desktop\stocks_web_app
python scripts/full_db_refresh.py
```

このコマンドは以下を順に実行します（内部で既存スクリプトを呼び出し）。

- ETF / benchmark 更新
- market indices 更新
- sector constituents 更新
- monitor assets 更新
- individual stock prices 更新
- indicator 再計算
- RS 更新
- fundamentals 更新
- CANSLIM 更新

---

## 個別コマンド（必要なときだけ）

### 市場価格の増分更新（DB反映）

```powershell
python backend/scripts/refresh_market_data.py --symbols "US:NVDA,US:QQQ,US:^SOX,US:SMH" --source yfinance

# 日本株（4桁コード）も指定可能
python backend/scripts/refresh_market_data.py --symbols "7203,6758,JP:9984" --source yfinance
```

### 日次の個別株価格更新（既存フロー）

```powershell
python src/update_recent_prices.py
```

### 指標再計算

```powershell
python src/calculate_indicators_batch.py
```

### RS更新

```powershell
python backend/scripts/update_rs_rating.py
```

---

## 手動起動（スクリプトを使わない場合）

PowerShell を3つ開いて実行します。

### Frontend (3000)

```powershell
cd C:\Users\o_van\Desktop\stocks_web_app\frontend
C:\Users\o_van\AppData\Local\Programs\Python\Python312\python.exe -m http.server 3000
```

### Backend (8000)

```powershell
cd C:\Users\o_van\Desktop\stocks_web_app\backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### API (8010)

```powershell
cd C:\Users\o_van\Desktop\stocks_web_app\backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8010
```

---

## 前提（最小限）

- Windows + PowerShell
- PostgreSQL が起動していること
- `.venv` が作成済みで、バックエンド依存関係が入っていること
- `uv` を使う場合は `uv` がインストール済みであること

---

## 補足

- `start_services_visible.cmd` は 3000 / 8000 / 8010 の既存リスナーを停止してから起動します。
- `Data Update` 画面の `Execute Update` は、選択内容に応じて更新処理をまとめて実行します。
- 初回データ構築や大規模再取得は `backend/scripts/update_db_full_20y.py` を使ってください。
