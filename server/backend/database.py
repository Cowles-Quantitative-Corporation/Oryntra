from __future__ import annotations

import json
import math
import os
import sqlite3
from datetime import date, datetime
from typing import Any, Iterable

import pandas as pd

_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "oryntra.db"
)
DB_PATH = os.path.abspath(os.path.expanduser(os.getenv("ORYNTRA_DB_PATH", _DEFAULT_DB_PATH)))


def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-65536")
    conn.execute("PRAGMA mmap_size=268435456")
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            email           TEXT NOT NULL UNIQUE COLLATE NOCASE,
            display_name    TEXT DEFAULT '',
            password_salt   TEXT NOT NULL,
            password_hash   TEXT NOT NULL,
            created_at      TEXT DEFAULT (datetime('now')),
            last_login_at   TEXT,
            last_subscription_prompt_est_date TEXT,
            legal_version   TEXT,
            legal_accepted_at TEXT
        )
    """)

    user_columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(users)").fetchall()
    }
    if "legal_version" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN legal_version TEXT")
    if "legal_accepted_at" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN legal_accepted_at TEXT")
    if "last_subscription_prompt_est_date" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_subscription_prompt_est_date TEXT")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_identities (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            provider        TEXT NOT NULL CHECK(provider IN ('google', 'apple')),
            subject         TEXT NOT NULL,
            verified_email  TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            last_login_at   TEXT,
            UNIQUE(provider, subject),
            UNIQUE(user_id, provider),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Server-side, short-lived OAuth state prevents callback forgery.  It is
    # deliberately separate from browser sessions: an unauthenticated visitor
    # must complete the provider round trip before a local session is created.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS oauth_login_states (
            state_hash      TEXT PRIMARY KEY,
            provider        TEXT NOT NULL CHECK(provider IN ('google', 'apple')),
            nonce           TEXT NOT NULL,
            code_verifier   TEXT,
            intent          TEXT NOT NULL CHECK(intent IN ('login', 'signup')),
            accept_legal    INTEGER NOT NULL DEFAULT 0,
            expires_at      TEXT NOT NULL,
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_sessions (
            token       TEXT PRIMARY KEY,
            user_id     INTEGER NOT NULL,
            created_at  TEXT DEFAULT (datetime('now')),
            expires_at  TEXT NOT NULL,
            show_subscription_offer INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    session_columns = {row[1] for row in cursor.execute("PRAGMA table_info(user_sessions)").fetchall()}
    if "show_subscription_offer" not in session_columns:
        cursor.execute("ALTER TABLE user_sessions ADD COLUMN show_subscription_offer INTEGER NOT NULL DEFAULT 0")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            plan_code       TEXT NOT NULL,
            plan_name       TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'ACTIVE',
            started_at      TEXT DEFAULT (datetime('now')),
            current_period_end TEXT,
            provider        TEXT DEFAULT 'manual_beta',
            provider_ref    TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Owner-only local test overrides. These intentionally live outside billing
    # records so diagnostic plan changes cannot cancel or rewrite a purchase.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS debug_subscription_overrides (
            user_id         INTEGER PRIMARY KEY,
            plan_code       TEXT NOT NULL,
            plan_name       TEXT NOT NULL,
            updated_at      TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Provisioned directly by an operator. Public subscriptions, emails, and
    # browser state never grant access to CQC's internal research surfaces.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS internal_operator_access (
            user_id         INTEGER PRIMARY KEY,
            status          TEXT NOT NULL DEFAULT 'ACTIVE',
            granted_at      TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analysis_usage (
            user_id         INTEGER NOT NULL,
            usage_date      TEXT NOT NULL,
            request_count   INTEGER NOT NULL DEFAULT 0,
            updated_at      TEXT DEFAULT (datetime('now')),
            PRIMARY KEY(user_id, usage_date),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Stores only a user-approved research decision ledger.  Browser-supplied
    # bars remain transient and are never written here.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS research_portfolio_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            label           TEXT NOT NULL,
            engine_id       TEXT NOT NULL,
            engine_version  TEXT NOT NULL,
            configuration_json TEXT NOT NULL,
            dataset_fingerprint TEXT NOT NULL,
            as_of_date      TEXT NOT NULL,
            created_at      TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS research_portfolio_directives (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id          INTEGER NOT NULL,
            symbol          TEXT NOT NULL COLLATE NOCASE,
            action          TEXT NOT NULL CHECK(action IN ('BUY','SELL','TRIM','HOLD')),
            target_weight   REAL NOT NULL,
            prior_weight    REAL NOT NULL,
            reference_price REAL NOT NULL,
            estimated_shares REAL NOT NULL,
            rationale       TEXT NOT NULL,
            lifecycle_json  TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(run_id) REFERENCES research_portfolio_runs(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
            added_at    TEXT    DEFAULT (datetime('now')),
            notes       TEXT    DEFAULT ''
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_watchlist (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            ticker      TEXT NOT NULL COLLATE NOCASE,
            added_at    TEXT DEFAULT (datetime('now')),
            notes       TEXT DEFAULT '',
            UNIQUE(user_id, ticker),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analysis_cache (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker      TEXT    NOT NULL COLLATE NOCASE,
            analyzed_at TEXT    DEFAULT (datetime('now')),
            data_json   TEXT    NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker          TEXT    NOT NULL COLLATE NOCASE,
            direction       TEXT    NOT NULL CHECK(direction IN ('LONG','SHORT')),
            entry_price     REAL    NOT NULL,
            stop_price      REAL    NOT NULL,
            target_price    REAL    NOT NULL,
            size            REAL    NOT NULL DEFAULT 100,
            status          TEXT    NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN','CLOSED','CANCELLED')),
            opened_at       TEXT    DEFAULT (datetime('now')),
            closed_at       TEXT,
            close_price     REAL,
            pnl             REAL,
            pnl_pct         REAL,
            notes           TEXT    DEFAULT '',
            setup_type      TEXT,
            quality_score   REAL
        )
    """)

    try:
        cols = [row[1] for row in cursor.execute(
            "PRAGMA table_info(paper_trades)").fetchall()]
        if "user_id" not in cols:
            cursor.execute(
                "ALTER TABLE paper_trades ADD COLUMN user_id INTEGER")
    except Exception:
        pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scan_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker      TEXT    NOT NULL COLLATE NOCASE,
            scanned_at  TEXT    DEFAULT (datetime('now')),
            setup_type  TEXT,
            score       REAL,
            price       REAL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS app_counters (
            key        TEXT PRIMARY KEY,
            value      INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    cursor.execute(
        "INSERT OR IGNORE INTO app_counters (key, value) VALUES ('stock_searches', 0)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv_bars (
            ticker      TEXT NOT NULL COLLATE NOCASE,
            timeframe   TEXT NOT NULL,
            timestamp   TEXT NOT NULL,
            open        REAL NOT NULL,
            high        REAL NOT NULL,
            low         REAL NOT NULL,
            close       REAL NOT NULL,
            volume      REAL NOT NULL,
            vwap        REAL,
            transactions INTEGER,
            provider    TEXT,
            adjusted    INTEGER DEFAULT 1,
            fetched_at  TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (ticker, timeframe, timestamp)
        )
    """)


    ohlcv_columns = {
        str(row[1]) for row in cursor.execute("PRAGMA table_info(ohlcv_bars)").fetchall()
    }
    if "vwap" not in ohlcv_columns:
        cursor.execute("ALTER TABLE ohlcv_bars ADD COLUMN vwap REAL")
    if "transactions" not in ohlcv_columns:
        cursor.execute("ALTER TABLE ohlcv_bars ADD COLUMN transactions INTEGER")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_ingest_runs (
            trading_date    TEXT PRIMARY KEY,
            endpoint        TEXT NOT NULL DEFAULT 'grouped_daily',
            status          TEXT NOT NULL,
            rows_received   INTEGER NOT NULL DEFAULT 0,
            rows_stored     INTEGER NOT NULL DEFAULT 0,
            request_id      TEXT,
            attempt_count   INTEGER NOT NULL DEFAULT 0,
            started_at      TEXT,
            completed_at    TEXT,
            error           TEXT,
            updated_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_symbols (
            ticker              TEXT PRIMARY KEY COLLATE NOCASE,
            name                TEXT,
            market              TEXT,
            locale              TEXT,
            primary_exchange    TEXT,
            type                TEXT,
            active              INTEGER,
            currency_name       TEXT,
            cik                 TEXT,
            composite_figi      TEXT,
            share_class_figi    TEXT,
            last_updated_utc    TEXT,
            source              TEXT DEFAULT 'polygon_reference',
            fetched_at          TEXT DEFAULT (datetime('now'))
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_cache_meta (
            key         TEXT PRIMARY KEY,
            value       TEXT,
            updated_at  TEXT DEFAULT (datetime('now'))
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pattern_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker          TEXT NOT NULL COLLATE NOCASE,
            timeframe       TEXT NOT NULL,
            timestamp       TEXT NOT NULL,
            pattern_name    TEXT NOT NULL,
            pattern_family  TEXT NOT NULL,
            direction       TEXT CHECK(direction IN ('BULLISH','BEARISH','NEUTRAL')),
            confidence      REAL DEFAULT 0,
            zone_low        REAL,
            zone_high       REAL,
            trigger_price   REAL,
            candle_index    INTEGER,
            context_json    TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(ticker, timeframe, timestamp, pattern_name, zone_low, zone_high)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pattern_outcomes (
            id                              INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_event_id                INTEGER NOT NULL,
            horizon_days                    INTEGER NOT NULL,
            forward_return_pct              REAL,
            max_favorable_excursion_pct     REAL,
            max_adverse_excursion_pct       REAL,
            target_hit                      INTEGER DEFAULT 0,
            stop_hit                        INTEGER DEFAULT 0,
            outcome                         TEXT CHECK(outcome IN ('WIN','LOSS','NEUTRAL','UNKNOWN')),
            evaluated_at                    TEXT DEFAULT (datetime('now')),
            UNIQUE(pattern_event_id, horizon_days),
            FOREIGN KEY(pattern_event_id) REFERENCES pattern_events(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pattern_stats (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_name    TEXT NOT NULL,
            ticker          TEXT COLLATE NOCASE,
            timeframe       TEXT NOT NULL,
            market_regime   TEXT DEFAULT 'ALL',
            sample_size     INTEGER DEFAULT 0,
            win_rate_1d     REAL,
            win_rate_3d     REAL,
            win_rate_5d     REAL,
            win_rate_10d    REAL,
            win_rate_20d    REAL,
            avg_return_1d   REAL,
            avg_return_3d   REAL,
            avg_return_5d   REAL,
            avg_return_10d  REAL,
            avg_return_20d  REAL,
            avg_mfe         REAL,
            avg_mae         REAL,
            expectancy      REAL,
            updated_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(pattern_name, ticker, timeframe, market_regime)
        )
    """)


    cursor.execute("DROP TABLE IF EXISTS edge_profiles")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vai_training_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      TEXT DEFAULT (datetime('now')),
            status          TEXT NOT NULL,
            samples         INTEGER DEFAULT 0,
            horizon_days    INTEGER DEFAULT 10,
            threshold       REAL,
            validation_json TEXT,
            terminal_output TEXT
        )
    """)

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_sessions_user ON user_sessions(user_id, expires_at)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_paper_trades_user_status ON paper_trades(user_id, status, opened_at)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_analysis_cache_ticker_time ON analysis_cache(ticker, analyzed_at)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_scan_history_ticker_time ON scan_history(ticker, scanned_at)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker_timeframe_time ON ohlcv_bars(ticker, timeframe, timestamp)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ohlcv_timeframe_time ON ohlcv_bars(timeframe, timestamp)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_market_ingest_status_date ON market_ingest_runs(status, trading_date)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_market_symbols_active_ticker ON market_symbols(active, ticker)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_pattern_events_lookup ON pattern_events(ticker, timeframe, pattern_name, timestamp)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_pattern_outcomes_event ON pattern_outcomes(pattern_event_id, horizon_days)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_pattern_stats_lookup ON pattern_stats(pattern_name, ticker, timeframe)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_vai_training_runs_created ON vai_training_runs(created_at)")

    conn.commit()
    conn.close()
    print(f"Database ready at {DB_PATH}")


def get_app_counter(key: str = "stock_searches") -> int:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO app_counters (key, value) VALUES (?, 0)", (key,))
        row = conn.execute(
            "SELECT value FROM app_counters WHERE key = ?", (key,)).fetchone()
        return int(row["value"] if row else 0)
    finally:
        conn.commit()
        conn.close()


def increment_app_counter(key: str = "stock_searches", amount: int = 1) -> int:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO app_counters (key, value) VALUES (?, 0)", (key,))
        conn.execute(
            """
            UPDATE app_counters
               SET value = value + ?, updated_at = datetime('now')
             WHERE key = ?
            """,
            (int(amount), key),
        )
        row = conn.execute(
            "SELECT value FROM app_counters WHERE key = ?", (key,)).fetchone()
        conn.commit()
        return int(row["value"] if row else 0)
    finally:
        conn.close()


def set_app_counter_min(key: str, minimum: int) -> int:
    conn = get_connection()
    try:
        minimum = int(minimum)
        conn.execute(
            "INSERT OR IGNORE INTO app_counters (key, value) VALUES (?, 0)", (key,))
        row = conn.execute(
            "SELECT value FROM app_counters WHERE key = ?", (key,)).fetchone()
        current = int(row["value"] if row else 0)
        if current < minimum:
            conn.execute(
                "UPDATE app_counters SET value = ?, updated_at = datetime('now') WHERE key = ?",
                (minimum, key),
            )
            current = minimum
        conn.commit()
        return int(current)
    finally:
        conn.close()


def store_ohlcv_bars(
    ticker: str,
    timeframe: str,
    hist: pd.DataFrame,
    provider: str | None = None,
    adjusted: bool = True,
) -> int:

    if hist is None or hist.empty:
        return 0

    ticker = ticker.upper().strip()
    timeframe = timeframe or "1d"
    rows = []

    for idx, row in hist.iterrows():
        try:
            ts = pd.Timestamp(idx).isoformat()
            rows.append((
                ticker,
                timeframe,
                ts,
                float(row["Open"]),
                float(row["High"]),
                float(row["Low"]),
                float(row["Close"]),
                float(row.get("Volume", 0) or 0),
                _maybe_float(row.get("VWAP", row.get("vwap"))),
                _maybe_int(row.get("Transactions", row.get("transactions"))),
                provider,
                1 if adjusted else 0,
            ))
        except Exception:
            continue

    if not rows:
        return 0

    conn = get_connection()
    conn.executemany("""
        INSERT INTO ohlcv_bars
        (ticker, timeframe, timestamp, open, high, low, close, volume,
         vwap, transactions, provider, adjusted)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker, timeframe, timestamp) DO UPDATE SET
            open=excluded.open,
            high=excluded.high,
            low=excluded.low,
            close=excluded.close,
            volume=excluded.volume,
            vwap=COALESCE(excluded.vwap, ohlcv_bars.vwap),
            transactions=COALESCE(excluded.transactions, ohlcv_bars.transactions),
            provider=excluded.provider,
            adjusted=excluded.adjusted,
            fetched_at=datetime('now')
    """, rows)
    conn.commit()
    conn.close()
    return len(rows)


def _period_cutoff_days(period: str) -> int | None:
    period = (period or "5y").strip().lower()
    mapping = {
        "5m": 14,
        "1mo": 45,
        "6mo": 210,
        "1y": 400,
        "2y": 760,
        "5y": 1900,
        "all": None,
    }
    return mapping.get(period, 1900)


def load_ohlcv_bars(ticker: str, timeframe: str = "1d", period: str = "5y") -> pd.DataFrame:
    ticker = ticker.upper().strip()
    timeframe = timeframe or "1d"
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT timestamp, open, high, low, close, volume
              FROM ohlcv_bars
             WHERE ticker = ? COLLATE NOCASE AND timeframe = ?
             ORDER BY timestamp ASC
            """,
            (ticker, timeframe),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    data = []
    for r in rows:
        try:
            data.append({
                "timestamp": pd.Timestamp(r["timestamp"]),
                "Open": float(r["open"]),
                "High": float(r["high"]),
                "Low": float(r["low"]),
                "Close": float(r["close"]),
                "Volume": float(r["volume"] or 0),
            })
        except Exception:
            continue
    if not data:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    df = pd.DataFrame(data).drop_duplicates(
        subset=["timestamp"]).sort_values("timestamp")
    df = df.set_index("timestamp")
    cutoff_days = _period_cutoff_days(period)
    if cutoff_days is not None and not df.empty:
        cutoff = df.index.max() - pd.Timedelta(days=int(cutoff_days))
        df = df[df.index >= cutoff]
    return df[["Open", "High", "Low", "Close", "Volume"]]


