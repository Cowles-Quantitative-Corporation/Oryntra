from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Iterator, Sequence

import pandas as pd

from .database import get_connection, get_market_symbol, store_ohlcv_bars
from .polygon_client import PolygonAPIError, polygon_get
from .twelvedata_client import TwelveDataAPIError, twelvedata_available, twelvedata_get

try:
    import yfinance as yf
except Exception:
    yf = None

_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,19}$")

_PERIOD_CALENDAR_DAYS: dict[str, int | None] = {
    "5m": 14,
    "1mo": 45,
    "3mo": 110,
    "6mo": 210,
    "1y": 400,
    "2y": 800,
    "5y": 1900,
    "10y": 3800,
    "all": 7300,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def normalize_ticker(value: str) -> str:
    ticker = str(value or "").upper().strip()
    if not ticker:
        raise ValueError("Please enter a ticker symbol.")
    if not _TICKER_RE.fullmatch(ticker):
        raise ValueError(f"'{ticker}' is not a valid ticker format.")
    return ticker


def normalize_period(value: str | None) -> str:
    period = str(value or "all").strip().lower()
    return period if period in _PERIOD_CALENDAR_DAYS else "all"


def normalize_provider_preference(value: str | None) -> str:
    provider = str(value or "auto").strip().lower()
    if provider not in {"auto", "cache_only", "polygon", "twelvedata", "yfinance"}:
        raise ValueError("Choose cache_only, auto, polygon, twelvedata, or yfinance as the data provider.")
    return provider


def period_calendar_days(period: str | None) -> int:
    value = _PERIOD_CALENDAR_DAYS[normalize_period(period)]
    return int(value or 730)


def _latest_reasonable_session_date(now: datetime | None = None) -> date:
    current = (now or _utc_now()).date()
    candidate = current - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _frame_from_rows(rows: Sequence[Any]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(
            columns=["Open", "High", "Low", "Close", "Volume", "VWAP", "Transactions"]
        )
    records: list[dict[str, Any]] = []
    adjusted_flags: list[bool] = []
    providers: set[str] = set()
    for row in rows:
        try:
            records.append(
                {
                    "timestamp": pd.Timestamp(row["timestamp"]),
                    "Open": float(row["open"]),
                    "High": float(row["high"]),
                    "Low": float(row["low"]),
                    "Close": float(row["close"]),
                    "Volume": float(row["volume"] or 0.0),
                    "VWAP": float(row["vwap"]) if row["vwap"] is not None else math.nan,
                    "Transactions": (
                        int(row["transactions"]) if row["transactions"] is not None else math.nan
                    ),
                }
            )
            try:
                adjusted_flags.append(bool(int(row["adjusted"])))
            except Exception:
                pass
            try:
                provider = str(row["provider"] or "").strip()
                if provider:
                    providers.add(provider)
            except Exception:
                pass
        except Exception:
            continue
    if not records:
        return pd.DataFrame(
            columns=["Open", "High", "Low", "Close", "Volume", "VWAP", "Transactions"]
        )
    frame = pd.DataFrame(records).drop_duplicates(subset=["timestamp"], keep="last")
    frame = frame.sort_values("timestamp").set_index("timestamp")
    if getattr(frame.index, "tz", None) is not None:
        frame.index = frame.index.tz_convert(None)
    if adjusted_flags:
        all_adjusted = bool(all(adjusted_flags))
        frame.attrs["all_adjusted"] = all_adjusted
        frame.attrs["adjusted_rows"] = int(sum(adjusted_flags))
        frame.attrs["row_count_with_adjustment_metadata"] = int(len(adjusted_flags))
        frame.attrs["price_basis"] = "adjusted_close" if all_adjusted else "mixed_or_unadjusted_close"
    if providers:
        frame.attrs["providers"] = sorted(providers)
    return frame


def _polygon_frame(results: Sequence[dict[str, Any]]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for bar in results or []:
        try:
            timestamp = pd.to_datetime(bar["t"], unit="ms", utc=True).tz_convert(None)
            values = {
                "Open": float(bar["o"]),
                "High": float(bar["h"]),
                "Low": float(bar["l"]),
                "Close": float(bar["c"]),
                "Volume": float(bar.get("v") or 0.0),
                "VWAP": float(bar["vw"]) if bar.get("vw") is not None else math.nan,
                "Transactions": int(bar["n"]) if bar.get("n") is not None else math.nan,
            }
            if not all(
                math.isfinite(float(values[key]))
                for key in ("Open", "High", "Low", "Close", "Volume")
            ):
                continue
            if min(values["Open"], values["High"], values["Low"], values["Close"]) <= 0:
                continue
            if values["High"] < values["Low"]:
                continue
            records.append({"timestamp": timestamp, **values})
        except Exception:
            continue
    if not records:
        return pd.DataFrame(
            columns=["Open", "High", "Low", "Close", "Volume", "VWAP", "Transactions"]
        )
    return (
        pd.DataFrame(records)
        .drop_duplicates(subset=["timestamp"], keep="last")
        .sort_values("timestamp")
        .set_index("timestamp")
    )


def _twelvedata_frame(values: Sequence[dict[str, Any]]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for bar in values or []:
        try:
            timestamp = pd.to_datetime(bar["datetime"], errors="coerce")
            if pd.isna(timestamp):
                continue
            if getattr(timestamp, "tzinfo", None) is not None:
                timestamp = timestamp.tz_convert(None)
            values_row = {
                "Open": float(bar["open"]), "High": float(bar["high"]), "Low": float(bar["low"]),
                "Close": float(bar["close"]), "Volume": float(bar.get("volume") or 0.0),
                "VWAP": math.nan, "Transactions": math.nan,
            }
            if not all(math.isfinite(float(values_row[key])) for key in ("Open", "High", "Low", "Close", "Volume")):
                continue
            if min(values_row["Open"], values_row["High"], values_row["Low"], values_row["Close"]) <= 0 or values_row["High"] < values_row["Low"]:
                continue
            records.append({"timestamp": timestamp, **values_row})
        except Exception:
            continue
    if not records:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume", "VWAP", "Transactions"])
    return pd.DataFrame(records).drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp").set_index("timestamp")


@dataclass(frozen=True)
class HistoryMetadata:
    ticker: str
    source: str
    provider: str
    from_cache: bool
    fallback_used: bool
    bars: int
    first_bar: str | None
    last_bar: str | None
    age_days: int | None
    requested_period: str
    sufficient: bool
    freshness: str
    warning: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class HistoryResult:
    ticker: str
    history: pd.DataFrame
    info: dict[str, Any]
    metadata: HistoryMetadata

    def as_fetcher_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "info": dict(self.info),
            "history": self.history,
            "fetched_at": _iso_now(),
            "provider": self.metadata.provider,
            "data_source": self.metadata.source,
            "from_cache": self.metadata.from_cache,
            "fallback_used": self.metadata.fallback_used,
            "cache_last_bar": self.metadata.last_bar,
            "cache_age_days": self.metadata.age_days,
            "market_data_metadata": {
                "source": self.metadata.source,
                "provider": self.metadata.provider,
                "bars": self.metadata.bars,
                "first_bar": self.metadata.first_bar,
                "last_bar": self.metadata.last_bar,
                "age_days": self.metadata.age_days,
                "requested_period": self.metadata.requested_period,
                "sufficient": self.metadata.sufficient,
                "freshness": self.metadata.freshness,
                "warning": self.metadata.warning,
                **self.metadata.details,
            },
        }


class MarketDataRepository:
    def __init__(self) -> None:
        self._ensure_schema()

    @staticmethod
    def _ensure_schema() -> None:
        conn = get_connection()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS market_data_negative_cache (
                    ticker      TEXT PRIMARY KEY COLLATE NOCASE,
                    reason      TEXT NOT NULL,
                    provider    TEXT,
                    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                    expires_at  TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS market_data_fetch_log (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker          TEXT NOT NULL COLLATE NOCASE,
                    requested_period TEXT,
                    source          TEXT NOT NULL,
                    provider        TEXT,
                    status          TEXT NOT NULL,
                    rows_received   INTEGER NOT NULL DEFAULT 0,
                    error           TEXT,
                    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE INDEX IF NOT EXISTS idx_market_negative_expires
                    ON market_data_negative_cache(expires_at);
                CREATE INDEX IF NOT EXISTS idx_market_fetch_ticker_created
                    ON market_data_fetch_log(ticker, created_at DESC);
                """
            )
            conn.commit()
        finally:
            conn.close()

    def cached_tickers(self, *, minimum_bars: int = 1) -> list[str]:
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT ticker
                  FROM ohlcv_bars
                 WHERE timeframe = '1d'
                 GROUP BY ticker
                HAVING COUNT(*) >= ?
                 ORDER BY ticker
                """,
                (max(1, int(minimum_bars)),),
            ).fetchall()
            return [str(row[0]).upper() for row in rows]
        finally:
            conn.close()

    def cache_summary(self, ticker: str) -> dict[str, Any]:
        symbol = normalize_ticker(ticker)
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS bars,
                       MIN(timestamp) AS first_bar,
                       MAX(timestamp) AS last_bar,
                       GROUP_CONCAT(DISTINCT provider) AS providers,
                       MIN(adjusted) AS all_adjusted
                  FROM ohlcv_bars
                 WHERE ticker = ? COLLATE NOCASE AND timeframe = '1d'
                """,
                (symbol,),
            ).fetchone()
            return dict(row) if row else {
                "bars": 0,
                "first_bar": None,
                "last_bar": None,
                "providers": None,
                "all_adjusted": None,
            }
        finally:
            conn.close()

    def load_local(
        self,
        ticker: str,
        *,
        period: str = "all",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        symbol = normalize_ticker(ticker)
        clean_period = normalize_period(period)
        params: list[Any] = [symbol]
        clauses = ["ticker = ? COLLATE NOCASE", "timeframe = '1d'"]
        if start_date:
            clauses.append("substr(timestamp, 1, 10) >= ?")
            params.append(str(start_date)[:10])
        if end_date:
            clauses.append("substr(timestamp, 1, 10) <= ?")
            params.append(str(end_date)[:10])
        conn = get_connection()
        try:
            rows = conn.execute(
                f"""
                SELECT timestamp, open, high, low, close, volume,
                       vwap, transactions, provider, adjusted
                  FROM ohlcv_bars
                 WHERE {' AND '.join(clauses)}
                 ORDER BY timestamp ASC
                """,
                params,
            ).fetchall()
        finally:
            conn.close()
        frame = _frame_from_rows(rows)
        if frame.empty or start_date:
            return frame
        days = _PERIOD_CALENDAR_DAYS[clean_period]
        if days is not None:
            cutoff = frame.index.max() - pd.Timedelta(days=int(days))
            frame = frame.loc[frame.index >= cutoff]
        return frame

    def load_adjusted(
        self,
        ticker: str,
        *,
        period: str = "all",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """Return only rows explicitly stored as adjusted/total-return compatible.

        This is intentionally stricter than ``load_local``.  Legacy Control caches
        can contain a mixture of old unadjusted rows and newer adjusted rows under
        the same ticker.  Beta must never infer that the whole history is adjusted
        from a ticker-level summary; it trains only on rows whose stored provenance
        explicitly says ``adjusted=1``.
        """
        symbol = normalize_ticker(ticker)
        clean_period = normalize_period(period)
        params: list[Any] = [symbol]
        clauses = ["ticker = ? COLLATE NOCASE", "timeframe = '1d'", "adjusted = 1"]
        if start_date:
            clauses.append("substr(timestamp, 1, 10) >= ?")
            params.append(str(start_date)[:10])
        if end_date:
            clauses.append("substr(timestamp, 1, 10) <= ?")
            params.append(str(end_date)[:10])
        conn = get_connection()
        try:
            rows = conn.execute(
                f"""
                SELECT timestamp, open, high, low, close, volume,
                       vwap, transactions, provider, adjusted
                  FROM ohlcv_bars
                 WHERE {' AND '.join(clauses)}
                 ORDER BY timestamp ASC
                """,
                params,
            ).fetchall()
        finally:
            conn.close()
        frame = _frame_from_rows(rows)
        if frame.empty or start_date:
            return frame
        days = _PERIOD_CALENDAR_DAYS[clean_period]
        if days is not None:
            cutoff = frame.index.max() - pd.Timedelta(days=int(days))
            frame = frame.loc[frame.index >= cutoff].copy()
            frame.attrs["all_adjusted"] = True
            frame.attrs["price_basis"] = "adjusted_close"
        return frame

    @staticmethod
    def _history_quality(
        frame: pd.DataFrame,
        *,
        minimum_bars: int,
        max_stale_days: int,
    ) -> tuple[bool, int | None, str]:
        if frame is None or frame.empty:
            return False, None, "missing"
        last = pd.Timestamp(frame.index.max()).date()
        age_days = max(0, (_latest_reasonable_session_date() - last).days)
        enough = len(frame) >= max(1, int(minimum_bars))
        fresh = age_days <= max(0, int(max_stale_days))
        if enough and fresh:
            return True, age_days, "fresh"
        if enough:
            return False, age_days, "stale"
        return False, age_days, "insufficient"

    def _negative_cache(self, ticker: str) -> dict[str, Any] | None:
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT ticker, reason, provider, created_at, expires_at
                  FROM market_data_negative_cache
                 WHERE ticker = ? COLLATE NOCASE
                   AND datetime(expires_at) > datetime('now')
                """,
                (ticker,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _set_negative_cache(
        self,
        ticker: str,
        reason: str,
        *,
        provider: str = "polygon",
        ttl_hours: int | None = None,
    ) -> None:
        ttl = max(1, int(ttl_hours or os.getenv("ORYNTRA_NEGATIVE_CACHE_HOURS", "12")))
        expires = (_utc_now() + timedelta(hours=ttl)).isoformat()
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO market_data_negative_cache
                    (ticker, reason, provider, created_at, expires_at)
                VALUES (?, ?, ?, datetime('now'), ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    reason=excluded.reason,
                    provider=excluded.provider,
                    created_at=datetime('now'),
                    expires_at=excluded.expires_at
                """,
                (ticker, str(reason)[:1000], provider, expires),
            )
            conn.commit()
        finally:
            conn.close()

    def _clear_negative_cache(self, ticker: str) -> None:
        conn = get_connection()
        try:
            conn.execute(
                "DELETE FROM market_data_negative_cache WHERE ticker = ? COLLATE NOCASE",
                (ticker,),
            )
            conn.commit()
        finally:
            conn.close()

    def _log_fetch(
        self,
        ticker: str,
        period: str,
        source: str,
        provider: str,
        status: str,
        rows: int = 0,
        error: str | None = None,
    ) -> None:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO market_data_fetch_log
                    (ticker, requested_period, source, provider, status,
                     rows_received, error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    period,
                    source,
                    provider,
                    status,
                    int(rows or 0),
                    str(error)[:1500] if error else None,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _info(ticker: str) -> dict[str, Any]:
        symbol = get_market_symbol(ticker) or {}
        return {
            "company_name": symbol.get("name") or ticker,
            "exchange": symbol.get("primary_exchange") or "",
            "market_cap": None,
            "shares_outstanding": None,
            "security_type": symbol.get("type"),
            "active": symbol.get("active"),
        }

    def _metadata(
        self,
        ticker: str,
        frame: pd.DataFrame,
        *,
        period: str,
        source: str,
        provider: str,
        from_cache: bool,
        fallback_used: bool,
        minimum_bars: int,
        max_stale_days: int,
        warning: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> HistoryMetadata:
        sufficient, age_days, freshness = self._history_quality(
            frame,
            minimum_bars=minimum_bars,
            max_stale_days=max_stale_days,
        )
        return HistoryMetadata(
            ticker=ticker,
            source=source,
            provider=provider,
            from_cache=from_cache,
            fallback_used=fallback_used,
            bars=int(len(frame)),
            first_bar=(pd.Timestamp(frame.index.min()).isoformat() if not frame.empty else None),
            last_bar=(pd.Timestamp(frame.index.max()).isoformat() if not frame.empty else None),
            age_days=age_days,
            requested_period=period,
            sufficient=sufficient,
            freshness=freshness,
            warning=warning,
            details=details or {},
        )

    def _fetch_polygon(self, ticker: str, period: str, *, api_key: str | None = None) -> pd.DataFrame:
        end = _latest_reasonable_session_date()
        start = end - timedelta(days=period_calendar_days(period))
        payload, response = polygon_get(
            f"/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
            params={
                "adjusted": "true",
                "sort": "asc",
                "limit": 50000,
            },
            api_key=api_key,
        )
        results = payload.get("results") or []
        frame = _polygon_frame(results)
        request_id = response.headers.get("X-Request-Id") or payload.get("request_id")
        if frame.empty:
            raise ValueError(f"No Massive/Polygon daily data was returned for '{ticker}'.")
        stored = store_ohlcv_bars(
            ticker,
            "1d",
            frame,
            provider="polygon_ticker_fallback",
            adjusted=True,
        )
        if stored <= 0:
            raise ValueError(f"Massive/Polygon returned unusable candles for '{ticker}'.")
        self._clear_negative_cache(ticker)
        self._log_fetch(
            ticker,
            period,
            "ticker_fallback",
            "polygon_ticker_fallback",
            "success",
            len(frame),
        )
        return frame

    def _fetch_twelvedata(self, ticker: str, period: str, *, api_key: str | None = None) -> pd.DataFrame:
        end = _latest_reasonable_session_date()
        start = end - timedelta(days=period_calendar_days(period))
        payload, _response = twelvedata_get(
            "/time_series",
            params={"symbol": ticker, "interval": "1day", "start_date": start.isoformat(), "end_date": end.isoformat(), "adjust": "all", "order": "ASC"},
            api_key=api_key,
        )
        frame = _twelvedata_frame(payload.get("values") or [])
        if frame.empty:
            raise ValueError(f"No Twelve Data daily data was returned for '{ticker}'.")
        stored = store_ohlcv_bars(ticker, "1d", frame, provider="twelvedata_ticker_fallback", adjusted=True)
        if stored <= 0:
            raise ValueError(f"Twelve Data returned unusable candles for '{ticker}'.")
        self._clear_negative_cache(ticker)
        self._log_fetch(ticker, period, "ticker_fallback", "twelvedata_ticker_fallback", "success", len(frame))
        return frame

    def _fetch_yfinance(self, ticker: str, period: str) -> pd.DataFrame:
        if yf is None:
            raise ValueError("yfinance is not installed.")
        yf_period = {
            "5m": "1mo",
            "1mo": "1mo",
            "3mo": "3mo",
            "6mo": "6mo",
            "1y": "1y",
            "2y": "2y",
            "5y": "5y",
            "10y": "10y",
            "all": "max",
        }[normalize_period(period)]
        ticker_obj = yf.Ticker(ticker)
        try:
            frame = ticker_obj.history(period=yf_period, auto_adjust=True, timeout=20)
        except TypeError:
            frame = ticker_obj.history(period=yf_period, auto_adjust=True)
        if frame is None or frame.empty:
            raise ValueError(f"No yfinance daily data was returned for '{ticker}'.")
        frame = frame.rename_axis("timestamp")
        keep = [column for column in ["Open", "High", "Low", "Close", "Volume"] if column in frame]
        frame = frame[keep].copy()
        if getattr(frame.index, "tz", None) is not None:
            frame.index = frame.index.tz_convert(None)
        frame["VWAP"] = math.nan
        frame["Transactions"] = math.nan
        store_ohlcv_bars(
            ticker,
            "1d",
            frame,
            provider="yfinance_emergency_fallback",
            adjusted=True,
        )
        self._clear_negative_cache(ticker)
        self._log_fetch(
            ticker,
            period,
            "ticker_fallback",
            "yfinance_emergency_fallback",
            "success",
            len(frame),
        )
        return frame

    def get_history(
        self,
        ticker: str,
        *,
        period: str = "all",
        minimum_bars: int = 20,
        allow_api: bool = True,
        force_refresh: bool = False,
        max_stale_days: int | None = None,
        allow_stale_on_error: bool = True,
        provider_preference: str = "auto",
        provider_api_keys: dict[str, str] | None = None,
        allow_platform_provider_keys: bool = True,
    ) -> HistoryResult:


        symbol = normalize_ticker(ticker)
        clean_period = normalize_period(period)
        selected_provider = normalize_provider_preference(provider_preference)
        supplied_keys = {str(name).lower(): str(value).strip() for name, value in (provider_api_keys or {}).items() if str(value).strip()}
        stale_days = max(
            0,
            int(max_stale_days if max_stale_days is not None else os.getenv("ORYNTRA_CACHE_MAX_STALE_DAYS", "7")),
        )
        local = self.load_local(symbol, period=clean_period)
        sufficient, _age, _freshness = self._history_quality(
            local,
            minimum_bars=minimum_bars,
            max_stale_days=stale_days,
        )
        if local is not None and not local.empty and sufficient and not force_refresh:
            providers = self.cache_summary(symbol).get("providers") or "local_market_cache"
            source = "local_grouped_cache" if "grouped" in providers else "local_ticker_cache"
            metadata = self._metadata(
                symbol,
                local,
                period=clean_period,
                source=source,
                provider=str(providers),
                from_cache=True,
                fallback_used=False,
                minimum_bars=minimum_bars,
                max_stale_days=stale_days,
            )
            return HistoryResult(symbol, local, self._info(symbol), metadata)

        if not allow_api or selected_provider == "cache_only":
            warning = None
            if local is not None and not local.empty:
                warning = (
                    f"Cache contains {len(local)} bars but does not meet the requested "
                    f"minimum/freshness requirement."
                )
                metadata = self._metadata(
                    symbol,
                    local,
                    period=clean_period,
                    source="stale_or_partial_cache",
                    provider=str(self.cache_summary(symbol).get("providers") or "local"),
                    from_cache=True,
                    fallback_used=False,
                    minimum_bars=minimum_bars,
                    max_stale_days=stale_days,
                    warning=warning,
                )
                return HistoryResult(symbol, local, self._info(symbol), metadata)
            raise ValueError(
                f"No cached daily history is available for '{symbol}'. "
                "Use cache_first to permit a rate-limited ticker fallback."
            )

        negative = self._negative_cache(symbol)
        if negative and not force_refresh:
            # Provider failures are scoped to the provider that produced them.
            # A failed paid-provider lookup must not block an explicitly selected
            # Yahoo request (or vice versa). Auto may honor any recent failure.
            negative_provider = str(negative.get("provider") or "").strip().lower()
            negative_applies = selected_provider == "auto" or negative_provider in {"", selected_provider}
            if negative_applies:
                if local is not None and not local.empty and allow_stale_on_error:
                    metadata = self._metadata(
                        symbol,
                        local,
                        period=clean_period,
                        source="stale_cache_negative_fallback",
                        provider=str(self.cache_summary(symbol).get("providers") or "local"),
                        from_cache=True,
                        fallback_used=False,
                        minimum_bars=minimum_bars,
                        max_stale_days=stale_days,
                        warning=f"Recent {negative_provider or 'provider'} lookup failed: {negative['reason']}",
                    )
                    return HistoryResult(symbol, local, self._info(symbol), metadata)
                raise ValueError(
                    f"Recent lookup for '{symbol}' failed and is temporarily cached: "
                    f"{negative['reason']}"
                )

        errors: list[str] = []
        fetched = False
        if selected_provider in {"auto", "polygon"}:
            polygon_key = supplied_keys.get("polygon")
            if not polygon_key and not allow_platform_provider_keys:
                if selected_provider == "polygon":
                    errors.append("No Polygon API key is saved for this account. Add one in API & Automation or use cached data.")
            else:
                try:
                    self._fetch_polygon(symbol, clean_period, api_key=polygon_key)
                    fetched = True
                except (PolygonAPIError, ValueError) as exc:
                    errors.append(str(exc))
                    self._log_fetch(symbol, clean_period, "ticker_fallback", "polygon_ticker_fallback", "failed", error=str(exc))
        if not fetched and selected_provider in {"auto", "twelvedata"}:
            twelve_key = supplied_keys.get("twelvedata")
            if not twelve_key and not allow_platform_provider_keys:
                if selected_provider == "twelvedata":
                    errors.append("No Twelve Data API key is saved for this account. Add one in API & Automation or use cached data.")
            elif not twelvedata_available(twelve_key):
                if selected_provider == "twelvedata":
                    errors.append("Twelve Data is not configured for this account or server.")
            else:
                try:
                    self._fetch_twelvedata(symbol, clean_period, api_key=twelve_key)
                    fetched = True
                except (TwelveDataAPIError, ValueError) as exc:
                    errors.append(str(exc))
                    self._log_fetch(symbol, clean_period, "ticker_fallback", "twelvedata_ticker_fallback", "failed", error=str(exc))
        if not fetched and selected_provider in {"auto", "yfinance"}:
            try:
                self._fetch_yfinance(symbol, clean_period)
                fetched = True
            except Exception as exc:
                errors.append(str(exc))

        refreshed = self.load_local(symbol, period=clean_period)
        if refreshed is not None and len(refreshed) >= max(1, minimum_bars):
            providers = self.cache_summary(symbol).get("providers") or "polygon_ticker_fallback"
            metadata = self._metadata(
                symbol,
                refreshed,
                period=clean_period,
                source="ticker_api_fallback_cached",
                provider=str(providers),
                from_cache=False,
                fallback_used=True,
                minimum_bars=minimum_bars,
                max_stale_days=stale_days,
                warning=("; ".join(errors) if errors and "polygon_ticker_fallback" not in str(providers) else None),
            )
            return HistoryResult(symbol, refreshed, self._info(symbol), metadata)

        reason = errors[-1] if errors else f"No usable daily history for '{symbol}'."
        self._set_negative_cache(symbol, reason, provider=selected_provider)
        if local is not None and not local.empty and allow_stale_on_error:
            metadata = self._metadata(
                symbol,
                local,
                period=clean_period,
                source="stale_cache_after_fallback_error",
                provider=str(self.cache_summary(symbol).get("providers") or "local"),
                from_cache=True,
                fallback_used=True,
                minimum_bars=minimum_bars,
                max_stale_days=stale_days,
                warning=reason,
            )
            return HistoryResult(symbol, local, self._info(symbol), metadata)
        raise ValueError(reason)

    def get_histories(
        self,
        tickers: Iterable[str],
        *,
        period: str = "all",
        minimum_bars: int = 20,
        allow_api: bool = False,
    ) -> Iterator[tuple[str, HistoryResult | None, str | None]]:

        seen: set[str] = set()
        for raw in tickers:
            try:
                ticker = normalize_ticker(raw)
            except ValueError as exc:
                yield str(raw), None, str(exc)
                continue
            if ticker in seen:
                continue
            seen.add(ticker)
            try:
                result = self.get_history(
                    ticker,
                    period=period,
                    minimum_bars=minimum_bars,
                    allow_api=allow_api,
                )
                yield ticker, result, None
            except Exception as exc:
                yield ticker, None, str(exc)

    def dataset_fingerprint(
        self,
        frames: dict[str, pd.DataFrame],
        *,
        configuration: dict[str, Any] | None = None,
    ) -> str:
        digest = hashlib.sha256()
        for ticker in sorted(frames):
            frame = frames[ticker]
            digest.update(ticker.encode("utf-8"))
            if frame is None or frame.empty:
                digest.update(b"EMPTY")
                continue
            stable = frame[["Open", "High", "Low", "Close", "Volume"]].copy()
            stable.index = pd.to_datetime(stable.index).strftime("%Y-%m-%d")
            digest.update(stable.to_csv(float_format="%.10g").encode("utf-8"))
        digest.update(
            json.dumps(configuration or {}, sort_keys=True, default=str).encode("utf-8")
        )
        return digest.hexdigest()


_REPOSITORY: MarketDataRepository | None = None


def get_market_repository() -> MarketDataRepository:
    global _REPOSITORY
    if _REPOSITORY is None:
        _REPOSITORY = MarketDataRepository()
    return _REPOSITORY


def get_history(
    ticker: str,
    *,
    period: str = "all",
    minimum_bars: int = 20,
    allow_api: bool = True,
    force_refresh: bool = False,
    provider_preference: str = "auto",
) -> HistoryResult:
    return get_market_repository().get_history(
        ticker,
        period=period,
        minimum_bars=minimum_bars,
        allow_api=allow_api,
        force_refresh=force_refresh,
        provider_preference=provider_preference,
    )
