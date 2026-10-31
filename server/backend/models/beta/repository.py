from __future__ import annotations

import json
import os
import random
import sqlite3
import threading
import time as time_module
import urllib.error
import urllib.parse
import urllib.request
from bisect import insort
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from .session import ET, SessionResolver
from .types import MacroRevision


@dataclass(frozen=True)
class SeriesPolicy:
    series_id: str
    category: str
    frequency: str
    # Retained for backwards compatibility/documentation only. FRED realtime_start
    # is date precision, so these clock fields are NOT treated as observed release
    # timestamps unless an ingestion row explicitly provides an exact timestamp.
    release_hour_et: int
    release_minute_et: int
    observations_per_year: int
    description: str
    actual_metric: Optional[str] = None
    actual_units: Optional[str] = None
    actual_transform: Optional[str] = None
    availability_mode: str = "fred_realtime_date_next_session"
    ingestion_mode: str = "alfred_vintage"


DEFAULT_SERIES_POLICIES: Tuple[SeriesPolicy, ...] = (
    SeriesPolicy("DGS3MO", "rates", "daily", 18, 0, 252, "3-Month Treasury", ingestion_mode="fred_observation_date"),
    SeriesPolicy("DGS2", "rates", "daily", 18, 0, 252, "2-Year Treasury", ingestion_mode="fred_observation_date"),
    SeriesPolicy("DGS5", "rates", "daily", 18, 0, 252, "5-Year Treasury", ingestion_mode="fred_observation_date"),
    SeriesPolicy("DGS10", "rates", "daily", 18, 0, 252, "10-Year Treasury", ingestion_mode="fred_observation_date"),
    SeriesPolicy("DGS30", "rates", "daily", 18, 0, 252, "30-Year Treasury", ingestion_mode="fred_observation_date"),
    SeriesPolicy("DFII5", "real_rates", "daily", 18, 0, 252, "5-Year TIPS real yield", ingestion_mode="fred_observation_date"),
    SeriesPolicy("DFII10", "real_rates", "daily", 18, 0, 252, "10-Year TIPS real yield", ingestion_mode="fred_observation_date"),
    SeriesPolicy("T5YIE", "inflation", "daily", 18, 0, 252, "5-Year breakeven inflation", ingestion_mode="fred_observation_date"),
    SeriesPolicy("T10YIE", "inflation", "daily", 18, 0, 252, "10-Year breakeven inflation", ingestion_mode="fred_observation_date"),
    SeriesPolicy("BAMLH0A0HYM2", "credit", "daily", 18, 0, 252, "US High Yield OAS", ingestion_mode="fred_observation_date"),
    SeriesPolicy("BAMLC0A0CM", "credit", "daily", 18, 0, 252, "US Corporate OAS", ingestion_mode="fred_observation_date"),
    SeriesPolicy("VIXCLS", "volatility", "daily", 18, 0, 252, "CBOE VIX", ingestion_mode="fred_observation_date"),
    SeriesPolicy("CPIAUCSL", "inflation", "monthly", 8, 30, 12, "CPI All Urban Consumers", "cpi", "mom_pct", "pct_1"),
    SeriesPolicy("CPILFESL", "inflation", "monthly", 8, 30, 12, "Core CPI", "core_cpi", "mom_pct", "pct_1"),
    SeriesPolicy("PCEPI", "inflation", "monthly", 8, 30, 12, "PCE Price Index", "pce", "mom_pct", "pct_1"),
    SeriesPolicy("PCEPILFE", "inflation", "monthly", 8, 30, 12, "Core PCE Price Index", "core_pce", "mom_pct", "pct_1"),
    SeriesPolicy("INDPRO", "growth", "monthly", 9, 15, 12, "Industrial Production", "industrial_production", "mom_pct", "pct_1"),
    SeriesPolicy("PAYEMS", "growth", "monthly", 8, 30, 12, "Nonfarm Payrolls", "payrolls", "thousands_change", "diff_1"),
    SeriesPolicy("UNRATE", "growth", "monthly", 8, 30, 12, "Unemployment Rate", "unemployment", "percent", "level"),
    SeriesPolicy("RSAFS", "growth", "monthly", 8, 30, 12, "Retail Sales", "retail_sales", "mom_pct", "pct_1"),
    SeriesPolicy("GDPC1", "growth", "quarterly", 8, 30, 4, "Real GDP", "real_gdp", "qoq_annualized_pct", "qoq_ann"),
    SeriesPolicy("ICSA", "growth", "weekly", 8, 30, 52, "Initial Claims", "initial_claims", "level", "level"),
)

