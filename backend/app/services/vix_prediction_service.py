from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
import urllib.parse

import numpy as np
import pandas as pd

from app.db.database import Database


@dataclass
class VixRunConfig:
    asof_date: date
    horizon_days: int = 5
    backtest_years: int = 3


class VixPredictionService:
    """Lightweight multi-horizon VIX forecaster with backtest + bias-gap analysis."""

    MODEL_VERSION = "vix_multi_horizon_ols_rag_bias_v1"
    VIX_SYMBOLS = ("US:^VIX", "US:VIX")
    SPX_SYMBOLS = ("US:^GSPC", "US:SPY")
    NDX_SYMBOLS = ("US:^IXIC", "US:QQQ")
    VVIX_SYMBOLS = ("US:^VVIX",)
    VIX3M_SYMBOLS = ("US:^VIX3M",)

    def __init__(self) -> None:
        self.db = Database()
        self._news_category_keywords = {
            "us_domestic_policy_macro": [
                "federal reserve", "fed", "fomc", "powell", "treasury", "congress", "white house",
                "senate", "house lawmakers", "sec", "cpi", "ppi", "nfp", "payroll", "unemployment",
                "pce", "ism", "pmi", "gdp", "consumer confidence", "retail sales", "durable goods"
            ],
            "global_macro_geo": [
                "war", "attack", "missile", "drone", "conflict", "invasion", "military",
                "ceasefire", "iran", "ukraine", "russia", "china", "taiwan", "middle east",
                "sanction", "tariff", "export control", "embargo", "trade war"
            ],
            "earnings_guidance": [
                "earnings", "guidance", "revenue miss", "revenue beat", "eps miss", "eps beat",
                "forecast cut", "outlook", "guidance cut", "guidance raise", "profit warning"
            ],
            "options_put_call_flow": [
                "put", "puts", "call", "calls", "put/call", "put-call", "call skew", "put skew",
                "gamma", "dealer gamma", "vanna", "charm", "hedging", "delta hedge", "options flow",
                "volatility crush", "implied volatility", "iv", "skew", "0dte", "0dtes"
            ],
            "geo": [
                "war", "attack", "missile", "drone", "conflict", "invasion", "military",
                "ceasefire", "iran", "ukraine", "russia", "china", "taiwan", "middle east"
            ],
            "policy": [
                "fed", "fomc", "powell", "ecb", "boj", "rate cut", "rate hike",
                "central bank", "minutes", "speech", "treasury yield"
            ],
            "inflation_macro": [
                "cpi", "ppi", "inflation", "deflation", "nfp", "payroll", "unemployment",
                "gdp", "recession", "ism", "pmi", "consumer confidence"
            ],
            "trade_sanction": [
                "tariff", "sanction", "export control", "ban", "embargo", "customs", "trade war"
            ],
            "credit_liquidity": [
                "liquidity", "funding stress", "bank run", "default", "downgrade",
                "credit spread", "yield spike", "repo", "margin call"
            ],
            "jobless_claims": [
                "jobless claims", "initial claims", "weekly claims", "unemployment claims",
                "unemployment insurance", "continuing claims", "labor market", "layoffs",
                "job cuts", "job losses", "mass layoffs", "workers filed", "filed for unemployment",
                "nonfarm payroll", "payrolls", "jobs report", "job openings", "jolts",
                "labor department", "department of labor", "dol report"
            ],
            "event_week": [
                "this week", "next week", "tomorrow", "upcoming", "scheduled", "meeting",
                "earnings", "guidance", "cpi", "fomc", "powell", "jobs report"
            ],
            "risk_tone": [
                "panic", "crisis", "shock", "volatile", "uncertainty", "risk-off", "selloff", "plunge"
            ],
            "war_conflict": [
                "war", "warfare", "armed conflict", "military strike", "airstrike", "air strike",
                "bombardment", "shelling", "troops deployed", "ground offensive", "counteroffensive",
                "warship", "nuclear threat", "ballistic missile", "hypersonic", "escalation",
                "nato", "alliance", "ceasefire", "truce", "casualties", "civilian", "frontline",
                "proxy war", "invasion", "occupation", "annexed", "territory", "no-fly zone",
                "ukraine war", "middle east war", "israel war", "gaza", "west bank", "hezbollah",
                "hamas", "houthi", "iran attack", "china taiwan", "south china sea", "strait"
            ],
            "frb_central_bank": [
                "federal reserve", "fed reserve", "fomc", "jerome powell", "powell speech",
                "rate decision", "rate cut", "rate hike", "basis points", "bps cut", "bps hike",
                "quantitative tightening", "qt", "quantitative easing", "qe", "balance sheet",
                "fed minutes", "dot plot", "fed funds rate", "overnight rate", "terminal rate",
                "pivot", "dovish", "hawkish", "data dependent", "blackout period",
                "beige book", "fed speak", "waller", "kugler", "goolsbee", "barkin", "daly",
                "ecb", "boj", "bank of england", "boe", "lagarde", "ueda", "central bank",
                "interest rate path", "neutral rate", "r-star", "inflation target"
            ],
        }

    # ------------------------- public API -------------------------
    def run_and_save(
        self,
        asof_date: str | None = None,
        horizon_days: int = 5,
        backtest_years: int = 3,
        refresh_news: bool = True,
    ) -> dict[str, Any]:
        if horizon_days < 1 or horizon_days > 10:
            raise ValueError("horizon_days must be between 1 and 10")
        if backtest_years < 1 or backtest_years > 10:
            raise ValueError("backtest_years must be between 1 and 10")

        if not self.db.connect():
            raise RuntimeError("DB connection failed")
        try:
            self.ensure_tables()
            latest_db_date = self._get_latest_vix_date()
            if latest_db_date is None:
                raise RuntimeError("No VIX price data found in price_daily")
            asof = datetime.strptime(asof_date, "%Y-%m-%d").date() if asof_date else latest_db_date
            if asof > latest_db_date:
                asof = latest_db_date
            # run_date = actual execution date (may be weekend/holiday after last VIX close)
            # News fetching and feature loading use run_date so weekend/holiday news is captured.
            run_date = date.today()
            if run_date < asof:
                run_date = asof

            news_refresh = None
            if bool(refresh_news):
                news_refresh = self._refresh_news_for_vix_context(run_date)

            cfg = VixRunConfig(asof_date=asof, horizon_days=horizon_days, backtest_years=backtest_years)
            df = self._load_feature_frame(end_date=asof)
            if df.empty:
                raise RuntimeError("Insufficient price history for VIX prediction")
            auto_news_weights = self._derive_auto_news_weights(df.copy(), cfg)
            df = self._apply_auto_news_weighted_score(df.copy(), auto_news_weights)

            feature_cols = [c for c in self._feature_columns() if c in df.columns]
            if len(feature_cols) < 5:
                raise RuntimeError("Insufficient feature columns for VIX prediction")

            backtest = self._run_backtest(df.copy(), cfg, feature_cols)
            backtest["auto_news_weights"] = auto_news_weights
            current_news = self._load_news_features(asof, news_end_date=run_date)
            current_news["news_refresh"] = news_refresh
            current_news["auto_news_weights"] = auto_news_weights
            current_news["rag_auto_weighted_risk_score_7d"] = self._score_current_news_with_auto_weights(
                current_news, auto_news_weights
            )
            current_news["news_usage"] = self._load_news_usage_details(asof, news_end_date=run_date)
            current_row = self._build_current_row(df, asof, current_news)
            forecasts = self._predict_multi_horizon(df.copy(), current_row, cfg, feature_cols, backtest)

            run_id = self._insert_run(cfg, current_news, backtest)
            self._insert_predictions(run_id, cfg, forecasts)
            payload = self.get_run(run_id)
            return payload
        finally:
            self.db.disconnect()

    def get_latest_run(self) -> dict[str, Any] | None:
        if not self.db.connect():
            return None
        try:
            self.ensure_tables()
            rows = self.db.execute_query(
                """
                SELECT run_id
                FROM vix_prediction_runs
                ORDER BY created_at DESC, run_id DESC
                LIMIT 1
                """
            ) or []
            if not rows:
                return None
            return self.get_run(int(rows[0][0]))
        finally:
            self.db.disconnect()

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 20), 200))
        if not self.db.connect():
            return []
        try:
            self.ensure_tables()
            rows = self.db.execute_query(
                """
                SELECT r.run_id, r.asof_date, r.horizon_days, r.backtest_years, r.model_version, r.created_at,
                       (SELECT AVG(v.predicted_vix) FROM vix_prediction_values v WHERE v.run_id = r.run_id) AS avg_pred_vix,
                       (SELECT AVG(v.confidence_score) FROM vix_prediction_values v WHERE v.run_id = r.run_id) AS avg_confidence,
                       (SELECT COUNT(*) FROM vix_prediction_values v WHERE v.run_id = r.run_id AND v.actual_vix IS NOT NULL) AS realized_count,
                       (SELECT AVG(ABS(v.predicted_vix - v.actual_vix)) FROM vix_prediction_values v WHERE v.run_id = r.run_id AND v.actual_vix IS NOT NULL) AS realized_mae
                FROM vix_prediction_runs r
                ORDER BY r.created_at DESC, r.run_id DESC
                LIMIT %s
                """,
                (limit,),
            ) or []
            out: list[dict[str, Any]] = []
            for r in rows:
                out.append(
                    {
                        "run_id": int(r[0]),
                        "asof_date": r[1].isoformat() if r[1] else None,
                        "horizon_days": int(r[2]),
                        "backtest_years": int(r[3]),
                        "model_version": r[4],
                        "created_at": r[5].isoformat() if r[5] else None,
                        "avg_pred_vix": float(r[6]) if r[6] is not None else None,
                        "avg_confidence": float(r[7]) if r[7] is not None else None,
                        "realized_count": int(r[8]) if r[8] is not None else 0,
                        "realized_mae": float(r[9]) if r[9] is not None else None,
                    }
                )
            return out
        finally:
            self.db.disconnect()

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        rows = self.db.execute_query(
            """
            SELECT run_id, asof_date, horizon_days, backtest_years, model_version,
                   feature_snapshot_json, news_features_json, backtest_summary_json,
                   created_at
            FROM vix_prediction_runs
            WHERE run_id = %s
            """,
            (int(run_id),),
        ) or []
        if not rows:
            return None
        r = rows[0]
        pred_rows = self.db.execute_query(
            """
            SELECT horizon_day, target_date, predicted_vix, predicted_vix_lower, predicted_vix_upper,
                   actual_vix, bias_adjustment, confidence_score, regime_bucket, blend_alpha_ols
            FROM vix_prediction_values
            WHERE run_id = %s
            ORDER BY horizon_day ASC
            """,
            (int(run_id),),
        ) or []
        predictions: list[dict[str, Any]] = []
        for p in pred_rows:
            predictions.append(
                {
                    "horizon_day": int(p[0]),
                    "target_date": p[1].isoformat() if p[1] else None,
                    "predicted_vix": float(p[2]) if p[2] is not None else None,
                    "predicted_vix_lower": float(p[3]) if p[3] is not None else None,
                    "predicted_vix_upper": float(p[4]) if p[4] is not None else None,
                    "actual_vix": float(p[5]) if p[5] is not None else None,
                    "bias_adjustment": float(p[6]) if p[6] is not None else 0.0,
                    "confidence_score": float(p[7]) if p[7] is not None else None,
                    "regime_bucket": p[8],
                    "blend_alpha_ols": float(p[9]) if len(p) > 9 and p[9] is not None else None,
                }
            )
        return {
            "run_id": int(r[0]),
            "asof_date": r[1].isoformat() if r[1] else None,
            "horizon_days": int(r[2]),
            "backtest_years": int(r[3]),
            "model_version": r[4],
            "feature_snapshot": self._json_loads(r[5], {}),
            "news_features": self._json_loads(r[6], {}),
            "backtest_summary": self._json_loads(r[7], {}),
            "created_at": r[8].isoformat() if r[8] else None,
            "predictions": predictions,
        }

    def backfill_actuals(self, run_limit: int = 500) -> dict[str, Any]:
        run_limit = max(1, min(int(run_limit or 500), 5000))
        if not self.db.connect():
            raise RuntimeError("DB connection failed")
        try:
            self.ensure_tables()
            latest_vix_date = self._get_latest_vix_date()
            if latest_vix_date is None:
                return {"status": "empty", "updated_rows": 0, "latest_vix_date": None}
            rows = self.db.execute_query(
                """
                SELECT v.run_id, v.horizon_day, v.target_date, p.close
                FROM vix_prediction_values v
                JOIN price_daily p
                  ON p.trading_date = v.target_date
                 AND p.symbol_key IN ('US:^VIX','US:VIX')
                WHERE v.actual_vix IS NULL
                  AND v.target_date <= %s
                  AND v.run_id IN (
                      SELECT run_id
                      FROM vix_prediction_runs
                      ORDER BY created_at DESC, run_id DESC
                      LIMIT %s
                  )
                """,
                (latest_vix_date, run_limit),
            ) or []
            updated = 0
            touched_runs: set[int] = set()
            seen_keys: set[tuple[int, int]] = set()
            for run_id, h, target_date, close in rows:
                key = (int(run_id), int(h))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                ok = self.db.execute_command(
                    """
                    UPDATE vix_prediction_values
                    SET actual_vix = %s
                    WHERE run_id = %s AND horizon_day = %s AND target_date = %s
                    """,
                    (float(close), int(run_id), int(h), target_date),
                )
                if ok:
                    updated += 1
                    touched_runs.add(int(run_id))
            return {
                "status": "success",
                "latest_vix_date": latest_vix_date.isoformat(),
                "updated_rows": int(updated),
                "touched_runs": sorted(list(touched_runs), reverse=True),
            }
        finally:
            self.db.disconnect()

    # ------------------------- schema -------------------------
    def ensure_tables(self) -> None:
        self.db.execute_command(
            """
            CREATE TABLE IF NOT EXISTS vix_prediction_runs (
                run_id BIGSERIAL PRIMARY KEY,
                asof_date DATE NOT NULL,
                horizon_days INTEGER NOT NULL,
                backtest_years INTEGER NOT NULL,
                model_version TEXT NOT NULL,
                feature_snapshot_json TEXT NULL,
                news_features_json TEXT NULL,
                backtest_summary_json TEXT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.db.execute_command(
            """
            CREATE TABLE IF NOT EXISTS vix_prediction_values (
                run_id BIGINT NOT NULL,
                horizon_day INTEGER NOT NULL,
                target_date DATE NOT NULL,
                predicted_vix DOUBLE PRECISION NOT NULL,
                predicted_vix_lower DOUBLE PRECISION NULL,
                predicted_vix_upper DOUBLE PRECISION NULL,
                actual_vix DOUBLE PRECISION NULL,
                bias_adjustment DOUBLE PRECISION NULL,
                confidence_score DOUBLE PRECISION NULL,
                regime_bucket TEXT NULL,
                blend_alpha_ols DOUBLE PRECISION NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (run_id, horizon_day)
            )
            """
        )
        self.db.execute_command(
            "ALTER TABLE vix_prediction_values ADD COLUMN IF NOT EXISTS blend_alpha_ols DOUBLE PRECISION NULL"
        )
        self.db.execute_command(
            """
            CREATE INDEX IF NOT EXISTS idx_vix_prediction_runs_created
            ON vix_prediction_runs (created_at DESC)
            """
        )

    # ------------------------- loading -------------------------
    def _get_latest_vix_date(self) -> date | None:
        rows = self.db.execute_query(
            "SELECT MAX(trading_date) FROM price_daily WHERE symbol_key IN ('US:^VIX','US:VIX')"
        ) or []
        return rows[0][0] if rows and rows[0] and rows[0][0] else None

    def _load_feature_frame(self, end_date: date) -> pd.DataFrame:
        start_date = end_date - timedelta(days=365 * 6)
        rows = self.db.execute_query(
            """
            SELECT symbol_key, trading_date, close
            FROM price_daily
            WHERE trading_date BETWEEN %s AND %s
              AND symbol_key IN ('US:^VIX','US:VIX','US:^GSPC','US:SPY','US:^IXIC','US:QQQ','US:^VVIX','US:^VIX3M')
            ORDER BY trading_date ASC
            """,
            (start_date, end_date),
        ) or []
        if not rows:
            return pd.DataFrame()

        recs = []
        for sym, d, c in rows:
            recs.append({"symbol_key": str(sym), "trading_date": pd.to_datetime(d), "close": float(c)})
        df = pd.DataFrame(recs)

        def pick_symbol(candidates: tuple[str, ...]) -> pd.Series | None:
            for sym in candidates:
                sub = df[df["symbol_key"] == sym][["trading_date", "close"]].drop_duplicates("trading_date", keep="last")
                if not sub.empty:
                    return sub.set_index("trading_date")["close"].rename(sym)
            return None

        vix = pick_symbol(self.VIX_SYMBOLS)
        spx = pick_symbol(self.SPX_SYMBOLS)
        ndx = pick_symbol(self.NDX_SYMBOLS)
        vvix = pick_symbol(self.VVIX_SYMBOLS)
        vix3m = pick_symbol(self.VIX3M_SYMBOLS)
        if vix is None:
            return pd.DataFrame()

        base = pd.DataFrame(index=vix.index.unique().sort_values())
        base["vix_close"] = vix.reindex(base.index)
        if spx is not None:
            base["spx_close"] = spx.reindex(base.index).ffill()
        if ndx is not None:
            base["ndx_close"] = ndx.reindex(base.index).ffill()

        base = base.sort_index()
        base["vix_ret_1d"] = base["vix_close"].pct_change()
        base["spx_ret_1d"] = base["spx_close"].pct_change() if "spx_close" in base else np.nan
        base["ndx_ret_1d"] = base["ndx_close"].pct_change() if "ndx_close" in base else np.nan
        base["vix_ma5"] = base["vix_close"].rolling(5).mean()
        base["vix_ma20"] = base["vix_close"].rolling(20).mean()
        base["vix_std5"] = base["vix_ret_1d"].rolling(5).std()
        base["vix_std20"] = base["vix_ret_1d"].rolling(20).std()
        base["vix_mr5"] = (base["vix_close"] / base["vix_ma5"]) - 1.0
        base["vix_mr20"] = (base["vix_close"] / base["vix_ma20"]) - 1.0
        base["spx_ret_5d"] = base["spx_close"].pct_change(5) if "spx_close" in base else np.nan
        base["ndx_ret_5d"] = base["ndx_close"].pct_change(5) if "ndx_close" in base else np.nan
        for lag in range(1, 6):
            base[f"vix_ret_lag{lag}"] = base["vix_ret_1d"].shift(lag)
            base[f"spx_ret_lag{lag}"] = base["spx_ret_1d"].shift(lag) if "spx_ret_1d" in base else np.nan

        # VVIX features (VIX of VIX — tail-spike leading indicator)
        if vvix is not None:
            base["vvix_close"] = vvix.reindex(base.index).ffill()
            base["vvix_ma5"] = base["vvix_close"].rolling(5).mean()
            base["vvix_ret_1d"] = base["vvix_close"].pct_change()
            base["vvix_mr5"] = (base["vvix_close"] / base["vvix_ma5"]) - 1.0

        # VIX term structure features (VIX vs VIX3M — contango/backwardation)
        # Negative spread = VIX < VIX3M = contango (VIX expected to rise toward VIX3M)
        # Positive spread = VIX > VIX3M = backwardation (VIX elevated, expected to revert down)
        if vix3m is not None:
            base["vix3m_close"] = vix3m.reindex(base.index).ffill()
            base["vix_vix3m_spread"] = base["vix_close"] - base["vix3m_close"]
            base["vix_vix3m_ratio"] = base["vix_close"] / base["vix3m_close"].replace(0, np.nan)
            base["vix3m_ma5"] = base["vix3m_close"].rolling(5).mean()
            base["ts_slope_5d"] = base["vix_vix3m_spread"].rolling(5).mean()

        # Market environment features (best effort)
        me_rows = self.db.execute_query(
            """
            SELECT check_date, data
            FROM market_environment
            WHERE check_date BETWEEN %s AND %s
            ORDER BY check_date ASC
            """,
            (start_date, end_date),
        ) or []
        if me_rows:
            me_records = []
            for d, data_json in me_rows:
                data_obj = self._json_loads(data_json, {})
                if not isinstance(data_obj, dict):
                    data_obj = {}
                me_records.append(
                    {
                        "trading_date": pd.to_datetime(d),
                        "me_vix_level": self._safe_nested_float(data_obj, ["vix", "level"], fallback_key="vix"),
                        "me_vix_change_pct": self._safe_nested_float(data_obj, ["vix", "change_pct"], fallback_key="vix_change_pct"),
                        "me_put_call_proxy": self._safe_nested_float(data_obj, ["sentiment", "put_call_proxy"], fallback_key="put_call_proxy"),
                        "me_risk_on_score": self._safe_nested_float(data_obj, ["scores", "risk_on_score"], fallback_key="risk_on_score"),
                    }
                )
            me = pd.DataFrame(me_records).drop_duplicates("trading_date", keep="last").set_index("trading_date")
            base = base.join(me, how="left")

        # News / LLM features per day (best effort, point-in-time via day aggregation)
        news_daily = self._load_daily_news_feature_series(start_date, end_date)
        if not news_daily.empty:
            base = base.join(news_daily, how="left")

        base = base.ffill()
        base["trading_date"] = base.index.date
        return base.reset_index(drop=True)

    def _load_daily_news_feature_series(self, start_date: date, end_date: date) -> pd.DataFrame:
        rows = self.db.execute_query(
            """
            SELECT DATE(published_at) AS d,
                   COUNT(*) AS doc_count,
                   STRING_AGG(COALESCE(title,'') || ' ' || COALESCE(content_text,''), ' ') AS all_text
            FROM rag_documents
            WHERE published_at IS NOT NULL
              AND DATE(published_at) BETWEEN %s AND %s
              AND symbol_key IN ('US:^VIX','US:VIX','US:^GSPC','US:SPY','US:^IXIC','US:QQQ')
            GROUP BY DATE(published_at)
            ORDER BY d ASC
            """,
            (start_date, end_date),
        ) or []
        if not rows:
            return pd.DataFrame()

        out = []
        for d, cnt, text_blob in rows:
            text = (text_blob or "").lower()
            scored = self._score_news_text_blob(text)
            hits = float(scored.get("news_risk_hits", 0.0))
            out.append(
                {
                    "trading_date": pd.to_datetime(d),
                    "news_doc_count": float(cnt or 0),
                    "news_risk_hits": hits,
                    "news_risk_density": hits / max(1.0, float(cnt or 0)),
                    "news_us_domestic_policy_macro_score": float(scored.get("news_us_domestic_policy_macro_score", 0.0)),
                    "news_global_macro_geo_score": float(scored.get("news_global_macro_geo_score", 0.0)),
                    "news_earnings_guidance_score": float(scored.get("news_earnings_guidance_score", 0.0)),
                    "news_options_put_call_flow_score": float(scored.get("news_options_put_call_flow_score", 0.0)),
                    "news_geo_score": float(scored.get("news_geo_score", 0.0)),
                    "news_policy_score": float(scored.get("news_policy_score", 0.0)),
                    "news_inflation_macro_score": float(scored.get("news_inflation_macro_score", 0.0)),
                    "news_trade_sanction_score": float(scored.get("news_trade_sanction_score", 0.0)),
                    "news_credit_liquidity_score": float(scored.get("news_credit_liquidity_score", 0.0)),
                    "news_event_week_score": float(scored.get("news_event_week_score", 0.0)),
                    "news_risk_tone_score": float(scored.get("news_risk_tone_score", 0.0)),
                    "news_war_conflict_score": float(scored.get("news_war_conflict_score", 0.0)),
                    "news_frb_central_bank_score": float(scored.get("news_frb_central_bank_score", 0.0)),
                    "news_jobless_claims_score": float(scored.get("news_jobless_claims_score", 0.0)),
                    "news_weighted_risk_score": float(scored.get("news_weighted_risk_score", 0.0)),
                }
            )
        news_df = pd.DataFrame(out).drop_duplicates("trading_date", keep="last").set_index("trading_date")
        news_df["news_doc_count_5d"] = news_df["news_doc_count"].rolling(5, min_periods=1).sum()
        news_df["news_risk_hits_5d"] = news_df["news_risk_hits"].rolling(5, min_periods=1).sum()
        for c in [
            "news_geo_score",
            "news_us_domestic_policy_macro_score",
            "news_global_macro_geo_score",
            "news_earnings_guidance_score",
            "news_options_put_call_flow_score",
            "news_policy_score",
            "news_inflation_macro_score",
            "news_trade_sanction_score",
            "news_credit_liquidity_score",
            "news_event_week_score",
            "news_risk_tone_score",
            "news_war_conflict_score",
            "news_frb_central_bank_score",
            "news_jobless_claims_score",
            "news_weighted_risk_score",
        ]:
            news_df[f"{c}_5d_mean"] = news_df[c].rolling(5, min_periods=1).mean()
        return news_df

    def _load_news_features(self, asof: date, news_end_date: date | None = None) -> dict[str, Any]:
        features: dict[str, Any] = {}
        end = news_end_date if news_end_date is not None else asof
        rows = self.db.execute_query(
            """
            SELECT COUNT(*),
                   STRING_AGG(COALESCE(title,'') || ' ' || COALESCE(content_text,''), ' ')
            FROM rag_documents
            WHERE published_at IS NOT NULL
              AND DATE(published_at) BETWEEN %s AND %s
              AND symbol_key IN ('US:^VIX','US:VIX','US:^GSPC','US:SPY','US:^IXIC','US:QQQ')
            """,
            (end - timedelta(days=7), end),
        ) or []
        if rows and rows[0]:
            features["rag_doc_count_7d"] = int(rows[0][0] or 0)
            text7 = str(rows[0][1] or "").lower()
            scored7 = self._score_news_text_blob(text7)
            features["rag_risk_hits_7d"] = float(scored7.get("news_risk_hits", 0.0))
            features["rag_risk_ratio_7d"] = float(features["rag_risk_hits_7d"]) / max(1.0, float(features["rag_doc_count_7d"]))
            features["rag_us_domestic_policy_macro_score_7d"] = float(scored7.get("news_us_domestic_policy_macro_score", 0.0))
            features["rag_global_macro_geo_score_7d"] = float(scored7.get("news_global_macro_geo_score", 0.0))
            features["rag_earnings_guidance_score_7d"] = float(scored7.get("news_earnings_guidance_score", 0.0))
            features["rag_options_put_call_flow_score_7d"] = float(scored7.get("news_options_put_call_flow_score", 0.0))
            features["rag_geo_score_7d"] = float(scored7.get("news_geo_score", 0.0))
            features["rag_policy_score_7d"] = float(scored7.get("news_policy_score", 0.0))
            features["rag_infl_macro_score_7d"] = float(scored7.get("news_inflation_macro_score", 0.0))
            features["rag_trade_sanction_score_7d"] = float(scored7.get("news_trade_sanction_score", 0.0))
            features["rag_credit_liquidity_score_7d"] = float(scored7.get("news_credit_liquidity_score", 0.0))
            features["rag_event_week_score_7d"] = float(scored7.get("news_event_week_score", 0.0))
            features["rag_risk_tone_score_7d"] = float(scored7.get("news_risk_tone_score", 0.0))
            features["rag_war_conflict_score_7d"] = float(scored7.get("news_war_conflict_score", 0.0))
            features["rag_frb_central_bank_score_7d"] = float(scored7.get("news_frb_central_bank_score", 0.0))
            features["rag_jobless_claims_score_7d"] = float(scored7.get("news_jobless_claims_score", 0.0))
            features["rag_weighted_risk_score_7d"] = float(scored7.get("news_weighted_risk_score", 0.0))

        llm_rows = self.db.execute_query(
            """
            SELECT symbol_key, llm_features_json, quality_metrics_json, asof, updated_at
            FROM llm_signal_feature_sets
            WHERE symbol_key IN ('US:^VIX','US:^GSPC','US:SPY')
              AND asof <= %s
            ORDER BY asof DESC, updated_at DESC
            LIMIT 6
            """,
            (asof,),
        ) or []
        llm_numeric: dict[str, float] = {}
        for sym, llm_json, qual_json, row_asof, _ in llm_rows:
            lf = self._json_loads(llm_json, {})
            if not isinstance(lf, dict):
                continue
            for k, v in lf.items():
                try:
                    val = float(v)
                except Exception:
                    continue
                key = f"llm_{str(sym).replace(':', '_')}_{k}".lower()
                llm_numeric[key] = val
        if llm_numeric:
            risk_sum = 0.0
            n = 0
            for k, v in llm_numeric.items():
                if any(tok in k for tok in ("risk", "vol", "uncert", "geo", "hawk", "panic")):
                    risk_sum += float(v)
                    n += 1
            features["llm_risk_signal_mean"] = float(risk_sum / n) if n > 0 else 0.0
            features["llm_feature_count_used"] = int(len(llm_numeric))
            # Add a few interpretable grouped aggregates for VIX forecasting.
            features["llm_geo_risk_signal"] = self._agg_llm_keyword_group(
                llm_numeric, ["geo", "war", "conflict", "sanction", "supply"]
            )
            features["llm_policy_risk_signal"] = self._agg_llm_keyword_group(
                llm_numeric, ["fed", "policy", "rate", "hawk", "inflation"]
            )
            features["llm_event_risk_signal"] = self._agg_llm_keyword_group(
                llm_numeric, ["event", "earnings", "surprise", "uncert", "vol"]
            )

        return features

    def _load_news_usage_details(self, asof: date, news_end_date: date | None = None) -> dict[str, Any]:
        end = news_end_date if news_end_date is not None else asof
        rows = self.db.execute_query(
            """
            SELECT symbol_key, source_type, source_ref, published_at, title
            FROM rag_documents
            WHERE published_at IS NOT NULL
              AND DATE(published_at) BETWEEN %s AND %s
              AND symbol_key IN ('US:^VIX','US:VIX','US:^GSPC','US:SPY','US:^IXIC','US:QQQ')
            ORDER BY published_at DESC
            LIMIT 50
            """,
            (end - timedelta(days=7), end),
        ) or []
        items = []
        by_symbol: dict[str, int] = {}
        by_source: dict[str, int] = {}
        for r in rows:
            sym = str(r[0] or "")
            src = str(r[1] or "")
            by_symbol[sym] = by_symbol.get(sym, 0) + 1
            by_source[src] = by_source.get(src, 0) + 1
            items.append(
                {
                    "symbol_key": sym,
                    "source_type": src,
                    "source_ref": str(r[2] or ""),
                    "published_at": r[3].isoformat() if r[3] else None,
                    "title": str(r[4] or ""),
                }
            )
        return {
            "window_days": 7,
            "docs_used_count": len(items),
            "docs_by_symbol": by_symbol,
            "docs_by_source_type": by_source,
            "docs_sample": items[:15],
        }

    def _refresh_news_for_vix_context(self, asof: date) -> dict[str, Any]:
        """
        Best-effort one-button RSS refresh for VIX/US-market context.
        Uses Google News RSS custom queries and ingests into rag_documents.
        """
        script_path = Path(__file__).resolve().parents[2] / "scripts" / "ingest_external_news_rss_backfill.py"
        if not script_path.exists():
            return {"status": "warning", "message": f"news ingest script not found: {script_path}"}

        feeds_file = Path(__file__).resolve().parents[2] / "ml_predictor_data" / "_vix_news_feeds_tmp.json"
        queries = [
            {"symbol_key": "US:^VIX", "label": "VIX macro vol", "query": "VIX volatility market selloff OR risk-off OR fear index"},
            {"symbol_key": "US:SPY", "label": "US macro policy", "query": "Federal Reserve FOMC Powell CPI PCE NFP US stocks volatility"},
            {"symbol_key": "US:QQQ", "label": "US tech risk", "query": "Nasdaq QQQ tech selloff earnings guidance volatility"},
            {"symbol_key": "US:^GSPC", "label": "SPX options flow", "query": "S&P 500 options flow put call ratio gamma dealer hedging skew"},
            {"symbol_key": "US:^VIX", "label": "Geo risk", "query": "US market geopolitical risk sanctions war oil shock volatility"},
            # Dedicated war/conflict feed — escalation events directly spike VIX
            {"symbol_key": "US:^VIX", "label": "War conflict", "query": "war military strike airstrike troops invasion escalation ceasefire nuclear NATO Ukraine Gaza Iran Taiwan"},
            # Dedicated FRB/Fed feed — rate-path surprises are the single biggest VIX driver
            {"symbol_key": "US:^VIX", "label": "FRB Fed policy", "query": "Federal Reserve FOMC Powell rate decision rate cut rate hike basis points hawkish dovish dot plot quantitative tightening balance sheet"},
            # Jobless claims — weekly labor market shock (pivotal variable for 5-day horizon)
            {"symbol_key": "US:^VIX", "label": "Jobless claims", "query": "jobless claims initial claims unemployment claims weekly claims labor department layoffs job cuts mass layoffs nonfarm payroll jobs report"},
        ]
        specs = []
        for q in queries:
            encoded = urllib.parse.quote(q["query"])
            url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
            specs.append(
                {
                    "symbol_key": q["symbol_key"],
                    "url": url,
                    "source_type": "google_news_rss",
                    "metadata": {"provider": "google-news-rss", "vix_query_label": q["label"], "query": q["query"]},
                }
            )
        try:
            feeds_file.parent.mkdir(parents=True, exist_ok=True)
            feeds_file.write_text(json.dumps(specs, ensure_ascii=False), encoding="utf-8")
            cmd = [
                sys.executable,
                str(script_path),
                "--asof", asof.isoformat(),
                "--months-back", "1",
                "--feeds-file", str(feeds_file),
                "--timeout", "8",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
            return {
                "status": "success" if proc.returncode == 0 else "warning",
                "returncode": int(proc.returncode),
                "queries": [{"symbol_key": q["symbol_key"], "label": q["label"], "query": q["query"]} for q in queries],
                "stdout_tail": (proc.stdout or "").splitlines()[-20:],
                "stderr_tail": (proc.stderr or "").splitlines()[-20:],
            }
        except Exception as e:
            return {"status": "warning", "message": str(e), "queries": queries}
        finally:
            try:
                if feeds_file.exists():
                    feeds_file.unlink()
            except Exception:
                pass

    # ------------------------- modeling -------------------------
    def _feature_columns(self) -> list[str]:
        return [
            "vix_close",
            "vix_ret_1d",
            "spx_ret_1d",
            "ndx_ret_1d",
            "vix_mr5",
            "vix_mr20",
            "vix_std5",
            "vix_std20",
            "spx_ret_5d",
            "ndx_ret_5d",
            "me_vix_change_pct",
            "me_put_call_proxy",
            "me_risk_on_score",
            "news_doc_count",
            "news_risk_hits",
            "news_risk_density",
            "news_doc_count_5d",
            "news_risk_hits_5d",
            "news_us_domestic_policy_macro_score",
            "news_global_macro_geo_score",
            "news_earnings_guidance_score",
            "news_options_put_call_flow_score",
            "news_geo_score",
            "news_policy_score",
            "news_inflation_macro_score",
            "news_trade_sanction_score",
            "news_credit_liquidity_score",
            "news_event_week_score",
            "news_risk_tone_score",
            "news_war_conflict_score",
            "news_frb_central_bank_score",
            "news_weighted_risk_score",
            "news_auto_weighted_risk_score",
            "news_weighted_risk_score_5d_mean",
            "news_options_put_call_flow_score_5d_mean",
            "news_war_conflict_score_5d_mean",
            "news_frb_central_bank_score_5d_mean",
            # jobless claims
            "news_jobless_claims_score",
            "news_jobless_claims_score_5d_mean",
            # VVIX (VIX of VIX — tail-spike leading indicator)
            "vvix_close",
            "vvix_ma5",
            "vvix_ret_1d",
            "vvix_mr5",
            # VIX term structure (contango / backwardation)
            "vix3m_close",
            "vix_vix3m_spread",
            "vix_vix3m_ratio",
            "ts_slope_5d",
            # PTI overrides (point-in-time)
            "news_risk_score_pti",
            "news_event_week_score_pti",
            "news_geo_score_pti",
            "news_options_put_call_flow_score_pti",
            "news_war_conflict_score_pti",
            "news_frb_central_bank_score_pti",
            "news_jobless_claims_score_pti",
            "llm_risk_signal_mean_pti",
            "llm_geo_risk_signal_pti",
            "llm_policy_risk_signal_pti",
            "llm_event_risk_signal_pti",
        ] + [f"vix_ret_lag{i}" for i in range(1, 6)] + [f"spx_ret_lag{i}" for i in range(1, 6)]

    def _build_current_row(self, df: pd.DataFrame, asof: date, current_news: dict[str, Any]) -> pd.Series:
        sub = df[df["trading_date"] <= asof].copy()
        if sub.empty:
            raise RuntimeError("No feature row available for asof")
        row = sub.iloc[-1].copy()
        row["news_risk_score_pti"] = float(
            current_news.get(
                "rag_auto_weighted_risk_score_7d",
                current_news.get("rag_weighted_risk_score_7d", current_news.get("rag_risk_ratio_7d", 0.0)),
            )
        )
        row["news_event_week_score_pti"] = float(current_news.get("rag_event_week_score_7d", 0.0))
        row["news_geo_score_pti"] = float(current_news.get("rag_geo_score_7d", 0.0))
        row["news_options_put_call_flow_score_pti"] = float(current_news.get("rag_options_put_call_flow_score_7d", 0.0))
        row["news_war_conflict_score_pti"] = float(current_news.get("rag_war_conflict_score_7d", 0.0))
        row["news_frb_central_bank_score_pti"] = float(current_news.get("rag_frb_central_bank_score_7d", 0.0))
        row["news_jobless_claims_score_pti"] = float(current_news.get("rag_jobless_claims_score_7d", 0.0))
        row["llm_risk_signal_mean_pti"] = float(current_news.get("llm_risk_signal_mean", 0.0))
        row["llm_geo_risk_signal_pti"] = float(current_news.get("llm_geo_risk_signal", 0.0))
        row["llm_policy_risk_signal_pti"] = float(current_news.get("llm_policy_risk_signal", 0.0))
        row["llm_event_risk_signal_pti"] = float(current_news.get("llm_event_risk_signal", 0.0))
        return row

    def _run_backtest(self, df: pd.DataFrame, cfg: VixRunConfig, feature_cols: list[str]) -> dict[str, Any]:
        df = df.copy()
        backtest_start = cfg.asof_date - timedelta(days=365 * cfg.backtest_years)
        df = df[df["trading_date"] <= cfg.asof_date].copy()
        df["news_risk_score_pti"] = df.get(
            "news_auto_weighted_risk_score",
            df.get("news_weighted_risk_score", df.get("news_risk_density", 0.0))
        ).fillna(0.0)
        df["news_event_week_score_pti"] = df.get("news_event_week_score", 0.0).fillna(0.0)
        df["news_geo_score_pti"] = df.get("news_geo_score", 0.0).fillna(0.0)
        df["news_options_put_call_flow_score_pti"] = df.get("news_options_put_call_flow_score", 0.0).fillna(0.0)
        df["news_war_conflict_score_pti"] = df.get("news_war_conflict_score", pd.Series(0.0, index=df.index)).fillna(0.0)
        df["news_frb_central_bank_score_pti"] = df.get("news_frb_central_bank_score", pd.Series(0.0, index=df.index)).fillna(0.0)
        df["llm_risk_signal_mean_pti"] = 0.0  # historical LLM coverage may be sparse; safe default
        df["llm_geo_risk_signal_pti"] = 0.0
        df["llm_policy_risk_signal_pti"] = 0.0
        df["llm_event_risk_signal_pti"] = 0.0

        bt_rows = df[df["trading_date"] >= backtest_start].copy()
        metrics_by_h: dict[str, Any] = {}
        residual_bucket_bias: dict[str, dict[str, float]] = {}
        training_rows_used = 0

        for h in range(1, cfg.horizon_days + 1):
            target_col = f"target_vix_tplus_{h}"
            bt_rows[target_col] = bt_rows["vix_close"].shift(-h)
            # Features can be sparse (news / market_environment / llm). Do not drop rows on feature NaN.
            # We only require target and core VIX close; feature NaN is handled downstream via nan_to_num.
            model_df = bt_rows.dropna(subset=[target_col, "vix_close"]).copy()
            if len(model_df) < 220:
                metrics_by_h[str(h)] = {"status": "insufficient_data", "rows": int(len(model_df))}
                continue

            split_idx = max(180, int(len(model_df) * 0.7))
            train_df = model_df.iloc[:split_idx].copy()
            test_df = model_df.iloc[split_idx:].copy()
            training_rows_used = max(training_rows_used, len(train_df))

            beta = self._fit_ols(train_df[feature_cols], train_df[target_col])
            yhat = self._predict_ols(test_df[feature_cols], beta)
            persist = test_df["vix_close"].to_numpy(dtype=float)
            best_alpha = 1.0
            best_mae = None
            for alpha in (0.0, 0.25, 0.5, 0.75, 1.0):  # weight on OLS; remainder on persistence
                yhat_mix = (float(alpha) * yhat) + ((1.0 - float(alpha)) * persist)
                resid_mix = test_df[target_col].to_numpy(dtype=float) - yhat_mix
                mae_mix = float(np.mean(np.abs(resid_mix))) if len(resid_mix) else None
                if mae_mix is None:
                    continue
                if best_mae is None or mae_mix < best_mae:
                    best_mae = mae_mix
                    best_alpha = float(alpha)
            yhat = (best_alpha * yhat) + ((1.0 - best_alpha) * persist)
            resid = test_df[target_col].to_numpy(dtype=float) - yhat
            mae = float(np.mean(np.abs(resid))) if len(resid) else None
            rmse = float(np.sqrt(np.mean(np.square(resid)))) if len(resid) else None

            bucket_cols = self._regime_bucket_labels(test_df)
            bucket_bias = {}
            for bucket in sorted(set(bucket_cols)):
                idx = np.array([b == bucket for b in bucket_cols], dtype=bool)
                if int(np.sum(idx)) < 5:
                    continue
                bucket_bias[bucket] = float(np.mean(resid[idx]))
            residual_bucket_bias[str(h)] = bucket_bias

            # Gap feature correlations for future use
            gap_features = {}
            for c in ("vix_close", "vix_std5", "spx_ret_1d", "news_risk_score_pti", "news_event_week_score_pti", "news_geo_score_pti", "news_options_put_call_flow_score_pti"):
                arr_x = test_df[c].to_numpy(dtype=float)
                if len(arr_x) == len(resid) and len(arr_x) > 10 and np.nanstd(arr_x) > 0:
                    corr = np.corrcoef(np.nan_to_num(arr_x), np.nan_to_num(resid))[0, 1]
                    if np.isfinite(corr):
                        gap_features[f"corr_resid_vs_{c}"] = float(corr)

            metrics_by_h[str(h)] = {
                "status": "ok",
                "rows_train": int(len(train_df)),
                "rows_test": int(len(test_df)),
                "mae": mae,
                "rmse": rmse,
                "residual_bias_mean": float(np.mean(resid)) if len(resid) else 0.0,
                "residual_bias_abs_mean": float(np.mean(np.abs(resid))) if len(resid) else 0.0,
                "best_blend_alpha_ols": float(best_alpha),
                "bucket_bias": bucket_bias,
                "gap_feature_signal": gap_features,
            }

        return {
            "backtest_start": backtest_start.isoformat(),
            "backtest_end": cfg.asof_date.isoformat(),
            "training_rows_used_max": int(training_rows_used),
            "metrics_by_horizon": metrics_by_h,
            "residual_bucket_bias": residual_bucket_bias,
        }

    def _predict_multi_horizon(
        self,
        df: pd.DataFrame,
        current_row: pd.Series,
        cfg: VixRunConfig,
        feature_cols: list[str],
        backtest: dict[str, Any],
    ) -> list[dict[str, Any]]:
        df = df.copy()
        df["news_risk_score_pti"] = df.get(
            "news_auto_weighted_risk_score",
            df.get("news_weighted_risk_score", df.get("news_risk_density", 0.0))
        ).fillna(0.0)
        df["news_event_week_score_pti"] = df.get("news_event_week_score", 0.0).fillna(0.0)
        df["news_geo_score_pti"] = df.get("news_geo_score", 0.0).fillna(0.0)
        df["news_options_put_call_flow_score_pti"] = df.get("news_options_put_call_flow_score", 0.0).fillna(0.0)
        df["llm_risk_signal_mean_pti"] = 0.0
        df["llm_geo_risk_signal_pti"] = 0.0
        df["llm_policy_risk_signal_pti"] = 0.0
        df["llm_event_risk_signal_pti"] = 0.0
        # inject current point-in-time features
        for c in [
            "news_risk_score_pti", "news_event_week_score_pti", "news_geo_score_pti",
            "news_options_put_call_flow_score_pti",
            "llm_risk_signal_mean_pti", "llm_geo_risk_signal_pti", "llm_policy_risk_signal_pti", "llm_event_risk_signal_pti"
        ]:
            df.loc[df.index[-1], c] = float(current_row.get(c, 0.0) or 0.0)

        outputs: list[dict[str, Any]] = []
        regime_bucket = self._single_regime_bucket(current_row)
        last_vix = float(current_row["vix_close"])
        target_dates = self._next_business_days(cfg.asof_date, cfg.horizon_days)
        all_bias = (backtest or {}).get("residual_bucket_bias", {}) or {}
        metrics = ((backtest or {}).get("metrics_by_horizon", {})) or {}

        for h in range(1, cfg.horizon_days + 1):
            target_col = f"target_vix_tplus_{h}"
            work = df.copy()
            work[target_col] = work["vix_close"].shift(-h)
            # Same policy as backtest: keep rows even if sparse feature columns are NaN.
            model_df = work.dropna(subset=[target_col, "vix_close"]).copy()
            if len(model_df) < 180:
                pred = last_vix
                # Operational UI requirement: keep forecast range tight and interpretable.
                # Use fixed +/-1% band instead of broad statistical interval.
                interval = max(float(pred) * 0.01, 0.01)
                outputs.append(
                    {
                        "horizon_day": h,
                        "target_date": target_dates[h - 1],
                        "predicted_vix": float(pred),
                        "predicted_vix_lower": float(max(8.0, pred - interval)),
                        "predicted_vix_upper": float(pred + interval),
                        "actual_vix": None,
                        "bias_adjustment": 0.0,
                        "confidence_score": 0.35,
                        "regime_bucket": regime_bucket,
                        "blend_alpha_ols": 0.0,
                    }
                )
                continue

            beta = self._fit_ols(model_df[feature_cols], model_df[target_col])
            x_now = pd.DataFrame([current_row], columns=feature_cols).fillna(0.0)
            base_pred_ols = float(self._predict_ols(x_now, beta)[0])
            h_metrics = metrics.get(str(h), {}) if isinstance(metrics, dict) else {}
            alpha_raw = h_metrics.get("best_blend_alpha_ols", 1.0)
            alpha = 1.0 if alpha_raw is None else float(alpha_raw)
            alpha = float(np.clip(alpha, 0.0, 1.0))
            base_pred = (alpha * base_pred_ols) + ((1.0 - alpha) * last_vix)

            bucket_bias = float((((all_bias.get(str(h)) or {}).get(regime_bucket)) or 0.0))
            # apply conservative bias correction
            pred = base_pred + np.clip(bucket_bias, -4.0, 4.0)
            pred = float(np.clip(pred, 8.0, 120.0))

            mae = float(h_metrics.get("mae") or max(1.2, last_vix * 0.06))
            # UI/decision support mode: display +/-1% envelope (user request).
            interval = max(float(pred) * 0.01, 0.01)
            confidence = float(np.clip(1.0 - (mae / max(last_vix, 12.0)), 0.15, 0.95))

            outputs.append(
                {
                    "horizon_day": h,
                    "target_date": target_dates[h - 1],
                    "predicted_vix": pred,
                    "predicted_vix_lower": float(max(8.0, pred - interval)),
                    "predicted_vix_upper": float(pred + interval),
                    "actual_vix": None,
                    "bias_adjustment": float(np.clip(bucket_bias, -4.0, 4.0)),
                    "confidence_score": confidence,
                    "regime_bucket": regime_bucket,
                    "blend_alpha_ols": alpha,
                }
            )

        return outputs

    # ------------------------- helpers -------------------------
    def _fit_ols(self, x_df: pd.DataFrame, y: pd.Series) -> np.ndarray:
        x = np.nan_to_num(x_df.to_numpy(dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
        yv = np.nan_to_num(y.to_numpy(dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
        x_mean = x.mean(axis=0)
        x_std = x.std(axis=0)
        x_std = np.where(x_std < 1e-9, 1.0, x_std)
        z = (x - x_mean) / x_std
        z = np.hstack([np.ones((z.shape[0], 1)), z])
        beta, *_ = np.linalg.lstsq(z, yv, rcond=None)
        packed = np.concatenate([beta, x_mean, x_std])
        return packed

    def _predict_ols(self, x_df: pd.DataFrame, packed_beta: np.ndarray) -> np.ndarray:
        x = np.nan_to_num(x_df.to_numpy(dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
        n_feat = x.shape[1]
        beta = packed_beta[: n_feat + 1]
        x_mean = packed_beta[n_feat + 1 : n_feat + 1 + n_feat]
        x_std = packed_beta[n_feat + 1 + n_feat : n_feat + 1 + n_feat + n_feat]
        x_std = np.where(x_std < 1e-9, 1.0, x_std)
        z = (x - x_mean) / x_std
        z = np.hstack([np.ones((z.shape[0], 1)), z])
        return z @ beta

    def _regime_bucket_labels(self, df: pd.DataFrame) -> list[str]:
        labels = []
        for _, r in df.iterrows():
            labels.append(
                self._single_regime_bucket(r)
            )
        return labels

    def _single_regime_bucket(self, row: pd.Series) -> str:
        vix = float(row.get("vix_close") or 0.0)
        spx_ret = float(row.get("spx_ret_1d") or 0.0)
        news = float(row.get("news_risk_score_pti") or 0.0)
        vix_band = "low" if vix < 16 else ("mid" if vix < 24 else "high")
        spx_band = "up" if spx_ret > 0.003 else ("down" if spx_ret < -0.003 else "flat")
        news_band = "news_hi" if news >= 0.5 else ("news_med" if news >= 0.2 else "news_lo")
        return f"{vix_band}|{spx_band}|{news_band}"

    def _next_business_days(self, asof: date, n: int) -> list[date]:
        out: list[date] = []
        cur = asof
        while len(out) < n:
            cur += timedelta(days=1)
            if cur.weekday() >= 5:
                continue
            out.append(cur)
        return out

    def _insert_run(self, cfg: VixRunConfig, current_news: dict[str, Any], backtest: dict[str, Any]) -> int:
        # feature snapshot is intentionally compact for UI display.
        latest_rows = self.db.execute_query(
            """
            SELECT trading_date, close
            FROM price_daily
            WHERE symbol_key IN ('US:^VIX','US:VIX')
              AND trading_date = %s
            ORDER BY close DESC
            LIMIT 1
            """,
            (cfg.asof_date,),
        ) or []
        feature_snapshot = {
            "vix_close_asof": float(latest_rows[0][1]) if latest_rows else None,
            "news_weighted_risk_score_7d": current_news.get("rag_weighted_risk_score_7d"),
            "news_auto_weighted_risk_score_7d": current_news.get("rag_auto_weighted_risk_score_7d"),
            "news_event_week_score_7d": current_news.get("rag_event_week_score_7d"),
            "llm_risk_signal_mean": current_news.get("llm_risk_signal_mean"),
        }
        self.db.execute_command(
            """
            INSERT INTO vix_prediction_runs
                (asof_date, horizon_days, backtest_years, model_version,
                 feature_snapshot_json, news_features_json, backtest_summary_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                cfg.asof_date,
                int(cfg.horizon_days),
                int(cfg.backtest_years),
                self.MODEL_VERSION,
                json.dumps(feature_snapshot, ensure_ascii=False),
                json.dumps(current_news or {}, ensure_ascii=False),
                json.dumps(backtest or {}, ensure_ascii=False),
            ),
        )
        rows = self.db.execute_query("SELECT MAX(run_id) FROM vix_prediction_runs") or []
        if not rows or rows[0][0] is None:
            raise RuntimeError("Failed to persist vix prediction run")
        return int(rows[0][0])

    def _insert_predictions(self, run_id: int, cfg: VixRunConfig, forecasts: list[dict[str, Any]]) -> None:
        # Try to fill actuals if data already exists (e.g., historical rerun).
        actual_map_rows = self.db.execute_query(
            """
            SELECT trading_date, close
            FROM price_daily
            WHERE symbol_key IN ('US:^VIX','US:VIX')
              AND trading_date > %s
              AND trading_date <= %s
            ORDER BY trading_date ASC
            """,
            (cfg.asof_date, cfg.asof_date + timedelta(days=14)),
        ) or []
        actual_map = {r[0]: float(r[1]) for r in actual_map_rows if r[0] is not None and r[1] is not None}

        for f in forecasts:
            target_date = f["target_date"]
            self.db.execute_command(
                """
                INSERT INTO vix_prediction_values
                    (run_id, horizon_day, target_date, predicted_vix, predicted_vix_lower, predicted_vix_upper,
                     actual_vix, bias_adjustment, confidence_score, regime_bucket, blend_alpha_ols)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id, horizon_day)
                DO UPDATE SET
                    target_date = EXCLUDED.target_date,
                    predicted_vix = EXCLUDED.predicted_vix,
                    predicted_vix_lower = EXCLUDED.predicted_vix_lower,
                    predicted_vix_upper = EXCLUDED.predicted_vix_upper,
                    actual_vix = EXCLUDED.actual_vix,
                    bias_adjustment = EXCLUDED.bias_adjustment,
                    confidence_score = EXCLUDED.confidence_score,
                    regime_bucket = EXCLUDED.regime_bucket,
                    blend_alpha_ols = EXCLUDED.blend_alpha_ols
                """,
                (
                    int(run_id),
                    int(f["horizon_day"]),
                    target_date,
                    float(f["predicted_vix"]),
                    float(f["predicted_vix_lower"]),
                    float(f["predicted_vix_upper"]),
                    actual_map.get(target_date),
                    float(f.get("bias_adjustment") or 0.0),
                    float(f.get("confidence_score") or 0.0),
                    str(f.get("regime_bucket") or ""),
                    float(f.get("blend_alpha_ols") or 0.0),
                ),
            )

    def _json_loads(self, raw: Any, default: Any) -> Any:
        if raw in (None, ""):
            return default
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(raw)
        except Exception:
            return default

    def _safe_nested_float(
        self,
        obj: dict[str, Any],
        path: list[str],
        fallback_key: str | None = None,
    ) -> float:
        cur: Any = obj
        try:
            for k in path:
                if not isinstance(cur, dict):
                    cur = None
                    break
                cur = cur.get(k)
            if cur is None and fallback_key and isinstance(obj, dict):
                cur = obj.get(fallback_key)
            return float(cur) if cur is not None else np.nan
        except Exception:
            return np.nan

    def _score_news_text_blob(self, text: str) -> dict[str, float]:
        text = (text or "").lower()
        cat_scores: dict[str, float] = {}
        total_hits = 0.0
        for cat, words in self._news_category_keywords.items():
            hits = 0
            for w in words:
                hits += text.count(w.lower())
            cat_scores[f"news_{cat}_score"] = float(hits)
            total_hits += float(hits)

        weighted = (
            1.35 * cat_scores.get("news_us_domestic_policy_macro_score", 0.0) +
            1.55 * cat_scores.get("news_global_macro_geo_score", 0.0) +
            0.90 * cat_scores.get("news_earnings_guidance_score", 0.0) +
            1.45 * cat_scores.get("news_options_put_call_flow_score", 0.0) +
            1.6  * cat_scores.get("news_geo_score", 0.0) +
            1.2  * cat_scores.get("news_policy_score", 0.0) +
            1.1  * cat_scores.get("news_inflation_macro_score", 0.0) +
            1.3  * cat_scores.get("news_trade_sanction_score", 0.0) +
            1.5  * cat_scores.get("news_credit_liquidity_score", 0.0) +
            1.7  * cat_scores.get("news_event_week_score", 0.0) +
            1.4  * cat_scores.get("news_risk_tone_score", 0.0) +
            # New dedicated categories — high weights because they are direct VIX movers
            1.8  * cat_scores.get("news_war_conflict_score", 0.0) +
            1.65 * cat_scores.get("news_frb_central_bank_score", 0.0)
        )
        out = dict(cat_scores)
        out["news_risk_hits"] = float(total_hits)
        out["news_weighted_risk_score"] = float(weighted)
        return out

    def _agg_llm_keyword_group(self, llm_numeric: dict[str, float], keywords: list[str]) -> float:
        vals: list[float] = []
        for k, v in (llm_numeric or {}).items():
            key_l = str(k).lower()
            if any(tok in key_l for tok in keywords):
                vals.append(float(v))
        if not vals:
            return 0.0
        return float(np.mean(vals))

    def _news_auto_weight_base_map(self) -> dict[str, str]:
        return {
            "us_domestic_policy_macro": "news_us_domestic_policy_macro_score",
            "global_macro_geo": "news_global_macro_geo_score",
            "earnings_guidance": "news_earnings_guidance_score",
            "options_put_call_flow": "news_options_put_call_flow_score",
            "geo": "news_geo_score",
            "policy": "news_policy_score",
            "infl_macro": "news_inflation_macro_score",
            "trade_sanction": "news_trade_sanction_score",
            "credit_liquidity": "news_credit_liquidity_score",
            "event_week": "news_event_week_score",
            "risk_tone": "news_risk_tone_score",
            "war_conflict": "news_war_conflict_score",
            "frb_central_bank": "news_frb_central_bank_score",
        }

    def _derive_auto_news_weights(self, df: pd.DataFrame, cfg: VixRunConfig) -> dict[str, float]:
        if df is None or df.empty or "vix_close" not in df.columns:
            return {k: 1.0 for k in self._news_auto_weight_base_map().keys()}
        work = df.copy()
        work = work[work["trading_date"] <= cfg.asof_date].copy()
        work = work[work["trading_date"] >= (cfg.asof_date - timedelta(days=365 * cfg.backtest_years))].copy()
        if work.empty:
            return {k: 1.0 for k in self._news_auto_weight_base_map().keys()}
        h = max(1, min(5, int(cfg.horizon_days)))
        work["future_abs_move"] = (work["vix_close"].shift(-h) - work["vix_close"]).abs() / work["vix_close"].clip(lower=1.0)
        base = self._news_auto_weight_base_map()
        scores: dict[str, float] = {}
        for key, col in base.items():
            if col not in work.columns:
                scores[key] = 0.05
                continue
            sub = work[[col, "future_abs_move"]].dropna()
            if len(sub) < 40 or float(np.nanstd(sub[col].to_numpy(dtype=float))) <= 1e-9:
                scores[key] = 0.05
                continue
            corr = np.corrcoef(
                np.nan_to_num(sub[col].to_numpy(dtype=float)),
                np.nan_to_num(sub["future_abs_move"].to_numpy(dtype=float))
            )[0, 1]
            if not np.isfinite(corr):
                corr = 0.0
            # Use positive contribution only; slight floor to keep diversity.
            scores[key] = float(max(0.05, abs(float(corr))))
        s = sum(scores.values())
        if s <= 0:
            return {k: 1.0 for k in base.keys()}
        normalized = {k: float(v / s) for k, v in scores.items()}
        return normalized

    def _apply_auto_news_weighted_score(self, df: pd.DataFrame, weights: dict[str, float]) -> pd.DataFrame:
        out = df.copy()
        base = self._news_auto_weight_base_map()
        score = np.zeros(len(out), dtype=float)
        for key, col in base.items():
            w = float((weights or {}).get(key, 0.0))
            if col in out.columns:
                score += w * np.nan_to_num(out[col].to_numpy(dtype=float), nan=0.0)
        out["news_auto_weighted_risk_score"] = score
        return out

    def _score_current_news_with_auto_weights(self, current_news: dict[str, Any], weights: dict[str, float]) -> float:
        mapping = {
            "us_domestic_policy_macro": float(current_news.get("rag_us_domestic_policy_macro_score_7d", 0.0) or 0.0),
            "global_macro_geo": float(current_news.get("rag_global_macro_geo_score_7d", 0.0) or 0.0),
            "earnings_guidance": float(current_news.get("rag_earnings_guidance_score_7d", 0.0) or 0.0),
            "options_put_call_flow": float(current_news.get("rag_options_put_call_flow_score_7d", 0.0) or 0.0),
            "geo": float(current_news.get("rag_geo_score_7d", 0.0) or 0.0),
            "policy": float(current_news.get("rag_policy_score_7d", 0.0) or 0.0),
            "infl_macro": float(current_news.get("rag_infl_macro_score_7d", 0.0) or 0.0),
            "trade_sanction": float(current_news.get("rag_trade_sanction_score_7d", 0.0) or 0.0),
            "credit_liquidity": float(current_news.get("rag_credit_liquidity_score_7d", 0.0) or 0.0),
            "event_week": float(current_news.get("rag_event_week_score_7d", 0.0) or 0.0),
            "risk_tone": float(current_news.get("rag_risk_tone_score_7d", 0.0) or 0.0),
        }
        total = 0.0
        for k, v in mapping.items():
            total += float((weights or {}).get(k, 0.0)) * float(v)
        return float(total)
