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
