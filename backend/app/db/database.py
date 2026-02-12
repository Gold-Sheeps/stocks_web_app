import sys
import os

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

import psycopg
from typing import Optional
from app.core.config import settings


class Database:
    """データベース接続クラス"""
    
    def __init__(self):
        self.connection: Optional[psycopg.Connection] = None
        self.cursor: Optional[psycopg.Cursor] = None
    
    def connect(self) -> bool:
        """データベースに接続"""
        try:
            conninfo = f"host={settings.postgres_host} port={settings.postgres_port} dbname={settings.postgres_db} user={settings.postgres_user} password={settings.postgres_password}"
            print(f"DB Connect: {conninfo} (PID: {os.getpid()})", flush=True)
            self.connection = psycopg.connect(conninfo)
            self.cursor = self.connection.cursor()
            return True
        except Exception as e:
            print(f"Database connection error: {e}")
            return False
    
    def disconnect(self) -> None:
        """データベースから切断"""
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()
    
    def execute_query(self, query: str, params: Optional[tuple] = None) -> Optional[list]:
        """SELECTクエリを実行"""
        try:
            print(f"DB DEBUG: Query: {query} Params: {params}") 
            if params:
                self.cursor.execute(query, params)
            else:
                self.cursor.execute(query)
            return self.cursor.fetchall()
        except Exception as e:
            self.connection.rollback()
            print(f"Query execution error: {e}")
            return None
    
    def execute_command(self, query: str, params: Optional[tuple] = None) -> bool:
        """INSERT/UPDATE/DELETEクエリを実行"""
        try:
            if params:
                self.cursor.execute(query, params)
            else:
                self.cursor.execute(query)
            self.connection.commit()
            return True
        except Exception as e:
            self.connection.rollback()
            print(f"Command execution error: {e}")
            return False
    
    def __enter__(self):
        """コンテキストマネージャ"""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """コンテキストマネージャ"""
        self.disconnect()


def get_db():
    """依存性注入用のDB接続ジェネレーター"""
    db = Database()
    db.connect()
    try:
        yield db
    finally:
        db.disconnect()
