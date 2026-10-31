"""Read-only, prefix-stable source preparation for reconstructed Gamma Beta research.

This adapter deliberately bypasses BetaService and BetaFeatureEngine.build's
whole-panel informative-column filter. Source lineage is an exact, conservatively
overinclusive prefix of the imported rows, not a claim of minimal dependencies or
historically issued forecasts. Daily FRED and adjusted market snapshots retain
their weaker historical-vintage status.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

import numpy as np
import pandas as pd

from ..models.beta.alpha_context import build_alpha_alt_market_context
from ..models.beta.features import BetaFeatureEngine
from ..models.beta.labels import RegimeLabeler
from ..models.beta.repository import DEFAULT_SERIES_POLICIES, MacroRepository
from ..models.beta.types import MacroRevision


SOURCE_SCHEMA = "gamma-beta-sources-v1"
_ET = "America/New_York"
_MACRO_FIELDS = (
    "series_id", "observation_date", "realtime_start", "realtime_end", "value",
    "release_at", "available_to_model_at", "eligible_session", "source",
    "fetched_at", "availability_precision",
)
_MARKET_FIELDS = ("ticker", "timeframe", "timestamp", "close", "provider", "adjusted", "fetched_at")
_FRED_DAILY = "FRED-output_type=1-observation-dated-network"
_ALFRED = {"FRED/ALFRED-output_type=1-realtime-period-network", "FRED/ALFRED-output_type=3-network"}


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _day(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError("A valid exchange-session date is required")
    # These are session labels, not instants to convert across timezones.
    return stamp.tz_localize(None).normalize()


def session_open(day: Any) -> pd.Timestamp:
    """Simulated regular NYSE open on an observed trading-session date."""
    return (_day(day) + pd.Timedelta(hours=9, minutes=30)).tz_localize(_ET).tz_convert("UTC")


def _aware(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError("Source availability must be a genuine timezone-aware stored timestamp")
    return stamp.tz_convert("UTC")


def _read_connection(path: str | Path) -> sqlite3.Connection:
    resolved = Path(path).resolve(strict=True)
    con = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


class _ReadOnlyMacroRepository(MacroRepository):
    """Do not invoke the native constructor, read environment keys, or initialize SQL."""

    def __init__(self, path: str | Path, evaluation_end: pd.Timestamp) -> None:
        self.db_path = Path(path).resolve(strict=True)
        self.strict = True
        with closing(self._connect()) as con:
            records = con.execute(
                f"SELECT {','.join(_MACRO_FIELDS)} FROM macro_revisions "
                "WHERE eligible_session <= ? ORDER BY series_id,eligible_session,realtime_start,observation_date",
                (evaluation_end.date().isoformat(),),
            ).fetchall()
        self.rows_by_series: dict[str, list[dict[str, Any]]] = {p.series_id: [] for p in DEFAULT_SERIES_POLICIES}
        self._events: dict[str, list[MacroRevision]] = {sid: [] for sid in self.rows_by_series}
        for stored in records:
            row = dict(stored)
            sid = row["series_id"]
            if sid not in self.rows_by_series:
                continue
            if row["source"] not in _ALFRED | {_FRED_DAILY}:
                raise ValueError(f"Unverified macro source for {sid}: {row['source']}")
            value = float(row["value"])
            if not np.isfinite(value):
                raise ValueError(f"Nonfinite macro source value for {sid}")
            available = _aware(row["available_to_model_at"])
            eligible = _day(row["eligible_session"])
            vintage = date.fromisoformat(row["realtime_start"])
            observation = date.fromisoformat(row["observation_date"])
            if observation > available.tz_convert(_ET).date() or vintage > available.tz_convert(_ET).date():
                raise ValueError(f"Macro observation/vintage is later than availability for {sid}")
            if available >= session_open(eligible):
                raise ValueError(f"Macro source is not available before its eligible open for {sid}")
            if row["source"] == _FRED_DAILY:
                if row["availability_precision"] != "observation_date_conservative_next_session" or vintage != observation:
                    raise ValueError(f"Observation-dated source semantics inconsistent for {sid}")
            elif row["availability_precision"] not in {"date_only_conservative_next_session", "exact_external_timestamp"}:
                raise ValueError(f"Unsupported macro availability precision for {sid}")
            if row["availability_precision"] != "exact_external_timestamp" and eligible.date() <= vintage:
                raise ValueError(f"Date-only macro source must wait until a later session for {sid}")
            row["value"] = value
            self.rows_by_series[sid].append(row)
            self._events[sid].append(MacroRevision(
                series_id=sid, observation_date=observation, realtime_start=vintage,
                realtime_end=date.fromisoformat(row["realtime_end"]) if row["realtime_end"] else None,
                value=value, release_at=datetime.fromisoformat(row["release_at"]),
                available_to_model_at=available.to_pydatetime(), eligible_session=eligible.date(),
                source=row["source"], fetched_at=datetime.fromisoformat(row["fetched_at"]),
                availability_precision=row["availability_precision"],
            ))

    def _connect(self) -> sqlite3.Connection:
        return _read_connection(self.db_path)

    def revisions(self, series_id: str) -> list[MacroRevision]:
        return self._events.get(series_id, [])


class _Prefix:
    def __init__(self, rows: list[dict[str, Any]], date_key: str) -> None:
        self.rows = rows
        self.dates = np.array([_day(row[date_key]).value for row in rows], dtype=np.int64)
        digest = sha256()
        self.hashes: list[str] = []
        for row in rows:
            digest.update(_json_bytes(row) + b"\n")
            self.hashes.append(digest.hexdigest())

    def count(self, day: pd.Timestamp, *, side: str = "right") -> int:
        return int(np.searchsorted(self.dates, day.value, side=side))

    def digest(self, count: int) -> str:
        return self.hashes[count - 1] if count else sha256().hexdigest()


class BetaSourceLineage:
    """Binary-search prefix manifests; no rescanning source databases per prediction."""

    def __init__(self, sessions: pd.DatetimeIndex, macro_rows: dict, market_rows: dict,
                 stock_symbols: list[str]) -> None:
        self.sessions = sessions
        self.stock_symbols = tuple(stock_symbols)
        self._macro = {sid: _Prefix(rows, "eligible_session") for sid, rows in sorted(macro_rows.items())}
        self._market = {sid: _Prefix(rows, "session") for sid, rows in sorted(market_rows.items())}
        digest = sha256()
        self._session_hashes = []
        for day in sessions:
            digest.update(day.date().isoformat().encode() + b"\n")
            self._session_hashes.append(digest.hexdigest())

    def _position(self, day: Any) -> tuple[pd.Timestamp, int]:
        stamp = _day(day)
        pos = int(self.sessions.searchsorted(stamp))
        if pos == len(self.sessions) or self.sessions[pos] != stamp:
            raise ValueError(f"No prepared Beta session for {stamp.date()}")
        return stamp, pos

    def prefix_digest(self, day: Any) -> str:
        stamp, pos = self._position(day)
        # Today's price row does not belong to an open-time feature dependency.
        market_end = self.sessions[pos - 1] if pos else stamp - pd.Timedelta(days=1)
        counts = {"macro": {}, "market": {}}
        for kind, prefixes, end in (("macro", self._macro, stamp), ("market", self._market, market_end)):
            for key, prefix in prefixes.items():
                count = prefix.count(end)
                counts[kind][key] = [count, prefix.digest(count)]
        return sha256(_json_bytes({"schema": SOURCE_SCHEMA, "session": stamp.date().isoformat(),
            "session_prefix_sha256": self._session_hashes[pos],
            "stock_symbols": self.stock_symbols, "sources": counts})).hexdigest()

    def at(self, day: Any) -> dict[str, Any]:
        stamp, pos = self._position(day)
        issue = session_open(stamp).isoformat()
        macro = []
        for sid, prefix in self._macro.items():
            count = prefix.count(stamp)
            if not count:
                continue
            row = prefix.rows[count - 1]
            daily = row["source"] == _FRED_DAILY
            macro.append({
                **row, "available_at": row["available_to_model_at"],
                "source_url": f"https://api.stlouisfed.org/fred/series/observations?series_id={sid}&output_type=1",
                "realtime_start_kind": "normalized_observation_date" if daily else "ALFRED_realtime_period_start",
                "historical_vintage_verified": not daily,
                "release_timestamp_is_observed": row["availability_precision"] == "exact_external_timestamp",
                "representative_kind": "last_eligible_row_in_source_prefix_not_complete_feature_dependencies",
                "source_table": "macro_revisions", "source_prefix_row_count": count,
                "source_prefix_sha256": prefix.digest(count),
                "source_prefix_order": ["eligible_session", "realtime_start", "observation_date"],
                "source_prefix_eligible_through": stamp.date().isoformat(),
                "lineage_scope": "all_eligible_series_rows_conservative_overinclusive",
            })
        market_end = self.sessions[pos - 1] if pos else stamp - pd.Timedelta(days=1)
        market = []
        for ticker, prefix in self._market.items():
            count = prefix.count(market_end)
            if not count:
                continue
            row = prefix.rows[count - 1]
            market.append({
                **row, "source_table": "ohlcv_bars", "source_prefix_row_count": count,
                "source_prefix_sha256": prefix.digest(count), "source_prefix_order": ["session", "timestamp"],
                "source_prefix_session_through": market_end.date().isoformat(),
                "last_observed_session": row["session"],
                "stale_sessions_at_previous_close": max(0, pos - 1 - int(self.sessions.searchsorted(_day(row["session"])))),
                "used_in_market_features": ticker in BetaFeatureEngine.MARKET_TICKERS,
                "used_in_stock_context": ticker in self.stock_symbols,
                "historical_adjustment_vintage_verified": False,
                "historical_vendor_availability_verified": False,
                "availability_basis": "previous_recorded_session_close_then_simulated_next_open",
                "lineage_scope": "all_prior_adjusted_close_rows_conservative_overinclusive",
            })
        return {
            "as_of": issue, "available_at": issue, "information_cutoff": issue,
            "as_of_session": stamp.date().isoformat(),
            "timestamp_semantics": "historical_simulation_NYSE_open_not_observed_historical_issuance",
            "source_lineage_schema": SOURCE_SCHEMA, "source_prefix_sha256": self.prefix_digest(stamp),
            "macro_provenance": macro, "market_provenance": market,
        }


def prepare_beta_inputs(market_db_path: str | Path, beta_db_path: str | Path,
                        stock_symbols: Iterable[str], evaluation_end: Any = None, *,
                        start: Any = "2006-01-01") -> dict[str, Any]:
    """Prepare causal raw candidate features without model/column selection or writes.

    Sessions are actual recorded SPY dates, not a future-complete intersection of
    stock histories. The fixed requested universe retains missing/pre-IPO stocks.
    The stock context has price-derived Alpha-Alt feature families only; no SEC
    value/quality inputs, forecast outputs, or consensus expectations are invented.
    """
    symbols = [str(s).strip().upper() for s in stock_symbols]
    if not symbols or any(not s for s in symbols) or len(set(symbols)) != len(symbols):
        raise ValueError("stock_symbols must be nonempty and unique after normalization")
    tickers = sorted(set(symbols).union(BetaFeatureEngine.MARKET_TICKERS))
    start_day = _day(start)
    end = _day(evaluation_end) if evaluation_end is not None else pd.Timestamp.max.normalize()
    if start_day > end:
        raise ValueError("start must precede evaluation_end")
    with closing(_read_connection(market_db_path)) as con:
        rows = con.execute(
            f"SELECT {','.join(_MARKET_FIELDS)} FROM ohlcv_bars WHERE timeframe='1d' AND adjusted=1 "
            f"AND UPPER(ticker) IN ({','.join('?' for _ in tickers)}) "
            "AND substr(timestamp,1,10)>=? AND substr(timestamp,1,10)<=? ORDER BY ticker,timestamp",
            (*tickers, start_day.date().isoformat(), end.date().isoformat()),
        ).fetchall()
    by_ticker: dict[str, list[dict[str, Any]]] = {ticker: [] for ticker in tickers}
    for stored in rows:
        row = dict(stored)
        ticker = str(row["ticker"]).upper()
        value = float(row["close"])
        if not np.isfinite(value) or value <= 0 or not row["provider"] or not row["fetched_at"]:
            raise ValueError(f"Market row lacks positive close or stored source identity for {ticker}")
        row.update(ticker=ticker, close=value, adjusted=int(row["adjusted"]), session=_day(row["timestamp"]).date().isoformat())
        if by_ticker[ticker] and by_ticker[ticker][-1]["session"] == row["session"]:
            raise ValueError(f"Duplicate recorded exchange-session rows for {ticker}")
        by_ticker[ticker].append(row)
    sessions = pd.DatetimeIndex([row["session"] for row in by_ticker["SPY"]])
    if sessions.empty:
        raise ValueError("No adjusted SPY session history in the requested range")
    if evaluation_end is not None and end not in sessions:
        raise ValueError(f"Requested evaluation_end {end.date()} lacks a recorded SPY session; latest is {sessions[-1].date()}")
    if (sessions.dayofweek >= 5).any():
        raise ValueError("Recorded SPY history includes a nontrading weekend label")
    # Preserve only rows on the fixed recorded session grid; neither market nor
    # stock feature builders consume off-grid rows, so exclude them from lineage.
    session_set = set(sessions.strftime("%Y-%m-%d"))
    by_ticker = {ticker: [row for row in records if row["session"] in session_set]
                 for ticker, records in by_ticker.items()}
    close_panel = pd.DataFrame({ticker: pd.Series(
        [r["close"] for r in records], index=pd.DatetimeIndex([r["session"] for r in records]), dtype=float)
        for ticker, records in by_ticker.items()}, index=sessions)
    market_prices = close_panel.reindex(columns=BetaFeatureEngine.MARKET_TICKERS)
    stock_prices = close_panel.reindex(columns=symbols)
    repo = _ReadOnlyMacroRepository(beta_db_path, sessions[-1])
    engine = BetaFeatureEngine()
    macro, macro_groups, targets = engine.build_macro(repo, sessions)
    market, market_groups = engine.build_market(market_prices, sessions)
    stock_context = build_alpha_alt_market_context(
        stock_prices, benchmark_returns=market_prices["SPY"].pct_change(fill_method=None))
    frame = macro.join(market).join(stock_context).replace([np.inf, -np.inf], np.nan)
    groups: dict[str, list[str]] = {}
    for source in (macro_groups, market_groups):
        for group, names in source.items():
            groups.setdefault(group, [])
            groups[group].extend(name for name in names if name not in groups[group])
    groups["alpha_alt_context"] = list(stock_context.columns)
    groups.setdefault("market", []).extend(stock_context.columns)
    events = (targets.ne(targets.shift(1)) & targets.notna()).any(axis=1).fillna(False)
    target = RegimeLabeler().build(targets, event_mask=events)
    lineage = BetaSourceLineage(sessions, repo.rows_by_series, by_ticker, symbols)
    return {
        "frame": frame, "groups": groups, "labels": target.labels,
        "event_mask": target.event_mask, "sessions": sessions, "lineage": lineage,
        "market_prices": market_prices, "stock_prices": stock_prices,
        "target_frame": targets, "availability_clock": pd.Series([session_open(s) for s in sessions], index=sessions),
        "diagnostics": {
            "schema": SOURCE_SCHEMA, "read_only": True, "network_used": False,
            "native_beta_runtime_modified": False, "whole_panel_feature_selection": False,
            "market_database": str(Path(market_db_path).resolve()),
            "macro_database": str(Path(beta_db_path).resolve()),
            "source_tables": ["ohlcv_bars", "macro_revisions"],
            "sessions": len(sessions), "session_start": sessions[0].date().isoformat(),
            "session_end": sessions[-1].date().isoformat(), "session_basis": "recorded_adjusted_SPY_exchange_dates",
            "stock_symbols": symbols, "candidate_columns": len(frame.columns),
            "macro_rows": sum(len(rows) for rows in repo.rows_by_series.values()),
            "macro_rows_by_series": {sid: len(rows) for sid, rows in repo.rows_by_series.items()},
            "market_rows_by_symbol": {sid: len(rows) for sid, rows in by_ticker.items()},
            "alpha_alt_context": dict(stock_context.attrs),
            "limitations": [
                "Daily FRED observation-dated histories do not attest historical revision vintages",
                "Adjusted market snapshots do not attest historical adjustment versions or vendor publication times",
                "Market feature forward fills use only prior rows and retain explicit source staleness",
                "Dates label observed sessions; NYSE 09:30 Eastern clocks are historical simulation boundaries",
                "Lineage hashes cover all eligible source prefixes, not minimal feature dependencies",
                "No consensus expectations or SEC value/quality context supplied",
            ],
        },
    }
