from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.db.database import Database
from app.models.schemas import DataFreshness


class FreshnessService:
    """Market-time-aware freshness checks for daily price datasets."""

    def __init__(self) -> None:
        self.db = Database()

    @staticmethod
    def _expected_trading_date(
        tz_name: str = "America/New_York",
        close_cutoff_hour_local: int = 18,
    ) -> tuple[datetime, date]:
        now_utc = datetime.now(timezone.utc)
        now_local = now_utc.astimezone(ZoneInfo(tz_name))

        candidate = now_local.date()
        if now_local.hour < int(close_cutoff_hour_local):
            candidate = candidate - timedelta(days=1)

        while candidate.weekday() >= 5:  # 5=Sat, 6=Sun
            candidate = candidate - timedelta(days=1)

        return now_local, candidate

    def _build_from_latest_rows(
        self,
        scope: str,
        latest_rows: list[tuple[str, date]],
        tz_name: str,
        close_cutoff_hour_local: int,
        note: str | None = None,
    ) -> DataFreshness:
        market_now, expected_date = self._expected_trading_date(
            tz_name=tz_name,
            close_cutoff_hour_local=close_cutoff_hour_local,
        )
        symbols_total = len(latest_rows)
        if symbols_total == 0:
            return DataFreshness(
                scope=scope,
                market_timezone=tz_name,
                market_now=market_now,
                expected_latest_date=expected_date,
                latest_date_max=None,
                latest_date_min=None,
                symbols_total=0,
                symbols_fresh=0,
                symbols_stale=0,
                is_fresh=False,
                stale_symbols_sample=[],
                note=note,
            )

        latest_dates = [r[1] for r in latest_rows if r[1] is not None]
        latest_date_max = max(latest_dates) if latest_dates else None
        latest_date_min = min(latest_dates) if latest_dates else None

        fresh_rows = [r for r in latest_rows if r[1] is not None and r[1] >= expected_date]
        stale_rows = [r for r in latest_rows if r[1] is None or r[1] < expected_date]
        stale_rows.sort(key=lambda x: (x[1] is None, x[1]), reverse=True)

        return DataFreshness(
            scope=scope,
            market_timezone=tz_name,
            market_now=market_now,
            expected_latest_date=expected_date,
            latest_date_max=latest_date_max,
            latest_date_min=latest_date_min,
            symbols_total=symbols_total,
            symbols_fresh=len(fresh_rows),
            symbols_stale=len(stale_rows),
            is_fresh=len(stale_rows) == 0 and symbols_total > 0,
            stale_symbols_sample=[r[0] for r in stale_rows[:10]],
            note=note,
        )

    def price_freshness_for_symbols(
        self,
        scope: str,
        symbol_keys: list[str],
        tz_name: str = "America/New_York",
        close_cutoff_hour_local: int = 18,
        note: str | None = None,
    ) -> DataFreshness:
        if not symbol_keys:
            return self._build_from_latest_rows(
                scope=scope,
                latest_rows=[],
                tz_name=tz_name,
                close_cutoff_hour_local=close_cutoff_hour_local,
                note=note,
            )

        if not self.db.connect():
            market_now, expected_date = self._expected_trading_date(tz_name, close_cutoff_hour_local)
            return DataFreshness(
                scope=scope,
                market_timezone=tz_name,
                market_now=market_now,
                expected_latest_date=expected_date,
                latest_date_max=None,
                latest_date_min=None,
                symbols_total=0,
                symbols_fresh=0,
                symbols_stale=0,
                is_fresh=False,
                stale_symbols_sample=[],
                note="DB connect failed while checking freshness.",
            )

        try:
            q = """
                WITH latest AS (
                    SELECT symbol_key, MAX(trading_date) AS latest_date
                    FROM price_daily
                    WHERE symbol_key = ANY(%s)
                    GROUP BY symbol_key
                )
                SELECT symbol_key, latest_date
                FROM latest
            """
            rows = self.db.execute_query(q, (symbol_keys,)) or []
            latest_rows = [(str(r[0]), r[1]) for r in rows]
            return self._build_from_latest_rows(
                scope=scope,
                latest_rows=latest_rows,
                tz_name=tz_name,
                close_cutoff_hour_local=close_cutoff_hour_local,
                note=note,
            )
        finally:
            self.db.disconnect()

    def price_freshness_for_active_instruments(
        self,
        scope: str,
        tz_name: str = "America/New_York",
        close_cutoff_hour_local: int = 18,
        note: str | None = None,
    ) -> DataFreshness:
        if not self.db.connect():
            market_now, expected_date = self._expected_trading_date(tz_name, close_cutoff_hour_local)
            return DataFreshness(
                scope=scope,
                market_timezone=tz_name,
                market_now=market_now,
                expected_latest_date=expected_date,
                latest_date_max=None,
                latest_date_min=None,
                symbols_total=0,
                symbols_fresh=0,
                symbols_stale=0,
                is_fresh=False,
                stale_symbols_sample=[],
                note="DB connect failed while checking freshness.",
            )

        try:
            q = """
                WITH latest AS (
                    SELECT p.symbol_key, MAX(p.trading_date) AS latest_date
                    FROM price_daily p
                    INNER JOIN instruments i ON i.symbol_key = p.symbol_key
                    WHERE i.is_active = true
                    GROUP BY p.symbol_key
                )
                SELECT symbol_key, latest_date
                FROM latest
            """
            rows = self.db.execute_query(q) or []
            latest_rows = [(str(r[0]), r[1]) for r in rows]
            return self._build_from_latest_rows(
                scope=scope,
                latest_rows=latest_rows,
                tz_name=tz_name,
                close_cutoff_hour_local=close_cutoff_hour_local,
                note=note,
            )
        finally:
            self.db.disconnect()
