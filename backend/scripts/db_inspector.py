#!/usr/bin/env python3
import os
import sys
import json
import psycopg
from datetime import datetime, date
from decimal import Decimal
from dotenv import load_dotenv

# --- 環境設定 ---
# 実行ディレクトリをbackendに合わせるための調整
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, BASE_DIR)
load_dotenv(os.path.join(BASE_DIR, '.env'))

# --- Django互換モード (ご要望に合わせて) ---
# 本プロジェクトはFastAPIベースですが、Django環境下でも動くようボイラープレートを記載します
def setup_django():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'app.core.config')
    try:
        import django
        django.setup()
        print("[System] Django detected and initialized.")
    except ImportError:
        pass

# JSONシリアライズ用ヘルパー
def json_serial(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Type {type(obj)} not serializable")

class DBInspector:
    def __init__(self):
        self.host = os.getenv("POSTGRES_HOST", "localhost")
        self.port = os.getenv("POSTGRES_PORT", "5432")
        self.db = os.getenv("POSTGRES_DB", "postgres")
        self.user = os.getenv("POSTGRES_USER", "postgres")
        self.pw = os.getenv("POSTGRES_PASSWORD", "test")
        self.conn = None

    def connect(self):
        conninfo = f"host={self.host} port={self.port} dbname={self.db} user={self.user} password={self.pw}"
        self.conn = psycopg.connect(conninfo)

    def close(self):
        if self.conn:
            self.conn.close()

    def run_query(self, query):
        with self.conn.cursor() as cur:
            cur.execute(query)
            try:
                columns = [desc[0] for desc in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
            except:
                return None

    def investigate(self):
        print("="*60)
        print(" DATABASE INVESTIGATION REPORT ")
        print("="*60)

        # 1. 全テーブル一覧とレコード数
        print("\n[1] Table Overview (Table Name & Row Counts)")
        print("-" * 50)
        table_query = """
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_type = 'BASE TABLE';
        """
        tables = self.run_query(table_query)
        relevant_tables = []
        keywords = ['monitor', 'dashboard', 'index', 'market', 'watch', 'alert', 'event', 'symbol', 'price']

        for t in tables:
            tname = t['table_name']
            count_res = self.run_query(f"SELECT COUNT(*) as cnt FROM {tname}")
            count = count_res[0]['cnt'] if count_res else 0
            print(f"- {tname.ljust(30)} : {count} rows")
            
            # キーワードマッチング
            if any(k in tname.lower() for k in keywords):
                relevant_tables.append(tname)

        # 2. テーブル構造の詳細 (Monitor関連)
        print("\n[2] Relevant Table Structures (Schema Analysis)")
        print("-" * 50)
        if not relevant_tables:
            print("No matching tables found for keywords.")
        else:
            for tname in relevant_tables:
                print(f"\nTABLE: {tname}")
                struct_query = f"""
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_name = '{tname}'
                    ORDER BY ordinal_position;
                """
                columns = self.run_query(struct_query)
                for col in columns:
                    print(f"  {col['column_name'].ljust(20)} | {col['data_type'].ljust(15)} | Nullable: {col['is_nullable']}")

        # 3. サンプルデータの抽出 (JSON形式)
        print("\n[3] Latest Data Samples (Top 3 records)")
        print("-" * 50)
        # 優先度の高いテーブルがあれば先に、なければデータが入っている全テーブルから抽出
        priority_keywords = ['watch', 'index', 'price']
        sample_targets = sorted(
            [t['table_name'] for t in tables], 
            key=lambda x: any(pk in x.lower() for pk in priority_keywords), 
            reverse=True
        )

        for tname in sample_targets:
            # データがあるものだけ表示
            count_res = self.run_query(f"SELECT COUNT(*) as cnt FROM {tname}")
            if not count_res or count_res[0]['cnt'] == 0:
                continue
            
            print(f"\nSAMPLE DATA: {tname}")
            # order by を推測 (id, created_at, trading_date など)
            struct = self.run_query(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{tname}'")
            cols = [s['column_name'] for s in struct]
            sort_col = next((c for c in cols if c in ['id', 'created_at', 'trading_date', 'updated_at', 'date']), cols[0])
            
            sample_query = f"SELECT * FROM {tname} ORDER BY {sort_col} DESC LIMIT 3"
            samples = self.run_query(sample_query)
            if samples:
                print(json.dumps(samples, indent=2, default=json_serial, ensure_ascii=False))

if __name__ == "__main__":
    setup_django()
    inspector = DBInspector()
    try:
        inspector.connect()
        inspector.investigate()
    except Exception as e:
        print(f"\n[Error] Investigation failed: {e}")
    finally:
        inspector.close()
    print("\n" + "="*60)
    print(" END OF REPORT ")
    print("="*60)
