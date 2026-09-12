"""Offline, availability-dated corporate panels for Minerva research."""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

import numpy as np
import pandas as pd


POSITIVE = {"revenue_growth_yoy", "gross_margin", "operating_margin", "net_income_margin", "free_cash_flow_margin", "earnings_surprise_pct", "guidance_revision_pct", "estimate_revision_pct", "insider_net_buy_pct"}
NEGATIVE = {"share_count_growth_yoy", "net_debt_to_ebitda"}


def build_quality_and_observed_panels(facts: Iterable[dict], sessions: pd.DatetimeIndex,
                                      symbols: Iterable[str], *, minimum_metrics: int = 2) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return cross-sectional quality ranks and an exact observed-data mask.

    Facts are usable only at their stated ``available_at`` session.  An issuer
    needs ``minimum_metrics`` current public facts before it is observed; zero
    score is therefore distinguishable from absent data through the companion
    boolean mask.
    """
    if not isinstance(sessions, pd.DatetimeIndex) or sessions.has_duplicates or not sessions.is_monotonic_increasing:
        raise ValueError("Corporate sessions must be unique and increasing")
    names = [str(symbol).upper().strip() for symbol in symbols]
    if not names or len(set(names)) != len(names) or not (1 <= minimum_metrics <= 9):
        raise ValueError("Corporate symbols or minimum metric count is invalid")
    scheduled: dict[pd.Timestamp, list[tuple[str, str, float]]] = defaultdict(list)
    for fact in facts:
        ticker, metric = str(fact.get("ticker") or "").upper().strip(), str(fact.get("metric") or "")
        if ticker not in names or metric not in POSITIVE | NEGATIVE:
            continue
        try:
            available = pd.Timestamp(fact["available_at"])
            value = float(fact["value"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Corporate fact requires finite available_at and value") from None
        if available.tzinfo is not None:
            available = available.tz_convert(None)
        available = available.normalize()
        if not np.isfinite(value):
            raise ValueError("Corporate fact value must be finite")
        scheduled[available].append((ticker, metric, value))
    raw = pd.DataFrame(np.nan, index=sessions, columns=names, dtype=float)
    current: dict[str, dict[str, float]] = {symbol: {} for symbol in names}
    for session in sessions:
        for ticker, metric, value in scheduled.get(session.normalize(), []):
            current[ticker][metric] = value
        for ticker in names:
            metrics = current[ticker]
            components = [np.tanh(metrics[metric] / 25.0) for metric in POSITIVE if metric in metrics]
            components += [-np.tanh(metrics[metric] / 4.0) for metric in NEGATIVE if metric in metrics]
            if len(components) >= minimum_metrics:
                raw.at[session, ticker] = float(np.mean(components))
    observed = raw.notna()
    ranked = raw.rank(axis=1, pct=True, method="average").sub(.5).mul(2).fillna(0.0)
    return ranked, observed
