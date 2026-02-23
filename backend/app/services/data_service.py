
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from decimal import Decimal
import time
import json
import traceback
from typing import List, Dict, Optional
import re
import subprocess
import sys
from pathlib import Path

from app.db.database import Database

class DataService:
    """データ取得・更新サービス (Symbol Key Unified Version)"""

    def __init__(self):
        self.db = Database()

    def log_system_event(self, job_name: str, status: str, message: str = None, details: dict = None):
        """システムログに記録"""
        try:
            self.db.connect()
            query = """
                INSERT INTO system_logs (job_name, status, message, details)
                VALUES (%s, %s, %s, %s)
            """
            self.db.execute_command(query, (
                job_name, 
                status, 
                message, 
                json.dumps(details) if details else None
            ))
        except Exception as e:
            print(f"Failed to log system event: {e}")
        finally:
            self.db.disconnect()

    def _get_symbol_key(self, raw_symbol: str) -> str:
        """Raw Symbolから統一Symbol Keyを生成 (US/JPのみ)"""
        # Rule based unification
        # JP: ^N225 or 4 digits
        if raw_symbol == "^N225" or (raw_symbol.isdigit() and len(raw_symbol) == 4):
            return f"JP:{raw_symbol}"
        else:
            # Everything else (Index, ETF, FX, Metal, US Stock) -> US:
            return f"US:{raw_symbol}"

    def _run_script(self, cmd: List[str], cwd: Path, timeout: int | None = None) -> dict:
        """Run a subprocess and return a small structured result for logs/UI."""
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        tail_lines = (stdout.splitlines() + stderr.splitlines())[-40:]
        return {
            "returncode": proc.returncode,
            "command": cmd,
            "output_tail": tail_lines,
        }

    def _get_latest_price_date(self):
        self.db.connect()
        try:
            row = self.db.execute_query("SELECT MAX(trading_date) FROM price_daily")
            return row[0][0] if row and row[0][0] else None
        finally:
            self.db.disconnect()

    def _run_ai_prediction_batch(self, repo_root: Path, max_tickers: int = 500) -> dict:
        latest_date = self._get_latest_price_date()
        if latest_date is None:
            return {
                "status": "skipped",
                "reason": "No price_daily data",
            }

        script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_daily_predictions.py"
        cmd = [
            sys.executable,
            str(script_path),
            "--asof",
            latest_date.isoformat(),
            "--max-tickers",
            str(int(max_tickers)),
        ]
        result = self._run_script(cmd, cwd=repo_root, timeout=7200)
        result["asof"] = latest_date.isoformat()
        result["status"] = "success" if result["returncode"] == 0 else "failed"
        return result

    def update_all_data(self, range_days: int = 14, targets: List[str] = None):
        """Data Update画面から統合バッチ(full_db_refresh.py)を実行"""
        targets = targets or []
        job_id = f"update_{int(time.time())}"
        self.log_system_event(
            "Data Update",
            "RUNNING",
            f"Started unified refresh for targets={targets} range={range_days}",
            {"job_id": job_id, "targets": targets, "range_days": range_days},
        )

        try:
            repo_root = Path(__file__).resolve().parents[3]
            script_path = repo_root / "scripts" / "full_db_refresh.py"
            if not script_path.exists():
                raise FileNotFoundError(f"Batch script not found: {script_path}")

            # Map UI targets to unified batch skip flags.
            selected = set(targets)
            has_etf_like = any(t in selected for t in ["Indices", "FX", "Metal", "Crypto", "Sector"])
            has_stocks = "Stocks" in selected
            has_sector = "Sector" in selected
            has_canslim = "CANSLIM" in selected
            has_fundamentals = "Fundamentals" in selected
            has_ai_prediction = ("AI" in selected) or ("AI Prediction" in selected)

            cmd = [
                sys.executable,
                str(script_path),
                "--backfill-days",
                str(range_days),
                "--delay",
                "0.5",
            ]
            if not has_etf_like:
                cmd.append("--skip-etf")
            if not has_stocks:
                cmd.append("--skip-individual")
                cmd.append("--skip-indicators")
                cmd.append("--skip-rs")
            if not (has_stocks or has_sector):
                cmd.append("--skip-fundamentals")
            if not has_canslim:
                cmd.append("--skip-canslim")

            main_result = self._run_script(cmd, cwd=repo_root)
            tail_lines = main_result["output_tail"]

            if main_result["returncode"] != 0:
                details = {
                    "job_id": job_id,
                    "main_refresh": main_result,
                }
                self.log_system_event("Data Update", "FAILED", "Unified refresh failed", details)
                return {
                    "status": "error",
                    "message": f"Unified refresh failed (exit={main_result['returncode']})",
                    "details": details,
                }

            post_steps: List[dict] = []

            # Sector rotation computation (DB-side) after price refresh.
            if has_sector:
                try:
                    ok = self.calculate_sector_rotation()
                    post_steps.append(
                        {
                            "step": "sector_rotation",
                            "status": "success" if ok else "failed",
                        }
                    )
                except Exception as e:
                    post_steps.append({"step": "sector_rotation", "status": "failed", "error": str(e)})

            # Run fetch_fundamentals.py for watchlist/portfolio symbols when requested.
            if has_fundamentals:
                fund_script = Path(__file__).resolve().parents[1] / "scripts" / "fetch_fundamentals.py"
                fund_result = self._run_script(
                    [sys.executable, str(fund_script), "--max-symbols", "200"],
                    cwd=repo_root,
                )
                fund_result["step"] = "fetch_fundamentals_watchlist_portfolio"
                fund_result["status"] = "success" if fund_result["returncode"] == 0 else "failed"
                post_steps.append(fund_result)
                if fund_result["returncode"] != 0:
                    print(
                        "[DataService] fetch_fundamentals exited "
                        f"{fund_result['returncode']}: {fund_result['output_tail']}"
                    )

            if has_ai_prediction:
                ai_result = self._run_ai_prediction_batch(repo_root, max_tickers=500)
                ai_result["step"] = "ai_prediction_batch"
                post_steps.append(ai_result)

            details = {
                "job_id": job_id,
                "main_refresh": main_result,
                "post_steps": post_steps,
            }
            failed_post = [s for s in post_steps if s.get("status") in ("failed", "error")]
            if failed_post:
                self.log_system_event("Data Update", "FAILED", "Unified refresh completed with post-step failures", details)
                return {
                    "status": "error",
                    "message": "Main refresh completed, but some post steps failed",
                    "processed": 1,
                    "failed": len(failed_post),
                    "details": details,
                }

            self.log_system_event("Data Update", "SUCCESS", "Unified refresh completed", details)
            # Keep legacy response keys for current frontend compatibility.
            return {
                "status": "success",
                "processed": 1 + len(post_steps),
                "failed": 0,
                "details": details,
            }

        except Exception as e:
            err_msg = str(e)
            trace = traceback.format_exc()
            self.log_system_event("Data Update", "FAILED", err_msg, {"trace": trace, "job_id": job_id})
            return {"status": "error", "message": err_msg}

    def _collect_symbols(self, targets: List[str]) -> Dict[str, List[str]]:
        """ターゲットに応じたシンボル収集 (Raw Symbols)"""
        symbols_map = {
            "Indices": [],
            "FX": [],
            "Metal": [],
            "Sector": [],
            "Stocks": []
        }
        
        if not targets:
            return symbols_map

        if "Indices" in targets:
            # yfinance tickers
            symbols_map["Indices"] = ["^DJI", "^GSPC", "^IXIC", "^N225", "^RUT", "^VIX"]
            
        if "FX" in targets:
            # yfinance tickers for FX
            symbols_map["FX"] = ["USDJPY=X", "EURUSD=X", "GBPUSD=X", "GBPJPY=X"]
            
        if "Metal" in targets:
            # yfinance tickers for Metal
            symbols_map["Metal"] = ["GC=F", "SI=F", "PL=F", "PA=F"]
            
        if "Sector" in targets:
            # yfinance tickers for Sectors
            symbols_map["Sector"] = ["XLE", "XLB", "XLP", "XLI", "XLRE", "XLU", "XLC", "XLV", "XLK", "XLF", "XLY"]
            # Ensure Benchmark is fetched
            if "^GSPC" not in symbols_map["Indices"]:
                symbols_map["Indices"].append("^GSPC")
            
        if "Stocks" in targets:
            # Query DB for Portfolio and Watchlist symbols
            self.db.connect()
            try:
                # Portfolio (trades table has symbol_key)
                p_rows = self.db.execute_query("SELECT DISTINCT symbol_key FROM trades")
                # Watchlist
                w_rows = self.db.execute_query("SELECT DISTINCT symbol_key FROM watchlist") 
                
                s_set = set()
                
                def extract_raw(key):
                    if not key: return None
                    if ':' in key:
                        return key.split(':', 1)[1]
                    return key # fallback
                
                if p_rows:
                    for r in p_rows: 
                        raw = extract_raw(r[0])
                        if raw: s_set.add(raw)
                if w_rows:
                    for r in w_rows: 
                        raw = extract_raw(r[0])
                        if raw: s_set.add(raw)
                
                symbols_map["Stocks"] = list(s_set)
            finally:
                self.db.disconnect()
                
        return symbols_map

    def _fetch_yfinance_batch(self, symbols: List[str], start: datetime, end: datetime) -> Dict[str, pd.DataFrame]:
        """yfinanceで一括取得"""
        result = {}
        try:
            # yfinance expects list of raw tickers
            df = yf.download(symbols, start=start, end=end, group_by='ticker', auto_adjust=False, progress=False, threads=True)
            
            if len(symbols) == 1:
                # yfinance behavior differs for single symbol
                if not df.empty:
                    result[symbols[0]] = df
            else:
                for sym in symbols:
                    try:
                        sym_df = df[sym]
                        if not sym_df.empty and not sym_df.isnull().all().all():
                             result[sym] = sym_df.dropna(how='all')
                    except KeyError:
                        continue
        except Exception as e:
            print(f"yfinance download error: {e}")
            # Fallback to individual
            for sym in symbols:
                try:
                    t = yf.Ticker(sym)
                    h = t.history(start=start, end=end, auto_adjust=False)
                    if not h.empty:
                        result[sym] = h
                except:
                    pass
        return result

    def _ensure_instrument(self, symbol_key: str, raw_symbol: str):
        """instrumentsテーブルにシンボルが存在することを確認し、なければ作成"""
        # Check if exists
        res = self.db.execute_query("SELECT symbol_key FROM instruments WHERE symbol_key = %s", (symbol_key,))
        if res:
            return

        # Determine Market/Name from symbol_key (guaranteed US: or JP:)
        market = symbol_key.split(':')[0] 
        name = raw_symbol
        currency = "USD"
        
        # Name heuristics
        if raw_symbol.startswith("^"):
            name = f"Index {raw_symbol}"
            if "N225" in raw_symbol: currency = "JPY"
        elif "=X" in raw_symbol:
            # FX
            if "JPY" in raw_symbol and "USD" in raw_symbol: 
                 # e.g. USDJPY=X -> JPY (Usually)
                 pass 
            name = raw_symbol
        elif "=F" in raw_symbol:
            name = f"Future {raw_symbol}"
        elif raw_symbol in ["XLE", "XLB", "XLP", "XLI", "XLRE", "XLU", "XLC", "XLV", "XLK", "XLF", "XLY"]:
            name = f"Sector ETF {raw_symbol}"
        elif market == 'JP':
            currency = 'JPY'
            name = f"Japan Stock {raw_symbol}"
            
        try:
            query = """
                INSERT INTO instruments (symbol_key, market, name, currency, is_active, created_at, updated_at)
                VALUES (%s, %s, %s, %s, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (symbol_key) DO NOTHING
            """
            self.db.execute_command(query, (symbol_key, market, name, currency))
        except Exception as e:
            print(f"Failed to create instrument {symbol_key}: {e}")

    def _save_price_data(self, symbol_key: str, raw_symbol: str, df: pd.DataFrame) -> bool:
        """DBに価格データを保存 (Upsert)"""
        self.db.connect()
        try:
            # 1. Ensure Instrument Exists (FK Constraint)
            self._ensure_instrument(symbol_key, raw_symbol)
            
            query = """
                INSERT INTO price_daily (symbol_key, trading_date, open, high, low, close, adj_close, volume, source, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (symbol_key, trading_date) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    adj_close = EXCLUDED.adj_close,
                    volume = EXCLUDED.volume,
                    updated_at = CURRENT_TIMESTAMP
            """
            
            for index, row in df.iterrows():
                try:
                    d = index.date()
                    op = Decimal(str(row.get('Open', 0)))
                    hi = Decimal(str(row.get('High', 0)))
                    lo = Decimal(str(row.get('Low', 0)))
                    cl = Decimal(str(row.get('Close', 0)))
                    ac = Decimal(str(row.get('Adj Close', cl)))
                    vol = int(row.get('Volume', 0))
                    
                    self.db.execute_command(query, (
                        symbol_key, d, op, hi, lo, cl, ac, vol, 'yfinance_update'
                    ))
                except Exception as row_e:
                    # print(f"Row error {symbol_key} {index}: {row_e}")
                    continue
            
            return True
        except Exception as e:
            # print(f"Save error {symbol_key}: {e}")
            return False
        finally:
            self.db.disconnect()

    def calculate_sector_rotation(self):
        """セクターローテーション計算と保存"""
        self.db.connect()
        try:
            # Benchmark US:^GSPC
            benchmark_key = "US:^GSPC"
            # Sectors (US: Prefix enforced)
            sectors_map = {
                "US:XLE": "XLE", "US:XLB": "XLB", "US:XLP": "XLP", "US:XLI": "XLI", 
                "US:XLRE": "XLRE", "US:XLU": "XLU", "US:XLC": "XLC", "US:XLV": "XLV", 
                "US:XLK": "XLK", "US:XLF": "XLF", "US:XLY": "XLY"
            }
            
            query = "SELECT trading_date, close FROM price_daily WHERE symbol_key = %s ORDER BY trading_date DESC LIMIT 30"
            bm_rows = self.db.execute_query(query, (benchmark_key,))
            if not bm_rows or len(bm_rows) < 22:
                # print(f"Not enough benchmark data for {benchmark_key}")
                return
            
            bm_data = {r[0]: float(r[1]) for r in bm_rows}
            latest_date = bm_rows[0][0]
            
            def get_ret(data_dict, days):
                dates = sorted(data_dict.keys(), reverse=True)
                if len(dates) <= days:
                    return 0.0
                curr = data_dict[dates[0]]
                past = data_dict[dates[days]]
                return ((curr - past) / past) * 100

            bm_ret_21 = get_ret(bm_data, 21)
            
            sector_metrics = []
            
            for sec_key, raw_sym in sectors_map.items():
                sec_rows = self.db.execute_query(query, (sec_key,))
                if not sec_rows or len(sec_rows) < 22:
                    continue
                    
                sec_data = {r[0]: float(r[1]) for r in sec_rows}
                
                sec_ret_21 = get_ret(sec_data, 21)
                sec_ret_5 = get_ret(sec_data, 5) 
                
                rel_str = sec_ret_21 - bm_ret_21
                
                sector_metrics.append({
                    "symbol_key": sec_key,
                    "return": sec_ret_21,
                    "momentum": sec_ret_5,
                    "rs": rel_str
                })
            
            sector_metrics.sort(key=lambda x: (x["return"], x["rs"]), reverse=True)
            
            # Upsert into sector_rotation (using etf_symbol_key)
            query_upsert = """
                INSERT INTO sector_rotation (etf_symbol_key, trading_date, current_return, momentum, relative_strength, rank, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (etf_symbol_key, trading_date) DO UPDATE SET
                    current_return = EXCLUDED.current_return,
                    momentum = EXCLUDED.momentum,
                    relative_strength = EXCLUDED.relative_strength,
                    rank = EXCLUDED.rank,
                    updated_at = CURRENT_TIMESTAMP
            """
            
            for rank, item in enumerate(sector_metrics, 1):
                self.db.execute_command(query_upsert, (
                    item["symbol_key"],
                    latest_date,
                    Decimal(str(item["return"])),
                    Decimal(str(item["momentum"])),
                    Decimal(str(item["rs"])),
                    rank
                ))
            
            self.log_system_event("Sector Rotation", "SUCCESS", f"Calculated for {latest_date}")
            return True
                
        except Exception as e:
            # print(f"Sector Calc Error: {e}")
            self.log_system_event("Sector Rotation", "FAILED", str(e))
            return False
        finally:
            self.db.disconnect()
