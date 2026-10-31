"""Interactive Oryntra Control risk-free series helpers.

Frozen confirmatory studies still consume explicit risk-free CSVs. This module
exists only for convenience/current-data Control studies, where a time-varying
historical cash-rate proxy is preferable to silently applying one constant rate
to an entire decade.

Control's historical Treasury mode is deliberately redundant:
1. FRED DGS3MO (preferred)
2. Federal Reserve Board H.15 3-month constant-maturity series
3. Yahoo ^IRX 13-week Treasury-bill yield as a clearly-labelled convenience
   fallback when both official CSV endpoints are unreachable.

Every source is lagged strictly: a trading session may only use a rate observed
strictly before that session date.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from io import StringIO
import csv
import json
import os
from pathlib import Path
import time
from typing import Any

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    import yfinance as yf
except Exception:  # pragma: no cover - optional runtime fallback
    yf = None

FRED_DGS3MO_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS3MO"
FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"
FED_H15_URL = (
    "https://www.federalreserve.gov/datadownload/Output.aspx?"
    "filetype=csv&from=&label=include&lastobs=&layout=seriescolumn&rel=H15&"
    "series=bf17364827e38702b42a58cf8eaa3f78&to=&type=package"
)
FED_H15_SERIES = "RIFLGFCM03_N.B"
YAHOO_TBILL_TICKER = "^IRX"
_CACHE_MAX_AGE_SECONDS = 24 * 60 * 60


def _cache_root() -> Path:
    configured = os.environ.get("ORYNTRA_CONTROL_DATA_DIR")
    root = Path(configured).expanduser() if configured else Path.home() / "Library" / "Application Support" / "Oryntra Control"
    path = root / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class RiskFreeMetadata:
    mode: str
    source: str
    annual_pct: float | None = None
    observations: int | None = None
    cache_path: str | None = None
    fetched: bool = False
    lag_rule: str | None = None
    fallback_used: bool = False
    source_chain: list[str] | None = None
    fred_api_key_configured: bool = False
    authenticated_fred: bool = False

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def _constant(index: pd.DatetimeIndex, annual_pct: float) -> pd.Series:
    annual = float(annual_pct) / 100.0
    if not np.isfinite(annual) or annual <= -0.99 or annual > 2.0:
        raise ValueError("Risk-free annual percent must be finite and between -99% and 200%")
    daily = (1.0 + annual) ** (1.0 / 252.0) - 1.0
    return pd.Series(daily, index=index, dtype=float, name="risk_free")


def _normalize_yield_series(dates: Any, values: Any, *, name: str = "DGS3MO") -> pd.Series:
    parsed_dates = pd.to_datetime(dates, errors="coerce")
    parsed_values = pd.to_numeric(values, errors="coerce")
    series = pd.Series(np.asarray(parsed_values, dtype=float), index=pd.DatetimeIndex(parsed_dates), name=name)
    series = series[series.index.notna() & np.isfinite(series.to_numpy(dtype=float))]
    if series.empty:
        raise ValueError("Historical Treasury source returned no usable observations")
    idx = pd.DatetimeIndex(series.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    series.index = idx.normalize()
    return series[~series.index.duplicated(keep="last")].sort_index()


def _parse_fred(text: str) -> pd.Series:
    frame = pd.read_csv(StringIO(text))
    date_col = "DATE" if "DATE" in frame.columns else "observation_date" if "observation_date" in frame.columns else None
    if date_col is None or "DGS3MO" not in frame.columns:
        raise ValueError("FRED DGS3MO response is missing expected columns")
    return _normalize_yield_series(frame[date_col], frame["DGS3MO"], name="DGS3MO")




def _parse_fred_api_json(payload: dict[str, Any]) -> pd.Series:
    rows = payload.get("observations") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("FRED API DGS3MO response has no observations")
    dates = [row.get("date") for row in rows]
    values = [row.get("value") for row in rows]
    return _normalize_yield_series(dates, values, name="DGS3MO")

def _is_fred_source(source: str) -> bool:
    return str(source or "").startswith("FRED")

def _is_authenticated_fred_source(source: str) -> bool:
    return str(source or "").startswith("FRED API DGS3MO (authenticated)")

def _parse_h15(text: str) -> pd.Series:
    """Parse the Federal Reserve Board DDP CSV in a format-tolerant way."""
    rows = list(csv.reader(StringIO(text)))
    if not rows:
        raise ValueError("Federal Reserve H.15 response was empty")

    # The DDP package contains metadata rows before a row beginning with
    # "Time Period" followed by mnemonic columns.
    header_i = None
    for i, row in enumerate(rows):
        if row and str(row[0]).strip().lower() == "time period":
            header_i = i
            break
    if header_i is None:
        # Some exports may already be a plain table.
        try:
            frame = pd.read_csv(StringIO(text))
        except Exception as exc:  # pragma: no cover - defensive
            raise ValueError("Federal Reserve H.15 response has no Time Period header") from exc
    else:
        frame = pd.DataFrame(rows[header_i + 1 :], columns=rows[header_i])

    cols = {str(c).strip(): c for c in frame.columns}
    date_col = cols.get("Time Period") or cols.get("DATE") or cols.get("Date")
    value_col = cols.get(FED_H15_SERIES)
    if date_col is None or value_col is None:
        raise ValueError("Federal Reserve H.15 response is missing the 3-month constant-maturity series")
    return _normalize_yield_series(frame[date_col], frame[value_col], name="DGS3MO")


def _http_session() -> requests.Session:
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        status=2,
        backoff_factor=0.35,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({
        "User-Agent": "Oryntra-Control/5.4.13 CQC research; historical Treasury proxy",
        "Accept": "text/csv,text/plain,*/*",
    })
    return session


def _fetch_fred(api_key: str | None = None) -> pd.Series:
    with _http_session() as session:
        if api_key:
            response = session.get(
                FRED_API_URL,
                params={"series_id": "DGS3MO", "api_key": api_key, "file_type": "json", "observation_start": "1950-01-01"},
                timeout=30,
            )
            response.raise_for_status()
            return _parse_fred_api_json(response.json())
        response = session.get(FRED_DGS3MO_URL, timeout=30)
        response.raise_for_status()
        return _parse_fred(response.text)


def _fetch_h15() -> pd.Series:
    with _http_session() as session:
        response = session.get(FED_H15_URL, timeout=30)
        response.raise_for_status()
        return _parse_h15(response.text)


def _fetch_yahoo_irx() -> pd.Series:
    if yf is None:
        raise ValueError("yfinance is unavailable for the Treasury fallback")
    ticker = yf.Ticker(YAHOO_TBILL_TICKER)
    try:
        frame = ticker.history(period="max", auto_adjust=False, timeout=25)
    except TypeError:
        frame = ticker.history(period="max", auto_adjust=False)
    if frame is None or frame.empty or "Close" not in frame:
        raise ValueError("Yahoo ^IRX returned no usable history")
    # ^IRX is quoted as an annualized percentage yield, e.g. 4.12 == 4.12%.
    return _normalize_yield_series(frame.index, frame["Close"], name="DGS3MO")


def _cache_meta_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".meta.json")


def _write_cache(path: Path, series: pd.Series, *, source: str, source_chain: list[str]) -> None:
    pd.DataFrame({"DATE": series.index.strftime("%Y-%m-%d"), "DGS3MO": series.to_numpy()}).to_csv(path, index=False)
    _cache_meta_path(path).write_text(json.dumps({"source": source, "source_chain": source_chain}, indent=2))


def _read_cache(path: Path) -> pd.Series:
    return _parse_fred(path.read_text())


def _cache_source(path: Path) -> str:
    meta = _cache_meta_path(path)
    if not meta.exists():
        return "cached historical Treasury series"
    try:
        return str(json.loads(meta.read_text()).get("source") or "cached historical Treasury series")
    except Exception:
        return "cached historical Treasury series"


def _load_fred(*, force_refresh: bool = False, fred_api_key: str | None = None) -> tuple[pd.Series, Path, bool]:
    """Load the historical Treasury proxy using a resilient source chain.

    Kept under the historical name for backwards-compatible tests/callers.
    The returned series carries source metadata in ``Series.attrs``.
    """
    path = _cache_root() / "fred_dgs3mo.csv"
    fresh = path.exists() and (time.time() - path.stat().st_mtime) <= _CACHE_MAX_AGE_SECONDS
    cached_source = _cache_source(path) if path.exists() else ""
    if fresh and not force_refresh and (not fred_api_key or _is_authenticated_fred_source(cached_source)):
        parsed = _read_cache(path)
        parsed.attrs["source"] = cached_source
        parsed.attrs["fallback_used"] = not _is_fred_source(cached_source)
        parsed.attrs["source_chain"] = ["cache"]
        parsed.attrs["authenticated_fred"] = bool(fred_api_key and _is_authenticated_fred_source(cached_source))
        return parsed, path, False

    attempts: list[str] = []
    if fresh and not force_refresh and fred_api_key and not _is_authenticated_fred_source(cached_source):
        try:
            parsed = _fetch_fred(fred_api_key)
            attempts.append("FRED API DGS3MO (authenticated): ok")
            _write_cache(path, parsed, source="FRED API DGS3MO (authenticated)", source_chain=attempts)
            parsed.attrs["source"] = "FRED API DGS3MO (authenticated)"
            parsed.attrs["fallback_used"] = False
            parsed.attrs["source_chain"] = attempts.copy()
            parsed.attrs["authenticated_fred"] = True
            return parsed, path, True
        except Exception as exc:
            attempts.append(f"FRED API DGS3MO (authenticated): {type(exc).__name__}: {exc}")
            parsed = _read_cache(path)
            parsed.attrs["source"] = cached_source
            parsed.attrs["fallback_used"] = True
            parsed.attrs["source_chain"] = attempts + ["fresh fallback cache: used"]
            parsed.attrs["authenticated_fred"] = False
            return parsed, path, False

    sources = [
        (("FRED API DGS3MO (authenticated)" if fred_api_key else "FRED DGS3MO"), (lambda: _fetch_fred(fred_api_key)) if fred_api_key else _fetch_fred),
        ("Federal Reserve Board H.15 3-month constant maturity", _fetch_h15),
        ("Yahoo ^IRX 13-week Treasury-bill yield fallback", _fetch_yahoo_irx),
    ]
    for source_name, fetcher in sources:
        try:
            parsed = fetcher()
            attempts.append(f"{source_name}: ok")
            _write_cache(path, parsed, source=source_name, source_chain=attempts)
            parsed.attrs["source"] = source_name
            parsed.attrs["fallback_used"] = not _is_fred_source(source_name)
            parsed.attrs["source_chain"] = attempts.copy()
            parsed.attrs["authenticated_fred"] = bool(fred_api_key and _is_fred_source(source_name))
            return parsed, path, True
        except Exception as exc:
            attempts.append(f"{source_name}: {type(exc).__name__}: {exc}")

    # A stale cache is still preferable to silently substituting a constant.
    if path.exists():
        parsed = _read_cache(path)
        parsed.attrs["source"] = _cache_source(path)
        parsed.attrs["fallback_used"] = True
        parsed.attrs["source_chain"] = attempts + ["stale cache: used"]
        parsed.attrs["authenticated_fred"] = False
        return parsed, path, False

    details = " | ".join(attempts)
    raise ValueError(
        "Historical Treasury risk-free data could not be loaded from FRED, Federal Reserve H.15, "
        "or Yahoo ^IRX, and no local cache exists. "
        f"Source attempts: {details}. Choose Manual constant or Zero only if you intentionally want that assumption."
    )


def _strict_prior_values(source: pd.Series, index: pd.DatetimeIndex) -> np.ndarray:
    """Latest observation strictly before each target date (no same-day look-ahead)."""
    source = source.sort_index()
    src_dates = source.index.to_numpy(dtype="datetime64[ns]")
    target_dates = pd.DatetimeIndex(index).tz_localize(None).normalize().to_numpy(dtype="datetime64[ns]")
    positions = np.searchsorted(src_dates, target_dates, side="left") - 1
    if (positions < 0).any():
        first = pd.Timestamp(index[np.where(positions < 0)[0][0]])
        raise ValueError(f"Historical Treasury series has no observation available before {first.date()}")
    return source.to_numpy(dtype=float)[positions]


def build_control_risk_free(
    index: pd.DatetimeIndex,
    *,
    mode: str = "fred_dgs3mo",
    annual_pct: float = 0.0,
    force_refresh: bool = False,
    fred_api_key: str | None = None,
) -> tuple[pd.Series, dict[str, Any]]:
    mode = str(mode or "fred_dgs3mo").strip().lower()
    if mode in {"zero", "none"}:
        return _constant(index, 0.0), RiskFreeMetadata(mode="zero", source="zero_cash_rate", annual_pct=0.0).payload()
    if mode in {"manual", "manual_constant", "constant"}:
        return _constant(index, annual_pct), RiskFreeMetadata(
            mode="manual_constant", source="operator_constant", annual_pct=float(annual_pct)
        ).payload()
    if mode != "fred_dgs3mo":
        raise ValueError("Choose fred_dgs3mo, manual_constant, or zero as the risk-free mode")

    if fred_api_key:
        annual_yield_pct, path, fetched = _load_fred(force_refresh=force_refresh, fred_api_key=fred_api_key)
    else:
        annual_yield_pct, path, fetched = _load_fred(force_refresh=force_refresh)
    prior_yield_pct = _strict_prior_values(annual_yield_pct, index)
    annual_decimal = prior_yield_pct / 100.0
    if (annual_decimal <= -0.99).any() or not np.isfinite(annual_decimal).all():
        raise ValueError("Historical Treasury proxy contains an invalid annual yield")
    daily = np.power(1.0 + annual_decimal, 1.0 / 252.0) - 1.0
    series = pd.Series(daily, index=index, dtype=float, name="risk_free")
    source_name = str(annual_yield_pct.attrs.get("source") or "historical 3-month Treasury proxy")
    source_chain = list(annual_yield_pct.attrs.get("source_chain") or [])
    fallback_used = bool(annual_yield_pct.attrs.get("fallback_used", not _is_fred_source(source_name)))
    metadata = RiskFreeMetadata(
        mode="fred_dgs3mo",
        source=source_name,
        observations=int(len(annual_yield_pct)),
        cache_path=str(path),
        fetched=fetched,
        lag_rule="latest Treasury-proxy observation strictly before each trading session",
        fallback_used=fallback_used,
        source_chain=source_chain,
        fred_api_key_configured=bool(fred_api_key),
        authenticated_fred=bool(annual_yield_pct.attrs.get("authenticated_fred", False)),
    ).payload()
    return series, metadata
