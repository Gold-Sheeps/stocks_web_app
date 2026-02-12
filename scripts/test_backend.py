"""
FastAPI Monitor エンドポイントのテストスクリプト
"""
import requests
import json

BASE_URL = "http://localhost:8000"

def test_root():
    """ルートエンドポイントのテスト"""
    print("=" * 60)
    print("Test 1: Root Endpoint")
    print("=" * 60)
    
    response = requests.get(f"{BASE_URL}/")
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2, ensure_ascii=False)}")
    print()


def test_health():
    """ヘルスチェックエンドポイントのテスト"""
    print("=" * 60)
    print("Test 2: Health Check")
    print("=" * 60)
    
    response = requests.get(f"{BASE_URL}/health")
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2, ensure_ascii=False)}")
    print()


def test_monitor():
    """Monitorエンドポイントのテスト"""
    print("=" * 60)
    print("Test 3: Monitor Endpoint")
    print("=" * 60)
    
    response = requests.get(f"{BASE_URL}/api/v1/monitor/")
    print(f"Status: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print("\n【資産サマリー】")
        portfolio = data['portfolio']
        print(f"  総資産（JPY）: ¥{portfolio['total_value_jpy']:,.0f}")
        print(f"  総コスト（JPY）: ¥{portfolio['total_cost_jpy']:,.0f}")
        print(f"  損益（JPY）: ¥{portfolio['total_gain_loss_jpy']:,.0f}")
        print(f"  損益率: {portfolio['total_gain_loss_pct']:.2f}%")
        if portfolio.get('ytd_start_date'):
            print(f"  YTD起点: {portfolio['ytd_start_date']}")
        
        print("\n【市場指数】")
        for idx in data['indices']:
            print(f"  {idx['name']}: {idx['current_price']} ({idx['change_pct']:+.2f}%)")
        
        print("\n【為替レート】")
        for fx in data['fx_rates']:
            print(f"  {fx['name']}: {fx['current_price']} ({fx['change_pct']:+.2f}%)")
        
        print("\n【メタル】")
        for metal in data['metals']:
            print(f"  {metal['name']}: ${metal['current_price']} ({metal['change_pct']:+.2f}%)")
        
        print(f"\n最終更新: {data['last_updated']}")
    else:
        print(f"Error: {response.text}")
    
    print()


def test_docs():
    """APIドキュメンテーション確認"""
    print("=" * 60)
    print("Swagger UI")
    print("=" * 60)
    print(f"URL: {BASE_URL}/docs")
    print()


if __name__ == "__main__":
    print("\n🚀 FastAPI Backend Test\n")
    
    try:
        test_root()
        test_health()
        test_monitor()
        test_docs()
        
        print("=" * 60)
        print("✅ All tests completed!")
        print("=" * 60)
        
    except requests.exceptions.ConnectionError:
        print("❌ サーバーに接続できません")
        print("   backend/run.py を起動してください")
    except Exception as e:
        print(f"❌ エラー: {e}")