def get_ohlcv_cache_summary(tickers: Iterable[str] | None = None, timeframe: str = "1d") -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        params: list[Any] = [timeframe or "1d"]
        where = "WHERE timeframe = ?"
        if tickers:
            clean = [str(t).upper().strip() for t in tickers if str(t).strip()]
            if clean:
                where += " AND ticker COLLATE NOCASE IN (%s)" % ",".join(
                    "?" for _ in clean)
                params.extend(clean)
        rows = conn.execute(
            f"""
            SELECT ticker, timeframe, COUNT(*) AS bars,
                   MIN(timestamp) AS first_timestamp,
                   MAX(timestamp) AS last_timestamp,
                   MAX(fetched_at) AS last_fetched_at,
                   MAX(provider) AS provider
              FROM ohlcv_bars
              {where}
             GROUP BY ticker, timeframe
             ORDER BY ticker ASC
            """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_ohlcv_cache_size_bytes() -> int:
    try:
        return int(os.path.getsize(DB_PATH))
    except Exception:
        return 0


def _maybe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def set_market_cache_meta(key: str, value: Any) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO market_cache_meta (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=datetime('now')
            """,
            (str(key), json.dumps(value, default=str)),
        )
        conn.commit()
    finally:
        conn.close()


def get_market_cache_meta(key: str, default: Any = None) -> Any:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM market_cache_meta WHERE key = ?", (str(key),)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return row["value"]


def get_successful_market_dates() -> set[str]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT trading_date FROM market_ingest_runs WHERE status = 'SUCCESS'"
        ).fetchall()
        return {str(row["trading_date"]) for row in rows}
    finally:
        conn.close()


def mark_market_ingest_started(trading_date: str, endpoint: str = "grouped_daily") -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO market_ingest_runs
                (trading_date, endpoint, status, attempt_count, started_at, updated_at)
            VALUES (?, ?, 'RUNNING', 1, datetime('now'), datetime('now'))
            ON CONFLICT(trading_date) DO UPDATE SET
                endpoint=excluded.endpoint,
                status='RUNNING',
                attempt_count=market_ingest_runs.attempt_count + 1,
                started_at=datetime('now'),
                completed_at=NULL,
                error=NULL,
                updated_at=datetime('now')
            """,
            (str(trading_date), str(endpoint)),
        )
        conn.commit()
    finally:
        conn.close()


def mark_market_ingest_failed(
    trading_date: str,
    error: str,
    *,
    status: str = "FAILED",
    rows_received: int = 0,
    request_id: str | None = None,
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO market_ingest_runs
                (trading_date, status, rows_received, rows_stored, request_id,
                 attempt_count, started_at, completed_at, error, updated_at)
            VALUES (?, ?, ?, 0, ?, 1, datetime('now'), datetime('now'), ?, datetime('now'))
            ON CONFLICT(trading_date) DO UPDATE SET
                status=excluded.status,
                rows_received=excluded.rows_received,
                rows_stored=0,
                request_id=excluded.request_id,
                completed_at=datetime('now'),
                error=excluded.error,
                updated_at=datetime('now')
            """,
            (
                str(trading_date),
                str(status),
                int(rows_received or 0),
                request_id,
                str(error)[:2000],
            ),
        )
        conn.commit()
    finally:
        conn.close()


def store_grouped_daily_bars(
    trading_date: str,
    results: Iterable[dict[str, Any]],
    *,
    provider: str = "polygon_grouped_daily",
    adjusted: bool = True,
    request_id: str | None = None,
    minimum_rows: int = 1000,
) -> dict[str, Any]:


    trading_date = str(trading_date)
    input_rows = list(results or [])
    normalized: list[tuple[Any, ...]] = []

    for bar in input_rows:
        try:
            ticker = str(bar.get("T") or bar.get("ticker") or "").upper().strip()
            if not ticker:
                continue
            ts_raw = bar.get("t") or bar.get("timestamp")
            if ts_raw is None:
                ts = pd.Timestamp(trading_date).isoformat()
            elif isinstance(ts_raw, (int, float)):
                ts = pd.to_datetime(ts_raw, unit="ms", utc=True).tz_convert(None).isoformat()
            else:
                ts = pd.Timestamp(ts_raw).isoformat()

            open_price = float(bar.get("o", bar.get("open")))
            high_price = float(bar.get("h", bar.get("high")))
            low_price = float(bar.get("l", bar.get("low")))
            close_price = float(bar.get("c", bar.get("close")))
            volume = float(bar.get("v", bar.get("volume", 0)) or 0)
            if not all(math.isfinite(value) for value in (open_price, high_price, low_price, close_price, volume)):
                continue
            if min(open_price, high_price, low_price, close_price) < 0:
                continue
            if high_price < low_price:
                continue

            normalized.append((
                ticker,
                "1d",
                ts,
                open_price,
                high_price,
                low_price,
                close_price,
                volume,
                _maybe_float(bar.get("vw", bar.get("vwap"))),
                _maybe_int(bar.get("n", bar.get("transactions"))),
                provider,
                1 if adjusted else 0,
            ))
        except Exception:
            continue

    if len(normalized) < max(1, int(minimum_rows)):
        raise ValueError(
            f"Grouped response for {trading_date} contained only "
            f"{len(normalized)} valid rows; expected at least {minimum_rows}."
        )

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany(
            """
            INSERT INTO ohlcv_bars
                (ticker, timeframe, timestamp, open, high, low, close, volume,
                 vwap, transactions, provider, adjusted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, timeframe, timestamp) DO UPDATE SET
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                close=excluded.close,
                volume=excluded.volume,
                vwap=excluded.vwap,
                transactions=excluded.transactions,
                provider=excluded.provider,
                adjusted=excluded.adjusted,
                fetched_at=datetime('now')
            """,
            normalized,
        )
        conn.execute(
            """
            INSERT INTO market_ingest_runs
                (trading_date, endpoint, status, rows_received, rows_stored,
                 request_id, attempt_count, started_at, completed_at, error, updated_at)
            VALUES (?, 'grouped_daily', 'SUCCESS', ?, ?, ?, 1,
                    datetime('now'), datetime('now'), NULL, datetime('now'))
            ON CONFLICT(trading_date) DO UPDATE SET
                endpoint='grouped_daily',
                status='SUCCESS',
                rows_received=excluded.rows_received,
                rows_stored=excluded.rows_stored,
                request_id=excluded.request_id,
                completed_at=datetime('now'),
                error=NULL,
                updated_at=datetime('now')
            """,
            (trading_date, len(input_rows), len(normalized), request_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "trading_date": trading_date,
        "rows_received": len(input_rows),
        "rows_stored": len(normalized),
        "request_id": request_id,
    }


def upsert_market_symbols(
    symbols: Iterable[dict[str, Any]],
    *,
    source: str = "polygon_reference",
) -> int:
    rows: list[tuple[Any, ...]] = []
    for item in symbols or []:
        ticker = str(item.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        rows.append((
            ticker,
            item.get("name"),
            item.get("market"),
            item.get("locale"),
            item.get("primary_exchange"),
            item.get("type"),
            1 if item.get("active") else 0,
            item.get("currency_name"),
            str(item.get("cik")) if item.get("cik") is not None else None,
            item.get("composite_figi"),
            item.get("share_class_figi"),
            item.get("last_updated_utc"),
            source,
        ))
    if not rows:
        return 0
    conn = get_connection()
    try:
        conn.executemany(
            """
            INSERT INTO market_symbols
                (ticker, name, market, locale, primary_exchange, type, active,
                 currency_name, cik, composite_figi, share_class_figi,
                 last_updated_utc, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                name=excluded.name,
                market=excluded.market,
                locale=excluded.locale,
                primary_exchange=excluded.primary_exchange,
                type=excluded.type,
                active=excluded.active,
                currency_name=excluded.currency_name,
                cik=excluded.cik,
                composite_figi=excluded.composite_figi,
                share_class_figi=excluded.share_class_figi,
                last_updated_utc=excluded.last_updated_utc,
                source=excluded.source,
                fetched_at=datetime('now')
            """,
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def get_market_symbol(ticker: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM market_symbols WHERE ticker = ? COLLATE NOCASE",
            (str(ticker).upper().strip(),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def prune_ohlcv_before(cutoff_date: str) -> int:
    cutoff = str(cutoff_date)
    conn = get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM ohlcv_bars WHERE timeframe = '1d' AND substr(timestamp, 1, 10) < ?",
            (cutoff,),
        )
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        conn.close()


def get_market_cache_status() -> dict[str, Any]:
    conn = get_connection()
    try:
        bars = conn.execute(
            """
            SELECT COUNT(*) AS rows,
                   COUNT(DISTINCT ticker) AS tickers,
                   COUNT(DISTINCT substr(timestamp, 1, 10)) AS dates,
                   MIN(substr(timestamp, 1, 10)) AS first_date,
                   MAX(substr(timestamp, 1, 10)) AS last_date
              FROM ohlcv_bars
             WHERE timeframe = '1d'
            """
        ).fetchone()
        runs = conn.execute(
            """
            SELECT COUNT(*) AS successful_dates,
                   MAX(trading_date) AS latest_success,
                   SUM(rows_stored) AS grouped_rows_stored
              FROM market_ingest_runs
             WHERE status = 'SUCCESS'
            """
        ).fetchone()
        last_run = conn.execute(
            """
            SELECT trading_date, status, rows_received, rows_stored,
                   completed_at, error
              FROM market_ingest_runs
             ORDER BY updated_at DESC
             LIMIT 1
            """
        ).fetchone()
        symbol_count = conn.execute(
            "SELECT COUNT(*) FROM market_symbols"
        ).fetchone()[0]
        return {
            "database_path": DB_PATH,
            "database_size_bytes": get_ohlcv_cache_size_bytes(),
            "ohlcv_rows": int(bars["rows"] or 0),
            "distinct_tickers": int(bars["tickers"] or 0),
            "distinct_dates": int(bars["dates"] or 0),
            "first_date": bars["first_date"],
            "last_date": bars["last_date"],
            "successful_grouped_dates": int(runs["successful_dates"] or 0),
            "latest_grouped_date": runs["latest_success"],
            "grouped_rows_stored": int(runs["grouped_rows_stored"] or 0),
            "reference_symbols": int(symbol_count or 0),
            "last_run": dict(last_run) if last_run else None,
        }
    finally:
        conn.close()


def store_pattern_events(
    ticker: str,
    timeframe: str,
    patterns: Iterable[dict[str, Any]],
) -> list[int]:

    ticker = ticker.upper().strip()
    timeframe = timeframe or "1d"
    ids: list[int] = []
    conn = get_connection()

    for p in patterns or []:
        try:
            timestamp = _iso_timestamp(p.get("timestamp"))
            if not timestamp:
                continue
            context = p.get("context") or {}
            zone_low = _maybe_float(p.get("zone_low"))
            zone_high = _maybe_float(p.get("zone_high"))

            conn.execute("""
                INSERT OR IGNORE INTO pattern_events
                (ticker, timeframe, timestamp, pattern_name, pattern_family, direction,
                 confidence, zone_low, zone_high, trigger_price, candle_index, context_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ticker,
                timeframe,
                timestamp,
                str(p.get("pattern_name", "UNKNOWN")),
                str(p.get("pattern_family", "UNKNOWN")),
                str(p.get("direction", "NEUTRAL")),
                _maybe_float(p.get("confidence")) or 0,
                zone_low,
                zone_high,
                _maybe_float(p.get("trigger_price")),
                p.get("candle_index"),
                json.dumps(context, default=str),
            ))

            row = conn.execute("""
                SELECT id FROM pattern_events
                WHERE ticker=? AND timeframe=? AND timestamp=? AND pattern_name=?
                  AND COALESCE(zone_low, -999999999) = COALESCE(?, -999999999)
                  AND COALESCE(zone_high, -999999999) = COALESCE(?, -999999999)
                ORDER BY id DESC LIMIT 1
            """, (
                ticker,
                timeframe,
                timestamp,
                str(p.get("pattern_name", "UNKNOWN")),
                zone_low,
                zone_high,
            )).fetchone()
            if row:
                ids.append(int(row["id"]))
        except Exception:
            continue

    conn.commit()
    conn.close()
    return ids


def evaluate_and_store_pattern_outcomes(
    ticker: str,
    timeframe: str,
    hist: pd.DataFrame,
    patterns: Iterable[dict[str, Any]],
    horizons: tuple[int, ...] = (1, 3, 5, 10, 20),
) -> int:


    if hist is None or hist.empty:
        return 0

    event_ids = store_pattern_events(ticker, timeframe, patterns)
    if not event_ids:
        return 0

    conn = get_connection()
    event_rows = conn.execute("""
        SELECT id, timestamp, pattern_name, direction, trigger_price, zone_low, zone_high, candle_index
        FROM pattern_events
        WHERE ticker=? AND timeframe=?
    """, (ticker.upper().strip(), timeframe)).fetchall()
    event_by_key = {(r["timestamp"], r["pattern_name"],
                     r["candle_index"]): r for r in event_rows}

    close = hist["Close"].astype(float).reset_index(drop=True)
    high = hist["High"].astype(float).reset_index(drop=True)
    low = hist["Low"].astype(float).reset_index(drop=True)
    count = 0

    for p in patterns or []:
        try:
            candle_index = p.get("candle_index")
            if candle_index is None:
                continue
            candle_index = int(candle_index)
            timestamp = _iso_timestamp(p.get("timestamp"))
            key = (timestamp, str(p.get("pattern_name", "UNKNOWN")), candle_index)
            event = event_by_key.get(key)
            if not event:
                continue

            entry = _maybe_float(event["trigger_price"]) or float(
                close.iloc[candle_index])
            direction = event["direction"] or "NEUTRAL"
            if entry <= 0 or direction == "NEUTRAL":
                continue

            for horizon in horizons:
                end_idx = candle_index + horizon
                if end_idx >= len(close):
                    continue

                future_close = float(close.iloc[end_idx])
                path_high = float(
                    high.iloc[candle_index + 1:end_idx + 1].max())
                path_low = float(low.iloc[candle_index + 1:end_idx + 1].min())

                if direction == "BULLISH":
                    fwd = (future_close - entry) / entry * 100
                    mfe = (path_high - entry) / entry * 100
                    mae = (path_low - entry) / entry * 100
                    target_hit = 1 if mfe >= 2.0 else 0
                    stop_hit = 1 if mae <= -1.0 else 0
                else:
                    fwd = (entry - future_close) / entry * 100
                    mfe = (entry - path_low) / entry * 100
                    mae = (entry - path_high) / entry * 100
                    target_hit = 1 if mfe >= 2.0 else 0
                    stop_hit = 1 if mae <= -1.0 else 0

                if fwd >= 1.0:
                    outcome = "WIN"
                elif fwd <= -1.0:
                    outcome = "LOSS"
                else:
                    outcome = "NEUTRAL"

                conn.execute("""
                    INSERT INTO pattern_outcomes
                    (pattern_event_id, horizon_days, forward_return_pct,
                     max_favorable_excursion_pct, max_adverse_excursion_pct,
                     target_hit, stop_hit, outcome)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(pattern_event_id, horizon_days) DO UPDATE SET
                        forward_return_pct=excluded.forward_return_pct,
                        max_favorable_excursion_pct=excluded.max_favorable_excursion_pct,
                        max_adverse_excursion_pct=excluded.max_adverse_excursion_pct,
                        target_hit=excluded.target_hit,
                        stop_hit=excluded.stop_hit,
                        outcome=excluded.outcome,
                        evaluated_at=datetime('now')
                """, (
                    int(event["id"]),
                    horizon,
                    round(float(fwd), 4),
                    round(float(mfe), 4),
                    round(float(mae), 4),
                    target_hit,
                    stop_hit,
                    outcome,
                ))
                count += 1
        except Exception:
            continue

    conn.commit()
    conn.close()
    rebuild_pattern_stats(ticker=ticker, timeframe=timeframe)
    return count


def rebuild_pattern_stats(ticker: str | None = None, timeframe: str = "1d") -> int:
    conn = get_connection()
    params: list[Any] = [timeframe]
    where = "pe.timeframe = ?"
    if ticker:
        where += " AND pe.ticker = ?"
        params.append(ticker.upper().strip())

    rows = conn.execute(f"""
        SELECT
            pe.pattern_name,
            pe.ticker,
            pe.timeframe,
            po.horizon_days,
            po.forward_return_pct,
            po.max_favorable_excursion_pct,
            po.max_adverse_excursion_pct,
            po.outcome
        FROM pattern_events pe
        JOIN pattern_outcomes po ON po.pattern_event_id = pe.id
        WHERE {where}
    """, params).fetchall()

    grouped: dict[tuple[str, str | None, str], list[sqlite3.Row]] = {}
    for r in rows:
        stats_ticker = r["ticker"] if ticker else None
        grouped.setdefault(
            (r["pattern_name"], stats_ticker, r["timeframe"]), []).append(r)

    saved = 0
    for (pattern_name, stats_ticker, tf), group in grouped.items():
        sample_events = len(
            {(pattern_name, g["ticker"], g["timeframe"]) for g in group})
        by_h = {h: [g for g in group if int(
            g["horizon_days"]) == h] for h in (1, 3, 5, 10, 20)}

        def win_rate(h: int) -> float | None:
            vals = by_h.get(h) or []
            if not vals:
                return None
            wins = sum(1 for x in vals if x["outcome"] == "WIN")
            return round(wins / len(vals) * 100, 2)

        def avg_ret(h: int) -> float | None:
            vals = [x["forward_return_pct"] for x in by_h.get(
                h, []) if x["forward_return_pct"] is not None]
            if not vals:
                return None
            return round(sum(vals) / len(vals), 4)

        all_fwd = [g["forward_return_pct"]
                   for g in group if g["forward_return_pct"] is not None]
        all_mfe = [g["max_favorable_excursion_pct"]
                   for g in group if g["max_favorable_excursion_pct"] is not None]
        all_mae = [g["max_adverse_excursion_pct"]
                   for g in group if g["max_adverse_excursion_pct"] is not None]
        expectancy = round(sum(all_fwd) / len(all_fwd), 4) if all_fwd else None

        sample_size = len(by_h.get(5) or group)

        conn.execute("""
            INSERT INTO pattern_stats
            (pattern_name, ticker, timeframe, market_regime, sample_size,
             win_rate_1d, win_rate_3d, win_rate_5d, win_rate_10d, win_rate_20d,
             avg_return_1d, avg_return_3d, avg_return_5d, avg_return_10d, avg_return_20d,
             avg_mfe, avg_mae, expectancy, updated_at)
            VALUES (?, ?, ?, 'ALL', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(pattern_name, ticker, timeframe, market_regime) DO UPDATE SET
                sample_size=excluded.sample_size,
                win_rate_1d=excluded.win_rate_1d,
                win_rate_3d=excluded.win_rate_3d,
                win_rate_5d=excluded.win_rate_5d,
                win_rate_10d=excluded.win_rate_10d,
                win_rate_20d=excluded.win_rate_20d,
                avg_return_1d=excluded.avg_return_1d,
                avg_return_3d=excluded.avg_return_3d,
                avg_return_5d=excluded.avg_return_5d,
                avg_return_10d=excluded.avg_return_10d,
                avg_return_20d=excluded.avg_return_20d,
                avg_mfe=excluded.avg_mfe,
                avg_mae=excluded.avg_mae,
                expectancy=excluded.expectancy,
                updated_at=datetime('now')
        """, (
            pattern_name,
            stats_ticker,
            tf,
            sample_size,
            win_rate(1), win_rate(3), win_rate(5), win_rate(10), win_rate(20),
            avg_ret(1), avg_ret(3), avg_ret(5), avg_ret(10), avg_ret(20),
            round(sum(all_mfe) / len(all_mfe), 4) if all_mfe else None,
            round(sum(all_mae) / len(all_mae), 4) if all_mae else None,
            expectancy,
        ))
        saved += 1

    conn.commit()
    conn.close()
    return saved


def get_pattern_stats(
    pattern_name: str | None = None,
    ticker: str | None = None,
    timeframe: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    conn = get_connection()
    where = []
    params: list[Any] = []
    if pattern_name:
        where.append("pattern_name = ?")
        params.append(pattern_name)
    if ticker:
        where.append("ticker = ?")
        params.append(ticker.upper().strip())
    if timeframe:
        where.append("timeframe = ?")
        params.append(timeframe)
    sql = "SELECT * FROM pattern_stats"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY sample_size DESC, ABS(COALESCE(expectancy, 0)) DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_pattern_events(ticker: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    conn = get_connection()
    if ticker:
        rows = conn.execute("""
            SELECT * FROM pattern_events
            WHERE ticker = ?
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
        """, (ticker.upper().strip(), limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM pattern_events
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
        """, (limit,)).fetchall()
    conn.close()
    out = []
    for r in rows:
        item = dict(r)
        try:
            item["context"] = json.loads(item.pop("context_json") or "{}")
        except Exception:
            item["context"] = {}
        out.append(item)
    return out


def _iso_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return pd.Timestamp(value).isoformat()
    except Exception:
        return str(value)


def _maybe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def store_vai_training_run(status: str, samples: int = 0, horizon_days: int = 10, threshold: float | None = None, validation: dict[str, Any] | None = None, terminal_output: str = "") -> int:
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO vai_training_runs (status, samples, horizon_days, threshold, validation_json, terminal_output)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(status), int(samples or 0), int(horizon_days or 10),
             threshold, json.dumps(validation or {}), terminal_output or ""),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_recent_vai_training_runs(limit: int = 10) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM vai_training_runs ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            try:
                item["validation"] = json.loads(
                    item.pop("validation_json") or "{}")
            except Exception:
                item["validation"] = {}
            out.append(item)
        return out
    finally:
        conn.close()
