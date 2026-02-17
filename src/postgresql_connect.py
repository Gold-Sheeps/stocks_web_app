import psycopg
from typing import Optional, List, Tuple, Any


class PostgreSQLConnect:
    """PostgreSQLデータベースに接続するためのクラス"""
    
    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        database: str = "postgres",
        user: str = "postgres",
        password: str = "test"
    ):
        """
        PostgreSQL接続の初期化
        
        Args:
            host: データベースホスト
            port: データベースポート
            database: データベース名
            user: ユーザー名
            password: パスワード
        """
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.connection = None
        self.cursor = None
    
    def connect(self) -> bool:
        """
        データベースに接続
        
        Returns:
            接続成功時True、失敗時False
        """
        try:
            # psycopg3の接続方法
            conninfo = f"host={self.host} port={self.port} dbname={self.database} user={self.user} password={self.password}"
            self.connection = psycopg.connect(conninfo)
            self.cursor = self.connection.cursor()
            print(f"[OK] データベース '{self.database}' に接続成功しました")
            return True
        except Exception as e:
            print(f"[ERR] 接続エラー: {e}")
            return False
    
    def disconnect(self) -> None:
        """データベースから切断"""
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()
            print("[OK] データベースから切断しました")
    
    def execute(self, query: str, params: Optional[Tuple] = None) -> Optional[List[Tuple]]:
        """
        SELECTクエリを実行してデータを取得
        
        Args:
            query: 実行するSQLクエリ
            params: クエリパラメータ
        
        Returns:
            クエリ結果のリスト
        """
        try:
            if params:
                self.cursor.execute(query, params)
            else:
                self.cursor.execute(query)
            results = self.cursor.fetchall()
            return results
        except Exception as e:
            print(f"[ERR] クエリ実行エラー: {e}")
            return None
    
    def command(self, query: str, params: Optional[Tuple] = None) -> bool:
        """
        INSERT/UPDATE/DELETEクエリを実行
        
        Args:
            query: 実行するSQLクエリ
            params: クエリパラメータ
        
        Returns:
            実行成功時True、失敗時False
        """
        try:
            if params:
                self.cursor.execute(query, params)
            else:
                self.cursor.execute(query)
            self.connection.commit()
            print(f"[OK] {self.cursor.rowcount} 行が更新されました")
            return True
        except Exception as e:
            self.connection.rollback()
            print(f"[ERR] 更新エラー: {e}")
            return False
    
    def __enter__(self):
        """コンテキストマネージャのenter"""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """コンテキストマネージャのexit"""
        self.disconnect()


# 使用例
if __name__ == "__main__":
    print("=" * 50)
    print("PostgreSQL 接続テスト")
    print("=" * 50)
    
    # データベースに接続
    db = PostgreSQLConnect(
        host="localhost",
        port=5432,
        database="postgres",
        user="postgres",
        password="test"
    )
    
    # 接続確認
    if db.connect():
        print("\n【接続成功】データベースに接続できました！\n")
        
        # 1. PostgreSQLバージョンを確認
        print("--- PostgreSQLバージョン ---")
        version = db.execute("SELECT version()")
        if version:
            print(f"バージョン: {version[0][0]}\n")
        
        # 2. 現在の日時を取得
        print("--- 現在の日時 ---")
        current_time = db.execute("SELECT NOW()")
        if current_time:
            print(f"現在時刻: {current_time[0][0]}\n")
        
        # 3. データベース内のテーブル一覧を取得
        print("--- テーブル一覧 (public schema) ---")
        tables = db.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
            ORDER BY table_name
            LIMIT 10
        """)
        if tables:
            if len(tables) == 0:
                print("テーブルが見つかりませんでした")
            else:
                for idx, table in enumerate(tables, 1):
                    print(f"{idx}. {table[0]}")
        else:
            print("テーブル情報を取得できませんでした")
        
        print("\n" + "=" * 50)
        
        # 切断
        db.disconnect()
    else:
        print("\n接続に失敗しました。")
        print("データベース設定を確認してください。")