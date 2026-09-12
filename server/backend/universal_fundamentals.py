"""Availability-dated fundamental scores for Universal V2 research."""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd


def build_fundamental_score_panel(events: Iterable[dict], sessions: pd.DatetimeIndex,
                                  symbols: Iterable[str], *, revenue_scale: float = .25,
                                  income_scale: float = .50) -> pd.DataFrame:
    """Convert prevalidated SEC filing events into a session-aligned score panel.

    An event becomes usable at the session close on its declared filing date,
    so the V2 ledger can only trade it at the following open. Missing history is
    represented by a neutral zero, never a backfilled estimate.
    """
    if not isinstance(sessions, pd.DatetimeIndex) or sessions.has_duplicates or not sessions.is_monotonic_increasing:
        raise ValueError("Fundamental sessions must be unique and increasing")
    if not (0 < revenue_scale <= 5 and 0 < income_scale <= 5):
        raise ValueError("Fundamental scales must be finite positive fractions")
    names = list(symbols)
    if len(set(names)) != len(names):
        raise ValueError("Fundamental symbols must be unique")
    panel = pd.DataFrame(0.0, index=sessions, columns=names)
    latest: dict[str, tuple[pd.Timestamp, float]] = {}
    ordered = sorted(events, key=lambda event: (str(event.get("available_date", "")), str(event.get("symbol", ""))))
    for event in ordered:
        symbol = str(event.get("symbol", ""))
        if symbol not in panel.columns:
            continue
        try:
            available = pd.Timestamp(event["available_date"]).normalize()
            revenue_growth = float(event["revenue_growth"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Fundamental events require a finite available_date and revenue_growth") from None
        if not np.isfinite(revenue_growth):
            raise ValueError("Fundamental revenue growth must be finite")
        score = .65 * math.tanh(revenue_growth / revenue_scale)
        if event.get("net_income_growth") is not None:
            try:
                income_growth = float(event["net_income_growth"])
            except (TypeError, ValueError):
                raise ValueError("Fundamental net income growth must be finite when supplied") from None
            if not np.isfinite(income_growth):
                raise ValueError("Fundamental net income growth must be finite when supplied")
            score += .35 * math.tanh(income_growth / income_scale)
        latest[symbol] = (available, float(np.clip(score, -1, 1)))
        start = sessions.searchsorted(available, side="left")
        if start < len(sessions):
            panel.iloc[start:, panel.columns.get_loc(symbol)] = latest[symbol][1]
    return panel


def build_fundamental_acceleration_panel(events: Iterable[dict], sessions: pd.DatetimeIndex,
                                         symbols: Iterable[str], *, decay_sessions: int = 63,
                                         revenue_scale: float = .25,
                                         income_scale: float = .50) -> pd.DataFrame:
    """Return a causal, decaying filing-to-filing change signal.

    This is deliberately *acceleration*, not an earnings surprise: it compares
    a newly public revenue/income-growth score with the issuer's last public
    score.  An event can affect a session only on or after ``available_date``;
    the impact then decays, which prevents a months-old filing from behaving as
    fresh information.  It needs the same availability-dated event records as
    ``build_fundamental_score_panel`` and is neutral for a first filing.
    """
    if not isinstance(sessions, pd.DatetimeIndex) or sessions.has_duplicates or not sessions.is_monotonic_increasing:
        raise ValueError("Fundamental sessions must be unique and increasing")
    if not (1 <= decay_sessions <= 756):
        raise ValueError("decay_sessions must be between 1 and 756")
    if not (0 < revenue_scale <= 5 and 0 < income_scale <= 5):
        raise ValueError("Fundamental scales must be finite positive fractions")
    names = list(symbols)
    if len(set(names)) != len(names):
        raise ValueError("Fundamental symbols must be unique")
    updates: dict[pd.Timestamp, list[tuple[str, float]]] = {}
    for event in events:
        symbol = str(event.get("symbol", ""))
        if symbol not in names:
            continue
        try:
            available = pd.Timestamp(event["available_date"]).normalize()
            revenue_growth = float(event["revenue_growth"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Fundamental events require a finite available_date and revenue_growth") from None
        if not np.isfinite(revenue_growth):
            raise ValueError("Fundamental revenue growth must be finite")
        score = .65 * math.tanh(revenue_growth / revenue_scale)
        if event.get("net_income_growth") is not None:
            try:
                income_growth = float(event["net_income_growth"])
            except (TypeError, ValueError):
                raise ValueError("Fundamental net income growth must be finite when supplied") from None
            if not np.isfinite(income_growth):
                raise ValueError("Fundamental net income growth must be finite when supplied")
            score += .35 * math.tanh(income_growth / income_scale)
        updates.setdefault(available, []).append((symbol, float(np.clip(score, -1, 1))))
    panel = pd.DataFrame(0.0, index=sessions, columns=names)
    prior_score: dict[str, float] = {}
    active_change: dict[str, tuple[float, int]] = {}
    for session in sessions:
        for symbol, score in sorted(updates.get(session.normalize(), [])):
            if symbol in prior_score:
                active_change[symbol] = (float(np.clip(score - prior_score[symbol], -1, 1)), 0)
            prior_score[symbol] = score
        for symbol, (change, age) in list(active_change.items()):
            panel.at[session, symbol] = change * math.exp(-age / decay_sessions)
            active_change[symbol] = (change, age + 1)
    return panel
