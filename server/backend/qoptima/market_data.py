from __future__ import annotations

"""Canonical market-price selection for QOptima research.

The research contract prefers explicitly adjusted / total-return-compatible price
series.  A raw ``Close`` series may still be used for live exploratory scans when
strict mode is disabled, but it is labeled unverified and must not be described as
a dividend-corrected backtest input.
"""

from typing import Any
import pandas as pd

_ADJUSTED_BASES = {"adjusted", "adjusted_close", "adjusted_or_total_return", "total_return"}


def _basis_from_history(repo, symbol: str, history: pd.DataFrame | None) -> str:
    attrs = dict(getattr(history, "attrs", {}) or {})
    basis = str(attrs.get("price_basis") or "").strip().lower()
    if basis in _ADJUSTED_BASES:
        return "total_return" if basis == "total_return" else "adjusted_close"
    if attrs.get("all_adjusted") is True:
        return "adjusted_close"
    try:
        summary = repo.cache_summary(symbol)
    except Exception:
        summary = {}
    if summary.get("all_adjusted") is True:
        return "adjusted_close"
    return "unverified_close"


def _load_explicit_adjusted(repo, symbol: str) -> pd.DataFrame | None:
    loader = getattr(repo, "load_adjusted", None)
    if not callable(loader):
        return None
    try:
        frame = loader(symbol, period="all")
    except TypeError:
        try:
            frame = loader(symbol)
        except Exception:
            return None
    except Exception:
        return None
    if frame is None or frame.empty or "Close" not in frame:
        return None
    return frame


def canonical_price_series(
    repo,
    symbol: str,
    history: pd.DataFrame,
    *,
    minimum_bars: int = 63,
    require_adjusted: bool = False,
) -> tuple[pd.Series, dict[str, Any]]:
    """Return the best price series plus auditable adjustment provenance.

    Priority:
    1. repository's explicitly adjusted history,
    2. an explicit ``Adj Close`` column if present,
    3. repository history explicitly marked adjusted/total-return,
    4. raw Close only when ``require_adjusted`` is false.

    Adjusted close is treated as split/dividend adjusted only to the extent the
    provider/repository says so.  Delisting-return handling is *not* inferred.
    Missing future observations remain separately visible to the walk-forward
    engine rather than being backfilled with future knowledge.
    """
    symbol = str(symbol).upper().strip()
    minimum = max(3, int(minimum_bars))
    explicit = _load_explicit_adjusted(repo, symbol)
    if explicit is not None and int(explicit["Close"].notna().sum()) >= minimum:
        series = explicit["Close"].astype(float).rename(symbol)
        return series, {
            "price_basis": "adjusted_close",
            "verified_adjusted": True,
            "source": "repository_explicit_adjusted",
            "bars": int(series.notna().sum()),
            "dividend_split_handling": "provider/repository adjusted close",
            "delisting_return_handling": "not inferred; depends on source coverage",
        }

    if history is not None and not history.empty and "Adj Close" in history:
        series = history["Adj Close"].astype(float).rename(symbol)
        if int(series.notna().sum()) >= minimum:
            return series, {
                "price_basis": "adjusted_close",
                "verified_adjusted": True,
                "source": "explicit_adj_close_column",
                "bars": int(series.notna().sum()),
                "dividend_split_handling": "provider Adj Close column",
                "delisting_return_handling": "not inferred; depends on source coverage",
            }

    basis = _basis_from_history(repo, symbol, history)
    if history is not None and not history.empty and "Close" in history and basis in {"adjusted_close", "total_return"}:
        series = history["Close"].astype(float).rename(symbol)
        if int(series.notna().sum()) >= minimum:
            return series, {
                "price_basis": basis,
                "verified_adjusted": True,
                "source": "history_attrs_or_cache_metadata",
                "bars": int(series.notna().sum()),
                "dividend_split_handling": "provider/repository marks series adjusted/total-return compatible",
                "delisting_return_handling": "not inferred; depends on source coverage",
            }

    if require_adjusted:
        available = int(history["Close"].notna().sum()) if history is not None and "Close" in history else 0
        raise ValueError(
            f"{symbol} does not have at least {minimum} bars of explicitly adjusted/total-return history "
            f"(raw/unknown Close bars available: {available}). QOptima causal backtests default to verified adjusted prices."
        )

    if history is None or history.empty or "Close" not in history:
        raise ValueError(f"No usable Close history for {symbol}")
    series = history["Close"].astype(float).rename(symbol)
    return series, {
        "price_basis": "unverified_close",
        "verified_adjusted": False,
        "source": "raw_close_fallback",
        "bars": int(series.notna().sum()),
        "dividend_split_handling": "UNVERIFIED",
        "delisting_return_handling": "UNVERIFIED",
        "warning": "Raw/unknown Close was used because strict adjusted-price mode was disabled.",
    }
