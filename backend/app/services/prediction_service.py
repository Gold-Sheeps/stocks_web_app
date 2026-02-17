from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

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
        sample_csv_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._reset_data_context()
        cfg = PredictorConfig(
            flat_band_pct=flat_band_pct,
            horizon_trading_days=horizon_trading_days,
            calibration=calibration,
            target_interval_coverage=target_interval_coverage,
        )
        as_of = datetime.strptime(as_of_date, "%Y-%m-%d").date()
        _ = sample_csv_path  # DB-only mode: keep API compatibility, ignore sample path.
        raw = self._load_prices(ticker, as_of=as_of)
        feat = self._build_feature_frame(ticker, raw, as_of=as_of)
        prepared = self._prepare_dataset(feat, as_of, cfg)
        return self._fit_predict(prepared, cfg, as_of)

    def backtest(
        self,
        ticker: str,
        as_of_date: str,
        flat_band_pct: float = 2.0,
        horizon_trading_days: int = 15,
        calibration: str = "sigmoid",
        target_interval_coverage: float = 0.80,
        sample_csv_path: Optional[str] = None,
        include_band_sweep: bool = True,
    ) -> Dict[str, Any]:
        self._reset_data_context()
        cfg = PredictorConfig(
            flat_band_pct=flat_band_pct,
            horizon_trading_days=horizon_trading_days,
            calibration=calibration,
            target_interval_coverage=target_interval_coverage,
        )
        as_of = datetime.strptime(as_of_date, "%Y-%m-%d").date()
        _ = sample_csv_path  # DB-only mode: keep API compatibility, ignore sample path.
        raw = self._load_prices(ticker, as_of=as_of)
        feat = self._build_feature_frame(ticker, raw, as_of=as_of)
        prepared = self._prepare_dataset(feat, as_of, cfg)
        bt = self._walk_forward_backtest(prepared, cfg)
        out: Dict[str, Any] = {
            "backtest": bt,
            "meta": self._build_meta(cfg, prepared["train_range"], as_of),
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

    def _build_meta(self, cfg: PredictorConfig, train_range: List[str], as_of: date) -> Dict[str, Any]:
        return {
            "model_type": "xgboost",
            "model_version": self._model_version,
            "calibration": cfg.calibration,
            "train_range": train_range,
            "feature_version": cfg.feature_version,
            "feature_set_used": cfg.feature_version,
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
    def _build_feature_frame(self, ticker: str, price_df: pd.DataFrame, as_of: date) -> pd.DataFrame:
        df = price_df.copy()

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

        # Relative / regime features (DB-only: QQQ + ^SOX/SMH fallback)
        qqq_df, qqq_symbol, qqq_fresh = self._fetch_benchmark("QQQ", as_of, min_rows=180)
        if qqq_df is None or qqq_df.empty:
            raise RuntimeError(
                "Benchmark data missing in DB: US:QQQ. "
                "Run `python backend/scripts/refresh_market_data.py --symbols "
                "\"US:QQQ\" --source yfinance` then retry."
            )
        self._db_freshness_by_symbol[qqq_symbol] = qqq_fresh
        qqq_ret1 = qqq_df["Close"].pct_change(1).reindex(df.index)
        qqq_ret5 = qqq_df["Close"].pct_change(5).reindex(df.index)
        qqq_vol20 = qqq_df["Close"].pct_change().rolling(20).std().reindex(df.index) * np.sqrt(252)
        qqq_sma50 = qqq_df["Close"].rolling(50).mean().reindex(df.index)
        df["ret_rel_qqq_1"] = df["ret_1"] - qqq_ret1
        df["ret_rel_qqq_5"] = df["ret_5"] - qqq_ret5
        df["qqq_vol_20"] = qqq_vol20
        df["qqq_trend_50"] = qqq_df["Close"].reindex(df.index) / qqq_sma50 - 1.0
        self._meta_sources["benchmarks"] = f"db:price_daily({qqq_symbol})"
        self._meta_origins["benchmarks"] = "ingested:yfinance"

        semi_df, semi_symbol, semi_fresh = self._fetch_benchmark("^SOX", as_of, min_rows=180)
        if semi_df is None or semi_df.empty:
            semi_df, semi_symbol, semi_fresh = self._fetch_benchmark("SMH", as_of, min_rows=180)
        if semi_df is None or semi_df.empty:
            raise RuntimeError(
                "Benchmark data missing in DB: US:^SOX (or US:SMH). "
                "Run `python backend/scripts/refresh_market_data.py --symbols "
                "\"US:^SOX,US:SMH\" --source yfinance` then retry."
            )
        self._db_freshness_by_symbol[semi_symbol] = semi_fresh
        semi_ret1 = semi_df["Close"].pct_change(1).reindex(df.index)
        semi_ret5 = semi_df["Close"].pct_change(5).reindex(df.index)
        df["ret_rel_semi_1"] = df["ret_1"] - semi_ret1
        df["ret_rel_semi_5"] = df["ret_5"] - semi_ret5
        self._meta_sources["sector"] = f"db:price_daily({semi_symbol})"
        self._meta_origins["sector"] = "ingested:yfinance"

        # DB-only mode: no live earnings calendar fetch.
        df["days_from_prev_earnings"] = np.nan
        df["days_to_next_earnings"] = np.nan

        return df

    def _fetch_benchmark(
        self, ticker: str, as_of: date, min_rows: int = 180
    ) -> Tuple[Optional[pd.DataFrame], str, Dict[str, Any]]:
        symbol_key = self._symbol_key(ticker)
        db_df, freshness = self._load_prices_from_db(symbol_key, as_of)
        if not self._is_db_data_sufficient(db_df, min_rows=min_rows):
            return None, symbol_key, freshness
        return db_df, symbol_key, freshness

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

        feature_cols = [
            c for c in out.columns
            if c
            not in [
                "Open", "High", "Low", "Close", "Volume",
                "target_close_h", "target_return_pct_h", "target_class", "future_date_h",
            ]
        ]

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

        model_df = out.loc[train_mask, feature_cols + ["target_class", "target_close_h"]].dropna()
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
    def _fit_predict(self, prepared: Dict[str, Any], cfg: PredictorConfig, as_of: date) -> Dict[str, Any]:
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
        sample_weight = self._class_sample_weight(y_cls)
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

        return {
            "probs": {
                "up": probs_int["up"],
                "down": probs_int["down"],
                "flat": probs_int["flat"],
            },
            "price_forecast": {
                "point": round(point, 4),
                "p10": round(p10, 4),
                "p50": round(p50, 4),
                "p90": round(p90, 4),
            },
            "meta": {
                **self._build_meta(cfg, prepared["train_range"], as_of),
                "target_name": "close_future",
                "target_unit": "price",
                "interval_target_coverage": cfg.target_interval_coverage,
                "interval_q": float(q_abs),
            },
        }

    def _walk_forward_backtest(self, prepared: Dict[str, Any], cfg: PredictorConfig) -> Dict[str, Any]:
        model_df = prepared["model_df"]
        feature_cols = prepared["feature_cols"]
        acc = self._sk_metrics["accuracy_score"]
        f1 = self._sk_metrics["f1_score"]
        ll = self._sk_metrics["log_loss"]

        X_all = model_df[feature_cols].to_numpy(dtype=float)
        y_cls_all = model_df["target_class"].astype(int).to_numpy()
        y_reg_all = model_df["target_close_h"].to_numpy(dtype=float)

        min_train = max(cfg.min_train_rows, 280)
        step = 30
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

            split = int(len(X_train) * 0.8)
            split = min(max(split, 120), len(X_train) - 30)
            if split <= 100:
                continue

            X_t, X_c = X_train[:split], X_train[split:]
            y_t, y_c = y_train_cls[:split], y_train_cls[split:]
            y_t_reg, y_c_reg = y_train_reg[:split], y_train_reg[split:]

            sw = self._class_sample_weight(y_train_cls)
            cv = self._sk_tscv(n_splits=2)
            models = []
            for seed in (11, 29):
                clf = self._xgb.XGBClassifier(
                    objective="multi:softprob",
                    num_class=3,
                    n_estimators=180,
                    max_depth=4,
                    learning_rate=0.045,
                    subsample=0.9,
                    colsample_bytree=0.9,
                    eval_metric="mlogloss",
                    tree_method="hist",
                    random_state=seed,
                )
                calib = self._sk_calib(estimator=clf, method=cfg.calibration, cv=cv)
                calib.fit(X_train, y_train_cls, sample_weight=sw)
                models.append(calib)

            p = np.mean([m.predict_proba(X_test) for m in models], axis=0)
            yhat = np.argmax(p, axis=1)
            y_true.extend(y_test_cls.tolist())
            y_pred.extend(yhat.tolist())
            prob_rows.extend([r for r in p])

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
            resid = np.abs(y_c_reg - c_pred)
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

        brier = self._multiclass_brier(y_true_arr, probs)
        ece = self._ece_multiclass(y_true_arr, probs, n_bins=10)
        coverage = float(np.mean(np.array(cover_flags, dtype=float)))
        interval_q_mean = float(np.mean(np.array(q_values, dtype=float))) if q_values else 0.0
        interval_width_mean = float(np.mean(np.array(width_values, dtype=float))) if width_values else 0.0
        clip_ratio = float(interval_clip_count / max(1, interval_total))
        cm = self._sk_metrics["confusion_matrix"](y_true_arr, y_pred_arr, labels=[0, 1, 2]).tolist()
        pr, rc, f1_cls, _ = self._sk_metrics["prfs"](y_true_arr, y_pred_arr, labels=[0, 1, 2], zero_division=0)
        cls_ratio = self._class_ratio(y_true_arr)

        return {
            "accuracy": float(acc(y_true_arr, y_pred_arr)),
            "macro_f1": float(f1(y_true_arr, y_pred_arr, average="macro")),
            "logloss": float(ll(y_true_arr, probs, labels=[0, 1, 2])),
            "brier": float(brier),
            "ece": float(ece),
            "interval_target_coverage": float(cfg.target_interval_coverage),
            "interval_q": interval_q_mean,
            "interval_width_mean": interval_width_mean,
            "interval_coverage": float(coverage),
            "interval_target_name": "close_future",
            "interval_target_unit": "price",
            "interval_clip_ratio": clip_ratio,
            "class_ratio": cls_ratio,
            "confusion_matrix": cm,
            "class_metrics": {
                "down": {"precision": float(pr[0]), "recall": float(rc[0]), "f1": float(f1_cls[0])},
                "flat": {"precision": float(pr[1]), "recall": float(rc[1]), "f1": float(f1_cls[1])},
                "up": {"precision": float(pr[2]), "recall": float(rc[2]), "f1": float(f1_cls[2])},
            },
        }

    # ----------------------------- utils -----------------------------
    def _label_class(self, ret_pct: float, band: float) -> int:
        # 0=down, 1=flat, 2=up
        if ret_pct >= band:
            return 2
        if ret_pct <= -band:
            return 0
        return 1

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

        floors = np.floor(vals).astype(int)
        remainder = int(100 - floors.sum())
        fracs = vals - floors
        order = np.argsort(-fracs)
        out = floors.copy()
        for i in range(max(0, remainder)):
            out[order[i % len(order)]] += 1

        if out.sum() != 100:
            diff = 100 - int(out.sum())
            out[np.argmax(out)] += diff

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