POLICY_BY_ID = {p.series_id: p for p in DEFAULT_SERIES_POLICIES}


class MacroRepository:
    """SQLite-backed point-in-time macro revision store.

    Important timestamp rule: FRED/ALFRED `realtime_start` is a *date*. It is not
    treated as an observed intraday release timestamp. Unless the supplied row has
    `exact_available_at`, Beta conservatively makes that revision usable on the next
    NYSE session. This intentionally gives up same-day information rather than leak.
    """

    API_URL = "https://api.stlouisfed.org/fred/series/observations"
    VINTAGE_DATES_URL = "https://api.stlouisfed.org/fred/series/vintagedates"

    def __init__(
        self,
        db_path: str | Path,
        *,
        fred_api_key: Optional[str] = None,
        strict: bool = True,
        session_resolver: Optional[SessionResolver] = None,
        user_agent: str = "Cowles Quantitative Corporation OryntraControl Beta/1.1",
        initial_realtime_start: str = "1990-01-01",
        overlap_days: int = 14,
        min_request_interval_seconds: float = 0.18,
        max_retries: int = 5,
        vintage_chunk_days: int = 1460,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.fred_api_key = fred_api_key or os.getenv("FRED_API_KEY")
        self.strict = bool(strict)
        self.session_resolver = session_resolver or SessionResolver()
        self.user_agent = user_agent
        self.initial_realtime_start = date.fromisoformat(initial_realtime_start)
        self.overlap_days = int(overlap_days)
        self.min_request_interval_seconds = float(min_request_interval_seconds)
        self.max_retries = int(max_retries)
        # FRED limits JSON observation requests to at most 2,000 vintage dates.
        # A 1,460-day realtime window is safely below that ceiling even for
        # daily series while keeping full initial syncs reasonably efficient.
        self.vintage_chunk_days = max(30, int(vintage_chunk_days))
        self._http_lock = threading.Lock()
        self._last_request_monotonic = 0.0
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _columns(con: sqlite3.Connection, table: str) -> set[str]:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}

    def _init_db(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS macro_revisions (
                    series_id TEXT NOT NULL,
                    observation_date TEXT NOT NULL,
                    realtime_start TEXT NOT NULL,
                    realtime_end TEXT,
                    value REAL NOT NULL,
                    release_at TEXT NOT NULL,
                    available_to_model_at TEXT NOT NULL,
                    eligible_session TEXT NOT NULL,
                    source TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    availability_precision TEXT NOT NULL DEFAULT 'date_only_conservative',
                    PRIMARY KEY(series_id, observation_date, realtime_start)
                );
                CREATE INDEX IF NOT EXISTS idx_macro_eligible
                    ON macro_revisions(series_id, eligible_session, observation_date);
                CREATE INDEX IF NOT EXISTS idx_macro_observation
                    ON macro_revisions(series_id, observation_date, realtime_start);
                CREATE TABLE IF NOT EXISTS sync_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    series_id TEXT NOT NULL,
                    synced_at TEXT NOT NULL,
                    rows_received INTEGER NOT NULL,
                    rows_upserted INTEGER NOT NULL,
                    output_type INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    realtime_start_used TEXT,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS sync_state (
                    series_id TEXT PRIMARY KEY,
                    last_realtime_start TEXT,
                    last_success_at TEXT,
                    last_error TEXT
                );
                """
            )
            cols = self._columns(con, "macro_revisions")
            if "availability_precision" not in cols:
                con.execute("ALTER TABLE macro_revisions ADD COLUMN availability_precision TEXT NOT NULL DEFAULT 'legacy_unknown'")
            sync_cols = self._columns(con, "sync_log")
            if "realtime_start_used" not in sync_cols:
                con.execute("ALTER TABLE sync_log ADD COLUMN realtime_start_used TEXT")
            if "error" not in sync_cols:
                con.execute("ALTER TABLE sync_log ADD COLUMN error TEXT")

    def _rate_limit(self) -> None:
        with self._http_lock:
            now = time_module.monotonic()
            wait = self.min_request_interval_seconds - (now - self._last_request_monotonic)
            if wait > 0:
                time_module.sleep(wait)
            self._last_request_monotonic = time_module.monotonic()

    @staticmethod
    def _fred_http_error(exc: urllib.error.HTTPError) -> RuntimeError:
        """Convert FRED's HTTP response into an actionable Control error.

        FRED commonly returns JSON such as {"error_code": 400,
        "error_message": "..."}.  urllib's default exception string discards that
        useful body and only shows ``HTTP Error 400: Bad Request``.
        """
        message = str(exc)
        try:
            raw = exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            raw = ""
        if raw:
            try:
                payload = json.loads(raw)
            except Exception:
                payload = None
            if isinstance(payload, dict):
                detail = payload.get("error_message") or payload.get("message") or payload.get("error")
                if detail:
                    message = str(detail).strip()
                else:
                    message = raw[:1000]
            else:
                message = raw[:1000]
        return RuntimeError(f"FRED HTTP {exc.code}: {message}")

    def _http_get_json(self, url: str) -> dict:
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries):
            self._rate_limit()
            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            try:
                with urllib.request.urlopen(req, timeout=60) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code not in {429, 500, 502, 503, 504} or attempt >= self.max_retries - 1:
                    raise self._fred_http_error(exc) from exc
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                delay = float(retry_after) if retry_after and str(retry_after).isdigit() else (0.5 * (2 ** attempt) + random.random() * 0.25)
                time_module.sleep(min(delay, 20.0))
            except (urllib.error.URLError, TimeoutError) as exc:
                last_exc = exc
                if attempt >= self.max_retries - 1:
                    raise
                time_module.sleep(min(0.5 * (2 ** attempt) + random.random() * 0.25, 20.0))
        raise RuntimeError(f"FRED request failed: {last_exc}")

    def _last_realtime_start(self, series_id: str) -> Optional[date]:
        with self._connect() as con:
            row = con.execute("SELECT last_realtime_start FROM sync_state WHERE series_id=?", (series_id,)).fetchone()
        if not row or not row["last_realtime_start"]:
            return None
        return date.fromisoformat(row["last_realtime_start"])

    def _incremental_start(self, series_id: str) -> date:
        last = self._last_realtime_start(series_id)
        if last is None:
            return self.initial_realtime_start
        return max(self.initial_realtime_start, last - timedelta(days=self.overlap_days))

    def _fetch_realtime_revision_window(self, series_id: str, *, realtime_start: date, realtime_end: date) -> List[dict]:
        """Fetch ALFRED revisions in the API's row-oriented real-time-period form.

        FRED ``output_type=1`` with a historical realtime period returns one row
        per revision with ``date``, ``value``, ``realtime_start`` and
        ``realtime_end``.  That is the representation Beta needs for a causal
        point-in-time revision ledger.

        Do **not** use output types 2/3 here: those are vintage-date crosstabs.
        In JSON their payload is organized by vintage columns rather than the
        four row fields consumed by ``sync_series``; earlier 5.4 builds therefore
        downloaded data successfully but inserted zero usable macro revisions.
        """
        rows: List[dict] = []
        offset = 0
        limit = 100000
        while True:
            params = {
                "series_id": series_id,
                "api_key": self.fred_api_key,
                "file_type": "json",
                "output_type": 1,
                "realtime_start": realtime_start.isoformat(),
                "realtime_end": realtime_end.isoformat(),
                "limit": limit,
                "offset": offset,
                "sort_order": "asc",
            }
            payload = self._http_get_json(f"{self.API_URL}?{urllib.parse.urlencode(params)}")
            batch = payload.get("observations", []) or []
            rows.extend(batch)
            count = int(payload.get("count", len(rows)))
            offset += len(batch)
            if not batch or offset >= count:
                break
        return rows

    def _fetch_observation_dated_rows(
        self,
        series_id: str,
        *,
        observation_start: Optional[date] = None,
        observation_end: Optional[date] = None,
    ) -> List[dict]:
        """Fetch present-day FRED history for observation-dated market series.

        For explicitly allowlisted daily financial/market series, Control uses
        observation-dated FRED history as the causal market input and admits each
        value only on the next NYSE session. This deliberate path does not depend
        on ALFRED archive coverage and is never used for revisable macro-release
        series such as CPI, PCE, payrolls, GDP, or claims.
        """
        if not self.fred_api_key:
            raise RuntimeError("FRED_API_KEY is required for Beta PIT ingestion.")
        rows: List[dict] = []
        offset = 0
        limit = 100000
        while True:
            params = {
                "series_id": series_id,
                "api_key": self.fred_api_key,
                "file_type": "json",
                "output_type": 1,
                "limit": limit,
                "offset": offset,
                "sort_order": "asc",
            }
            if observation_start is not None:
                params["observation_start"] = observation_start.isoformat()
            if observation_end is not None:
                params["observation_end"] = observation_end.isoformat()
            payload = self._http_get_json(f"{self.API_URL}?{urllib.parse.urlencode(params)}")
            batch = payload.get("observations", [])
            for item in batch:
                normalized = dict(item)
                obs = normalized.get("date")
                if obs:
                    # FRED-mode output_type=1 reports today's realtime period,
                    # which is not the historical availability date. For these
                    # allowlisted market series, the observation date is the
                    # actual causal event date; next-session admission remains
                    # conservative and avoids same-day lookahead.
                    normalized["realtime_start"] = obs
                    normalized["realtime_end"] = None
                    normalized["__oryntra_source"] = "FRED-output_type=1-observation-dated-network"
                    normalized["__oryntra_output_type"] = 1
                rows.append(normalized)
            count = int(payload.get("count", len(rows)))
            offset += len(batch)
            if not batch or offset >= count:
                break
        return rows

    def _fetch_vintage_dates(self, series_id: str) -> List[date]:
        """Return ALFRED's actual vintage dates for *series_id*.

        Do not infer archive coverage from a hard-coded year. Some FRED series
        were created long before ALFRED started archiving their vintages. The
        observations endpoint can return a misleading 400 ("series does not
        exist in ALFRED") when a requested realtime window is entirely before
        the first archived vintage. Querying the dedicated vintage-dates
        endpoint first gives the true archive coverage and prevents those empty
        pre-archive requests.
        """
        if not self.fred_api_key:
            raise RuntimeError("FRED_API_KEY is required for ALFRED vintage discovery.")
        out: List[date] = []
        offset = 0
        limit = 10000
        while True:
            params = {
                "series_id": series_id,
                "api_key": self.fred_api_key,
                "file_type": "json",
                "limit": limit,
                "offset": offset,
                "sort_order": "asc",
            }
            payload = self._http_get_json(f"{self.VINTAGE_DATES_URL}?{urllib.parse.urlencode(params)}")
            batch = payload.get("vintage_dates", []) or []
            for raw in batch:
                try:
                    out.append(date.fromisoformat(str(raw)))
                except (TypeError, ValueError):
                    continue
            count = int(payload.get("count", len(out)))
            offset += len(batch)
            if not batch or offset >= count:
                break
        return sorted(set(out))

    def _fetch_vintage_rows(
        self,
        series_id: str,
        *,
        realtime_start: Optional[date] = None,
        realtime_end: Optional[date] = None,
    ) -> List[dict]:
        """Fetch row-oriented ALFRED revision history for a revisable series.

        The vintage-date endpoint is used only to discover whether ALFRED has an
        archive and where that archive begins.  The actual revision ledger is
        fetched with ``output_type=1`` over a historical realtime period because
        that format exposes the revision's start/end dates directly.
        """
        if not self.fred_api_key:
            raise RuntimeError(
                "Strict Beta PIT ingestion requires FRED_API_KEY because the model must retrieve "
                "FRED/ALFRED real-time revision history."
            )
        requested_start = realtime_start or self._incremental_start(series_id)
        end = realtime_end or date.today()
        if requested_start > end:
            return []

        vintages = [d for d in self._fetch_vintage_dates(series_id) if d <= end]
        if not vintages:
            return []
        archive_start = vintages[0]
        start = max(requested_start, archive_start)
        if start > end:
            return []

        return self._fetch_realtime_revision_window(
            series_id, realtime_start=start, realtime_end=end
        )

    def _availability_from_row(self, row: dict, rt_start: date) -> tuple[datetime, datetime, date, str]:
        # Tests/integrations with a licensed release feed may provide a genuine exact
        # timestamp. FRED itself does not, so never invent one from policy clock fields.
        exact = row.get("exact_available_at") or row.get("release_at_exact")
        if exact:
            dt = datetime.fromisoformat(str(exact))
            if dt.tzinfo is None:
                raise ValueError("exact_available_at must be timezone-aware")
            eligible = self.session_resolver.eligible_session(dt)
            return dt, dt, eligible, "exact_external_timestamp"

        # Date-only conservative semantics. 23:59:59 ET guarantees next-session use.
        marker = datetime.combine(rt_start, time(23, 59, 59), tzinfo=ET)
        eligible = self.session_resolver.next_session(rt_start, include_today=False)
        return marker, marker, eligible, "date_only_conservative_next_session"

    def sync_series(
        self,
        policy: SeriesPolicy,
        *,
        rows: Optional[Sequence[dict]] = None,
        force_full: bool = False,
    ) -> dict:
        start = self.initial_realtime_start if force_full else self._incremental_start(policy.series_id)
        fetched_at = datetime.now(tz=ZoneInfo("UTC"))
        try:
            if rows is not None:
                raw = list(rows)
                default_source = "FRED/ALFRED-realtime-period-injected-fixture"
                sync_output_type = 1
            elif policy.ingestion_mode == "fred_observation_date":
                raw = self._fetch_observation_dated_rows(policy.series_id, observation_start=start)
                default_source = "FRED-output_type=1-observation-dated-network"
                sync_output_type = 1
            else:
                raw = self._fetch_vintage_rows(policy.series_id, realtime_start=start)
                default_source = "FRED/ALFRED-output_type=1-realtime-period-network"
                sync_output_type = 1
            upserted = 0
            max_rt: Optional[date] = self._last_realtime_start(policy.series_id)
            with self._connect() as con:
                for row in raw:
                    if row.get("value") in (None, "", "."):
                        continue
                    try:
                        obs_date = date.fromisoformat(row["date"])
                        rt_start = date.fromisoformat(row["realtime_start"])
                        rt_end = date.fromisoformat(row["realtime_end"]) if row.get("realtime_end") else None
                        value = float(row["value"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    release_at, available_at, eligible, precision = self._availability_from_row(row, rt_start)
                    con.execute(
                        """
                        INSERT INTO macro_revisions(
                            series_id, observation_date, realtime_start, realtime_end,
                            value, release_at, available_to_model_at, eligible_session,
                            source, fetched_at, availability_precision
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(series_id, observation_date, realtime_start) DO UPDATE SET
                            realtime_end=excluded.realtime_end,
                            value=excluded.value,
                            release_at=excluded.release_at,
                            available_to_model_at=excluded.available_to_model_at,
                            eligible_session=excluded.eligible_session,
                            source=excluded.source,
                            fetched_at=excluded.fetched_at,
                            availability_precision=excluded.availability_precision
                        """,
                        (
                            policy.series_id, obs_date.isoformat(), rt_start.isoformat(),
                            rt_end.isoformat() if rt_end else None, value,
                            release_at.isoformat(), available_at.isoformat(), eligible.isoformat(),
                            row.get("__oryntra_source", default_source),
                            fetched_at.isoformat(),
                            ("observation_date_conservative_next_session" if row.get("__oryntra_output_type") == 1 else precision),
                        ),
                    )
                    upserted += 1
                    max_rt = rt_start if max_rt is None else max(max_rt, rt_start)
                con.execute(
                    "INSERT INTO sync_log(series_id, synced_at, rows_received, rows_upserted, output_type, status, realtime_start_used, error) "
                    "VALUES (?, ?, ?, ?, ?, 'ok', ?, NULL)",
                    (policy.series_id, fetched_at.isoformat(), len(raw), upserted, sync_output_type, start.isoformat()),
                )
                con.execute(
                    "INSERT INTO sync_state(series_id, last_realtime_start, last_success_at, last_error) VALUES (?, ?, ?, NULL) "
                    "ON CONFLICT(series_id) DO UPDATE SET last_realtime_start=excluded.last_realtime_start, "
                    "last_success_at=excluded.last_success_at, last_error=NULL",
                    (policy.series_id, max_rt.isoformat() if max_rt else None, fetched_at.isoformat()),
                )
            return {
                "series_id": policy.series_id,
                "rows_received": len(raw),
                "rows_upserted": upserted,
                "output_type": sync_output_type,
                "ingestion_mode": policy.ingestion_mode if rows is None else "injected_fixture",
                "realtime_start_used": start.isoformat(),
                "incremental": not force_full and start > self.initial_realtime_start,
            }
        except Exception as exc:
            with self._connect() as con:
                con.execute(
                    "INSERT INTO sync_log(series_id, synced_at, rows_received, rows_upserted, output_type, status, realtime_start_used, error) "
                    "VALUES (?, ?, 0, 0, ?, 'error', ?, ?)",
                    (policy.series_id, fetched_at.isoformat(), 1, start.isoformat(), str(exc)),
                )
                con.execute(
                    "INSERT INTO sync_state(series_id, last_realtime_start, last_success_at, last_error) VALUES (?, NULL, NULL, ?) "
                    "ON CONFLICT(series_id) DO UPDATE SET last_error=excluded.last_error",
                    (policy.series_id, str(exc)),
                )
            raise

    def sync_all(self, policies: Sequence[SeriesPolicy] = DEFAULT_SERIES_POLICIES, *, force_full: bool = False) -> dict:
        results, errors = [], []
        for policy in policies:
            try:
                results.append(self.sync_series(policy, force_full=force_full))
            except Exception as exc:
                errors.append({"series_id": policy.series_id, "error": str(exc)})
                if self.strict:
                    break
        if self.strict and errors:
            raise RuntimeError(f"Strict macro sync failed: {errors[0]}")
        return {"results": results, "errors": errors}

    def revisions(self, series_id: str) -> List[MacroRevision]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM macro_revisions WHERE series_id=? ORDER BY eligible_session, realtime_start, observation_date",
                (series_id,),
            ).fetchall()
        out: List[MacroRevision] = []
        for row in rows:
            out.append(
                MacroRevision(
                    series_id=row["series_id"],
                    observation_date=date.fromisoformat(row["observation_date"]),
                    realtime_start=date.fromisoformat(row["realtime_start"]),
                    realtime_end=date.fromisoformat(row["realtime_end"]) if row["realtime_end"] else None,
                    value=float(row["value"]),
                    release_at=datetime.fromisoformat(row["release_at"]),
                    available_to_model_at=datetime.fromisoformat(row["available_to_model_at"]),
                    eligible_session=date.fromisoformat(row["eligible_session"]),
                    source=row["source"],
                    fetched_at=datetime.fromisoformat(row["fetched_at"]),
                    availability_precision=row["availability_precision"] if "availability_precision" in row.keys() else "legacy_unknown",
                )
            )
        return out

    def latest_sync(self, series_id: str) -> Optional[dict]:
        with self._connect() as con:
            row = con.execute("SELECT * FROM sync_log WHERE series_id=? ORDER BY id DESC LIMIT 1", (series_id,)).fetchone()
        return dict(row) if row else None

    def integrity_report(self) -> dict:
        with self._connect() as con:
            total = con.execute("SELECT COUNT(*) AS n FROM macro_revisions").fetchone()["n"]
            network_rows = con.execute(
                "SELECT COUNT(*) AS n FROM macro_revisions WHERE source IN ('FRED/ALFRED-output_type=1-realtime-period-network','FRED/ALFRED-output_type=3-network')"
            ).fetchone()["n"]
            observation_rows = con.execute("SELECT COUNT(*) AS n FROM macro_revisions WHERE source='FRED-output_type=1-observation-dated-network'").fetchone()["n"]
            fixture_rows = con.execute(
                "SELECT COUNT(*) AS n FROM macro_revisions WHERE source IN ('FRED/ALFRED-realtime-period-injected-fixture','FRED/ALFRED-output_type=3-injected-fixture')"
            ).fetchone()["n"]
            approved_network_rows = int(network_rows) + int(observation_rows)
            bad = int(total) - approved_network_rows - int(fixture_rows)
            rows = con.execute("SELECT DISTINCT series_id FROM macro_revisions").fetchall()
            precision_rows = con.execute(
                "SELECT availability_precision, COUNT(*) AS n FROM macro_revisions GROUP BY availability_precision"
            ).fetchall()
        present = {row["series_id"] for row in rows}
        required = {p.series_id for p in DEFAULT_SERIES_POLICIES}
        missing = sorted(required - present)
        coverage = len(required & present) / max(1, len(required))
        precisions = {row["availability_precision"]: int(row["n"]) for row in precision_rows}
        return {
            "strict": self.strict,
            "revision_rows": int(total),
            "series_count": int(len(present)),
            "required_series_count": int(len(required)),
            "required_series_coverage_pct": float(coverage * 100.0),
            "missing_required_series": missing,
            "non_vintage_rows": int(max(0, bad)),
            "network_vintage_rows": int(network_rows),
            "fred_observation_dated_rows": int(observation_rows),
            "approved_network_rows": int(approved_network_rows),
            "injected_fixture_rows": int(fixture_rows),
            "required_series_complete": bool(not missing),
            "real_fred_provenance": bool(total > 0 and approved_network_rows == total and fixture_rows == 0),
            "availability_precision": precisions,
            "intraday_time_claimed_from_fred_date_only": False,
            "pit_integrity": bool(self.strict and total > 0 and bad == 0 and not missing),
        }

    def _state_series(self, series_id: str, sessions: pd.DatetimeIndex, lags: Sequence[int]) -> pd.DataFrame:
        sessions = pd.DatetimeIndex(pd.to_datetime(sessions)).tz_localize(None).normalize().sort_values().unique()
        events = self.revisions(series_id)
        columns = ["level"]
        for lag in sorted(set(int(x) for x in lags if int(x) > 0)):
            columns.extend([f"diff_{lag}", f"pct_{lag}"])
        out = pd.DataFrame(index=sessions, columns=columns, dtype=float)
        state: Dict[date, float] = {}
        obs_dates: List[date] = []
        event_i = 0
        for session in sessions:
            sd = session.date()
            while event_i < len(events) and events[event_i].eligible_session <= sd:
                event = events[event_i]
                if event.observation_date not in state:
                    insort(obs_dates, event.observation_date)
                state[event.observation_date] = event.value
                event_i += 1
            if not obs_dates:
                continue
            latest = state[obs_dates[-1]]
            out.at[session, "level"] = latest
            for lag in lags:
                lag = int(lag)
                if lag <= 0 or len(obs_dates) <= lag:
                    continue
                prev = state[obs_dates[-1 - lag]]
                out.at[session, f"diff_{lag}"] = latest - prev
                if prev != 0:
                    out.at[session, f"pct_{lag}"] = latest / prev - 1.0
        return out

    def pit_transform_panel(self, sessions: pd.DatetimeIndex, policies: Sequence[SeriesPolicy] = DEFAULT_SERIES_POLICIES) -> Dict[str, pd.DataFrame]:
        result: Dict[str, pd.DataFrame] = {}
        for policy in policies:
            if policy.frequency == "daily":
                lags = (1, 21, 63, 252)
            elif policy.frequency == "weekly":
                lags = (1, 4, 13, 52)
            elif policy.frequency == "monthly":
                lags = (1, 3, 12)
            elif policy.frequency == "quarterly":
                lags = (1, 4)
            else:
                lags = (1,)
            result[policy.series_id] = self._state_series(policy.series_id, sessions, lags)
        return result

    def value_state_as_of(self, series_id: str, when: datetime) -> Dict[date, float]:
        if when.tzinfo is None:
            raise ValueError("when must be timezone-aware")
        with self._connect() as con:
            rows = con.execute(
                "SELECT observation_date, realtime_start, value, available_to_model_at FROM macro_revisions "
                "WHERE series_id=? ORDER BY observation_date, realtime_start",
                (series_id,),
            ).fetchall()
        state: Dict[date, float] = {}
        for row in rows:
            available = datetime.fromisoformat(row["available_to_model_at"])
            if available <= when:
                state[date.fromisoformat(row["observation_date"])] = float(row["value"])
        return state

    def first_release_events(self, series_id: str) -> List[MacroRevision]:
        events = self.revisions(series_id)
        first: Dict[date, MacroRevision] = {}
        for event in events:
            current = first.get(event.observation_date)
            if current is None or event.realtime_start < current.realtime_start:
                first[event.observation_date] = event
        return [first[d] for d in sorted(first)]
