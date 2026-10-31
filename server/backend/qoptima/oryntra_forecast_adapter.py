from __future__ import annotations

"""Adapters from Oryntra model outputs to QOptima's point-in-time forecast contract.

The adapter deliberately accepts only calibrated *predicted_return* outputs.  It
will not convert generic rank/signal scores into expected returns, because doing
so would silently change units and invalidate QRS/forecast-error calculations.
"""

from typing import Any, Iterable
import numpy as np
import pandas as pd


def records_from_predicted_return_panel(
    predicted_return: pd.DataFrame,
    *,
    model_id: str,
    model_version: str,
    horizon_sessions: int,
    ready: pd.DataFrame | None = None,
    decision_dates: Iterable[Any] | None = None,
    information_lag_sessions: int = 0,
    metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Convert a causal Oryntra predicted-return panel into ForecastRecord mappings.

    Each row is treated as a forecast created at that session using information
    through that same session unless ``information_lag_sessions`` explicitly
    moves the cutoff backward.  The values remain *period_return* because Alpha
    V1/V1.2 predicted returns are calibrated to their configured horizon rather
    than annualized units.
    """
    if not isinstance(predicted_return, pd.DataFrame) or predicted_return.empty:
        return []
    if not isinstance(predicted_return.index, pd.DatetimeIndex):
        raise ValueError("Oryntra predicted-return panel requires a DatetimeIndex")
    if predicted_return.index.has_duplicates or not predicted_return.index.is_monotonic_increasing:
        raise ValueError("Oryntra predicted-return panel requires unique increasing timestamps")
    if predicted_return.columns.has_duplicates:
        raise ValueError("Oryntra predicted-return panel requires unique symbols")
    if not str(model_id).strip() or not str(model_version).strip():
        raise ValueError("Oryntra forecast adapter requires model_id and model_version")
    horizon = int(horizon_sessions)
    if horizon <= 0:
        raise ValueError("Oryntra forecast horizon must be positive")
    lag = max(0, int(information_lag_sessions))

    allowed_dates = None
    if decision_dates is not None:
        allowed_dates = {pd.Timestamp(x).tz_localize(None) if pd.Timestamp(x).tzinfo else pd.Timestamp(x) for x in decision_dates}
    if ready is not None:
        if not (isinstance(ready, pd.DataFrame) and ready.index.equals(predicted_return.index) and ready.columns.equals(predicted_return.columns)):
            raise ValueError("Oryntra ready panel must exactly align with predicted_return")

    records: list[dict[str, Any]] = []
    index = predicted_return.index
    for row_i, timestamp in enumerate(index):
        ts = pd.Timestamp(timestamp)
        ts_naive = ts.tz_localize(None) if ts.tzinfo else ts
        if allowed_dates is not None and ts_naive not in allowed_dates:
            continue
        cutoff_i = row_i - lag
        if cutoff_i < 0:
            continue
        cutoff = pd.Timestamp(index[cutoff_i])
        values: dict[str, float] = {}
        for symbol in predicted_return.columns:
            value = predicted_return.at[timestamp, symbol]
            if ready is not None and not bool(ready.at[timestamp, symbol]):
                continue
            if pd.notna(value) and np.isfinite(float(value)):
                values[str(symbol).upper().strip()] = float(value)
        if not values:
            continue
        records.append({
            "model_id": str(model_id).strip(),
            "model_version": str(model_version).strip(),
            "created_at": ts.isoformat(),
            "information_cutoff": cutoff.isoformat(),
            "horizon_sessions": horizon,
            "units": "period_return",
            "values": values,
            "metadata": {
                "source": "oryntra_calibrated_predicted_return_panel",
                "information_lag_sessions": lag,
                **dict(metadata or {}),
            },
        })
    return records


def attach_realized_outcomes_from_prices(
    records: Iterable[dict[str, Any]],
    prices: pd.DataFrame,
) -> list[dict[str, Any]]:
    """Attach realized horizon returns only after their completion timestamp.

    This helper is for research-ledger construction.  ``realized_at`` is the
    actual horizon-end session, allowing QOptima to exclude the outcome from any
    earlier historical decision.
    """
    if not isinstance(prices, pd.DataFrame) or prices.empty or not isinstance(prices.index, pd.DatetimeIndex):
        raise ValueError("realized-outcome attachment requires a dated price panel")
    if prices.index.has_duplicates or not prices.index.is_monotonic_increasing:
        raise ValueError("price panel must have unique increasing dates")
    out: list[dict[str, Any]] = []
    for raw in records:
        row = dict(raw)
        created = pd.Timestamp(row.get("created_at"))
        if created.tzinfo is not None:
            created_cmp = created.tz_convert(None)
        else:
            created_cmp = created
        loc = prices.index.searchsorted(created_cmp, side="right") - 1
        horizon = int(row.get("horizon_sessions") or 0)
        end = loc + horizon
        symbols = list((row.get("values") or {}).keys())
        if loc >= 0 and horizon > 0 and end < len(prices.index):
            start_px = prices.iloc[loc].reindex(symbols).astype(float)
            end_px = prices.iloc[end].reindex(symbols).astype(float)
            valid = np.isfinite(start_px.to_numpy()) & np.isfinite(end_px.to_numpy()) & (start_px.to_numpy() > 0)
            if valid.all():
                realized = end_px.div(start_px).sub(1.0)
                row["realized_outcome"] = {str(k).upper(): float(v) for k, v in realized.items()}
                row["realized_at"] = pd.Timestamp(prices.index[end]).isoformat()
        out.append(row)
    return out
