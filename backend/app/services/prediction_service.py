from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
from app.db.database import Database


@dataclass
class PredictorConfig:
    flat_band_pct: float = 2.0
    horizon_trading_days: int = 15
    calibration: str = "sigmoid"  # sigmoid | isotonic
    min_train_rows: int = 260
    feature_version: str = "v2_ohlcv_ta_relregime_event"
    target_interval_coverage: float = 0.80


class PredictionService:
    def __init__(self) -> None:
        self._xgb = None
        self._sk_metrics = None
        self._sk_calib = None
        self._sk_tscv = None
        self._model_version = "xgb_db_only_v1"
        self._reset_data_context()
        self._load_ml_dependencies()

    def _reset_data_context(self) -> None:
        self._meta_sources: Dict[str, str] = {}
        self._meta_origins: Dict[str, str] = {}
        self._db_freshness_by_symbol: Dict[str, Dict[str, Any]] = {}

    def _load_ml_dependencies(self) -> None:
        try:
            import xgboost as xgb  # type: ignore
            from sklearn.calibration import CalibratedClassifierCV  # type: ignore
            from sklearn.metrics import (
                accuracy_score,
                confusion_matrix,
                f1_score,
                log_loss,
                precision_recall_fscore_support,
            )  # type: ignore
            from sklearn.model_selection import TimeSeriesSplit  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "ML dependencies are missing. Install xgboost and scikit-learn."
            ) from e
        self._xgb = xgb
        self._sk_calib = CalibratedClassifierCV
        self._sk_tscv = TimeSeriesSplit
        self._sk_metrics = {
            "accuracy_score": accuracy_score,
            "f1_score": f1_score,
            "log_loss": log_loss,
            "confusion_matrix": confusion_matrix,
            "prfs": precision_recall_fscore_support,
        }

    # ----------------------------- public API -----------------------------
    def predict(
        self,
        ticker: str,
        as_of_date: str,
        flat_band_pct: float = 2.0,
        horizon_trading_days: int = 15,
        calibration: str = "sigmoid",
        target_interval_coverage: float = 0.80,
        feature_version: str = "v2_ohlcv_ta_relregime_event",
        class_weight_mode: str = "off",
        class_weight: Optional[Dict[str, float]] = None,
        threshold_mode: str = "argmax",
        t_down: Optional[float] = None,
        t_up: Optional[float] = None,
        regime_symbols: Optional[List[str]] = None,
        relative_symbols: Optional[List[str]] = None,
        sector_symbols: Optional[List[str]] = None,
        sample_csv_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._reset_data_context()
        cfg = PredictorConfig(
            flat_band_pct=flat_band_pct,
            horizon_trading_days=horizon_trading_days,
            calibration=calibration,
            target_interval_coverage=target_interval_coverage,
            feature_version=feature_version,
        )
        as_of = datetime.strptime(as_of_date, "%Y-%m-%d").date()
        _ = sample_csv_path  # DB-only mode: keep API compatibility, ignore sample path.
        raw = self._load_prices(ticker, as_of=as_of)
        feat = self._build_feature_frame(
            ticker,
            raw,
            as_of=as_of,
            regime_symbols=regime_symbols,
            relative_symbols=relative_symbols,
            sector_symbols=sector_symbols,
        )
        prepared = self._prepare_dataset(feat, as_of, cfg)
        return self._fit_predict(
            prepared,
            cfg,
            as_of,
            class_weight_mode=class_weight_mode,
            class_weight=class_weight,
            threshold_mode=threshold_mode,
            t_down=t_down,
            t_up=t_up,
        )

    def backtest(
        self,
        ticker: str,
        as_of_date: str,
        flat_band_pct: float = 2.0,
        horizon_trading_days: int = 15,
        calibration: str = "sigmoid",
        target_interval_coverage: float = 0.80,
        feature_version: str = "v2_ohlcv_ta_relregime_event",
        sample_csv_path: Optional[str] = None,
        include_band_sweep: bool = True,
        max_folds: Optional[int] = None,
        fold_step: Optional[int] = None,
        train_window: Optional[int] = None,
        use_ensemble: bool = True,
        purge_days: Optional[int] = None,
        embargo_mode: str = "pct",
        embargo_days: int = 5,
        embargo_pct: float = 0.01,
        prob_mode: str = "meta",
        temp_scale: Union[bool, str] = True,
        meta_band_mode: str = "fixed",
        meta_band_q: float = 0.65,
        meta_band_min_pct: float = 0.5,
        meta_band_max_pct: float = 10.0,
        label_mode: str = "fixed",
        target_return: float = 0.05,
        tbm_vol_span: int = 20,
        tbm_k: float = 1.5,
        class_weight_mode: str = "off",
        class_weight: Optional[Dict[str, float]] = None,
        threshold_mode: str = "argmax",
        t_down: Optional[float] = None,
        t_up: Optional[float] = None,
        threshold_search: bool = False,
        down_recall_min: float = 0.10,
        pred_down_min_pct: float = 0.05,
        threshold_grid: Optional[Dict[str, List[float]]] = None,
        t_buy_grid: Optional[List[float]] = None,
        fixed_thresholds: Optional[List[float]] = None,
        min_coverage: float = 0.05,
        min_trades: int = 50,
        regime_symbols: Optional[List[str]] = None,
        relative_symbols: Optional[List[str]] = None,
        sector_symbols: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        self._reset_data_context()
        cfg = PredictorConfig(
            flat_band_pct=flat_band_pct,
            horizon_trading_days=horizon_trading_days,
            calibration=calibration,
            target_interval_coverage=target_interval_coverage,
            feature_version=feature_version,
        )
        as_of = datetime.strptime(as_of_date, "%Y-%m-%d").date()
        _ = sample_csv_path  # DB-only mode: keep API compatibility, ignore sample path.
        raw = self._load_prices(ticker, as_of=as_of)
        feat = self._build_feature_frame(
            ticker,
            raw,
            as_of=as_of,
            regime_symbols=regime_symbols,
            relative_symbols=relative_symbols,
            sector_symbols=sector_symbols,
        )
        prepared = self._prepare_dataset(feat, as_of, cfg)
        bt = self._walk_forward_backtest(
            prepared,
            cfg,
            max_folds=max_folds,
            fold_step=fold_step,
            train_window=train_window,
            use_ensemble=use_ensemble,
            purge_days=purge_days,
            embargo_mode=embargo_mode,
            embargo_days=embargo_days,
            embargo_pct=embargo_pct,
            prob_mode=prob_mode,
            temp_scale=temp_scale,
            meta_band_mode=meta_band_mode,
            meta_band_q=meta_band_q,
            meta_band_min_pct=meta_band_min_pct,
            meta_band_max_pct=meta_band_max_pct,
            label_mode=label_mode,
            target_return=target_return,
            tbm_vol_span=tbm_vol_span,
            tbm_k=tbm_k,
            class_weight_mode=class_weight_mode,
            class_weight=class_weight,
            threshold_mode=threshold_mode,
            t_down=t_down,
            t_up=t_up,
            threshold_search=threshold_search,
            down_recall_min=down_recall_min,
            pred_down_min_pct=pred_down_min_pct,
            threshold_grid=threshold_grid,
            t_buy_grid=t_buy_grid,
            fixed_thresholds=fixed_thresholds,
            min_coverage=min_coverage,
            min_trades=min_trades,
        )
        out: Dict[str, Any] = {
            "backtest": bt,
            "meta": self._build_meta(cfg, prepared["train_range"], as_of, prepared["feature_cols"]),
        }
        if include_band_sweep:
            sweep = self._band_sweep(feat, as_of, cfg, [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0])
            out["band_sweep"] = sweep["rows"]
            out["best_flat_band_pct"] = sweep["best_band"]
        return out

    # ----------------------------- data -----------------------------
    def _symbol_key(self, ticker: str) -> str:
        t = str(ticker).strip().upper()
        return t if ":" in t else f"US:{t}"

    def _raw_symbol(self, ticker: str) -> str:
        t = str(ticker).strip().upper()
        return t.split(":", 1)[1] if ":" in t else t

    def _load_prices(self, ticker: str, as_of: date) -> pd.DataFrame:
        symbol_key = self._symbol_key(ticker)
        db_df, freshness = self._load_prices_from_db(symbol_key, as_of)
        if not self._is_db_data_sufficient(db_df):
            raise RuntimeError(
                f"Price data missing in DB: {symbol_key}. "
                "Run `python backend/scripts/refresh_market_data.py --symbols "
                f"\"{symbol_key},US:QQQ,US:^SOX,US:SMH\" --source yfinance` then retry."
            )
        self._meta_sources["prices"] = f"db:price_daily({symbol_key})"
        self._meta_origins["prices"] = "ingested:yfinance"
        self._db_freshness_by_symbol[symbol_key] = freshness
        return db_df

    def _load_prices_from_db(self, symbol_key: str, as_of: date) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        freshness = {
            "symbol": symbol_key,
            "max_date": None,
            "rows": 0,
            "updated_at_max": None,
            "as_of_date": as_of.isoformat(),
        }
        db = Database()
        try:
            if not db.connect():
                return empty, freshness
            q = """
                SELECT trading_date, open, high, low, close, volume
                FROM price_daily
                WHERE symbol_key = %s AND trading_date <= %s
                ORDER BY trading_date
            """
            rows = db.execute_query(q, (symbol_key, as_of))
            fresh_q = """
                SELECT COUNT(*), MAX(trading_date), MAX(updated_at)
                FROM price_daily
                WHERE symbol_key = %s AND trading_date <= %s
            """
            fresh_row = db.execute_query(fresh_q, (symbol_key, as_of))
            if fresh_row and fresh_row[0]:
                freshness["rows"] = int(fresh_row[0][0] or 0)
                freshness["max_date"] = (
                    fresh_row[0][1].isoformat() if fresh_row[0][1] is not None else None
                )
                freshness["updated_at_max"] = (
                    fresh_row[0][2].isoformat() if fresh_row[0][2] is not None else None
                )
            if not rows:
                return empty, freshness
            df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
            df["Date"] = pd.to_datetime(df["Date"])
            df = df.set_index("Date").sort_index()
            return self._normalize_ohlcv(df), freshness
        except Exception:
            return empty, freshness
        finally:
            db.disconnect()

    def _is_db_data_sufficient(self, df: pd.DataFrame, min_rows: int = 300) -> bool:
        return bool(df is not None and not df.empty and len(df) >= min_rows)

    def _build_db_freshness(self) -> Dict[str, Any]:
        symbols = sorted(self._db_freshness_by_symbol.keys())
        max_date = None
        total_rows = 0
        for s in symbols:
            entry = self._db_freshness_by_symbol[s]
            total_rows += int(entry.get("rows") or 0)
            d = entry.get("max_date")
            if d is not None and (max_date is None or d > max_date):
                max_date = d
        return {
            "db_table": "price_daily",
            "symbols": symbols,
            "max_date": max_date,
            "rows": total_rows,
            "by_symbol": self._db_freshness_by_symbol,
        }

    def _build_meta(
        self,
        cfg: PredictorConfig,
        train_range: List[str],
        as_of: date,
        used_feature_columns: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        used_cols = list(used_feature_columns or [])
        feature_hash = hashlib.sha256(json.dumps(used_cols, ensure_ascii=False).encode("utf-8")).hexdigest()
        return {
            "model_type": "xgboost",
            "model_version": self._model_version,
            "calibration": cfg.calibration,
            "train_range": train_range,
            "feature_version": cfg.feature_version,
            "feature_set_used": cfg.feature_version,
            "used_feature_columns": used_cols,
            "n_features": int(len(used_cols)),
            "feature_hash": feature_hash,
            "as_of_date": as_of.isoformat(),
            "sources": self._meta_sources,
            "origins": self._meta_origins,
            "db_freshness": self._build_db_freshness(),
            "db_symbols_used": sorted(self._db_freshness_by_symbol.keys()),
        }

    def _normalize_ohlcv(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        out = df.copy()
        if isinstance(out.columns, pd.MultiIndex):
            out.columns = [c[0] for c in out.columns]
        rename_map = {
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "adj close": "Adj Close",
            "volume": "Volume",
        }
        out.columns = [rename_map.get(str(c).strip().lower(), c) for c in out.columns]
        keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in out.columns]
        out = out[keep].dropna().sort_index()
        for c in keep:
            out[c] = pd.to_numeric(out[c], errors="coerce")
        out = out.dropna(subset=[c for c in ["Open", "High", "Low", "Close"] if c in out.columns])
        out.index = pd.to_datetime(out.index).tz_localize(None)
        return out

    # ----------------------------- features -----------------------------
    def _build_feature_frame(
        self,
        ticker: str,
        price_df: pd.DataFrame,
        as_of: date,
        regime_symbols: Optional[List[str]] = None,
        relative_symbols: Optional[List[str]] = None,
        sector_symbols: Optional[List[str]] = None,
        feature_version: Optional[str] = None,
    ) -> pd.DataFrame:
        df = price_df.copy()
        regime_list = self._normalize_symbol_list(regime_symbols, ["US:QQQ"])
        relative_list = self._normalize_symbol_list(relative_symbols, ["US:QQQ"])
        sector_list = self._normalize_symbol_list(sector_symbols, [])

        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        vol = df["Volume"]

        for n in (1, 5, 10, 20):
            df[f"ret_{n}"] = close.pct_change(n)
        df["vol_20"] = close.pct_change().rolling(20).std() * np.sqrt(252)

        prev_close = close.shift(1)
        tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        df["atr14"] = tr.rolling(14).mean()
        df["atr14_pct"] = df["atr14"] / close

        for n in (20, 50, 200):
            sma = close.rolling(n).mean()
            df[f"sma{n}_dist"] = close / sma - 1.0
        ema21 = close.ewm(span=21, adjust=False).mean()
        df["ema21_dist"] = close / ema21 - 1.0

        # RSI(14)
        diff = close.diff()
        up = diff.clip(lower=0.0)
        down = -diff.clip(upper=0.0)
        roll_up = up.ewm(alpha=1 / 14, adjust=False).mean()
        roll_down = down.ewm(alpha=1 / 14, adjust=False).mean()
        rs = roll_up / roll_down.replace(0, np.nan)
        df["rsi14"] = 100 - (100 / (1 + rs))

        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        df["macd"] = ema12 - ema26
        df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
        df["macd_hist"] = df["macd"] - df["macd_signal"]

        # Bollinger position
        mid = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper = mid + 2 * std
        lower = mid - 2 * std
        width = (upper - lower).replace(0, np.nan)
        df["bb_pos"] = (close - lower) / width

        # Volume features
        df["vol_chg_1"] = vol.pct_change(1)
        df["vol_ratio_20"] = vol / vol.rolling(20).mean()

        # Relative / regime features (reference symbols are configurable and missing-safe).
        reg_df, reg_symbol, reg_fresh = self._fetch_first_available(regime_list, as_of, min_rows=180)
        rel_df, rel_symbol, rel_fresh = self._fetch_first_available(relative_list, as_of, min_rows=180)
        sec_df, sec_symbol, sec_fresh = self._fetch_first_available(sector_list, as_of, min_rows=180)

        if reg_df is not None and reg_symbol is not None and reg_fresh is not None:
            self._db_freshness_by_symbol[reg_symbol] = reg_fresh
            reg_vol20 = reg_df["Close"].pct_change().rolling(20).std().reindex(df.index) * np.sqrt(252)
            reg_sma50 = reg_df["Close"].rolling(50).mean().reindex(df.index)
            df["qqq_vol_20"] = reg_vol20
            df["qqq_trend_50"] = reg_df["Close"].reindex(df.index) / reg_sma50 - 1.0
            df["regime_missing_flag"] = 0.0
            self._meta_sources["benchmarks"] = f"db:price_daily({reg_symbol})"
            self._meta_origins["benchmarks"] = "ingested:yfinance"
        else:
            df["qqq_vol_20"] = np.nan
            df["qqq_trend_50"] = np.nan
            df["regime_missing_flag"] = 1.0

        if rel_df is not None and rel_symbol is not None and rel_fresh is not None:
            self._db_freshness_by_symbol[rel_symbol] = rel_fresh
            rel_ret1 = rel_df["Close"].pct_change(1).reindex(df.index)
            rel_ret5 = rel_df["Close"].pct_change(5).reindex(df.index)
            df["ret_rel_qqq_1"] = df["ret_1"] - rel_ret1
            df["ret_rel_qqq_5"] = df["ret_5"] - rel_ret5
            df["relative_missing_flag"] = 0.0
            self._meta_sources["relative"] = f"db:price_daily({rel_symbol})"
            self._meta_origins["relative"] = "ingested:yfinance"
        else:
            df["ret_rel_qqq_1"] = np.nan
            df["ret_rel_qqq_5"] = np.nan
            df["relative_missing_flag"] = 1.0

        if sec_df is not None and sec_symbol is not None and sec_fresh is not None:
            self._db_freshness_by_symbol[sec_symbol] = sec_fresh
            sec_ret1 = sec_df["Close"].pct_change(1).reindex(df.index)
            sec_ret5 = sec_df["Close"].pct_change(5).reindex(df.index)
            df["ret_rel_semi_1"] = df["ret_1"] - sec_ret1
            df["ret_rel_semi_5"] = df["ret_5"] - sec_ret5
            df["sector_missing_flag"] = 0.0
            self._meta_sources["sector"] = f"db:price_daily({sec_symbol})"
            self._meta_origins["sector"] = "ingested:yfinance"
        else:
            df["ret_rel_semi_1"] = np.nan
            df["ret_rel_semi_5"] = np.nan
            df["sector_missing_flag"] = 1.0

        # DB-only mode: no live earnings calendar fetch.
        df["days_from_prev_earnings"] = np.nan
        df["days_to_next_earnings"] = np.nan
        # v3 candidate features: fundamentals + fundflow/orderflow proxies.
        fund_df = self._load_fundamentals(ticker, as_of)
        if fund_df is not None and not fund_df.empty:
            fund = fund_df[["period_end_date", "eps"]].dropna(subset=["period_end_date"]).copy()
            fund["fund_date"] = pd.to_datetime(fund["period_end_date"]).dt.tz_localize(None)
            fund = fund.sort_values("fund_date")
            base = pd.DataFrame({"Date": df.index}).sort_values("Date")
            merged = pd.merge_asof(
                base,
                fund[["fund_date", "eps"]],
                left_on="Date",
                right_on="fund_date",
                direction="backward",
            )
            # "days_since_earnings" uses latest known reporting date in DB as a leak-safe proxy.
            df["days_since_earnings"] = (
                pd.to_datetime(df.index) - pd.to_datetime(merged["fund_date"]).to_numpy()
            ) / np.timedelta64(1, "D")
        else:
            df["days_since_earnings"] = np.nan
        df["eps_surprise"] = np.nan
        df["guidance_surprise"] = np.nan
        df["estimate_revision_1w"] = np.nan
        df["estimate_revision_4w"] = np.nan
        df["eps_surprise_missing"] = df["eps_surprise"].isna().astype(float)
        df["guidance_surprise_missing"] = df["guidance_surprise"].isna().astype(float)
        df["estimate_revision_1w_missing"] = df["estimate_revision_1w"].isna().astype(float)
        df["estimate_revision_4w_missing"] = df["estimate_revision_4w"].isna().astype(float)

        vol_ma20 = vol.rolling(20).mean()
        vol_ma50 = vol.rolling(50).mean()
        df["relative_volume"] = vol_ma20 / vol_ma50.replace(0.0, np.nan)
        up_vol = np.where(close.diff() > 0.0, vol, 0.0)
        down_vol = np.where(close.diff() < 0.0, vol, 0.0)
        up_sum = pd.Series(up_vol, index=df.index).rolling(10).sum()
        down_sum = pd.Series(down_vol, index=df.index).rolling(10).sum()
        df["up_down_volume_ratio"] = up_sum / down_sum.replace(0.0, np.nan)
        dist_day = ((close < close.shift(1)) & (vol > vol.shift(1))).astype(float)
        df["distribution_day_count_25d"] = dist_day.rolling(25).sum()
        vwap20 = (close * vol).rolling(20).sum() / vol.rolling(20).sum().replace(0.0, np.nan)
        df["vwap_deviation"] = close / vwap20 - 1.0
        rolling_high_20_prev = high.rolling(20).max().shift(1)
        broke_out_prev = close.shift(1) > rolling_high_20_prev
        failed_now = close < close.shift(1)
        df["failed_breakout_flag"] = (broke_out_prev & failed_now).astype(float)

        # === v3/v4 extra: Market Environment + Fundamentals ratios + Advanced TA ===
        _fv = str(feature_version or "v2_ohlcv_ta_relregime_event")
        if _fv.startswith("v3") or _fv.startswith("v4"):
            df = self._merge_market_environment_features(df)
            df = self._merge_fundamentals_ratios(df, ticker)
            df = self._merge_advanced_ta_from_db(df, ticker)
        if _fv.startswith("v4"):
            df = self._add_signal_composite_features(df)

        return df

    def _load_fundamentals(self, ticker: str, as_of: date) -> Optional[pd.DataFrame]:
        symbol_raw = self._raw_symbol(ticker)
        db = Database()
        try:
            if not db.connect():
                return None
            q = """
                SELECT period_end_date, eps
                FROM fundamentals
                WHERE symbol = %s AND period_end_date <= %s
                ORDER BY period_end_date
            """
            rows = db.execute_query(q, (symbol_raw, as_of))
            if not rows:
                return None
            out = pd.DataFrame(rows, columns=["period_end_date", "eps"])
            self._meta_sources["fundamentals"] = f"db:fundamentals({symbol_raw})"
            self._meta_origins["fundamentals"] = "ingested:db"
            return out
        except Exception:
            return None
        finally:
            db.disconnect()

    # ---- v3 Market Environment features ----

    def _merge_market_environment_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        market_environment テーブルから全日次レコードを取得し、
        df の各日付に asof merge（backward）で結合する。
        ルックアヘッドバイアス防止: その日以前の最新データのみ使用。
        """
        import json as _json
        db = Database()
        try:
            if not db.connect():
                for col in self._market_env_feature_cols():
                    df[col] = np.nan
                return df

            rows = db.execute_query(
                "SELECT check_date, data FROM market_environment ORDER BY check_date"
            )
            if not rows:
                for col in self._market_env_feature_cols():
                    df[col] = np.nan
                return df

            me_records = []
            for check_date, data_raw in rows:
                data = data_raw if isinstance(data_raw, dict) else _json.loads(data_raw)
                r: Dict[str, Any] = {"check_date": pd.Timestamp(check_date)}

                def _sf(v: Any) -> Optional[float]:
                    try:
                        return float(v) if v is not None else np.nan
                    except (TypeError, ValueError):
                        return np.nan

                r["me_vix"] = _sf(data.get("vix"))
                r["me_vix_change_pct"] = _sf(data.get("vix_change_pct"))
                r["me_nasdaq_dd"] = _sf(data.get("nasdaq_dd_count"))
                r["me_sp500_dd"] = _sf(data.get("sp500_dd_count"))
                r["me_ad_ratio"] = _sf(data.get("ad_ratio"))
                r["me_new_highs"] = _sf(data.get("new_highs"))
                r["me_new_lows"] = _sf(data.get("new_lows"))
                r["me_hy_oas"] = _sf(data.get("hy_oas"))
                r["me_yield_spread_2y10y"] = _sf(data.get("yield_spread_2y10y"))
                r["me_nfci"] = _sf(data.get("nfci"))
                r["me_fci"] = _sf(data.get("fci"))
                r["me_erp"] = _sf(data.get("erp"))
                r["me_skew"] = _sf(data.get("skew"))
                r["me_put_call_proxy"] = _sf(data.get("put_call_proxy"))
                r["me_position_limit_pct"] = _sf(data.get("position_limit_pct"))
                r["me_ho_triggered"] = 1.0 if bool(data.get("ho_triggered")) else 0.0
                r["me_ho_alert_days_remaining"] = _sf(data.get("ho_alert_days_remaining"))
                r["me_ho_cluster_count"] = _sf(data.get("ho_cluster_count"))
                buy_perm = str(data.get("buy_permission", "")).upper()
                r["me_market_gate_on"] = 1.0 if buy_perm == "ON" else 0.0
                r["me_market_gate_half"] = 1.0 if buy_perm == "HALF" else 0.0
                r["me_market_gate_off"] = 1.0 if buy_perm == "OFF" else 0.0

                r["me_regime_score"] = float({
                    "強気": 2, "注意": 1, "新規買い停止": 0,
                }.get(str(data.get("regime", "")), np.nan) or np.nan)
                r["me_qqq_trend_score"] = float({
                    "uptrend": 2, "sideways": 1, "correction": 0, "downtrend": -1,
                }.get(str(data.get("qqq_trend", "")), np.nan) or np.nan)
                r["me_spy_trend_score"] = float({
                    "uptrend": 2, "sideways": 1, "correction": 0, "downtrend": -1,
                }.get(str(data.get("spy_trend", "")), np.nan) or np.nan)

                nasdaq_dd = r["me_nasdaq_dd"] if not np.isnan(r["me_nasdaq_dd"]) else 0.0
                sp500_dd = r["me_sp500_dd"] if not np.isnan(r["me_sp500_dd"]) else 0.0
                dd_total = nasdaq_dd + sp500_dd
                r["me_dd_total"] = dd_total
                r["me_dd_severe"] = 1.0 if dd_total >= 10 else 0.0
                r["me_nasdaq_dd_ge_4"] = 1.0 if nasdaq_dd >= 4 else 0.0
                r["me_nasdaq_dd_ge_5"] = 1.0 if nasdaq_dd >= 5 else 0.0
                r["me_sp500_dd_ge_4"] = 1.0 if sp500_dd >= 4 else 0.0
                r["me_sp500_dd_ge_5"] = 1.0 if sp500_dd >= 5 else 0.0
                r["me_dd_gate_risk"] = 1.0 if (nasdaq_dd >= 5 or sp500_dd >= 5) else 0.0

                vix = r["me_vix"]
                r["me_vix_above_20"] = 1.0 if (not np.isnan(vix) and vix >= 20) else (np.nan if np.isnan(vix) else 0.0)
                r["me_vix_above_30"] = 1.0 if (not np.isnan(vix) and vix >= 30) else (np.nan if np.isnan(vix) else 0.0)
                r["me_vix_18_20"] = 1.0 if (not np.isnan(vix) and 18 <= vix <= 20) else (np.nan if np.isnan(vix) else 0.0)
                r["me_vix_gt_20"] = 1.0 if (not np.isnan(vix) and vix > 20) else (np.nan if np.isnan(vix) else 0.0)

                hy_oas = r["me_hy_oas"]
                nfci = r["me_nfci"]
                credit = 0.0
                if not np.isnan(hy_oas) and hy_oas >= 5.0:
                    credit += 1.0
                if not np.isnan(nfci) and nfci > 0.5:
                    credit += 1.0
                r["me_credit_stress"] = credit

                me_records.append(r)

            me_df = pd.DataFrame(me_records).set_index("check_date").sort_index()

            df_reset = df.reset_index()
            date_col = df_reset.columns[0]
            df_reset[date_col] = pd.to_datetime(df_reset[date_col])
            df_sorted = df_reset.sort_values(date_col)

            me_reset = me_df.reset_index().rename(columns={"check_date": date_col})
            me_reset[date_col] = pd.to_datetime(me_reset[date_col])
            me_reset = me_reset.sort_values(date_col)

            merged = pd.merge_asof(
                df_sorted,
                me_reset,
                on=date_col,
                direction="backward",
            )
            merged.set_index(date_col, inplace=True)
            merged.index.name = df.index.name
            merged = merged.reindex(df.index)
            return merged

        except Exception as e:
            print(f"[PredictionService] _merge_market_environment_features error: {e}")
            for col in self._market_env_feature_cols():
                df[col] = np.nan
            return df
        finally:
            db.disconnect()

    def _market_env_feature_cols(self) -> List[str]:
        return [
            "me_vix", "me_vix_change_pct", "me_nasdaq_dd", "me_sp500_dd",
            "me_ad_ratio", "me_new_highs", "me_new_lows",
            "me_hy_oas", "me_yield_spread_2y10y", "me_nfci", "me_fci", "me_erp",
            "me_skew", "me_put_call_proxy", "me_position_limit_pct",
            "me_ho_triggered", "me_ho_alert_days_remaining", "me_ho_cluster_count",
            "me_market_gate_on", "me_market_gate_half", "me_market_gate_off",
            "me_regime_score", "me_qqq_trend_score", "me_spy_trend_score",
            "me_dd_total", "me_dd_severe",
            "me_nasdaq_dd_ge_4", "me_nasdaq_dd_ge_5",
            "me_sp500_dd_ge_4", "me_sp500_dd_ge_5",
            "me_dd_gate_risk",
            "me_vix_above_20", "me_vix_above_30", "me_vix_18_20", "me_vix_gt_20",
            "me_credit_stress",
        ]

    def _merge_fundamentals_ratios(self, df: pd.DataFrame, ticker: str) -> pd.DataFrame:
        """
        fundamentals_ratios_latest から銘柄固有の比率データを追加。
        1銘柄1行なので全行に同じ値を設定する。
        """
        symbol_raw = self._raw_symbol(ticker)
        db = Database()
        try:
            if not db.connect():
                for col in self._fundamentals_ratio_feature_cols():
                    df[col] = np.nan
                return df

            row = db.execute_query("""
                SELECT pe_ratio, forward_pe, roe_pct, roa_pct, net_margin_pct,
                       debt_equity, current_ratio, quick_ratio, asset_turnover, equity_ratio_pct
                FROM fundamentals_ratios_latest
                WHERE symbol = %s
            """, (symbol_raw,))

            def _sf(v: Any) -> float:
                try:
                    return float(v) if v is not None else np.nan
                except (TypeError, ValueError):
                    return np.nan

            if row and row[0]:
                r = row[0]
                df["fund_pe"] = _sf(r[0])
                df["fund_forward_pe"] = _sf(r[1])
                df["fund_roe"] = _sf(r[2])
                df["fund_roa"] = _sf(r[3])
                df["fund_net_margin"] = _sf(r[4])
                df["fund_debt_equity"] = _sf(r[5])
                df["fund_current_ratio"] = _sf(r[6])
                df["fund_quick_ratio"] = _sf(r[7])
                df["fund_asset_turnover"] = _sf(r[8])
                df["fund_equity_ratio"] = _sf(r[9])
            else:
                for col in self._fundamentals_ratio_feature_cols():
                    df[col] = np.nan
            return df
        except Exception as e:
            print(f"[PredictionService] _merge_fundamentals_ratios error: {e}")
            for col in self._fundamentals_ratio_feature_cols():
                df[col] = np.nan
            return df
        finally:
            db.disconnect()

    def _fundamentals_ratio_feature_cols(self) -> List[str]:
        return [
            "fund_pe", "fund_forward_pe", "fund_roe", "fund_roa", "fund_net_margin",
            "fund_debt_equity", "fund_current_ratio", "fund_quick_ratio",
            "fund_asset_turnover", "fund_equity_ratio",
        ]

    def _merge_advanced_ta_from_db(self, df: pd.DataFrame, ticker: str) -> pd.DataFrame:
        """
        indicator_daily テーブルの追加テクニカル指標を取得して結合。
        """
        symbol_key = self._symbol_key(ticker)
        db = Database()
        try:
            if not db.connect():
                for col in self._advanced_ta_feature_cols():
                    df[col] = np.nan
                return df

            # 実際に存在するカラムのみ取得
            wanted = [
                "vwap20", "obv", "mfi14", "adx14", "plus_di14", "minus_di14",
                "bb_upper20", "bb_lower20", "bb_width20", "bb_percent_b",
                "ichimoku_tenkan9", "ichimoku_kijun26",
                "ichimoku_senkou_a", "ichimoku_senkou_b", "ichimoku_chikou",
            ]
            existing_rows = db.execute_query(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'indicator_daily' AND column_name = ANY(%s)",
                (wanted,),
            )
            existing = [r[0] for r in (existing_rows or [])]
            if not existing:
                for col in self._advanced_ta_feature_cols():
                    df[col] = np.nan
                return df

            col_str = ", ".join(existing)
            rows = db.execute_query(
                f"SELECT trading_date, {col_str} FROM indicator_daily "
                "WHERE symbol_key = %s ORDER BY trading_date",
                (symbol_key,),
            )
            if not rows:
                for col in self._advanced_ta_feature_cols():
                    df[col] = np.nan
                return df

            ta_df = pd.DataFrame(rows, columns=["trading_date"] + existing)
            ta_df["trading_date"] = pd.to_datetime(ta_df["trading_date"])
            ta_df.set_index("trading_date", inplace=True)
            ta_df.columns = [f"ta_{c}" for c in ta_df.columns]
            for c in ta_df.columns:
                ta_df[c] = pd.to_numeric(ta_df[c], errors="coerce")

            df = df.join(ta_df, how="left")

            # 不足列は NaN で補完
            for col in self._advanced_ta_feature_cols():
                if col not in df.columns:
                    df[col] = np.nan

            # 派生特徴量
            if "ta_bb_upper20" in df.columns and "ta_bb_lower20" in df.columns:
                bb_w = df["ta_bb_upper20"] - df["ta_bb_lower20"]
                df["ta_bb_derived_width"] = bb_w
                df["ta_bb_derived_pct_b"] = (
                    (df["Close"] - df["ta_bb_lower20"]) / bb_w.replace(0, np.nan)
                )
            if "ta_plus_di14" in df.columns and "ta_minus_di14" in df.columns:
                df["ta_di_diff"] = df["ta_plus_di14"] - df["ta_minus_di14"]
            if "ta_ichimoku_senkou_a" in df.columns and "ta_ichimoku_senkou_b" in df.columns:
                df["ta_kumo_thickness"] = df["ta_ichimoku_senkou_a"] - df["ta_ichimoku_senkou_b"]

            return df
        except Exception as e:
            print(f"[PredictionService] _merge_advanced_ta_from_db error: {e}")
            for col in self._advanced_ta_feature_cols():
                df[col] = np.nan
            return df
        finally:
            db.disconnect()

    def _advanced_ta_feature_cols(self) -> List[str]:
        return [
            "ta_vwap20", "ta_obv", "ta_mfi14", "ta_adx14",
            "ta_plus_di14", "ta_minus_di14",
            "ta_bb_upper20", "ta_bb_lower20", "ta_bb_width20", "ta_bb_percent_b",
            "ta_ichimoku_tenkan9", "ta_ichimoku_kijun26",
            "ta_ichimoku_senkou_a", "ta_ichimoku_senkou_b", "ta_ichimoku_chikou",
            "ta_bb_derived_width", "ta_bb_derived_pct_b",
            "ta_di_diff", "ta_kumo_thickness",
        ]

    def _add_signal_composite_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add leak-safe composite features that approximate chart/CANSLIM/market gating signals."""
        out = df.copy()

        def _safe_num(col: str, default=np.nan) -> pd.Series:
            if col not in out.columns:
                return pd.Series(default, index=out.index, dtype=float)
            return pd.to_numeric(out[col], errors="coerce")

        close = _safe_num("Close")
        high = _safe_num("High")
        low = _safe_num("Low")
        vol = _safe_num("Volume")

        sma50_dist = _safe_num("sma50_dist")
        sma200_dist = _safe_num("sma200_dist")
        ema21_dist = _safe_num("ema21_dist")
        rsi14 = _safe_num("rsi14")
        macd_hist = _safe_num("macd_hist")
        atr14_pct = _safe_num("atr14_pct")
        vol_ratio_20 = _safe_num("vol_ratio_20")
        rel_qqq_5 = _safe_num("ret_rel_qqq_5")
        rel_semi_5 = _safe_num("ret_rel_semi_5")
        qqq_trend_50 = _safe_num("qqq_trend_50")
        me_regime_score = _safe_num("me_regime_score")
        me_position_limit_pct = _safe_num("me_position_limit_pct")
        me_dd_total = _safe_num("me_dd_total")
        me_vix = _safe_num("me_vix")
        me_credit_stress = _safe_num("me_credit_stress")
        me_qqq_trend_score = _safe_num("me_qqq_trend_score")
        fund_roe = _safe_num("fund_roe")
        fund_net_margin = _safe_num("fund_net_margin")
        fund_forward_pe = _safe_num("fund_forward_pe")
        relative_volume = _safe_num("relative_volume")
        distribution_day_count_25d = _safe_num("distribution_day_count_25d")
        vwap_deviation = _safe_num("vwap_deviation")
        failed_breakout_flag = _safe_num("failed_breakout_flag")
        ta_adx14 = _safe_num("ta_adx14")
        ta_mfi14 = _safe_num("ta_mfi14")
        ta_di_diff = _safe_num("ta_di_diff")
        ta_kumo_thickness = _safe_num("ta_kumo_thickness")

        # Approximate breakout signal quality (price trend + volume + follow-through + no recent failure).
        rolling_high_20_prev = high.rolling(20).max().shift(1)
        breakout_dist = close / rolling_high_20_prev.replace(0.0, np.nan) - 1.0
        out["sig_breakout_distance"] = breakout_dist
        out["sig_breakout_near_flag"] = ((breakout_dist >= -0.03) & (breakout_dist <= 0.05)).astype(float)
        out["sig_breakout_quality"] = (
            (breakout_dist.clip(-0.1, 0.1).fillna(0.0) * 8.0)
            + (vol_ratio_20.fillna(1.0) - 1.0).clip(-1.0, 3.0) * 0.8
            + vwap_deviation.fillna(0.0).clip(-0.2, 0.2) * 2.0
            + (macd_hist.fillna(0.0) > 0).astype(float) * 0.4
            - failed_breakout_flag.fillna(0.0) * 1.2
        )

        # Approximate CANSLIM-style score from growth/leadership/market components.
        out["sig_canslim_proxy_score"] = (
            (fund_roe.fillna(0.0) / 20.0).clip(-2.0, 3.0)
            + (fund_net_margin.fillna(0.0) / 15.0).clip(-2.0, 3.0)
            + rel_qqq_5.fillna(0.0).clip(-0.2, 0.2) * 6.0
            + rel_semi_5.fillna(0.0).clip(-0.2, 0.2) * 4.0
            + (vol_ratio_20.fillna(1.0) - 1.0).clip(-1.0, 2.0) * 0.5
            + (me_regime_score.fillna(1.0) - 1.0) * 0.8
        )
        out["sig_leadership_proxy"] = (
            rel_qqq_5.fillna(0.0).clip(-0.2, 0.2) * 5.0
            + rel_semi_5.fillna(0.0).clip(-0.2, 0.2) * 5.0
            + (sma50_dist.fillna(0.0) > 0).astype(float) * 0.5
        )

        # Market gate / risk state proxies.
        out["sig_market_gate_proxy"] = (
            (me_regime_score.fillna(1.0) - 1.0) * 1.5
            + (me_qqq_trend_score.fillna(1.0) - 1.0) * 0.8
            - (me_dd_total.fillna(0.0) / 10.0).clip(0.0, 2.0)
            - ((me_vix.fillna(15.0) - 20.0) / 10.0).clip(0.0, 2.0)
            - me_credit_stress.fillna(0.0) * 0.7
            + (me_position_limit_pct.fillna(50.0) / 100.0 - 0.5) * 1.2
        )
        out["sig_risk_off_penalty"] = (
            ((me_vix.fillna(15.0) - 20.0) / 10.0).clip(lower=0.0)
            + (me_dd_total.fillna(0.0) / 6.0).clip(lower=0.0, upper=3.0)
            + me_credit_stress.fillna(0.0)
            + (distribution_day_count_25d.fillna(0.0) / 8.0).clip(0.0, 2.0)
        )

        # Trend quality / continuation.
        out["sig_trend_quality"] = (
            sma50_dist.fillna(0.0).clip(-0.3, 0.3) * 3.0
            + sma200_dist.fillna(0.0).clip(-0.5, 0.5) * 2.0
            + ema21_dist.fillna(0.0).clip(-0.2, 0.2) * 2.0
            + qqq_trend_50.fillna(0.0).clip(-0.2, 0.2) * 2.0
            + (ta_adx14.fillna(20.0) - 20.0).clip(-20.0, 30.0) / 20.0
            + ta_di_diff.fillna(0.0).clip(-50.0, 50.0) / 25.0
        )
        out["sig_overheat_penalty"] = (
            ((rsi14.fillna(50.0) - 70.0) / 10.0).clip(lower=0.0)
            + ((ta_mfi14.fillna(50.0) - 80.0) / 10.0).clip(lower=0.0)
            + (atr14_pct.fillna(0.0) / 0.05).clip(0.0, 3.0) * 0.3
        )

        # Valuation/quality blend and execution readiness.
        out["sig_quality_value_balance"] = (
            (fund_roe.fillna(0.0) / 20.0).clip(-2.0, 3.0)
            + (fund_net_margin.fillna(0.0) / 15.0).clip(-2.0, 3.0)
            - (fund_forward_pe.fillna(25.0) / 30.0).clip(0.0, 4.0) * 0.5
        )
        out["sig_execution_readiness"] = (
            relative_volume.fillna(1.0).clip(0.0, 5.0) * 0.4
            + (vol_ratio_20.fillna(1.0).clip(0.0, 5.0)) * 0.4
            + (vwap_deviation.fillna(0.0).clip(-0.2, 0.2) * 4.0)
            + (ta_kumo_thickness.fillna(0.0).clip(-20.0, 20.0) / 20.0) * 0.3
        )

        # Final fused score for model to learn around.
        out["sig_fused_setup_score"] = (
            out["sig_breakout_quality"].fillna(0.0) * 0.30
            + out["sig_canslim_proxy_score"].fillna(0.0) * 0.25
            + out["sig_trend_quality"].fillna(0.0) * 0.20
            + out["sig_market_gate_proxy"].fillna(0.0) * 0.15
            + out["sig_execution_readiness"].fillna(0.0) * 0.10
            - out["sig_risk_off_penalty"].fillna(0.0) * 0.25
            - out["sig_overheat_penalty"].fillna(0.0) * 0.15
        )
        return out

    def _feature_columns_for_version(self, feature_cols_all: List[str], version: str) -> List[str]:
        v = str(version or "v2_ohlcv_ta_relregime_event")
        v3_extra = {
            "days_since_earnings",
            "eps_surprise",
            "guidance_surprise",
            "estimate_revision_1w",
            "estimate_revision_4w",
            "eps_surprise_missing",
            "guidance_surprise_missing",
            "estimate_revision_1w_missing",
            "estimate_revision_4w_missing",
            "relative_volume",
            "up_down_volume_ratio",
            "distribution_day_count_25d",
            "vwap_deviation",
            "failed_breakout_flag",
        }
        if v == "v3_ohlcv_ta_relregime_event_fundflow":
            return list(feature_cols_all)
        if v == "v4_ohlcv_ta_relregime_event_fundflow_signals":
            return list(feature_cols_all)
        if v == "v2_ohlcv_ta_relregime_event":
            # v3 専用プレフィックス（me_, fund_, ta_）も v2 から除外
            _v3_prefixes = ("me_", "fund_", "ta_")
            return [
                c for c in feature_cols_all
                if c not in v3_extra
                and not any(c.startswith(p) for p in _v3_prefixes)
            ]
        raise ValueError(
            f"Unsupported feature_version={v}. "
            "Use v2_ohlcv_ta_relregime_event, v3_ohlcv_ta_relregime_event_fundflow, "
            "or v4_ohlcv_ta_relregime_event_fundflow_signals."
        )

    def _fetch_benchmark(
        self, ticker: str, as_of: date, min_rows: int = 180
    ) -> Tuple[Optional[pd.DataFrame], str, Dict[str, Any]]:
        symbol_key = self._symbol_key(ticker)
        db_df, freshness = self._load_prices_from_db(symbol_key, as_of)
        if not self._is_db_data_sufficient(db_df, min_rows=min_rows):
            return None, symbol_key, freshness
        return db_df, symbol_key, freshness

    def _normalize_symbol_list(self, symbols: Optional[List[str]], default: List[str]) -> List[str]:
        src = symbols if symbols is not None else default
        out: List[str] = []
        for s in src:
            t = str(s).strip().upper()
            if not t:
                continue
            if ":" not in t:
                t = f"US:{t}"
            if t not in out:
                out.append(t)
        return out

    def _fetch_first_available(
        self,
        symbols: List[str],
        as_of: date,
        min_rows: int = 180,
    ) -> Tuple[Optional[pd.DataFrame], Optional[str], Optional[Dict[str, Any]]]:
        for sym in symbols:
            df, symbol_key, fresh = self._fetch_benchmark(sym, as_of, min_rows=min_rows)
            if df is not None and not df.empty:
                return df, symbol_key, fresh
        return None, None, None

    def _prepare_dataset(self, df: pd.DataFrame, as_of: date, cfg: PredictorConfig) -> Dict[str, Any]:
        out = df.copy().sort_index()
        h = cfg.horizon_trading_days

        out["target_close_h"] = out["Close"].shift(-h)
        out["target_return_pct_h"] = (out["target_close_h"] / out["Close"] - 1.0) * 100.0
        out["target_class"] = out["target_return_pct_h"].apply(
            lambda r: self._label_class(r, cfg.flat_band_pct) if pd.notna(r) else np.nan
        )
        out["future_date_h"] = out.index.to_series().shift(-h)

        cutoff = pd.Timestamp(as_of)
        train_mask = (
            (out.index <= cutoff)
            & (out["future_date_h"].notna())
            & (out["future_date_h"] <= cutoff)
            & out["target_class"].notna()
        )

        feature_cols_all = [
            c for c in out.columns
            if c
            not in [
                "Open", "High", "Low", "Close", "Volume",
                "target_close_h", "target_return_pct_h", "target_class", "future_date_h",
            ]
        ]
        feature_cols = self._feature_columns_for_version(feature_cols_all, cfg.feature_version)

        # Robust missing handling:
        # - all-NaN feature -> 0.0
        # - otherwise forward-fill then median-fill
        for c in feature_cols:
            s = out[c]
            if s.isna().all():
                out[c] = 0.0
            else:
                med = float(s.median()) if pd.notna(s.median()) else 0.0
                out[c] = s.ffill().fillna(med)

        model_df = out.loc[
            train_mask,
            feature_cols + ["target_class", "target_close_h", "target_return_pct_h", "Close"],
        ].dropna()
        if len(model_df) < cfg.min_train_rows:
            raise RuntimeError(
                f"Not enough rows for training: {len(model_df)} < {cfg.min_train_rows}"
            )

        pred_candidates = out.loc[out.index <= cutoff].dropna(subset=feature_cols)
        if pred_candidates.empty:
            raise RuntimeError("No prediction row available for as_of_date.")
        pred_row = pred_candidates.iloc[-1]

        train_range = [str(model_df.index.min().date()), str(model_df.index.max().date())]
        return {
            "full_df": out,
            "model_df": model_df,
            "feature_cols": feature_cols,
            "pred_row": pred_row,
            "train_range": train_range,
        }

    # ----------------------------- modeling -----------------------------
    def _fit_predict(
        self,
        prepared: Dict[str, Any],
        cfg: PredictorConfig,
        as_of: date,
        class_weight_mode: str = "off",
        class_weight: Optional[Dict[str, float]] = None,
        threshold_mode: str = "argmax",
        t_down: Optional[float] = None,
        t_up: Optional[float] = None,
    ) -> Dict[str, Any]:
        model_df = prepared["model_df"]
        feature_cols = prepared["feature_cols"]
        pred_row = prepared["pred_row"]

        X = model_df[feature_cols].to_numpy(dtype=float)
        y_cls = model_df["target_class"].astype(int).to_numpy()
        y_reg = model_df["target_close_h"].to_numpy(dtype=float)

        split = int(len(model_df) * 0.8)
        split = max(split, cfg.min_train_rows // 2)
        if split >= len(model_df) - 30:
            split = len(model_df) - 30
        if split < 100:
            raise RuntimeError("Not enough rows after time split.")

        X_train, X_cal = X[:split], X[split:]
        y_train_cls, y_cal_cls = y_cls[:split], y_cls[split:]
        y_train_reg, y_cal_reg = y_reg[:split], y_reg[split:]

        cv = self._sk_tscv(n_splits=3)
        class_weight_effective = self._resolve_class_weight_map(class_weight_mode, class_weight, y_cls)
        if class_weight_effective is None:
            sample_weight = self._class_sample_weight(y_cls)
        else:
            sample_weight = self._sample_weight_from_class_map(y_cls, class_weight_effective)
        models = []
        for seed in (11, 29, 47):
            clf = self._xgb.XGBClassifier(
                objective="multi:softprob",
                num_class=3,
                n_estimators=420,
                max_depth=4,
                learning_rate=0.035,
                subsample=0.9,
                colsample_bytree=0.9,
                eval_metric="mlogloss",
                tree_method="hist",
                random_state=seed,
            )
            calib = self._sk_calib(estimator=clf, method=cfg.calibration, cv=cv)
            calib.fit(X, y_cls, sample_weight=sample_weight)
            models.append(calib)

        x_pred = pred_row[feature_cols].to_numpy(dtype=float).reshape(1, -1)
        proba = np.mean([m.predict_proba(x_pred)[0] for m in models], axis=0)
        threshold_mode_used = str(threshold_mode or "argmax").lower()
        if threshold_mode_used not in ("argmax", "threshold"):
            threshold_mode_used = "argmax"
        thresholds_applied = None
        predicted_class = int(np.argmax(proba))
        if threshold_mode_used == "threshold":
            td = float(t_down) if t_down is not None else 0.35
            tu = float(t_up) if t_up is not None else 0.45
            predicted_class = int(self._predict_classes_threshold(np.asarray([proba], dtype=float), td, tu)[0])
            thresholds_applied = {"t_down": td, "t_up": tu}

        # Price point + conformal-style interval via calibration residual quantiles.
        reg = self._xgb.XGBRegressor(
            objective="reg:squarederror",
            n_estimators=380,
            max_depth=4,
            learning_rate=0.04,
            subsample=0.9,
            colsample_bytree=0.9,
            tree_method="hist",
            random_state=42,
        )
        reg.fit(X_train, y_train_reg)
        cal_pred = reg.predict(X_cal)
        resid = np.abs(y_cal_reg - cal_pred)
        q_abs = self._split_conformal_q(resid, cfg.target_interval_coverage)
        point_raw = float(reg.predict(x_pred)[0])
        point = max(0.0, float(point_raw))
        p10 = max(0.0, float(point_raw - q_abs))
        p50 = max(0.0, float(point_raw))
        p90 = max(0.0, float(point_raw + q_abs))

        if p10 > p50:
            p10, p50 = p50, p10
        if p50 > p90:
            p50, p90 = p90, p50

        probs_float = {"down": float(proba[0] * 100), "flat": float(proba[1] * 100), "up": float(proba[2] * 100)}
        probs_int = self._round_to_100(probs_float)
        action_policy = self._derive_action_policy(probs_int)

        return {
            "probs": {
                "up": probs_int["up"],
                "down": probs_int["down"],
                "flat": probs_int["flat"],
            },
            "action": action_policy["action"],
            "confidence": action_policy["confidence"],
            "margin": action_policy["margin"],
            "price_forecast": {
                "point": round(point, 4),
                "p10": round(p10, 4),
                "p50": round(p50, 4),
                "p90": round(p90, 4),
            },
            "meta": {
                **self._build_meta(cfg, prepared["train_range"], as_of, prepared["feature_cols"]),
                "target_name": "close_future",
                "target_unit": "price",
                "interval_target_coverage": cfg.target_interval_coverage,
                "interval_q": float(q_abs),
                "threshold_mode": threshold_mode_used,
                "thresholds_applied": thresholds_applied,
                "predicted_class": int(predicted_class),
                "class_weight_mode": str(class_weight_mode or "off").lower(),
                "class_weight": (
                    None if class_weight_effective is None
                    else {
                        "down": float(class_weight_effective[0]),
                        "flat": float(class_weight_effective[1]),
                        "up": float(class_weight_effective[2]),
                    }
                ),
            },
        }

    def _derive_action_policy(self, probs_int: Dict[str, int]) -> Dict[str, Any]:
        p_up = int(probs_int.get("up", 0))
        p_down = int(probs_int.get("down", 0))
        p_flat = int(probs_int.get("flat", 0))

        vals = sorted([p_up, p_down, p_flat], reverse=True)
        confidence = int(vals[0]) if vals else 0
        margin = int(vals[0] - vals[1]) if len(vals) >= 2 else 0

        # No-trade zone safety valve.
        c_min = 45
        delta_min = 6
        theta_buy = 8
        theta_sell = 8
        if confidence < c_min or margin < delta_min:
            action = "HOLD"
        elif (p_up - p_down) >= theta_buy:
            action = "BUY"
        elif (p_down - p_up) >= theta_sell:
            action = "SELL"
        else:
            action = "HOLD"

        return {
            "action": action,
            "confidence": confidence,
            "margin": margin,
        }

    def _derive_action_from_probs(
        self,
        p_up: float,
        p_down: float,
        p_flat: float,
        c_min: float = 0.45,
        delta_min: float = 0.06,
        theta_buy: float = 0.08,
        theta_sell: float = 0.08,
    ) -> int:
        pvals = np.array([float(p_up), float(p_down), float(p_flat)], dtype=float)
        pvals = np.clip(pvals, 0.0, 1.0)
        confidence = float(np.max(pvals))
        sorted_vals = np.sort(pvals)
        margin = float(sorted_vals[-1] - sorted_vals[-2]) if len(sorted_vals) >= 2 else 0.0
        if confidence < c_min or margin < delta_min:
            return 0
        if (p_up - p_down) >= theta_buy:
            return 1
        if (p_down - p_up) >= theta_sell:
            return -1
        return 0

    def _compute_trading_metrics(self, actions: List[int], step_returns: List[float]) -> Dict[str, Any]:
        acts = np.asarray(actions, dtype=int)
        rets = np.asarray(step_returns, dtype=float)
        trade_mask = acts != 0
        traded = rets[trade_mask]
        trade_count = int(np.sum(trade_mask))
        hit_rate = float(np.mean(traded > 0.0)) if trade_count > 0 else 0.0
        avg_return = float(np.mean(traded)) if trade_count > 0 else 0.0

        gross_profit = float(np.sum(traded[traded > 0.0])) if trade_count > 0 else 0.0
        gross_loss = float(np.sum(np.abs(traded[traded < 0.0]))) if trade_count > 0 else 0.0
        if gross_loss > 0.0:
            profit_factor = float(gross_profit / gross_loss)
        elif gross_profit > 0.0:
            profit_factor = float("inf")
        else:
            profit_factor = 0.0

        equity = np.cumprod(1.0 + rets) if len(rets) > 0 else np.array([1.0], dtype=float)
        peak = np.maximum.accumulate(equity)
        drawdown = (equity / np.where(peak > 0.0, peak, 1.0)) - 1.0
        max_drawdown = float(np.min(drawdown)) if len(drawdown) > 0 else 0.0

        return {
            "trade_count": trade_count,
            "hit_rate": hit_rate,
            "avg_return": avg_return,
            "max_drawdown": max_drawdown,
            "profit_factor": profit_factor,
            "action_counts": {
                "buy": int(np.sum(acts == 1)),
                "sell": int(np.sum(acts == -1)),
                "hold": int(np.sum(acts == 0)),
            },
            "cost_model": {
                "fee_bps_per_side": 5.0,
                "slippage_bps_per_side": 5.0,
                "round_trip_cost": 0.0020,
            },
        }

    def _walk_forward_backtest(
        self,
        prepared: Dict[str, Any],
        cfg: PredictorConfig,
        max_folds: Optional[int] = None,
        fold_step: Optional[int] = None,
        train_window: Optional[int] = None,
        use_ensemble: bool = True,
        purge_days: Optional[int] = None,
        embargo_mode: str = "pct",
        embargo_days: int = 5,
        embargo_pct: float = 0.01,
        prob_mode: str = "meta",
        temp_scale: Union[bool, str] = True,
        meta_band_mode: str = "fixed",
        meta_band_q: float = 0.65,
        meta_band_min_pct: float = 0.5,
        meta_band_max_pct: float = 10.0,
        label_mode: str = "fixed",
        target_return: float = 0.05,
        tbm_vol_span: int = 20,
        tbm_k: float = 1.5,
        class_weight_mode: str = "off",
        class_weight: Optional[Dict[str, float]] = None,
        threshold_mode: str = "argmax",
        t_down: Optional[float] = None,
        t_up: Optional[float] = None,
        threshold_search: bool = False,
        down_recall_min: float = 0.10,
        pred_down_min_pct: float = 0.05,
        threshold_grid: Optional[Dict[str, List[float]]] = None,
        t_buy_grid: Optional[List[float]] = None,
        fixed_thresholds: Optional[List[float]] = None,
        min_coverage: float = 0.05,
        min_trades: int = 50,
    ) -> Dict[str, Any]:
        model_df = prepared["model_df"]
        full_df = prepared["full_df"]
        feature_cols = prepared["feature_cols"]
        acc = self._sk_metrics["accuracy_score"]
        f1 = self._sk_metrics["f1_score"]
        ll = self._sk_metrics["log_loss"]
        target_transform = "raw_price"
        target_scale_factor = 1.0
        return_abs_med = float(
            np.nanmedian(np.abs(full_df.get("target_return_pct_h", pd.Series(dtype=float)).to_numpy(dtype=float)))
        ) if "target_return_pct_h" in full_df.columns else None

        X_all = model_df[feature_cols].to_numpy(dtype=float)
        y_cls_all = model_df["target_class"].astype(int).to_numpy()
        y_reg_all = model_df["target_close_h"].to_numpy(dtype=float)
        close_all = model_df["Close"].to_numpy(dtype=float)
        y_ret_all = (
            full_df.loc[model_df.index, "target_return_pct_h"].to_numpy(dtype=float)
            if "target_return_pct_h" in full_df.columns
            else np.zeros(len(model_df), dtype=float)
        )

        min_train = max(cfg.min_train_rows, 280)
        step = int(fold_step) if fold_step is not None and int(fold_step) > 0 else 30
        test_window = 15

        y_true: List[int] = []
        y_pred: List[int] = []
        prob_rows: List[np.ndarray] = []
        trade_actions: List[int] = []
        trade_step_returns: List[float] = []
        cover_flags: List[int] = []
        q_values: List[float] = []
        width_values: List[float] = []
        interval_clip_count = 0
        interval_total = 0
        prior_prob_rows: List[np.ndarray] = []
        mom_prob_rows: List[np.ndarray] = []
        prior_pred: List[int] = []
        mom_pred: List[int] = []
        last_fold_interval_audit: List[Dict[str, Any]] = []
        bias_values: List[float] = []
        bias_abs_values: List[float] = []
        temp_values: List[float] = []
        p_trade_values: List[float] = []
        side_samples_train = 0
        side_samples_calib = 0
        side_samples_test = 0
        meta_warnings: List[str] = []
        fold_stats: List[Dict[str, Any]] = []
        meta_rate_train_values: List[float] = []
        meta_rate_calib_values: List[float] = []
        meta_rate_test_values: List[float] = []
        extreme_meta_rate_folds = 0
        band_mode_used = str(meta_band_mode or "fixed").lower()
        if band_mode_used not in ("fixed", "quantile"):
            band_mode_used = "fixed"
        label_mode_used = str(label_mode or "fixed").lower()
        if label_mode_used not in ("fixed", "tbm", "up5_2w"):
            label_mode_used = "fixed"
        if label_mode_used == "up5_2w":
            return self._walk_forward_backtest_up5(
                model_df=model_df,
                full_df=full_df,
                feature_cols=feature_cols,
                cfg=cfg,
                max_folds=max_folds,
                fold_step=fold_step,
                train_window=train_window,
                use_ensemble=use_ensemble,
                purge_days=purge_days,
                embargo_mode=embargo_mode,
                embargo_days=embargo_days,
                embargo_pct=embargo_pct,
                temp_scale=temp_scale,
                calibration=cfg.calibration,
                target_return=float(target_return),
                threshold_search=bool(threshold_search),
                t_buy_grid=t_buy_grid,
                fixed_thresholds=fixed_thresholds,
                min_coverage=float(min_coverage),
                min_trades=int(min_trades),
            )
        meta_label_definition = "tbm_non_flat" if label_mode_used == "tbm" else "abs_return_gt_band"
        tbm_span = max(5, int(tbm_vol_span))
        tbm_k_used = float(max(0.1, tbm_k))
        band_q = float(meta_band_q)
        band_q = min(max(band_q, 0.01), 0.99)
        band_min = float(meta_band_min_pct)
        band_max = float(meta_band_max_pct)
        if band_min > band_max:
            band_min, band_max = band_max, band_min
        temp_scale_mode = self._resolve_temp_scale_mode(temp_scale)
        temp_grid = [round(x, 2) for x in np.arange(0.5, 3.01, 0.05)]
        class_weight_mode_used = str(class_weight_mode or "off").lower()
        class_weight_effective = self._resolve_class_weight_map(
            class_weight_mode_used,
            class_weight,
            y_cls_all,
        )
        threshold_mode_used = str(threshold_mode or "argmax").lower()
        if threshold_mode_used not in ("argmax", "threshold"):
            threshold_mode_used = "argmax"
        threshold_warnings: List[str] = []
        threshold_selection: Dict[str, Any] = {"best": None, "candidates": []}
        thresholds_applied: Optional[Dict[str, float]] = None
        warned_fold_direct = False

        n = len(model_df)
        purge_n = int(cfg.horizon_trading_days if purge_days is None else max(0, int(purge_days)))
        if str(embargo_mode).lower() == "days":
            embargo_n = max(0, int(embargo_days))
            embargo_mode_used = "days"
        else:
            embargo_mode_used = "pct"
            embargo_n = max(0, int(np.ceil(n * float(max(0.0, embargo_pct)))))
            if embargo_n == 0:
                embargo_n = 1

        fold_count = 0
        next_eligible_start = min_train
        seeds = (11, 29) if use_ensemble else (11,)
        for start in range(min_train, n - test_window, step):
            if start < next_eligible_start:
                continue
            if max_folds is not None and fold_count >= int(max_folds):
                break
            end = min(start + test_window, n)
            train_end = max(0, start - purge_n)
            if train_end <= 120:
                continue
            train_from = 0
            if train_window is not None and int(train_window) > 0:
                train_from = max(0, train_end - int(train_window))
            X_train = X_all[train_from:train_end]
            y_train_reg = y_reg_all[train_from:train_end]
            X_test = X_all[start:end]
            y_test_close = y_reg_all[start:end]
            tbm_vol_frac = None
            if label_mode_used == "tbm":
                tbm_vol_frac = self._tbm_fold_vol_frac(close_all[train_from:train_end], tbm_span)
                y_cls_fold_all = self._tbm_labels_with_scalar_vol(
                    close_all,
                    cfg.horizon_trading_days,
                    tbm_vol_frac,
                    tbm_k_used,
                )
                y_train_cls = y_cls_fold_all[train_from:train_end]
                y_test_cls = y_cls_fold_all[start:end]
            else:
                y_train_cls = y_cls_all[train_from:train_end]
                y_test_cls = y_cls_all[start:end]

            split = int(len(X_train) * 0.8)
            split = min(max(split, 120), len(X_train) - 30)
            if split <= 100:
                continue

            X_t, X_c = X_train[:split], X_train[split:]
            y_t, y_c = y_train_cls[:split], y_train_cls[split:]
            y_t_reg, y_c_reg = y_train_reg[:split], y_train_reg[split:]
            y_ret_train = y_ret_all[train_from:train_end]
            y_ret_t = y_ret_train[:split]
            y_ret_c = y_ret_train[split:]
            y_ret_test = y_ret_all[start:end]

            p_cal_raw: Optional[np.ndarray] = None
            p_test_raw: Optional[np.ndarray] = None
            mode_used = str(prob_mode or "meta").lower()
            fold_temp_stats: Dict[str, Any] = {}

            if mode_used == "direct":
                present_classes = np.array(sorted(list(set(y_t.tolist()))), dtype=int)
                if len(present_classes) < 2:
                    p_const = np.zeros((len(X_test), 3), dtype=float)
                    p_const[:, int(present_classes[0])] = 1.0
                    p_test_raw = p_const
                    p_cal_raw = np.tile(p_const[0], (len(X_c), 1))
                    meta_warnings.append(
                        f"fold_start={start}: direct fallback (single class in train={present_classes.tolist()})"
                    )
                else:
                    class_to_idx = {int(c): i for i, c in enumerate(present_classes.tolist())}
                    y_t_mapped = np.array([class_to_idx[int(v)] for v in y_t], dtype=int)
                    if class_weight_effective is None:
                        sw = self._class_sample_weight(y_t_mapped)
                    else:
                        sw = self._sample_weight_from_class_map(y_t, class_weight_effective)
                    p_cal_full_list: List[np.ndarray] = []
                    p_test_full_list: List[np.ndarray] = []
                    for seed in seeds:
                        clf = self._xgb.XGBClassifier(
                            objective="multi:softprob",
                            num_class=int(len(present_classes)),
                            n_estimators=180,
                            max_depth=4,
                            learning_rate=0.045,
                            subsample=0.9,
                            colsample_bytree=0.9,
                            eval_metric="mlogloss",
                            tree_method="hist",
                            random_state=seed,
                        )
                        clf.fit(X_t, y_t_mapped, sample_weight=sw)
                        p_cal_small = clf.predict_proba(X_c)
                        p_test_small = clf.predict_proba(X_test)
                        p_cal_full = np.full((len(X_c), 3), 1e-6, dtype=float)
                        p_test_full = np.full((len(X_test), 3), 1e-6, dtype=float)
                        for j, c in enumerate(present_classes.tolist()):
                            p_cal_full[:, int(c)] = p_cal_small[:, j]
                            p_test_full[:, int(c)] = p_test_small[:, j]
                        p_cal_full_list.append(self._safe_probs(p_cal_full))
                        p_test_full_list.append(self._safe_probs(p_test_full))

                    p_cal_raw = np.mean(p_cal_full_list, axis=0)
                    p_test_raw = np.mean(p_test_full_list, axis=0)
                    mc_calib = self._fit_multiclass_ovr_calibrator(p_cal_raw, y_c, cfg.calibration)
                    p_cal_raw = mc_calib(p_cal_raw)
                    p_test_raw = mc_calib(p_test_raw)
            else:
                band_used_pct = float(cfg.flat_band_pct)
                if label_mode_used == "tbm":
                    y_meta_t = (y_t != 1).astype(int)
                    y_meta_c = (y_c != 1).astype(int)
                    y_meta_train = (y_train_cls != 1).astype(int)
                    y_meta_test = (y_test_cls != 1).astype(int)
                else:
                    if band_mode_used == "quantile":
                        band_used_pct = float(np.nanquantile(np.abs(y_ret_t), band_q))
                        band_used_pct = float(np.clip(band_used_pct, band_min, band_max))
                    y_meta_t = (np.abs(y_ret_t) > band_used_pct).astype(int)
                    y_meta_c = (np.abs(y_ret_c) > band_used_pct).astype(int)
                    y_meta_train = (np.abs(y_ret_train) > band_used_pct).astype(int)
                    y_meta_test = (np.abs(y_ret_test) > band_used_pct).astype(int)
                meta_rate_train = float(np.mean(y_meta_train)) if len(y_meta_train) else 0.0
                meta_rate_calib = float(np.mean(y_meta_c)) if len(y_meta_c) else 0.0
                meta_rate_test = float(np.mean(y_meta_test)) if len(y_meta_test) else 0.0
                meta_rate_train_values.append(meta_rate_train)
                meta_rate_calib_values.append(meta_rate_calib)
                meta_rate_test_values.append(meta_rate_test)
                if (
                    meta_rate_train < 0.01 or meta_rate_train > 0.99
                    or meta_rate_calib < 0.01 or meta_rate_calib > 0.99
                    or meta_rate_test < 0.01 or meta_rate_test > 0.99
                ):
                    extreme_meta_rate_folds += 1
                    meta_warnings.append(
                        "fold_start={s}: extreme_meta_pos_rate train={tr:.4f} calib={ca:.4f} test={te:.4f}".format(
                            s=start, tr=meta_rate_train, ca=meta_rate_calib, te=meta_rate_test
                        )
                    )

                if class_weight_effective is None:
                    sw_meta = self._binary_sample_weight(y_meta_t)
                else:
                    flat_w = float(class_weight_effective[1])
                    event_w = float((class_weight_effective[0] + class_weight_effective[2]) / 2.0)
                    sw_meta = np.where(y_meta_t == 1, event_w, flat_w).astype(float)
                meta_raw_cal_list: List[np.ndarray] = []
                meta_raw_test_list: List[np.ndarray] = []
                for seed in seeds:
                    pos_meta = int(np.sum(y_meta_t == 1))
                    neg_meta = int(np.sum(y_meta_t == 0))
                    spw_meta = float(neg_meta / max(1, pos_meta))
                    clf_meta = self._xgb.XGBClassifier(
                        objective="binary:logistic",
                        n_estimators=180,
                        max_depth=4,
                        learning_rate=0.045,
                        subsample=0.9,
                        colsample_bytree=0.9,
                        eval_metric="logloss",
                        tree_method="hist",
                        random_state=seed,
                        scale_pos_weight=spw_meta,
                    )
                    clf_meta.fit(X_t, y_meta_t, sample_weight=sw_meta[: len(y_meta_t)])
                    meta_raw_cal_list.append(clf_meta.predict_proba(X_c)[:, 1])
                    meta_raw_test_list.append(clf_meta.predict_proba(X_test)[:, 1])
                meta_raw_cal = np.mean(meta_raw_cal_list, axis=0)
                meta_raw_test = np.mean(meta_raw_test_list, axis=0)
                meta_calibrator = self._fit_binary_calibrator(meta_raw_cal, y_meta_c, cfg.calibration)
                p_trade_cal = meta_calibrator(meta_raw_cal)
                p_trade_test = meta_calibrator(meta_raw_test)

                event_train_mask = y_meta_t == 1
                event_cal_mask = y_meta_c == 1
                side_samples_train_fold = int(np.sum(event_train_mask))
                side_samples_calib_fold = int(np.sum(event_cal_mask))
                side_samples_test_fold = int(np.sum(y_meta_test))
                side_samples_train += side_samples_train_fold
                side_samples_calib += side_samples_calib_fold
                side_samples_test += side_samples_test_fold
                p_up_cond_cal: np.ndarray
                p_up_cond_test: np.ndarray
                side_fallback_used = False

                if side_samples_train_fold < 60:
                    p_prior = 0.5
                    msg = f"fold_start={start}: side_model fallback (too few samples={side_samples_train_fold})"
                    meta_warnings.append(msg)
                    p_up_cond_cal = np.full(len(X_c), p_prior, dtype=float)
                    p_up_cond_test = np.full(len(X_test), p_prior, dtype=float)
                    side_fallback_used = True
                else:
                    if label_mode_used == "tbm":
                        y_side_train = (y_t[event_train_mask] == 2).astype(int)
                        y_side_cal = (y_c[event_cal_mask] == 2).astype(int) if side_samples_calib_fold > 0 else np.array([])
                    else:
                        y_side_train = (y_ret_t[event_train_mask] > 0.0).astype(int)
                        y_side_cal = (y_ret_c[event_cal_mask] > 0.0).astype(int) if side_samples_calib_fold > 0 else np.array([])
                    if len(np.unique(y_side_train)) < 2 or side_samples_calib_fold < 30 or len(np.unique(y_side_cal)) < 2:
                        p_prior = float(np.mean(y_side_train)) if len(y_side_train) else 0.5
                        msg = f"fold_start={start}: side_model fallback (side train/calib not enough class balance)"
                        meta_warnings.append(msg)
                        p_up_cond_cal = np.full(len(X_c), p_prior, dtype=float)
                        p_up_cond_test = np.full(len(X_test), p_prior, dtype=float)
                        side_fallback_used = True
                    else:
                        if class_weight_effective is None:
                            sw_side = self._binary_sample_weight(y_side_train)
                        else:
                            sw_side = np.where(
                                y_side_train == 1,
                                float(class_weight_effective[2]),
                                float(class_weight_effective[0]),
                            ).astype(float)
                        side_raw_cal_list: List[np.ndarray] = []
                        side_raw_test_list: List[np.ndarray] = []
                        for seed in seeds:
                            pos_side = int(np.sum(y_side_train == 1))
                            neg_side = int(np.sum(y_side_train == 0))
                            spw_side = float(neg_side / max(1, pos_side))
                            clf_side = self._xgb.XGBClassifier(
                                objective="binary:logistic",
                                n_estimators=180,
                                max_depth=4,
                                learning_rate=0.045,
                                subsample=0.9,
                                colsample_bytree=0.9,
                                eval_metric="logloss",
                                tree_method="hist",
                                random_state=seed,
                                scale_pos_weight=spw_side,
                            )
                            clf_side.fit(
                                X_t[event_train_mask],
                                y_side_train,
                                sample_weight=sw_side,
                            )
                            side_raw_cal_list.append(clf_side.predict_proba(X_c)[:, 1])
                            side_raw_test_list.append(clf_side.predict_proba(X_test)[:, 1])
                        side_raw_cal = np.mean(side_raw_cal_list, axis=0)
                        side_raw_test = np.mean(side_raw_test_list, axis=0)
                        side_calibrator = self._fit_binary_calibrator(
                            side_raw_cal[event_cal_mask],
                            y_side_cal,
                            cfg.calibration,
                        )
                        p_up_cond_cal = side_calibrator(side_raw_cal)
                        p_up_cond_test = side_calibrator(side_raw_test)

                p_cal_raw = self._compose_meta_probs(p_trade_cal, p_up_cond_cal)
                p_test_raw = self._compose_meta_probs(p_trade_test, p_up_cond_test)
                if temp_scale_mode == "fold":
                    p_trade_test_before = np.asarray(p_trade_test, dtype=float).copy()
                    t_trade = self._search_temperature_binary(p_trade_cal, y_meta_c, temp_grid)
                    p_trade_cal = self._temperature_scale_binary(p_trade_cal, t_trade)
                    p_trade_test = self._temperature_scale_binary(p_trade_test, t_trade)

                    t_side = 1.0
                    if side_samples_calib_fold > 0 and np.any(event_cal_mask):
                        if label_mode_used == "tbm":
                            y_side_cal_eval = (y_c[event_cal_mask] == 2).astype(int)
                        else:
                            y_side_cal_eval = (y_ret_c[event_cal_mask] > 0.0).astype(int)
                        p_side_cal_eval = p_up_cond_cal[event_cal_mask]
                        if len(y_side_cal_eval) > 0 and len(np.unique(y_side_cal_eval)) >= 2:
                            t_side = self._search_temperature_binary(p_side_cal_eval, y_side_cal_eval, temp_grid)
                    p_up_cond_cal = self._temperature_scale_binary(p_up_cond_cal, t_side)
                    p_up_cond_test = self._temperature_scale_binary(p_up_cond_test, t_side)

                    p_cal_after = self._compose_meta_probs(p_trade_cal, p_up_cond_cal)
                    p_test_after = self._compose_meta_probs(p_trade_test, p_up_cond_test)
                    extreme_before = self._extreme_rate(p_test_raw)
                    extreme_after = self._extreme_rate(p_test_after)
                    ll_before = float(ll(y_c, self._safe_probs(p_cal_raw), labels=[0, 1, 2]))
                    ll_after = float(ll(y_c, self._safe_probs(p_cal_after), labels=[0, 1, 2]))
                    br_before = float(self._multiclass_brier(y_c, self._safe_probs(p_cal_raw)))
                    br_after = float(self._multiclass_brier(y_c, self._safe_probs(p_cal_after)))
                    temp_applied = bool((ll_after <= ll_before) and (br_after <= br_before))
                    fallback_reason = None if temp_applied else "worse_logloss_or_brier_on_calib"
                    if not temp_applied:
                        p_cal_after = self._safe_probs(p_cal_raw)
                        p_test_after = self._safe_probs(p_test_raw)
                    fold_temp_stats = {
                        "temp_T_trade": float(t_trade),
                        "temp_T_side": float(t_side),
                        "extreme_rate_before_temp": float(extreme_before),
                        "extreme_rate_after_temp": float(extreme_after),
                        "logloss_calib_before_temp": ll_before,
                        "logloss_calib_after_temp": ll_after,
                        "brier_calib_before_temp": br_before,
                        "brier_calib_after_temp": br_after,
                        "temp_applied": temp_applied,
                        "temp_fallback_reason": fallback_reason,
                        "p_trade_mean_before_temp": float(np.mean(p_trade_test_before)) if len(p_trade_test_before) else 0.0,
                        "p_trade_std_before_temp": float(np.std(p_trade_test_before)) if len(p_trade_test_before) else 0.0,
                    }
                    # recompute after-temp distribution stats for diagnostic visibility
                    p_trade_after_test = 1.0 - p_test_after[:, 1]
                    fold_temp_stats["p_trade_mean_after_temp"] = float(np.mean(p_trade_after_test))
                    fold_temp_stats["p_trade_std_after_temp"] = float(np.std(p_trade_after_test))
                    print(
                        "[FOLD TEMP] start={s} T_trade={tt:.2f} T_side={ts:.2f} "
                        "extreme(before/after)={eb:.4f}/{ea:.4f} p_trade_std(before/after)={sb:.4f}/{sa:.4f}".format(
                            s=start,
                            tt=float(t_trade),
                            ts=float(t_side),
                            eb=float(extreme_before),
                            ea=float(extreme_after),
                            sb=float(fold_temp_stats["p_trade_std_before_temp"]),
                            sa=float(fold_temp_stats["p_trade_std_after_temp"]),
                        )
                    )
                    p_cal_raw = p_cal_after
                    p_test_raw = p_test_after
                fold_stats.append(
                    {
                        "fold_id": int(fold_count + 1),
                        "n_train": int(len(X_train)),
                        "n_calib": int(len(X_c)),
                        "n_test": int(len(X_test)),
                        "label_mode": label_mode_used,
                        "tbm_vol_frac": float(tbm_vol_frac) if tbm_vol_frac is not None else None,
                        "tbm_barrier_pct": float(100.0 * tbm_k_used * tbm_vol_frac) if tbm_vol_frac is not None else None,
                        "class_ratio_train": self._class_ratio(y_train_cls),
                        "class_ratio_calib": self._class_ratio(y_c),
                        "class_ratio_test": self._class_ratio(y_test_cls),
                        "flat_ratio_train": float(np.mean(y_train_cls == 1)),
                        "flat_ratio_calib": float(np.mean(y_c == 1)),
                        "flat_ratio_test": float(np.mean(y_test_cls == 1)),
                        "meta_pos_rate_train": meta_rate_train,
                        "meta_pos_rate_calib": meta_rate_calib,
                        "meta_pos_rate_test": meta_rate_test,
                        "meta_flat_complement_ok": bool(
                            abs(meta_rate_train - (1.0 - float(np.mean(y_train_cls == 1)))) <= 1e-6
                            and abs(meta_rate_calib - (1.0 - float(np.mean(y_c == 1)))) <= 1e-6
                            and abs(meta_rate_test - (1.0 - float(np.mean(y_test_cls == 1)))) <= 1e-6
                        ),
                        "meta_flat_complement_error": {
                            "train": float(abs(meta_rate_train - (1.0 - float(np.mean(y_train_cls == 1))))),
                            "calib": float(abs(meta_rate_calib - (1.0 - float(np.mean(y_c == 1))))),
                            "test": float(abs(meta_rate_test - (1.0 - float(np.mean(y_test_cls == 1))))),
                        },
                        "band_used_pct": float(band_used_pct),
                        "side_samples_train": side_samples_train_fold,
                        "side_samples_calib": side_samples_calib_fold,
                        "side_samples_test": side_samples_test_fold,
                        "side_fallback_used": bool(side_fallback_used),
                        "p_trade_mean_test": float(np.mean(p_trade_test)) if len(p_trade_test) else 0.0,
                        "p_trade_std_test": float(np.std(p_trade_test)) if len(p_trade_test) else 0.0,
                    }
                )
                if fold_temp_stats:
                    fold_stats[-1].update(fold_temp_stats)

            p_cal_raw = self._safe_probs(p_cal_raw)
            p_test_raw = self._safe_probs(p_test_raw)
            if temp_scale_mode == "global":
                best_t = self._search_temperature(p_cal_raw, y_c, temp_grid)
                p_cal_t = self._temperature_scale(p_cal_raw, best_t)
                p_test_t = self._temperature_scale(p_test_raw, best_t)
                ll_before = float(ll(y_c, p_cal_raw, labels=[0, 1, 2]))
                ll_after = float(ll(y_c, p_cal_t, labels=[0, 1, 2]))
                br_before = float(self._multiclass_brier(y_c, p_cal_raw))
                br_after = float(self._multiclass_brier(y_c, p_cal_t))
                if ll_after <= ll_before and br_after <= br_before:
                    p = p_test_t
                    global_temp_applied = True
                    global_temp_reason = None
                else:
                    p = p_test_raw
                    best_t = 1.0
                    global_temp_applied = False
                    global_temp_reason = "worse_logloss_or_brier_on_calib"
            elif temp_scale_mode == "fold":
                if mode_used == "meta":
                    best_t = 1.0
                    p = self._safe_probs(p_test_raw)
                    global_temp_applied = True
                    global_temp_reason = None
                else:
                    if not warned_fold_direct:
                        meta_warnings.append(
                            "temp_scale=fold currently applies to meta mode only; direct mode falls back to off."
                        )
                        warned_fold_direct = True
                    best_t = 1.0
                    p = self._safe_probs(p_test_raw)
                    global_temp_applied = False
                    global_temp_reason = "fold_mode_direct_not_supported"
            else:
                best_t = 1.0
                p = self._safe_probs(p_test_raw)
                global_temp_applied = True
                global_temp_reason = None
            temp_values.extend([float(best_t)] * len(X_test))
            p_trade_values.extend((1.0 - p[:, 1]).astype(float).tolist())
            if fold_stats:
                fold_stats[-1]["global_temp_applied"] = bool(global_temp_applied)
                fold_stats[-1]["global_temp_fallback_reason"] = global_temp_reason
            for i in range(len(X_test)):
                p_down_i = float(p[i, 0])
                p_flat_i = float(p[i, 1])
                p_up_i = float(p[i, 2])
                action_i = self._derive_action_from_probs(
                    p_up_i, p_down_i, p_flat_i, c_min=0.45, delta_min=0.06, theta_buy=0.08, theta_sell=0.08
                )
                trade_actions.append(int(action_i))
                ret_pct = float(y_ret_test[i]) if i < len(y_ret_test) else 0.0
                gross_ret = (ret_pct / 100.0) * float(action_i)
                total_cost = 0.0020 if action_i != 0 else 0.0
                trade_step_returns.append(float(gross_ret - total_cost))
            y_true.extend(y_test_cls.tolist())
            prob_rows.extend([r for r in p])

            prior_probs = np.bincount(y_train_cls, minlength=3).astype(float)
            prior_probs = np.clip(prior_probs / max(1.0, float(np.sum(prior_probs))), 1e-9, 1.0)
            prior_probs = prior_probs / np.sum(prior_probs)
            prior_batch = np.tile(prior_probs, (len(X_test), 1))
            prior_prob_rows.extend([r for r in prior_batch])
            prior_cls = int(np.argmax(prior_probs))
            prior_pred.extend([prior_cls] * len(X_test))

            mom_feature = "ret_5" if "ret_5" in feature_cols else ("ret_1" if "ret_1" in feature_cols else None)
            mom_batch: List[np.ndarray] = []
            mom_cls_batch: List[int] = []
            for i in range(len(X_test)):
                if mom_feature is None:
                    cls = 1
                else:
                    ret_pct = float(model_df.iloc[start + i][mom_feature]) * 100.0
                    cls = int(self._label_class(ret_pct, cfg.flat_band_pct))
                v = np.zeros(3, dtype=float)
                v[cls] = 1.0
                mom_batch.append(v)
                mom_cls_batch.append(cls)
            mom_prob_rows.extend(mom_batch)
            mom_pred.extend(mom_cls_batch)

            reg = self._xgb.XGBRegressor(
                objective="reg:squarederror",
                n_estimators=220,
                max_depth=4,
                learning_rate=0.05,
                subsample=0.9,
                colsample_bytree=0.9,
                tree_method="hist",
                random_state=42,
            )
            reg.fit(X_t, y_t_reg)
            c_pred = reg.predict(X_c)
            bias = float(np.mean(y_c_reg - c_pred))
            bias_abs = float(np.mean(np.abs(y_c_reg - c_pred)))
            bias_values.extend([bias] * len(X_test))
            bias_abs_values.extend([bias_abs] * len(X_test))
            c_pred_adj = c_pred + bias
            resid = np.abs(y_c_reg - c_pred_adj)
            q_abs = self._split_conformal_q(resid, cfg.target_interval_coverage)
            t_pred = reg.predict(X_test) + bias
            low_raw = t_pred - q_abs
            high_raw = t_pred + q_abs
            low = np.maximum(0.0, low_raw)
            high = np.maximum(low, high_raw)
            interval_clip_count += int(np.sum(low_raw < 0.0))
            interval_total += int(len(X_test))
            q_values.extend([float(q_abs)] * len(X_test))
            width_values.extend((high - low).astype(float).tolist())
            cov = ((y_test_close >= low) & (y_test_close <= high)).astype(int)
            cover_flags.extend(cov.tolist())
            fold_audit: List[Dict[str, Any]] = []
            for i in range(len(X_test)):
                row_idx = start + i
                dt = model_df.index[row_idx]
                dt_text = str(dt.date()) if hasattr(dt, "date") else str(dt)
                fold_audit.append(
                    {
                        "date": dt_text,
                        "t_index": int(row_idx),
                        "y_true": float(y_test_close[i]),
                        "y_pred": float(t_pred[i]),
                        "y_true_raw": float(y_test_close[i] * target_scale_factor),
                        "y_pred_raw": float(t_pred[i] * target_scale_factor),
                        "q": float(q_abs),
                        "lower": float(low[i]),
                        "upper": float(high[i]),
                        "raw_close_t": float(full_df.loc[dt, "Close"]) if dt in full_df.index else None,
                        "raw_close_future": float(y_test_close[i] * target_scale_factor),
                        "covered": int(cov[i]),
                        "target_name": "close_future",
                        "target_unit": "price",
                        "target_transform": target_transform,
                        "target_scale_factor": float(target_scale_factor),
                        "bias": float(bias),
                        "bias_abs": float(bias_abs),
                    }
                )
            last_fold_interval_audit = fold_audit
            fold_count += 1
            next_eligible_start = end + embargo_n

        if len(y_true) == 0:
            raise RuntimeError("Backtest failed: no valid walk-forward folds.")

        probs = np.vstack(prob_rows)
        y_true_arr = np.array(y_true, dtype=int)
        if threshold_mode_used == "argmax":
            y_pred_arr = np.argmax(probs, axis=1).astype(int)
            thresholds_applied = None
        else:
            td_values = self._default_threshold_down_grid()
            tu_values = self._default_threshold_up_grid()
            if threshold_grid is not None:
                td_values = [float(v) for v in threshold_grid.get("down", td_values)]
                tu_values = [float(v) for v in threshold_grid.get("up", tu_values)]
            search_used = bool(threshold_search or t_down is None or t_up is None)
            if search_used:
                candidates = self._threshold_candidates(
                    y_true_arr=y_true_arr,
                    probs=probs,
                    t_down_values=td_values,
                    t_up_values=tu_values,
                )
                selected = self._select_threshold_candidate(
                    candidates=candidates,
                    down_recall_min=float(down_recall_min),
                    pred_down_min_pct=float(pred_down_min_pct),
                )
                best = selected["best"]
                ranked_pool = selected["ranked"]
                if selected["fallback_unconstrained"]:
                    threshold_warnings.append(
                        "threshold_search: no candidates satisfy constraints; fallback to unconstrained best."
                    )
                thresholds_applied = {"t_down": float(best["t_down"]), "t_up": float(best["t_up"])}
                threshold_selection = {
                    "best": best,
                    "candidates": ranked_pool[:10],
                    "constraints": {
                        "down_recall_min": float(down_recall_min),
                        "pred_down_min_pct": float(pred_down_min_pct),
                        "satisfied_count": int(selected["satisfied_count"]),
                        "searched_count": int(len(candidates)),
                    },
                }
            else:
                thresholds_applied = {"t_down": float(t_down), "t_up": float(t_up)}
                threshold_selection = {
                    "best": {
                        "t_down": float(t_down),
                        "t_up": float(t_up),
                    },
                    "candidates": [],
                }
            y_pred_arr = self._predict_classes_threshold(
                probs,
                float(thresholds_applied["t_down"]),
                float(thresholds_applied["t_up"]),
            )

        brier = self._multiclass_brier(y_true_arr, probs)
        ece = self._ece_multiclass(y_true_arr, probs, n_bins=10)
        coverage = float(np.mean(np.array(cover_flags, dtype=float)))
        interval_q_mean = float(np.mean(np.array(q_values, dtype=float))) if q_values else 0.0
        interval_width_mean = float(np.mean(np.array(width_values, dtype=float))) if width_values else 0.0
        clip_ratio = float(interval_clip_count / max(1, interval_total))
        cm = self._sk_metrics["confusion_matrix"](y_true_arr, y_pred_arr, labels=[0, 1, 2]).tolist()
        pr, rc, f1_cls, _ = self._sk_metrics["prfs"](y_true_arr, y_pred_arr, labels=[0, 1, 2], zero_division=0)
        cls_ratio = self._class_ratio(y_true_arr)
        prior_probs_arr = np.vstack(prior_prob_rows)
        mom_probs_arr = np.vstack(mom_prob_rows)
        mom_probs_arr = np.clip(mom_probs_arr, 1e-6, 1.0)
        mom_probs_arr = mom_probs_arr / np.sum(mom_probs_arr, axis=1, keepdims=True)
        prior_pred_arr = np.array(prior_pred, dtype=int)
        mom_pred_arr = np.array(mom_pred, dtype=int)
        interval_target_name = "close_future"
        interval_target_unit = "price"
        interval_y_source = "target_close_h"
        target_mismatch = not (
            interval_target_name == "close_future"
            and interval_target_unit == "price"
            and interval_y_source == "target_close_h"
        )
        if target_mismatch:
            print(
                "[WARN] Interval coverage target mismatch detected: "
                f"name={interval_target_name} unit={interval_target_unit} y_source={interval_y_source}"
            )
        if extreme_meta_rate_folds > 0:
            meta_warnings.append(
                f"extreme_meta_label_rate_folds={extreme_meta_rate_folds}/{max(1, fold_count)} "
                "(meta_label_rate outside [1%,99%])"
            )
        if return_abs_med is not None and return_abs_med < 0.2:
            meta_warnings.append(
                f"target_return_pct_h median abs is very small ({return_abs_med:.4f}); "
                "check return unit consistency (pct vs fraction)."
            )

        model_logloss = float(ll(y_true_arr, probs, labels=[0, 1, 2]))
        prior_logloss = float(ll(y_true_arr, prior_probs_arr, labels=[0, 1, 2]))
        mom_logloss = float(ll(y_true_arr, mom_probs_arr, labels=[0, 1, 2]))
        prior_brier = float(self._multiclass_brier(y_true_arr, prior_probs_arr))
        mom_brier = float(self._multiclass_brier(y_true_arr, mom_probs_arr))
        class_ratio_after_meta = self._class_ratio(y_true_arr)
        trading_metrics = self._compute_trading_metrics(trade_actions, trade_step_returns)
        return {
            "accuracy": float(acc(y_true_arr, y_pred_arr)),
            "macro_f1": float(f1(y_true_arr, y_pred_arr, average="macro")),
            "logloss": model_logloss,
            "brier": float(brier),
            "ece": float(ece),
            "interval_target_coverage": float(cfg.target_interval_coverage),
            "interval_q": interval_q_mean,
            "interval_width_mean": interval_width_mean,
            "interval_coverage": float(coverage),
            "interval_target_name": interval_target_name,
            "interval_target_unit": interval_target_unit,
            "target_transform": target_transform,
            "target_scale_factor": float(target_scale_factor),
            "interval_clip_ratio": clip_ratio,
            "interval_target_mismatch_warn": bool(target_mismatch),
            "interval_target_y_source": interval_y_source,
            "interval_audit_sample": last_fold_interval_audit[:50],
            "bias": float(np.mean(np.array(bias_values, dtype=float))) if bias_values else 0.0,
            "bias_abs": float(np.mean(np.array(bias_abs_values, dtype=float))) if bias_abs_values else 0.0,
            "class_ratio": cls_ratio,
            "class_ratio_after_meta": class_ratio_after_meta,
            "confusion_matrix": cm,
            "class_metrics": {
                "down": {"precision": float(pr[0]), "recall": float(rc[0]), "f1": float(f1_cls[0])},
                "flat": {"precision": float(pr[1]), "recall": float(rc[1]), "f1": float(f1_cls[1])},
                "up": {"precision": float(pr[2]), "recall": float(rc[2]), "f1": float(f1_cls[2])},
            },
            "trading_metrics": trading_metrics,
            "meta_metrics": {
                "prob_mode": mode_used if mode_used in ("direct", "meta") else "meta",
                "label_mode": label_mode_used,
                "meta_label_definition": meta_label_definition,
                "tbm_policy": {"vol_span": int(tbm_span), "k": float(tbm_k_used)},
                "return_unit": "pct",
                "band_unit": "pct",
                "flat_band_pct": float(cfg.flat_band_pct),
                "meta_band_mode": band_mode_used,
                "meta_band_policy": {
                    "q_target": float(band_q),
                    "band_min_pct": float(band_min),
                    "band_max_pct": float(band_max),
                },
                "target_return_abs_median_pct": return_abs_med,
                "meta_label_rate_train": float(np.mean(np.array(meta_rate_train_values, dtype=float))) if meta_rate_train_values else None,
                "meta_label_rate_calib": float(np.mean(np.array(meta_rate_calib_values, dtype=float))) if meta_rate_calib_values else None,
                "meta_label_rate_test": float(np.mean(np.array(meta_rate_test_values, dtype=float))) if meta_rate_test_values else None,
                "p_trade_mean": float(np.mean(np.array(p_trade_values, dtype=float))) if p_trade_values else 0.0,
                "p_trade_std": float(np.std(np.array(p_trade_values, dtype=float))) if p_trade_values else 0.0,
                "side_samples_train": int(side_samples_train),
                "side_samples_calib": int(side_samples_calib),
                "side_samples_test": int(side_samples_test),
                "fold_stats": fold_stats,
                "warnings": meta_warnings,
            },
            "threshold_selection": threshold_selection,
            "warnings": threshold_warnings,
            "thresholds_applied": thresholds_applied,
            "threshold_mode": threshold_mode_used,
            "class_weight_mode": class_weight_mode_used,
            "class_weight": (
                None if class_weight_effective is None
                else {
                    "down": float(class_weight_effective[0]),
                    "flat": float(class_weight_effective[1]),
                    "up": float(class_weight_effective[2]),
                }
            ),
            "prob_calibration": {
                "ece_bins": self._reliability_bins_multiclass(y_true_arr, probs, n_bins=10),
                "reliability_bins_pmax_033_100": self._reliability_bins_multiclass(
                    y_true_arr, probs, n_bins=10, conf_min=0.33, conf_max=1.0
                ),
                "temp_scale_mode": temp_scale_mode,
            },
            "temperature_scaling": {
                "enabled": bool(temp_scale_mode != "off"),
                "best_T": float(np.mean(np.array(temp_values, dtype=float))) if temp_values else 1.0,
                "search_grid": temp_grid if temp_scale_mode != "off" else [1.0],
            },
            "validation": {
                "purge_days": int(purge_n),
                "embargo_mode": embargo_mode_used,
                "embargo_days": int(embargo_n),
                "embargo_pct": float(embargo_pct),
                "folds_used": int(fold_count),
            },
            "baselines": {
                "baseline_prior": {
                    "accuracy": float(acc(y_true_arr, prior_pred_arr)),
                    "macro_f1": float(f1(y_true_arr, prior_pred_arr, average="macro")),
                    "logloss": prior_logloss,
                    "brier": prior_brier,
                },
                "baseline_momentum": {
                    "accuracy": float(acc(y_true_arr, mom_pred_arr)),
                    "macro_f1": float(f1(y_true_arr, mom_pred_arr, average="macro")),
                    "logloss": mom_logloss,
                    "brier": mom_brier,
                },
                "model_vs_baseline": {
                    "beats_prior_logloss": bool(model_logloss < prior_logloss),
                    "beats_prior_brier": bool(float(brier) < prior_brier),
                    "beats_momentum_logloss": bool(model_logloss < mom_logloss),
                    "beats_momentum_brier": bool(float(brier) < mom_brier),
                },
            },
        }

    def _walk_forward_backtest_up5(
        self,
        model_df: pd.DataFrame,
        full_df: pd.DataFrame,
        feature_cols: List[str],
        cfg: PredictorConfig,
        max_folds: Optional[int],
        fold_step: Optional[int],
        train_window: Optional[int],
        use_ensemble: bool,
        purge_days: Optional[int],
        embargo_mode: str,
        embargo_days: int,
        embargo_pct: float,
        temp_scale: Union[bool, str],
        calibration: str,
        target_return: float,
        threshold_search: bool,
        t_buy_grid: Optional[List[float]],
        fixed_thresholds: Optional[List[float]],
        min_coverage: float,
        min_trades: int,
    ) -> Dict[str, Any]:
        acc = self._sk_metrics["accuracy_score"]
        f1 = self._sk_metrics["f1_score"]
        ll = self._sk_metrics["log_loss"]
        cm_func = self._sk_metrics["confusion_matrix"]

        X_all = model_df[feature_cols].to_numpy(dtype=float)
        y_ret_frac_all = (
            full_df.loc[model_df.index, "target_return_pct_h"].to_numpy(dtype=float) / 100.0
            if "target_return_pct_h" in full_df.columns
            else np.zeros(len(model_df), dtype=float)
        )
        y_bin_all = (y_ret_frac_all >= float(target_return)).astype(int)

        min_train = max(cfg.min_train_rows, 280)
        step = int(fold_step) if fold_step is not None and int(fold_step) > 0 else 30
        test_window = 15
        temp_scale_mode = self._resolve_temp_scale_mode(temp_scale)
        temp_grid = [round(x, 2) for x in np.arange(0.5, 3.01, 0.05)]

        y_true: List[int] = []
        prob_up_rows: List[float] = []
        realized_returns: List[float] = []
        eval_dates: List[str] = []
        temp_values: List[float] = []
        warnings: List[str] = []
        fold_count = 0
        n = len(model_df)
        purge_n = int(cfg.horizon_trading_days if purge_days is None else max(0, int(purge_days)))
        if str(embargo_mode).lower() == "days":
            embargo_n = max(0, int(embargo_days))
            embargo_mode_used = "days"
        else:
            embargo_mode_used = "pct"
            embargo_n = max(0, int(np.ceil(n * float(max(0.0, embargo_pct)))))
            if embargo_n == 0:
                embargo_n = 1

        next_eligible_start = min_train
        seeds = (11, 29) if use_ensemble else (11,)

        for start in range(min_train, n - test_window, step):
            if start < next_eligible_start:
                continue
            if max_folds is not None and fold_count >= int(max_folds):
                break
            end = min(start + test_window, n)
            train_end = max(0, start - purge_n)
            if train_end <= 120:
                continue
            train_from = 0
            if train_window is not None and int(train_window) > 0:
                train_from = max(0, train_end - int(train_window))

            X_train = X_all[train_from:train_end]
            X_test = X_all[start:end]
            y_train = y_bin_all[train_from:train_end]
            y_test = y_bin_all[start:end]
            y_ret_test = y_ret_frac_all[start:end]

            split = int(len(X_train) * 0.8)
            split = min(max(split, 120), len(X_train) - 30)
            if split <= 100:
                continue

            X_t, X_c = X_train[:split], X_train[split:]
            y_t, y_c = y_train[:split], y_train[split:]

            if len(np.unique(y_t)) < 2:
                p_prior = float(np.mean(y_t)) if len(y_t) else 0.0
                p_cal = np.full(len(X_c), p_prior, dtype=float)
                p_test = np.full(len(X_test), p_prior, dtype=float)
                warnings.append(f"fold_start={start}: binary fallback (single class in train)")
            else:
                sw = self._binary_sample_weight(y_t)
                p_cal_raw_list: List[np.ndarray] = []
                p_test_raw_list: List[np.ndarray] = []
                for seed in seeds:
                    pos = int(np.sum(y_t == 1))
                    neg = int(np.sum(y_t == 0))
                    spw = float(neg / max(1, pos))
                    clf = self._xgb.XGBClassifier(
                        objective="binary:logistic",
                        n_estimators=180,
                        max_depth=4,
                        learning_rate=0.045,
                        subsample=0.9,
                        colsample_bytree=0.9,
                        eval_metric="logloss",
                        tree_method="hist",
                        random_state=seed,
                        scale_pos_weight=spw,
                    )
                    clf.fit(X_t, y_t, sample_weight=sw)
                    p_cal_raw_list.append(clf.predict_proba(X_c)[:, 1])
                    p_test_raw_list.append(clf.predict_proba(X_test)[:, 1])
                p_cal_raw = np.mean(p_cal_raw_list, axis=0)
                p_test_raw = np.mean(p_test_raw_list, axis=0)
                cal_fn = self._fit_binary_calibrator(p_cal_raw, y_c, calibration)
                p_cal = cal_fn(p_cal_raw)
                p_test = cal_fn(p_test_raw)

            best_t = 1.0
            if temp_scale_mode != "off":
                t_cur = self._search_temperature_binary(p_cal, y_c, temp_grid)
                p_cal_t = self._temperature_scale_binary(p_cal, t_cur)
                p_test_t = self._temperature_scale_binary(p_test, t_cur)
                try:
                    ll_before = float(ll(y_c, np.vstack([1.0 - p_cal, p_cal]).T, labels=[0, 1]))
                    ll_after = float(ll(y_c, np.vstack([1.0 - p_cal_t, p_cal_t]).T, labels=[0, 1]))
                    if ll_after <= ll_before:
                        p_test = p_test_t
                        best_t = float(t_cur)
                except Exception:
                    best_t = 1.0
            temp_values.extend([float(best_t)] * len(X_test))

            y_true.extend(y_test.tolist())
            prob_up_rows.extend(np.asarray(p_test, dtype=float).tolist())
            realized_returns.extend(np.asarray(y_ret_test, dtype=float).tolist())
            eval_dates.extend([str(d) for d in model_df.index[start:end].tolist()])
            fold_count += 1
            next_eligible_start = end + embargo_n

        if len(y_true) == 0:
            raise RuntimeError("Backtest failed: no valid walk-forward folds.")

        y_true_arr = np.asarray(y_true, dtype=int)
        prob_up_arr = np.clip(np.asarray(prob_up_rows, dtype=float), 1e-6, 1.0 - 1e-6)
        realized_arr = np.asarray(realized_returns, dtype=float)

        if t_buy_grid:
            t_vals = [float(v) for v in t_buy_grid]
        else:
            t_vals = self._auto_t_buy_grid_from_probs(prob_up_arr)
        candidates = self._threshold_candidates_up5(y_true_arr, prob_up_arr, realized_arr, t_vals)
        fixed_metrics = self._eval_fixed_thresholds_up5(
            probs=prob_up_arr,
            y_true=y_true_arr,
            future_returns=realized_arr,
            thresholds=fixed_thresholds,
        )
        p70 = next((m for m in fixed_metrics if abs(float(m.get("t_buy", -1.0)) - 0.7) < 1e-12), None)
        if candidates and all(int(c.get("n_trades", 0)) == 0 for c in candidates):
            warnings.append("no_trade_all_thresholds: prob_up5_max < min_grid")
        if threshold_search:
            selected = self._select_threshold_candidate_up5(
                candidates=candidates,
                min_coverage=float(min_coverage),
                min_trades=int(min_trades),
            )
            best = selected["best"]
            fallback_unconstrained = bool(selected["fallback_unconstrained"])
        else:
            chosen = t_vals[0] if t_vals else 0.5
            best = next((c for c in candidates if abs(float(c["t_buy"]) - float(chosen)) < 1e-12), candidates[0])
            fallback_unconstrained = False

        t_buy_best = float(best["t_buy"])
        y_pred_arr = (prob_up_arr >= t_buy_best).astype(int)
        cm = cm_func(y_true_arr, y_pred_arr, labels=[0, 1]).tolist()
        tn, fp = int(cm[0][0]), int(cm[0][1])
        fn, tp = int(cm[1][0]), int(cm[1][1])
        n_trades = int(np.sum(y_pred_arr == 1))
        coverage = float(n_trades / max(1, len(y_pred_arr)))
        win_rate = float(tp / max(1, tp + fp))
        buy_returns = realized_arr[y_pred_arr == 1]
        avg_return = float(np.mean(buy_returns)) if len(buy_returns) else 0.0
        median_return = float(np.median(buy_returns)) if len(buy_returns) else 0.0
        prob_summary = {
            "min": float(np.min(prob_up_arr)),
            "max": float(np.max(prob_up_arr)),
            "p50": float(np.quantile(prob_up_arr, 0.50)),
            "p90": float(np.quantile(prob_up_arr, 0.90)),
            "p95": float(np.quantile(prob_up_arr, 0.95)),
            "p99": float(np.quantile(prob_up_arr, 0.99)),
            "mean": float(np.mean(prob_up_arr)),
            "std": float(np.std(prob_up_arr)),
        }
        prob_calibration_audit = self._prob_calibration_audit_binary(y_true_arr, prob_up_arr, hi=0.8, lo=0.2)
        prob_calibration_audit = self._prob_calibration_audit_binary(
            y_true_arr,
            prob_up_arr,
            hi=0.7,
            lo=0.3,
            dates=eval_dates,
            future_returns=realized_arr,
            top_n=50,
        )

        p2 = np.vstack([1.0 - prob_up_arr, prob_up_arr]).T
        brier = float(np.mean((prob_up_arr - y_true_arr) ** 2))
        ece = self._ece_binary(y_true_arr, prob_up_arr, n_bins=10)

        label_dist = {
            "negative_0": float(np.mean(y_true_arr == 0)),
            "positive_1": float(np.mean(y_true_arr == 1)),
        }
        pred_dist = {
            "buy_1": coverage,
            "no_trade_0": float(1.0 - coverage),
        }

        return {
            "accuracy": float(acc(y_true_arr, y_pred_arr)),
            "macro_f1": float(f1(y_true_arr, y_pred_arr, average="macro")),
            "logloss": float(ll(y_true_arr, p2, labels=[0, 1])),
            "brier": brier,
            "ece": float(ece),
            "interval_target_coverage": float(cfg.target_interval_coverage),
            "interval_q": None,
            "interval_width_mean": None,
            "interval_coverage": None,
            "interval_target_name": "return_up5_binary",
            "interval_target_unit": "fraction",
            "target_transform": "future_return_fraction",
            "target_scale_factor": 1.0,
            "interval_clip_ratio": None,
            "interval_target_mismatch_warn": False,
            "interval_target_y_source": "target_return_pct_h",
            "interval_audit_sample": [],
            "bias": 0.0,
            "bias_abs": 0.0,
            "class_ratio": None,
            "class_ratio_after_meta": None,
            "confusion_matrix": cm,
            "class_metrics": {
                "no_trade": {
                    "precision": float(tn / max(1, tn + fn)),
                    "recall": float(tn / max(1, tn + fp)),
                    "f1": float((2.0 * tn) / max(1, (2 * tn + fp + fn))),
                },
                "buy": {
                    "precision": win_rate,
                    "recall": float(tp / max(1, tp + fn)),
                    "f1": float((2.0 * tp) / max(1, (2 * tp + fp + fn))),
                },
            },
            "trading_metrics": {
                "trade_count": n_trades,
                "action_counts": {"buy": n_trades, "hold": int(len(y_pred_arr) - n_trades)},
            },
            "meta_metrics": {
                "prob_mode": "binary_up5",
                "label_mode": "up5_2w",
                "target_return": float(target_return),
                "horizon_trading_days": int(cfg.horizon_trading_days),
                "warnings": warnings,
            },
            "threshold_selection": {
                "best": best,
                "candidates": candidates,
                "fallback_unconstrained": fallback_unconstrained,
                "constraints": {
                    "min_coverage": float(min_coverage),
                    "min_trades": int(min_trades),
                },
            },
            "fixed_threshold_metrics": fixed_metrics,
            "warnings": warnings,
            "thresholds_applied": {"t_buy": t_buy_best},
            "threshold_mode": "buy_threshold",
            "class_weight_mode": "off",
            "class_weight": None,
            "prob_calibration": {
                "temp_scale_mode": temp_scale_mode,
            },
            "temperature_scaling": {
                "enabled": bool(temp_scale_mode != "off"),
                "best_T": float(np.mean(np.asarray(temp_values, dtype=float))) if temp_values else 1.0,
                "search_grid": temp_grid if temp_scale_mode != "off" else [1.0],
            },
            "validation": {
                "purge_days": int(purge_n),
                "embargo_mode": embargo_mode_used,
                "embargo_days": int(embargo_n),
                "embargo_pct": float(embargo_pct),
                "folds_used": int(fold_count),
            },
            "baselines": {},
            "label_distribution": label_dist,
            "prediction_distribution": pred_dist,
            "prob_up5_summary": prob_summary,
            "prob_calibration_audit": prob_calibration_audit,
            "win_rate_at_best": win_rate,
            "coverage_at_best": coverage,
            "n_trades_at_best": n_trades,
            "avg_return_at_best": avg_return,
            "median_return_at_best": median_return,
            "precision_at_p70": (
                p70.get("precision", p70.get("win_rate")) if isinstance(p70, dict) else None
            ),
            "coverage_at_p70": p70.get("coverage") if isinstance(p70, dict) else None,
            "n_trades_at_p70": p70.get("n_trades") if isinstance(p70, dict) else None,
            "avg_return_at_p70": p70.get("avg_return") if isinstance(p70, dict) else None,
        }

    def _default_t_buy_grid(self) -> List[float]:
        return [round(x, 2) for x in np.arange(0.50, 0.96, 0.05)]

    def _auto_t_buy_grid_from_probs(self, probs: np.ndarray) -> List[float]:
        p = np.asarray(probs, dtype=float).reshape(-1)
        if p.size == 0:
            return self._default_t_buy_grid()
        q_levels = [0.50, 0.60, 0.70, 0.80, 0.85, 0.90, 0.95, 0.97, 0.99]
        vals = [float(np.quantile(p, q)) for q in q_levels]
        vals = [float(np.clip(v, 0.01, 0.99)) for v in vals]
        uniq = sorted({round(v, 4) for v in vals})
        return uniq if uniq else self._default_t_buy_grid()

    def _threshold_candidates_up5(
        self,
        y_true_arr: np.ndarray,
        prob_up_arr: np.ndarray,
        realized_returns: np.ndarray,
        t_buy_values: List[float],
    ) -> List[Dict[str, Any]]:
        cm_func = self._sk_metrics["confusion_matrix"]
        out: List[Dict[str, Any]] = []
        n = len(y_true_arr)
        for t_buy in t_buy_values:
            yhat = (prob_up_arr >= float(t_buy)).astype(int)
            cm = cm_func(y_true_arr, yhat, labels=[0, 1]).tolist()
            tn, fp = int(cm[0][0]), int(cm[0][1])
            fn, tp = int(cm[1][0]), int(cm[1][1])
            n_trades = int(np.sum(yhat == 1))
            coverage = float(n_trades / max(1, n))
            win_rate = float(tp / max(1, tp + fp))
            buy_ret = realized_returns[yhat == 1]
            avg_return = float(np.mean(buy_ret)) if len(buy_ret) else 0.0
            median_return = float(np.median(buy_ret)) if len(buy_ret) else 0.0
            out.append(
                {
                    "t_buy": float(t_buy),
                    "win_rate": win_rate,
                    "coverage": coverage,
                    "n_trades": n_trades,
                    "avg_return": avg_return,
                    "median_return": median_return,
                    "confusion_2x2": cm,
                }
            )
        return out

    def _eval_fixed_thresholds_up5(
        self,
        probs: np.ndarray,
        y_true: np.ndarray,
        future_returns: np.ndarray,
        thresholds: Optional[List[float]] = None,
    ) -> List[Dict[str, Any]]:
        cm_func = self._sk_metrics["confusion_matrix"]
        p = np.clip(np.asarray(probs, dtype=float).reshape(-1), 1e-6, 1.0 - 1e-6)
        y = np.asarray(y_true, dtype=int).reshape(-1)
        r = np.asarray(future_returns, dtype=float).reshape(-1)
        n = int(len(y))
        if not (len(p) == len(y) == len(r)):
            raise ValueError("fixed-threshold evaluation input lengths must match.")

        raw = [0.5, 0.6, 0.7, 0.75, 0.8] if thresholds is None else [float(v) for v in thresholds]
        raw.append(0.7)
        t_vals = sorted({float(np.clip(v, 0.0, 1.0)) for v in raw})

        out: List[Dict[str, Any]] = []
        for t_buy in t_vals:
            buy_mask = p >= float(t_buy)
            yhat = buy_mask.astype(int)
            n_trades = int(np.sum(buy_mask))
            coverage = float(n_trades / max(1, n))
            cm = cm_func(y, yhat, labels=[0, 1]).tolist()
            if n_trades > 0:
                precision = float(np.mean(y[buy_mask] == 1))
                avg_return = float(np.mean(r[buy_mask]))
            else:
                precision = 0.0
                avg_return = 0.0
            out.append(
                {
                    "t_buy": float(t_buy),
                    "n_trades": n_trades,
                    "coverage": coverage,
                    "precision": precision,
                    "win_rate": precision,
                    "avg_return": avg_return,
                    "confusion_matrix": cm,
                    "n_samples": n,
                }
            )
        return out

    def _reliability_bins_binary(
        self,
        y_true: np.ndarray,
        p_pos: np.ndarray,
        n_bins: int = 10,
    ) -> List[Dict[str, Any]]:
        y = np.asarray(y_true, dtype=int).reshape(-1)
        p = np.clip(np.asarray(p_pos, dtype=float).reshape(-1), 1e-6, 1.0 - 1e-6)
        bins = np.linspace(0.0, 1.0, n_bins + 1)
        rows: List[Dict[str, Any]] = []
        for i in range(n_bins):
            lo, hi = float(bins[i]), float(bins[i + 1])
            if i < n_bins - 1:
                mask = (p >= lo) & (p < hi)
            else:
                mask = (p >= lo) & (p <= hi)
            cnt = int(np.sum(mask))
            if cnt == 0:
                rows.append(
                    {
                        "bin_lo": lo,
                        "bin_hi": hi,
                        "count": 0,
                        "pred_mean": None,
                        "actual_rate": None,
                        "gap_actual_minus_pred": None,
                    }
                )
                continue
            pred_mean = float(np.mean(p[mask]))
            actual_rate = float(np.mean(y[mask] == 1))
            rows.append(
                {
                    "bin_lo": lo,
                    "bin_hi": hi,
                    "count": cnt,
                    "pred_mean": pred_mean,
                    "actual_rate": actual_rate,
                    "gap_actual_minus_pred": float(actual_rate - pred_mean),
                }
            )
        return rows

    def _prob_calibration_audit_binary(
        self,
        y_true: np.ndarray,
        p_pos: np.ndarray,
        hi: float = 0.8,
        lo: float = 0.2,
        dates: Optional[List[str]] = None,
        future_returns: Optional[np.ndarray] = None,
        top_n: int = 50,
    ) -> Dict[str, Any]:
        y = np.asarray(y_true, dtype=int).reshape(-1)
        p = np.clip(np.asarray(p_pos, dtype=float).reshape(-1), 1e-6, 1.0 - 1e-6)
        rets = np.asarray(future_returns, dtype=float).reshape(-1) if future_returns is not None else np.zeros_like(p)
        dts = list(dates or [])
        if len(dts) != len(p):
            dts = [None] * len(p)
        hi_mask = p >= float(hi)
        lo_mask = p <= float(lo)
        high_conf_miss_count = int(np.sum(hi_mask & (y == 0)))
        low_conf_miss_count = int(np.sum(lo_mask & (y == 1)))

        high_items: List[Dict[str, Any]] = []
        for i in np.where(hi_mask & (y == 0))[0].tolist():
            high_items.append(
                {
                    "date": dts[i],
                    "probability": float(p[i]),
                    "realized_return": float(rets[i]),
                    "actual": int(y[i]),
                }
            )
        high_items.sort(key=lambda x: x["probability"], reverse=True)
        low_items: List[Dict[str, Any]] = []
        for i in np.where(lo_mask & (y == 1))[0].tolist():
            low_items.append(
                {
                    "date": dts[i],
                    "probability": float(p[i]),
                    "realized_return": float(rets[i]),
                    "actual": int(y[i]),
                }
            )
        low_items.sort(key=lambda x: x["probability"])

        return {
            "reliability_bins": self._reliability_bins_binary(y, p, n_bins=10),
            "high_confidence_miss": {
                "threshold": float(hi),
                "count": high_conf_miss_count,
                "base_count": int(np.sum(hi_mask)),
                "rate": float(high_conf_miss_count / max(1, int(np.sum(hi_mask)))),
                "top_misses": high_items[: int(max(0, top_n))],
            },
            "low_confidence_miss": {
                "threshold": float(lo),
                "count": low_conf_miss_count,
                "base_count": int(np.sum(lo_mask)),
                "rate": float(low_conf_miss_count / max(1, int(np.sum(lo_mask)))),
                "top_misses": low_items[: int(max(0, top_n))],
            },
        }

    def _select_threshold_candidate_up5(
        self,
        candidates: List[Dict[str, Any]],
        min_coverage: float,
        min_trades: int,
    ) -> Dict[str, Any]:
        feasible = [
            c for c in candidates
            if float(c.get("coverage", 0.0)) >= float(min_coverage)
            and int(c.get("n_trades", 0)) >= int(min_trades)
        ]
        ranked_pool = feasible if feasible else candidates
        ranked_pool = sorted(
            ranked_pool,
            key=lambda c: (
                -float(c.get("win_rate", 0.0)),
                -int(c.get("n_trades", 0)),
                -float(c.get("coverage", 0.0)),
            ),
        )
        return {
            "best": ranked_pool[0],
            "ranked": ranked_pool,
            "fallback_unconstrained": len(feasible) == 0,
            "satisfied_count": len(feasible),
        }

    # ----------------------------- utils -----------------------------
    def _resolve_class_weight_map(
        self,
        mode: str,
        class_weight: Optional[Dict[str, float]],
        y_ref: np.ndarray,
    ) -> Optional[Dict[int, float]]:
        m = str(mode or "off").lower()
        if m == "off":
            return None
        if m == "custom":
            if not class_weight:
                return None
            return {
                0: float(class_weight.get("down", 1.0)),
                1: float(class_weight.get("flat", 1.0)),
                2: float(class_weight.get("up", 1.0)),
            }
        if m == "balanced":
            y = np.asarray(y_ref, dtype=int)
            vals, counts = np.unique(y, return_counts=True)
            w = {int(v): float(len(y) / (len(vals) * c)) for v, c in zip(vals, counts)}
            return {0: float(w.get(0, 1.0)), 1: float(w.get(1, 1.0)), 2: float(w.get(2, 1.0))}
        return None

    def _sample_weight_from_class_map(self, y: np.ndarray, class_map: Dict[int, float]) -> np.ndarray:
        yy = np.asarray(y, dtype=int)
        return np.array([float(class_map.get(int(v), 1.0)) for v in yy], dtype=float)

    def _predict_classes_threshold(self, probs: np.ndarray, t_down: float, t_up: float) -> np.ndarray:
        p = np.asarray(probs, dtype=float)
        down = p[:, 0]
        up = p[:, 2]
        out = np.full(len(p), 1, dtype=int)
        out[up >= float(t_up)] = 2
        out[down >= float(t_down)] = 0
        return out

    def _default_threshold_down_grid(self) -> List[float]:
        return [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]

    def _default_threshold_up_grid(self) -> List[float]:
        return [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65]

    def _threshold_candidates(
        self,
        y_true_arr: np.ndarray,
        probs: np.ndarray,
        t_down_values: List[float],
        t_up_values: List[float],
    ) -> List[Dict[str, Any]]:
        f1 = self._sk_metrics["f1_score"]
        cm_func = self._sk_metrics["confusion_matrix"]
        out: List[Dict[str, Any]] = []
        for td in t_down_values:
            for tu in t_up_values:
                yhat = self._predict_classes_threshold(probs, float(td), float(tu))
                cm = cm_func(y_true_arr, yhat, labels=[0, 1, 2]).tolist()
                down_denom = float(sum(cm[0]))
                down_recall = float(cm[0][0] / down_denom) if down_denom > 0.0 else 0.0
                pred_down_pct = float(np.mean(yhat == 0))
                out.append(
                    {
                        "t_down": float(td),
                        "t_up": float(tu),
                        "macro_f1": float(f1(y_true_arr, yhat, average="macro")),
                        "down_recall": down_recall,
                        "pred_down_pct": pred_down_pct,
                        "confusion_matrix": cm,
                    }
                )
        return out

    def _select_threshold_candidate(
        self,
        candidates: List[Dict[str, Any]],
        down_recall_min: float,
        pred_down_min_pct: float,
    ) -> Dict[str, Any]:
        feasible = [
            c for c in candidates
            if float(c.get("down_recall", 0.0)) >= float(down_recall_min)
            and float(c.get("pred_down_pct", 0.0)) >= float(pred_down_min_pct)
        ]
        ranked_pool = feasible if feasible else candidates
        ranked_pool = sorted(
            ranked_pool,
            key=lambda c: (
                -float(c.get("macro_f1", 0.0)),
                -float(c.get("down_recall", 0.0)),
                abs(float(c.get("pred_down_pct", 0.0)) - 0.20),
            ),
        )
        return {
            "best": ranked_pool[0],
            "ranked": ranked_pool,
            "fallback_unconstrained": len(feasible) == 0,
            "satisfied_count": len(feasible),
        }

    def _binary_sample_weight(self, y: np.ndarray) -> np.ndarray:
        vals, counts = np.unique(y, return_counts=True)
        w = {int(v): float(len(y) / (len(vals) * c)) for v, c in zip(vals, counts)}
        return np.array([w[int(v)] for v in y], dtype=float)

    def _safe_probs(self, probs: np.ndarray, eps: float = 1e-6) -> np.ndarray:
        p = np.asarray(probs, dtype=float)
        p = np.clip(p, eps, 1.0 - eps)
        row_sum = np.sum(p, axis=1, keepdims=True)
        row_sum[row_sum <= 0.0] = 1.0
        return p / row_sum

    def _fit_binary_calibrator(
        self,
        p_cal: np.ndarray,
        y_cal: np.ndarray,
        method: str,
    ) -> Callable[[np.ndarray], np.ndarray]:
        p_arr = np.asarray(p_cal, dtype=float).reshape(-1)
        y_arr = np.asarray(y_cal, dtype=int).reshape(-1)
        if len(p_arr) == 0 or len(y_arr) == 0 or len(np.unique(y_arr)) < 2:
            return lambda p: np.clip(np.asarray(p, dtype=float), 1e-6, 1.0 - 1e-6)
        if str(method).lower() == "isotonic":
            from sklearn.isotonic import IsotonicRegression  # type: ignore

            iso = IsotonicRegression(out_of_bounds="clip")
            iso.fit(p_arr, y_arr)
            return lambda p: np.clip(
                np.asarray(iso.predict(np.asarray(p, dtype=float).reshape(-1)), dtype=float),
                1e-6,
                1.0 - 1e-6,
            )
        from sklearn.linear_model import LogisticRegression  # type: ignore

        lr = LogisticRegression(solver="lbfgs", max_iter=200)
        lr.fit(p_arr.reshape(-1, 1), y_arr)
        return lambda p: np.clip(
            np.asarray(
                lr.predict_proba(np.asarray(p, dtype=float).reshape(-1, 1))[:, 1],
                dtype=float,
            ),
            1e-6,
            1.0 - 1e-6,
        )

    def _fit_multiclass_ovr_calibrator(
        self,
        p_cal: np.ndarray,
        y_cal: np.ndarray,
        method: str,
    ) -> Callable[[np.ndarray], np.ndarray]:
        p_ref = self._safe_probs(p_cal)
        y_ref = np.asarray(y_cal, dtype=int).reshape(-1)
        cal_fns: List[Callable[[np.ndarray], np.ndarray]] = []
        for cls in [0, 1, 2]:
            y_bin = (y_ref == cls).astype(int)
            cal_fns.append(self._fit_binary_calibrator(p_ref[:, cls], y_bin, method))

        def _apply(p_in: np.ndarray) -> np.ndarray:
            p_arr = self._safe_probs(p_in)
            out = np.zeros_like(p_arr, dtype=float)
            for cls in [0, 1, 2]:
                out[:, cls] = cal_fns[cls](p_arr[:, cls])
            return self._safe_probs(out)

        return _apply

    def _compose_meta_probs(self, p_trade: np.ndarray, p_up_cond: np.ndarray) -> np.ndarray:
        pt = np.asarray(p_trade, dtype=float).reshape(-1)
        pu = np.asarray(p_up_cond, dtype=float).reshape(-1)
        pt = np.clip(pt, 1e-6, 1.0 - 1e-6)
        pu = np.clip(pu, 1e-6, 1.0 - 1e-6)
        p_up = pt * pu
        p_down = pt * (1.0 - pu)
        p_flat = 1.0 - pt
        out = np.vstack([p_down, p_flat, p_up]).T
        return self._safe_probs(out)

    def _temperature_scale(self, probs: np.ndarray, temperature: float) -> np.ndarray:
        p = self._safe_probs(probs)
        t = float(max(1e-6, temperature))
        logits = np.log(np.clip(p, 1e-12, 1.0)) / t
        logits = logits - np.max(logits, axis=1, keepdims=True)
        ex = np.exp(logits)
        return self._safe_probs(ex / np.sum(ex, axis=1, keepdims=True))

    def _temperature_scale_binary(self, probs: np.ndarray, temperature: float) -> np.ndarray:
        p = np.asarray(probs, dtype=float).reshape(-1)
        p = np.clip(p, 1e-6, 1.0 - 1e-6)
        t = float(max(1e-6, temperature))
        z = np.log(p / (1.0 - p))
        z_t = z / t
        out = 1.0 / (1.0 + np.exp(-z_t))
        return np.clip(out, 1e-6, 1.0 - 1e-6)

    def _search_temperature(self, probs_cal: np.ndarray, y_cal: np.ndarray, grid: List[float]) -> float:
        if probs_cal is None or len(probs_cal) == 0 or y_cal is None or len(y_cal) == 0:
            return 1.0
        ll = self._sk_metrics["log_loss"]
        best_t = 1.0
        best_loss = float("inf")
        for t in grid:
            p_t = self._temperature_scale(probs_cal, float(t))
            try:
                cur = float(ll(y_cal, p_t, labels=[0, 1, 2]))
            except Exception:
                continue
            if cur < best_loss:
                best_loss = cur
                best_t = float(t)
        return best_t

    def _search_temperature_binary(self, probs_cal: np.ndarray, y_cal: np.ndarray, grid: List[float]) -> float:
        p_arr = np.asarray(probs_cal, dtype=float).reshape(-1)
        y_arr = np.asarray(y_cal, dtype=int).reshape(-1)
        if len(p_arr) == 0 or len(y_arr) == 0 or len(np.unique(y_arr)) < 2:
            return 1.0
        ll = self._sk_metrics["log_loss"]
        best_t = 1.0
        best_loss = float("inf")
        for t in grid:
            p_t = self._temperature_scale_binary(p_arr, float(t))
            try:
                cur = float(ll(y_arr, np.vstack([1.0 - p_t, p_t]).T, labels=[0, 1]))
            except Exception:
                continue
            if cur < best_loss:
                best_loss = cur
                best_t = float(t)
        return best_t

    def _resolve_temp_scale_mode(self, temp_scale: Union[bool, str]) -> str:
        if isinstance(temp_scale, bool):
            return "global" if temp_scale else "off"
        mode = str(temp_scale or "off").strip().lower()
        if mode == "on":
            return "global"
        if mode in ("off", "global", "fold"):
            return mode
        return "off"

    def _extreme_rate(self, probs: np.ndarray, lo: float = 0.01, hi: float = 0.99) -> float:
        p = self._safe_probs(probs)
        if len(p) == 0:
            return 0.0
        extreme = (np.max(p, axis=1) >= hi) | (np.min(p, axis=1) <= lo)
        return float(np.mean(extreme.astype(float)))

    def _label_class(self, ret_pct: float, band: float) -> int:
        # 0=down, 1=flat, 2=up
        if ret_pct >= band:
            return 2
        if ret_pct <= -band:
            return 0
        return 1

    def _up5_labels_from_close(self, close: np.ndarray, horizon: int, target_return: float) -> np.ndarray:
        c = np.asarray(close, dtype=float).reshape(-1)
        h = max(1, int(horizon))
        trg = float(target_return)
        n = len(c)
        out = np.full(n, np.nan, dtype=float)
        if n <= h:
            return out
        for i in range(n - h):
            base = c[i]
            fut = c[i + h]
            if not np.isfinite(base) or not np.isfinite(fut) or base == 0.0:
                continue
            ret = (fut / base) - 1.0
            out[i] = 1.0 if ret >= trg else 0.0
        return out

    def _class_sample_weight(self, y: np.ndarray) -> np.ndarray:
        vals, counts = np.unique(y, return_counts=True)
        w = {int(v): float(len(y) / (len(vals) * c)) for v, c in zip(vals, counts)}
        return np.array([w[int(v)] for v in y], dtype=float)

    def _class_ratio(self, y: np.ndarray) -> Dict[str, float]:
        n = max(1, len(y))
        return {
            "down": float(np.sum(y == 0) / n),
            "flat": float(np.sum(y == 1) / n),
            "up": float(np.sum(y == 2) / n),
        }

    def _tbm_fold_vol_frac(self, close_train: np.ndarray, span: int) -> float:
        arr = np.asarray(close_train, dtype=float)
        if arr.size < 10:
            return 0.01
        ret = pd.Series(arr).pct_change().dropna()
        if ret.empty:
            return 0.01
        vol = ret.ewm(span=max(5, int(span)), adjust=False).std().dropna()
        if vol.empty:
            return 0.01
        out = float(np.nanmedian(vol.to_numpy(dtype=float)))
        if not np.isfinite(out):
            return 0.01
        return float(np.clip(out, 1e-4, 0.20))

    def _tbm_labels_with_scalar_vol(
        self,
        close_all: np.ndarray,
        horizon: int,
        vol_frac: float,
        k: float,
    ) -> np.ndarray:
        c = np.asarray(close_all, dtype=float)
        n = len(c)
        h = max(1, int(horizon))
        vf = float(max(1e-6, vol_frac))
        kk = float(max(0.1, k))
        labels = np.ones(n, dtype=int)  # 0=down,1=flat(time-out),2=up
        for i in range(n):
            end = min(n, i + h + 1)
            if i + 1 >= end:
                labels[i] = 1
                continue
            c0 = c[i]
            up_b = c0 * (1.0 + kk * vf)
            dn_b = c0 * (1.0 - kk * vf)
            hit = 1
            for px in c[i + 1 : end]:
                if px >= up_b:
                    hit = 2
                    break
                if px <= dn_b:
                    hit = 0
                    break
            labels[i] = hit
        return labels

    def _reliability_bins_multiclass(
        self,
        y_true: np.ndarray,
        probs: np.ndarray,
        n_bins: int = 10,
        conf_min: float = 0.0,
        conf_max: float = 1.0,
    ) -> List[Dict[str, Any]]:
        conf = np.max(probs, axis=1)
        pred = np.argmax(probs, axis=1)
        lo_edge = float(max(0.0, min(1.0, conf_min)))
        hi_edge = float(max(lo_edge, min(1.0, conf_max)))
        bins = np.linspace(lo_edge, hi_edge, n_bins + 1)
        rows: List[Dict[str, Any]] = []
        for i in range(n_bins):
            lo, hi = float(bins[i]), float(bins[i + 1])
            if i < n_bins - 1:
                mask = (conf >= lo) & (conf < hi)
            else:
                mask = (conf >= lo) & (conf <= hi)
            cnt = int(np.sum(mask))
            if cnt == 0:
                rows.append(
                    {
                        "bin_lo": lo,
                        "bin_hi": hi,
                        "count": 0,
                        "pred_mean": None,
                        "actual_rate": None,
                        "confidence_mean": None,
                        "accuracy_mean": None,
                    }
                )
                continue
            rows.append(
                {
                    "bin_lo": lo,
                    "bin_hi": hi,
                    "count": cnt,
                    "pred_mean": float(np.mean(conf[mask])),
                    "actual_rate": float(np.mean(pred[mask] == y_true[mask])),
                    "confidence_mean": float(np.mean(conf[mask])),
                    "accuracy_mean": float(np.mean(pred[mask] == y_true[mask])),
                }
            )
        return rows

    def _band_sweep(
        self,
        feat_df: pd.DataFrame,
        as_of: date,
        base_cfg: PredictorConfig,
        bands: List[float],
    ) -> Dict[str, Any]:
        rows: List[Dict[str, Any]] = []
        for b in bands:
            cfg = PredictorConfig(
                flat_band_pct=b,
                horizon_trading_days=base_cfg.horizon_trading_days,
                calibration=base_cfg.calibration,
                min_train_rows=base_cfg.min_train_rows,
                feature_version=base_cfg.feature_version,
                target_interval_coverage=base_cfg.target_interval_coverage,
            )
            try:
                prepared = self._prepare_dataset(feat_df, as_of, cfg)
                bt = self._walk_forward_backtest_quick(prepared, cfg)
                rows.append(
                    {
                        "flat_band_pct": b,
                        "macro_f1": bt["macro_f1"],
                        "logloss": bt["logloss"],
                        "ece": bt["ece"],
                        "accuracy": bt["accuracy"],
                    }
                )
            except Exception as e:
                rows.append(
                    {
                        "flat_band_pct": b,
                        "error": str(e),
                    }
                )
        valid = [r for r in rows if "error" not in r]
        if valid:
            best = sorted(valid, key=lambda r: (-r["macro_f1"], r["logloss"], r["ece"]))[0]["flat_band_pct"]
        else:
            best = base_cfg.flat_band_pct
        return {"rows": rows, "best_band": best}

    def _walk_forward_backtest_quick(self, prepared: Dict[str, Any], cfg: PredictorConfig) -> Dict[str, Any]:
        model_df = prepared["model_df"]
        feature_cols = prepared["feature_cols"]
        acc = self._sk_metrics["accuracy_score"]
        f1 = self._sk_metrics["f1_score"]
        ll = self._sk_metrics["log_loss"]

        X_all = model_df[feature_cols].to_numpy(dtype=float)
        y_cls_all = model_df["target_class"].astype(int).to_numpy()
        y_reg_all = model_df["target_close_h"].to_numpy(dtype=float)

        min_train = max(cfg.min_train_rows, 300)
        step = 80
        test_window = 15
        y_true: List[int] = []
        y_pred: List[int] = []
        prob_rows: List[np.ndarray] = []
        cover_flags: List[int] = []
        q_values: List[float] = []
        width_values: List[float] = []
        interval_clip_count = 0
        interval_total = 0

        n = len(model_df)
        for start in range(min_train, n - test_window, step):
            end = min(start + test_window, n)
            X_train = X_all[:start]
            y_train_cls = y_cls_all[:start]
            y_train_reg = y_reg_all[:start]
            X_test = X_all[start:end]
            y_test_cls = y_cls_all[start:end]
            y_test_close = y_reg_all[start:end]

            sw = self._class_sample_weight(y_train_cls)
            cv = self._sk_tscv(n_splits=2)
            clf = self._xgb.XGBClassifier(
                objective="multi:softprob",
                num_class=3,
                n_estimators=120,
                max_depth=4,
                learning_rate=0.05,
                subsample=0.9,
                colsample_bytree=0.9,
                eval_metric="mlogloss",
                tree_method="hist",
                random_state=11,
            )
            calib = self._sk_calib(estimator=clf, method=cfg.calibration, cv=cv)
            calib.fit(X_train, y_train_cls, sample_weight=sw)
            p = calib.predict_proba(X_test)
            yhat = np.argmax(p, axis=1)
            y_true.extend(y_test_cls.tolist())
            y_pred.extend(yhat.tolist())
            prob_rows.extend([r for r in p])

            reg = self._xgb.XGBRegressor(
                objective="reg:squarederror",
                n_estimators=140,
                max_depth=4,
                learning_rate=0.05,
                subsample=0.9,
                colsample_bytree=0.9,
                tree_method="hist",
                random_state=11,
            )
            split_reg = min(max(int(len(X_train) * 0.8), 120), len(X_train) - 30)
            if split_reg <= 100:
                continue
            X_t_reg, X_c_reg = X_train[:split_reg], X_train[split_reg:]
            y_t_reg, y_c_reg = y_train_reg[:split_reg], y_train_reg[split_reg:]
            reg.fit(X_t_reg, y_t_reg)
            pred_cal = reg.predict(X_c_reg)
            resid = np.abs(y_c_reg - pred_cal)
            q_abs = self._split_conformal_q(resid, cfg.target_interval_coverage)
            t_pred = reg.predict(X_test)
            low_raw = t_pred - q_abs
            high_raw = t_pred + q_abs
            low = np.maximum(0.0, low_raw)
            high = np.maximum(low, high_raw)
            interval_clip_count += int(np.sum(low_raw < 0.0))
            interval_total += int(len(X_test))
            q_values.extend([float(q_abs)] * len(X_test))
            width_values.extend((high - low).astype(float).tolist())
            cov = ((y_test_close >= low) & (y_test_close <= high)).astype(int)
            cover_flags.extend(cov.tolist())

        if len(y_true) == 0:
            raise RuntimeError("Backtest failed: no valid walk-forward folds.")

        probs = np.vstack(prob_rows)
        y_true_arr = np.array(y_true, dtype=int)
        y_pred_arr = np.array(y_pred, dtype=int)
        interval_q_mean = float(np.mean(np.array(q_values, dtype=float))) if q_values else 0.0
        interval_width_mean = float(np.mean(np.array(width_values, dtype=float))) if width_values else 0.0
        clip_ratio = float(interval_clip_count / max(1, interval_total))
        return {
            "accuracy": float(acc(y_true_arr, y_pred_arr)),
            "macro_f1": float(f1(y_true_arr, y_pred_arr, average="macro")),
            "logloss": float(ll(y_true_arr, probs, labels=[0, 1, 2])),
            "brier": float(self._multiclass_brier(y_true_arr, probs)),
            "ece": float(self._ece_multiclass(y_true_arr, probs, n_bins=10)),
            "interval_target_coverage": float(cfg.target_interval_coverage),
            "interval_q": interval_q_mean,
            "interval_width_mean": interval_width_mean,
            "interval_coverage": float(np.mean(np.array(cover_flags, dtype=float))),
            "interval_target_name": "close_future",
            "interval_target_unit": "price",
            "interval_clip_ratio": clip_ratio,
        }

    def _round_to_100(self, probs_pct: Dict[str, float]) -> Dict[str, int]:
        keys = ["up", "down", "flat"]
        vals = np.array([probs_pct[k] for k in keys], dtype=float)
        vals = np.clip(vals, 0.0, 100.0)
        out = np.floor(vals + 0.5).astype(int)  # round half-up
        out = np.clip(out, 0, 100)
        diff = 100 - int(out.sum())
        if diff != 0:
            out[int(np.argmax(out))] += diff
            out = np.clip(out, 0, 100)
            if int(out.sum()) != 100:
                out[int(np.argmax(out))] += 100 - int(out.sum())
                out = np.clip(out, 0, 100)

        return {k: int(v) for k, v in zip(keys, out)}

    def _split_conformal_q(self, abs_residuals: np.ndarray, target_coverage: float) -> float:
        arr = np.asarray(abs_residuals, dtype=float)
        arr = arr[np.isfinite(arr)]
        if arr.size == 0:
            return 0.0
        tc = float(np.clip(target_coverage, 0.01, 0.99))
        alpha = 1.0 - tc
        n = int(arr.size)
        q_level = min(1.0, np.ceil((n + 1) * (1.0 - alpha)) / n)
        try:
            return float(np.quantile(arr, q_level, method="higher"))
        except TypeError:
            return float(np.quantile(arr, q_level, interpolation="higher"))

    def _multiclass_brier(self, y_true: np.ndarray, probs: np.ndarray) -> float:
        n = len(y_true)
        k = probs.shape[1]
        y_onehot = np.zeros((n, k), dtype=float)
        y_onehot[np.arange(n), y_true] = 1.0
        return float(np.mean(np.sum((probs - y_onehot) ** 2, axis=1) / k))

    def _ece_multiclass(self, y_true: np.ndarray, probs: np.ndarray, n_bins: int = 10) -> float:
        conf = np.max(probs, axis=1)
        pred = np.argmax(probs, axis=1)
        bins = np.linspace(0.0, 1.0, n_bins + 1)
        ece = 0.0
        for i in range(n_bins):
            lo, hi = bins[i], bins[i + 1]
            if i < n_bins - 1:
                mask = (conf >= lo) & (conf < hi)
            else:
                mask = (conf >= lo) & (conf <= hi)
            if not np.any(mask):
                continue
            acc_bin = np.mean(pred[mask] == y_true[mask])
            conf_bin = np.mean(conf[mask])
            w = np.mean(mask)
            ece += w * abs(acc_bin - conf_bin)
        return float(ece)

    def _ece_binary(self, y_true: np.ndarray, p_pos: np.ndarray, n_bins: int = 10) -> float:
        y = np.asarray(y_true, dtype=int).reshape(-1)
        p = np.clip(np.asarray(p_pos, dtype=float).reshape(-1), 1e-6, 1.0 - 1e-6)
        bins = np.linspace(0.0, 1.0, n_bins + 1)
        ece = 0.0
        for i in range(n_bins):
            lo, hi = bins[i], bins[i + 1]
            if i < n_bins - 1:
                mask = (p >= lo) & (p < hi)
            else:
                mask = (p >= lo) & (p <= hi)
            if not np.any(mask):
                continue
            acc_bin = float(np.mean(y[mask] == 1))
            conf_bin = float(np.mean(p[mask]))
            ece += float(np.mean(mask)) * abs(acc_bin - conf_bin)
        return float(ece)
