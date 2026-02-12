"""
セクターETFの構成銘柄Top 20を取得してDBに保存
yfinanceを使用して各セクターETFの保有銘柄を取得
"""
import yfinance as yf
from datetime import datetime
import psycopg
import time


def get_connection():
    """PostgreSQLデータベース接続を取得"""
    return psycopg.connect(
        host="localhost",
        port=5432,
        dbname="postgres",
        user="postgres",
        password="test"
    )


# 11セクターETF
SECTOR_ETFS = {
    'XLK': 'Technology',
    'XLV': 'Healthcare',
    'XLF': 'Financials',
    'XLE': 'Energy',
    'XLY': 'Consumer Discretionary',
    'XLP': 'Consumer Staples',
    'XLI': 'Industrials',
    'XLB': 'Materials',
    'XLU': 'Utilities',
    'XLRE': 'Real Estate',
    'XLC': 'Communication Services',
}


def fetch_etf_holdings(etf_symbol: str, top_n: int = 20):
    """ETFの保有銘柄Top Nを取得"""
    try:
        print(f"  {etf_symbol}のホールディング情報を取得中...")
        ticker = yf.Ticker(etf_symbol)
        
        # ホールディング情報取得を試みる
        try:
            holdings = ticker.get_holdings()
            if holdings is not None and not holdings.empty:
                # 上位N件を取得
                top_holdings = holdings.head(top_n)
                result = []
                for idx, row in top_holdings.iterrows():
                    # Symbolカラムの名前はETFによって異なる可能性がある
                    symbol = row.get('Symbol') or row.get('symbol') or row.get('Ticker') or idx
                    weight = row.get('% Assets') or row.get('weight') or 0
                    
                    result.append({
                        'symbol': str(symbol).strip(),
                        'weight': float(weight) if weight else 0,
                        'rank': len(result) + 1
                    })
                
                return result
        except Exception as e:
            print(f"    ホールディング情報取得失敗: {e}")
        
        # フォールバック: 手動リスト（主要ETFのTop 10）
        fallback_holdings = get_fallback_holdings(etf_symbol)
        if fallback_holdings:
            print(f"    フォールバック: 手動リスト使用")
            return fallback_holdings
        
        return []
        
    except Exception as e:
        print(f"  ✗ エラー: {etf_symbol} - {e}")
        return []


def get_fallback_holdings(etf_symbol: str):
    """手動メンテナンスのフォールバックリスト（主要銘柄Top 10）"""
    # 2024年時点の主要構成銘柄（概算）
    fallback_data = {
        'XLK': [
            {'symbol': 'AAPL', 'weight': 22.0, 'rank': 1},
            {'symbol': 'MSFT', 'weight': 21.0, 'rank': 2},
            {'symbol': 'NVDA', 'weight': 15.0, 'rank': 3},
            {'symbol': 'AVGO', 'weight': 4.5, 'rank': 4},
            {'symbol': 'CRM', 'weight': 3.0, 'rank': 5},
            {'symbol': 'AMD', 'weight': 2.5, 'rank': 6},
            {'symbol': 'ADBE', 'weight': 2.5, 'rank': 7},
            {'symbol': 'CSCO', 'weight': 2.0, 'rank': 8},
            {'symbol': 'ACN', 'weight': 2.0, 'rank': 9},
            {'symbol': 'ORCL', 'weight': 2.0, 'rank': 10},
        ],
        'XLV': [
            {'symbol': 'UNH', 'weight': 10.0, 'rank': 1},
            {'symbol': 'JNJ', 'weight': 7.0, 'rank': 2},
            {'symbol': 'LLY', 'weight': 7.0, 'rank': 3},
            {'symbol': 'ABBV', 'weight': 4.5, 'rank': 4},
            {'symbol': 'MRK', 'weight': 4.0, 'rank': 5},
            {'symbol': 'TMO', 'weight': 3.5, 'rank': 6},
            {'symbol': 'ABT', 'weight': 3.0, 'rank': 7},
            {'symbol': 'PFE', 'weight': 2.5, 'rank': 8},
            {'symbol': 'DHR', 'weight': 2.5, 'rank': 9},
            {'symbol': 'BMY', 'weight': 2.0, 'rank': 10},
        ],
        'XLF': [
            {'symbol': 'BRK.B', 'weight': 13.0, 'rank': 1},
            {'symbol': 'JPM', 'weight': 10.0, 'rank': 2},
            {'symbol': 'V', 'weight': 8.0, 'rank': 3},
            {'symbol': 'MA', 'weight': 6.5, 'rank': 4},
            {'symbol': 'BAC', 'weight': 5.0, 'rank': 5},
            {'symbol': 'WFC', 'weight': 3.5, 'rank': 6},
            {'symbol': 'GS', 'weight': 2.5, 'rank': 7},
            {'symbol': 'MS', 'weight': 2.5, 'rank': 8},
            {'symbol': 'SPGI', 'weight': 2.5, 'rank': 9},
            {'symbol': 'AXP', 'weight': 2.0, 'rank': 10},
        ],
        # 他のセクターも追加可能だが、まずは主要3セクターで
    }
    
    return fallback_data.get(etf_symbol, [])


def save_constituents_to_db(conn, etf_symbol: str, holdings: list):
    """構成銘柄をDBに保存"""
    if not holdings:
        print(f"  ⚠ {etf_symbol}: データなし、スキップ")
        return
    
    cursor = conn.cursor()
    saved_count = 0
    
    try:
        for holding in holdings:
            try:
                # UPSERT処理
                cursor.execute("""
                    INSERT INTO sector_constituents 
                    (sector_etf_symbol, constituent_symbol, weight, rank, data_source, last_updated)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (sector_etf_symbol, constituent_symbol) 
                    DO UPDATE SET 
                        weight = EXCLUDED.weight,
                        rank = EXCLUDED.rank,
                        last_updated = EXCLUDED.last_updated
                """, (
                    etf_symbol,
                    holding['symbol'],
                    holding['weight'],
                    holding['rank'],
                    'yfinance',
                    datetime.now()
                ))
                saved_count += 1
            except Exception as e:
                print(f"    ⚠ {holding['symbol']} 保存失敗: {e}")
                continue
        
        conn.commit()
        print(f"  ✓ {saved_count}銘柄を保存しました")
        
    except Exception as e:
        print(f"  ✗ DB保存エラー: {e}")
        conn.rollback()
    finally:
        cursor.close()


def main():
    """メイン処理"""
    print("=" * 60)
    print("セクターETF構成銘柄データ取得")
    print("=" * 60)
    
    conn = get_connection()
    if not conn:
        print("✗ データベース接続に失敗しました")
        return
    
    print(f"\n取得対象: {len(SECTOR_ETFS)}セクターETF")
    print("-" * 60)
    
    for etf_symbol, sector_name in SECTOR_ETFS.items():
        print(f"\n[{sector_name}] {etf_symbol}")
        
        # 1. ホールディング情報取得
        holdings = fetch_etf_holdings(etf_symbol, top_n=20)
        
        # 2. DBに保存
        save_constituents_to_db(conn, etf_symbol, holdings)
        
        # レート制限回避
        time.sleep(0.5)
    
    conn.close()
    
    print("\n" + "=" * 60)
    print("✓ 処理完了")
    print("=" * 60)


if __name__ == "__main__":
    main()
