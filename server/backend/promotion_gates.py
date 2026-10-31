"""Explicit CQC prospective/live-promotion gates for Oryntra Control 5.4.

These gates are descriptive safeguards, not a claim that any model currently
passes them. Paper/shadow research remains available regardless of the result.
"""
from __future__ import annotations

from typing import Any
import numpy as np
import pandas as pd

MIN_SHARPE = 1.5
MIN_UNIVERSE = 101  # requirement is strictly > 100 tickers
MIN_SESSIONS = 2521  # requirement is strictly > 10 trading years
MAX_NEGATIVE_CALENDAR_YEARS = 2
MAX_RELATIVE_DRAWDOWN = -0.10


def benchmark_relative_drawdown(strategy: pd.Series, benchmark: pd.Series) -> float | None:
    idx = strategy.dropna().index.intersection(benchmark.dropna().index)
    if not len(idx):
        return None
    wp = (1.0 + strategy.reindex(idx).astype(float)).cumprod()
    wb = (1.0 + benchmark.reindex(idx).astype(float)).cumprod()
    if (wb <= 0).any():
        return None
    active = wp / wb
    dd = active / active.cummax() - 1.0
    return float(dd.min()) if len(dd) else None


def negative_calendar_years(strategy: pd.Series) -> list[int]:
    if strategy.empty:
        return []
    s = strategy.copy(); s.index = pd.DatetimeIndex(s.index)
    annual = s.groupby(s.index.year).apply(lambda x: float((1.0 + x).prod() - 1.0))
    return [int(year) for year, value in annual.items() if value < 0]


def evaluate_promotion_gates(
    strategy: pd.Series, benchmark: pd.Series, risk_free: pd.Series,
    *, universe_size: int,
) -> dict[str, Any]:
    strategy = strategy.dropna().astype(float).sort_index()
    rf = risk_free.reindex(strategy.index).fillna(0.0).astype(float)
    excess = strategy - rf
    vol = float(excess.std(ddof=0)) if len(excess) else 0.0
    sharpe = float(np.sqrt(252.0) * excess.mean() / vol) if vol > 1e-12 else None
    neg = negative_calendar_years(strategy)
    rel_dd = benchmark_relative_drawdown(strategy, benchmark)
    gates = {
        "sharpe": {"required": f">= {MIN_SHARPE}", "observed": sharpe, "passed": sharpe is not None and sharpe >= MIN_SHARPE},
        "universe": {"required": "> 100 tickers", "observed": int(universe_size), "passed": int(universe_size) >= MIN_UNIVERSE},
        "history": {"required": "> 10 trading years", "observed_sessions": int(len(strategy)), "passed": int(len(strategy)) >= MIN_SESSIONS},
        "negative_calendar_years": {"required": f"<= {MAX_NEGATIVE_CALENDAR_YEARS}", "observed": len(neg), "years": neg, "passed": len(neg) <= MAX_NEGATIVE_CALENDAR_YEARS},
        "benchmark_relative_drawdown": {"required": ">= -10%", "observed": rel_dd, "passed": rel_dd is not None and rel_dd >= MAX_RELATIVE_DRAWDOWN},
    }
    return {
        "passed_all": all(bool(v["passed"]) for v in gates.values()),
        "gates": gates,
        "paper_operation_allowed": True,
        "live_promotion_allowed": all(bool(v["passed"]) for v in gates.values()),
        "benchmark_relative_definition": "A(t)=W_portfolio(t)/W_benchmark(t); maximum drawdown of A from its prior peak must be >= -10%.",
    }
