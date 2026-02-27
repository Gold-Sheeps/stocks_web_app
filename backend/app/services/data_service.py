
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

    def _log_update_step(
        self,
        job_id: str,
        step: str,
        status: str,
        message: str,
        details: dict | None = None,
    ) -> None:
        payload = {"job_id": job_id, "step": step}
        if details:
            payload.update(details)
        self.log_system_event("Data Update", status, message, payload)

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

    def _resolve_script_path(self, repo_root: Path, script_name: str) -> tuple[Path | None, List[str]]:
        """
        Resolve script path across known layouts.
        Prefer pathlib + repo-root-relative lookup to avoid cwd/path separator issues.
        """
        candidates = [
            repo_root / "backend" / "app" / "scripts" / script_name,
            repo_root / "backend" / "scripts" / script_name,
            repo_root / "scripts" / script_name,
        ]
        for p in candidates:
            if p.exists():
                return p, [str(c) for c in candidates]
        return None, [str(c) for c in candidates]

    def _run_resolved_script(
        self,
        repo_root: Path,
        step_name: str,
        script_name: str,
        args: List[str] | None = None,
        timeout: int | None = None,
        missing_status: str = "failed",
    ) -> dict:
        script_path, searched = self._resolve_script_path(repo_root, script_name)
        if script_path is None:
            msg = (
                f"[DataService] post-step script not found step={step_name} "
                f"script={script_name} repo_root={repo_root} searched={searched}"
            )
            print(msg)
            return {
                "returncode": 2,
                "command": [sys.executable, f"<unresolved:{script_name}>", *(args or [])],
                "output_tail": [msg],
                "step": step_name,
                "status": missing_status,
                "script_name": script_name,
                "searched_paths": searched,
                "repo_root": str(repo_root),
            }

        return self._run_script(
            [sys.executable, str(script_path), *(args or [])],
            cwd=repo_root,
            timeout=timeout,
        )

    def _post_step_preflight(self, repo_root: Path, targets: List[str]) -> List[dict]:
        selected = set(targets or [])
        checks: List[tuple[str, str]] = []
        if "Fundamentals" in selected:
            checks.append(("fetch_fundamentals_watchlist_portfolio", "fetch_fundamentals.py"))
        if "MarketEnvironment" in selected:
            checks.append(("fetch_market_environment", "fetch_market_environment.py"))
        if ("AI" in selected) or ("AI Prediction" in selected):
            checks.append(("llm_signal_extraction_batch", "run_llm_signal_extraction_batch.py"))
            checks.append(("ai_prediction_batch", "run_daily_predictions.py"))
        out: List[dict] = []
        for step_name, script_name in checks:
            p, searched = self._resolve_script_path(repo_root, script_name)
            out.append(
                {
                    "step": step_name,
                    "script_name": script_name,
                    "found": bool(p),
                    "resolved_path": str(p) if p else None,
                    "searched_paths": searched,
                }
            )
            if not p:
                print(
                    f"[DataService] preflight missing post-step script step={step_name} "
                    f"script={script_name} repo_root={repo_root} searched={searched}"
                )
        return out

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

        result = self._run_resolved_script(
            repo_root=repo_root,
            step_name="ai_prediction_batch",
            script_name="run_daily_predictions.py",
            args=["--asof", latest_date.isoformat(), "--max-tickers", str(int(max_tickers))],
            timeout=7200,
            missing_status="failed",
        )
        result["asof"] = latest_date.isoformat()
        result["status"] = "success" if result["returncode"] == 0 else "failed"
        return result

    def _run_llm_signal_extraction_batch(self, repo_root: Path, max_symbols: int = 500) -> dict:
        latest_date = self._get_latest_price_date()
        if latest_date is None:
            return {
                "status": "skipped",
                "reason": "No price_daily data",
            }
        result = self._run_resolved_script(
            repo_root=repo_root,
            step_name="llm_signal_extraction_batch",
            script_name="run_llm_signal_extraction_batch.py",
            args=["--asof", latest_date.isoformat(), "--max-symbols", str(int(max_symbols))],
            timeout=3600,
            missing_status="warning",
        )
        result["asof"] = latest_date.isoformat()
        # Do not fail the whole update flow on LLM feature extraction failure; prediction can degrade safely.
        result["status"] = "success" if result["returncode"] == 0 else "warning"
        return result

    def update_all_data(self, range_days: int = 14, targets: List[str] = None, job_id: str | None = None):
        """Data Update画面から統合バッチ(full_db_refresh.py)を実行"""
        targets = targets or []
        job_id = job_id or f"update_{int(time.time())}"
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
            post_step_preflight = self._post_step_preflight(repo_root, targets)

            # Map UI targets to unified batch skip flags.
            selected = set(targets)
            has_etf_like = any(t in selected for t in ["Indices", "FX", "Metal", "Crypto", "Sector"])
            has_stocks = "Stocks" in selected
            has_sector = "Sector" in selected
            has_canslim = "CANSLIM" in selected
            has_fundamentals = "Fundamentals" in selected
            has_market_env = "MarketEnvironment" in selected
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

            self._log_update_step(
                job_id,
                "main_refresh",
                "RUNNING",
                "Main unified refresh started",
                {"command": cmd, "targets": targets, "range_days": range_days},
            )
            main_result = self._run_script(cmd, cwd=repo_root)
            tail_lines = main_result["output_tail"]

            self._log_update_step(
                job_id,
                "main_refresh",
                "SUCCESS" if main_result["returncode"] == 0 else "FAILED",
                (
                    "Main unified refresh completed"
                    if main_result["returncode"] == 0
                    else f"Main unified refresh failed (exit={main_result['returncode']})"
                ),
                {
                    "returncode": main_result["returncode"],
                    "output_tail": tail_lines[-20:],
                },
            )

            if main_result["returncode"] != 0:
                details = {
                    "job_id": job_id,
                    "main_refresh": main_result,
                    "post_step_preflight": post_step_preflight,
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
                self._log_update_step(job_id, "sector_rotation", "RUNNING", "Sector rotation calculation started")
                try:
                    ok = self.calculate_sector_rotation()
                    self._log_update_step(
                        job_id,
                        "sector_rotation",
                        "SUCCESS" if ok else "FAILED",
                        "Sector rotation calculation completed" if ok else "Sector rotation calculation failed",
                    )
                    post_steps.append(
                        {
                            "step": "sector_rotation",
                            "status": "success" if ok else "failed",
                        }
                    )
                except Exception as e:
                    self._log_update_step(
                        job_id, "sector_rotation", "FAILED", "Sector rotation calculation exception", {"error": str(e)}
                    )
                    post_steps.append({"step": "sector_rotation", "status": "failed", "error": str(e)})

            # Run fetch_fundamentals.py for watchlist/portfolio symbols when requested.
            if has_fundamentals:
                self._log_update_step(
                    job_id,
                    "fetch_fundamentals_watchlist_portfolio",
                    "RUNNING",
                    "Fundamentals update started",
                )
                fund_result = self._run_resolved_script(
                    repo_root=repo_root,
                    step_name="fetch_fundamentals_watchlist_portfolio",
                    script_name="fetch_fundamentals.py",
                    args=["--max-symbols", "200"],
                )
                fund_result["step"] = "fetch_fundamentals_watchlist_portfolio"
                fund_result["status"] = "success" if fund_result["returncode"] == 0 else "failed"
                self._log_update_step(
                    job_id,
                    "fetch_fundamentals_watchlist_portfolio",
                    "SUCCESS" if fund_result["returncode"] == 0 else "FAILED",
                    "Fundamentals update completed" if fund_result["returncode"] == 0 else "Fundamentals update failed",
                    {
                        "returncode": fund_result["returncode"],
                        "output_tail": (fund_result.get("output_tail") or [])[-20:],
                    },
                )
                post_steps.append(fund_result)
                if fund_result["returncode"] != 0:
                    print(
                        "[DataService] fetch_fundamentals exited "
                        f"{fund_result['returncode']}: {fund_result['output_tail']}"
                    )

            if has_market_env:
                self._log_update_step(
                    job_id,
                    "fetch_market_environment",
                    "RUNNING",
                    "Market Environment update started",
                )
                me_result = self._run_resolved_script(
                    repo_root=repo_root,
                    step_name="fetch_market_environment",
                    script_name="fetch_market_environment.py",
                    args=[],
                    timeout=120,
                )
                me_result["step"] = "fetch_market_environment"
                me_result["status"] = "success" if me_result["returncode"] == 0 else "failed"
                self._log_update_step(
                    job_id,
                    "fetch_market_environment",
                    "SUCCESS" if me_result["returncode"] == 0 else "FAILED",
                    "Market Environment update completed" if me_result["returncode"] == 0 else "Market Environment update failed",
                    {
                        "returncode": me_result["returncode"],
                        "output_tail": (me_result.get("output_tail") or [])[-20:],
                    },
                )
                post_steps.append(me_result)
                if me_result["returncode"] != 0:
                    print(f"[DataService] fetch_market_environment exited {me_result['returncode']}: {me_result['output_tail']}")

            if has_ai_prediction:
                self._log_update_step(
                    job_id,
                    "llm_signal_extraction_batch",
                    "RUNNING",
                    "LLM signal extraction batch started",
                )
                llm_result = self._run_llm_signal_extraction_batch(repo_root, max_symbols=500)
                llm_result["step"] = "llm_signal_extraction_batch"
                self._log_update_step(
                    job_id,
                    "llm_signal_extraction_batch",
                    "SUCCESS" if llm_result.get("status") == "success" else "FAILED",
                    "LLM signal extraction batch completed" if llm_result.get("status") == "success" else "LLM signal extraction batch failed",
                    {
                        "returncode": llm_result.get("returncode"),
                        "status": llm_result.get("status"),
                        "output_tail": (llm_result.get("output_tail") or [])[-20:],
                    },
                )
                post_steps.append(llm_result)
                self._log_update_step(
                    job_id,
                    "ai_prediction_batch",
                    "RUNNING",
                    "AI prediction batch started",
                )
                ai_result = self._run_ai_prediction_batch(repo_root, max_tickers=500)
                ai_result["step"] = "ai_prediction_batch"
                self._log_update_step(
                    job_id,
                    "ai_prediction_batch",
                    "SUCCESS" if ai_result.get("status") == "success" else "FAILED",
                    "AI prediction batch completed" if ai_result.get("status") == "success" else "AI prediction batch failed",
                    {
                        "returncode": ai_result.get("returncode"),
                        "status": ai_result.get("status"),
                        "asof": ai_result.get("asof"),
                        "output_tail": (ai_result.get("output_tail") or [])[-20:],
                    },
                )
                post_steps.append(ai_result)

            details = {
                "job_id": job_id,
                "main_refresh": main_result,
                "post_step_preflight": post_step_preflight,
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
            
            # Upsert into sector_rotation.
            # NOTE:
            # - Current table PK is (symbol, trading_date)
            # - etf_symbol_key is supplemental key used by newer code paths
            query_upsert = """
                INSERT INTO sector_rotation (symbol, etf_symbol_key, trading_date, current_return, momentum, relative_strength, rank, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (symbol, trading_date) DO UPDATE SET
                    etf_symbol_key = EXCLUDED.etf_symbol_key,
                    current_return = EXCLUDED.current_return,
                    momentum = EXCLUDED.momentum,
                    relative_strength = EXCLUDED.relative_strength,
                    rank = EXCLUDED.rank,
                    updated_at = CURRENT_TIMESTAMP
            """

            success_count = 0
            failed_count = 0
            for rank, item in enumerate(sector_metrics, 1):
                # Keep both legacy symbol (e.g. XLK) and normalized key (US:XLK).
                legacy_symbol = item["symbol_key"].split(":", 1)[1] if ":" in item["symbol_key"] else item["symbol_key"]
                ok = self.db.execute_command(
                    query_upsert,
                    (
                        legacy_symbol,
                        item["symbol_key"],
                        latest_date,
                        Decimal(str(item["return"])),
                        Decimal(str(item["momentum"])),
                        Decimal(str(item["rs"])),
                        rank,
                    ),
                )
                if ok:
                    success_count += 1
                else:
                    failed_count += 1

            if success_count == 0:
                self.log_system_event(
                    "Sector Rotation",
                    "FAILED",
                    f"Insert failed for all sectors (latest_date={latest_date}, failed={failed_count})",
                )
                return False

            if failed_count > 0:
                self.log_system_event(
                    "Sector Rotation",
                    "FAILED",
                    f"Partial failure (latest_date={latest_date}, success={success_count}, failed={failed_count})",
                )
                return False

            self.log_system_event("Sector Rotation", "SUCCESS", f"Calculated for {latest_date} ({success_count} sectors)")
            return True
                
        except Exception as e:
            # print(f"Sector Calc Error: {e}")
            self.log_system_event("Sector Rotation", "FAILED", str(e))
            return False
        finally:
            self.db.disconnect()
