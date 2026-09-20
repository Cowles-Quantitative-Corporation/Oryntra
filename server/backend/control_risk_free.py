"""Interactive Oryntra Control risk-free series helpers.

Frozen confirmatory studies still consume explicit risk-free CSVs.  This module
exists only for convenience/current-data Control studies, where a time-varying
historical cash-rate proxy is preferable to silently applying one constant rate
to an entire decade.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from io import StringIO
import os
from pathlib import Path
import time
from typing import Any

import numpy as np
import pandas as pd
import requests

FRED_DGS3MO_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS3MO"
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

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def _constant(index: pd.DatetimeIndex, annual_pct: float) -> pd.Series:
    annual = float(annual_pct) / 100.0
    if not np.isfinite(annual) or annual <= -0.99 or annual > 2.0:
        raise ValueError("Risk-free annual percent must be finite and between -99% and 200%")
    daily = (1.0 + annual) ** (1.0 / 252.0) - 1.0
    return pd.Series(daily, index=index, dtype=float, name="risk_free")


def _parse_fred(text: str) -> pd.Series:
    frame = pd.read_csv(StringIO(text))
    date_col = "DATE" if "DATE" in frame.columns else "observation_date" if "observation_date" in frame.columns else None
    if date_col is None or "DGS3MO" not in frame.columns:
        raise ValueError("FRED DGS3MO response is missing expected columns")
    dates = pd.to_datetime(frame[date_col], errors="coerce")
    values = pd.to_numeric(frame["DGS3MO"], errors="coerce")
    series = pd.Series(values.to_numpy(dtype=float), index=pd.DatetimeIndex(dates), name="DGS3MO")
    series = series[series.index.notna() & np.isfinite(series.to_numpy(dtype=float))]
    if series.empty:
        raise ValueError("FRED DGS3MO returned no usable observations")
    idx = pd.DatetimeIndex(series.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    series.index = idx.normalize()
    return series[~series.index.duplicated(keep="last")].sort_index()


def _load_fred(*, force_refresh: bool = False) -> tuple[pd.Series, Path, bool]:
    path = _cache_root() / "fred_dgs3mo.csv"
    fresh = path.exists() and (time.time() - path.stat().st_mtime) <= _CACHE_MAX_AGE_SECONDS
    fetched = False
    if force_refresh or not fresh:
        try:
            response = requests.get(
                FRED_DGS3MO_URL,
                timeout=30,
                headers={"User-Agent": "Oryntra-Control/5.1 CQC research"},
            )
            response.raise_for_status()
            parsed = _parse_fred(response.text)
            # Store normalized source observations, not derived daily returns.
            pd.DataFrame({"DATE": parsed.index.strftime("%Y-%m-%d"), "DGS3MO": parsed.to_numpy()}).to_csv(path, index=False)
            fetched = True
            return parsed, path, fetched
        except Exception:
            if not path.exists():
                raise ValueError(
                    "Historical Treasury risk-free data could not be loaded from FRED and no local cache exists. "
                    "Choose Manual constant or Zero risk-free mode for this convenience backtest."
                )
    parsed = _parse_fred(path.read_text())
    return parsed, path, fetched


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

    annual_yield_pct, path, fetched = _load_fred(force_refresh=force_refresh)
    prior_yield_pct = _strict_prior_values(annual_yield_pct, index)
    annual_decimal = prior_yield_pct / 100.0
    if (annual_decimal <= -0.99).any() or not np.isfinite(annual_decimal).all():
        raise ValueError("Historical Treasury proxy contains an invalid annual yield")
    daily = np.power(1.0 + annual_decimal, 1.0 / 252.0) - 1.0
    series = pd.Series(daily, index=index, dtype=float, name="risk_free")
    metadata = RiskFreeMetadata(
        mode="fred_dgs3mo",
        source="FRED DGS3MO 3-month Treasury constant maturity rate",
        observations=int(len(annual_yield_pct)),
        cache_path=str(path),
        fetched=fetched,
        lag_rule="latest DGS3MO observation strictly before each trading session",
    ).payload()
    return series, metadata
