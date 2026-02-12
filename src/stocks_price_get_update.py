import postgresql_connect


def create_all_tables(db):
    """
    株式市場データ管理用の全テーブルを作成
    """
    
    # 1) instruments（銘柄マスタ）
    print("Creating table: instruments...")
    db.command("""
        CREATE TABLE IF NOT EXISTS instruments (
            symbol_key VARCHAR(20) PRIMARY KEY,
            market VARCHAR(10) NOT NULL CHECK (market IN ('US', 'JP', 'MACRO')),
            name VARCHAR(255) NOT NULL,
            currency VARCHAR(3) NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 2) price_daily（価格：日足OHLCV）
    print("Creating table: price_daily...")
    db.command("""
        CREATE TABLE IF NOT EXISTS price_daily (
            symbol_key VARCHAR(20) NOT NULL,
            trading_date DATE NOT NULL,
            open NUMERIC(12, 4) NOT NULL,
            high NUMERIC(12, 4) NOT NULL,
            low NUMERIC(12, 4) NOT NULL,
            close NUMERIC(12, 4) NOT NULL,
            adj_close NUMERIC(12, 4),
            volume BIGINT,
            source VARCHAR(50),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol_key, trading_date),
            FOREIGN KEY (symbol_key) REFERENCES instruments(symbol_key) ON DELETE CASCADE
        )
    """)
    
    # 3) indicator_daily（計算済み指標：日次）
    print("Creating table: indicator_daily...")
    db.command("""
        CREATE TABLE IF NOT EXISTS indicator_daily (
            symbol_key VARCHAR(20) NOT NULL,
            trading_date DATE NOT NULL,
            sma20 NUMERIC(12, 4),
            sma50 NUMERIC(12, 4),
            sma200 NUMERIC(12, 4),
            ema21 NUMERIC(12, 4),
            ema50 NUMERIC(12, 4),
            rsi14 NUMERIC(5, 2),
            macd NUMERIC(12, 4),
            macd_signal NUMERIC(12, 4),
            macd_hist NUMERIC(12, 4),
            pivot NUMERIC(12, 4),
            buy_zone_upper NUMERIC(12, 4),
            accum_dist NUMERIC(20, 4),
            vsa_label VARCHAR(50),
            institution_score NUMERIC(5, 2),
            institution_flag BOOLEAN,
            high_52w NUMERIC(12, 4),
            low_52w NUMERIC(12, 4),
            dist_to_52w_high_pct NUMERIC(5, 2),
            rs_score NUMERIC(5, 2),
            atr14 NUMERIC(12, 4),
            calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            version VARCHAR(10),
            PRIMARY KEY (symbol_key, trading_date),
            FOREIGN KEY (symbol_key) REFERENCES instruments(symbol_key) ON DELETE CASCADE
        )
    """)
    
    # 4) signal_daily（シグナル：日次フラグ＋否定条件）
    print("Creating table: signal_daily...")
    db.command("""
        CREATE TABLE IF NOT EXISTS signal_daily (
            symbol_key VARCHAR(20) NOT NULL,
            trading_date DATE NOT NULL,
            gc_dc_state VARCHAR(20),
            macd_cross_state VARCHAR(20),
            pattern_hs_state VARCHAR(20),
            pattern_double_top_state VARCHAR(20),
            overheat_state VARCHAR(20),
            invalidation_json JSONB,
            calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            version VARCHAR(10),
            PRIMARY KEY (symbol_key, trading_date),
            FOREIGN KEY (symbol_key) REFERENCES instruments(symbol_key) ON DELETE CASCADE
        )
    """)
    
    # 5) themes（テーママスタ）
    print("Creating table: themes...")
    db.command("""
        CREATE TABLE IF NOT EXISTS themes (
            theme_key VARCHAR(50) PRIMARY KEY,
            market VARCHAR(10) NOT NULL,
            theme_name VARCHAR(255) NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 6) instrument_themes（銘柄×テーマ：多対多）
    print("Creating table: instrument_themes...")
    db.command("""
        CREATE TABLE IF NOT EXISTS instrument_themes (
            symbol_key VARCHAR(20) NOT NULL,
            theme_key VARCHAR(50) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol_key, theme_key),
            FOREIGN KEY (symbol_key) REFERENCES instruments(symbol_key) ON DELETE CASCADE,
            FOREIGN KEY (theme_key) REFERENCES themes(theme_key) ON DELETE CASCADE
        )
    """)
    
    # 7) theme_rank_daily（Rotation用：テーマ順位）
    print("Creating table: theme_rank_daily...")
    db.command("""
        CREATE TABLE IF NOT EXISTS theme_rank_daily (
            theme_key VARCHAR(50) NOT NULL,
            trading_date DATE NOT NULL,
            rank INTEGER,
            score NUMERIC(10, 4),
            ret_5d NUMERIC(8, 4),
            ret_21d NUMERIC(8, 4),
            ret_63d NUMERIC(8, 4),
            delta_rank INTEGER,
            delta_score NUMERIC(10, 4),
            calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            version VARCHAR(10),
            PRIMARY KEY (theme_key, trading_date),
            FOREIGN KEY (theme_key) REFERENCES themes(theme_key) ON DELETE CASCADE
        )
    """)
    
    # 8) watchlist_items（Watchlist）
    print("Creating table: watchlist_items...")
    db.command("""
        CREATE TABLE IF NOT EXISTS watchlist_items (
            watchlist_item_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            symbol_key VARCHAR(20) NOT NULL,
            status VARCHAR(20) CHECK (status IN ('RESEARCH', 'SETUP', 'ACTION')),
            memo TEXT,
            is_alert_enabled BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (symbol_key) REFERENCES instruments(symbol_key) ON DELETE CASCADE
        )
    """)
    
    # 9) alert_rules（アラート定義）
    print("Creating table: alert_rules...")
    db.command("""
        CREATE TABLE IF NOT EXISTS alert_rules (
            alert_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            symbol_key VARCHAR(20) NOT NULL,
            rule_type VARCHAR(50) NOT NULL,
            params_json JSONB,
            is_enabled BOOLEAN DEFAULT TRUE,
            cooldown_policy VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (symbol_key) REFERENCES instruments(symbol_key) ON DELETE CASCADE
        )
    """)
    
    # 10) alert_events（アラート発火ログ）
    print("Creating table: alert_events...")
    db.command("""
        CREATE TABLE IF NOT EXISTS alert_events (
            event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            alert_id UUID NOT NULL,
            symbol_key VARCHAR(20) NOT NULL,
            trading_date DATE NOT NULL,
            fired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            fire_condition_json JSONB,
            clear_condition_json JSONB,
            suppressed BOOLEAN DEFAULT FALSE,
            resolved_at TIMESTAMP,
            FOREIGN KEY (alert_id) REFERENCES alert_rules(alert_id) ON DELETE CASCADE,
            FOREIGN KEY (symbol_key) REFERENCES instruments(symbol_key) ON DELETE CASCADE
        )
    """)
    
    # 11) trades（取引台帳）
    print("Creating table: trades...")
    db.command("""
        CREATE TABLE IF NOT EXISTS trades (
            trade_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            symbol_key VARCHAR(20) NOT NULL,
            side VARCHAR(4) CHECK (side IN ('BUY', 'SELL')),
            trade_date DATE NOT NULL,
            shares NUMERIC(12, 4) NOT NULL,
            price NUMERIC(12, 4) NOT NULL,
            currency VARCHAR(3) NOT NULL,
            fx_rate_to_jpy NUMERIC(10, 4),
            memo TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (symbol_key) REFERENCES instruments(symbol_key) ON DELETE CASCADE
        )
    """)
    
    # 12) data_overrides（欠損補完）
    print("Creating table: data_overrides...")
    db.command("""
        CREATE TABLE IF NOT EXISTS data_overrides (
            override_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            symbol_key VARCHAR(20) NOT NULL,
            field_name VARCHAR(100) NOT NULL,
            value_numeric NUMERIC(20, 4),
            value_text TEXT,
            value_date DATE,
            effective_from DATE,
            expires_on DATE,
            source VARCHAR(100),
            memo TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (symbol_key) REFERENCES instruments(symbol_key) ON DELETE CASCADE
        )
    """)
    
    print("\n✅ All tables created successfully!")


if __name__ == "__main__":
    print("=" * 60)
    print("株式市場データベース スキーマ作成")
    print("=" * 60)
    
    db = postgresql_connect.PostgreSQLConnect()
    
    if db.connect():
        try:
            create_all_tables(db)
        except Exception as e:
            print(f"\n❌ エラーが発生しました: {e}")
        finally:
            db.disconnect()
    else:
        print("データベース接続に失敗しました")
