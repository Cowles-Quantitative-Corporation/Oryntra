"""Custom benchmark parsing/evaluation for Oryntra Control desktop research.

The desktop app accepts simple slash syntax for equal-weight baskets, e.g.
``SPY/QQQ/IWM``.  Weighted baskets use ``TICKER:WEIGHT`` legs, e.g.
``SPY:0.6/QQQ:0.4``.  Benchmark definitions are evaluation metadata; they do
not modify the strategy except when one is explicitly chosen as the model
benchmark.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .alpha_evaluation import regression_alpha
from .market_repository import normalize_ticker


def parse_benchmark_spec(value: str) -> dict[str, Any]:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Enter a benchmark ticker or slash-separated basket")
    legs = [part.strip() for part in raw.split("/") if part.strip()]
    if not legs:
        raise ValueError("Benchmark contains no tickers")
    weighted = any(":" in leg for leg in legs)
    if weighted and not all(":" in leg for leg in legs):
        raise ValueError("Weighted benchmarks must give every leg a weight, e.g. SPY:0.6/QQQ:0.4")
    components: dict[str, float] = {}
    canonical_legs: list[str] = []
    if weighted:
        for leg in legs:
            symbol_text, weight_text = leg.rsplit(":", 1)
            symbol = normalize_ticker(symbol_text)
            try:
                weight = float(weight_text)
            except Exception as exc:
                raise ValueError(f"Invalid weight in benchmark leg '{leg}'") from exc
            if not math.isfinite(weight) or weight <= 0:
                raise ValueError("Benchmark weights must be positive finite numbers")
            components[symbol] = components.get(symbol, 0.0) + weight
        total = sum(components.values())
        components = {symbol: weight / total for symbol, weight in components.items()}
        canonical_legs = [f"{symbol}:{components[symbol]:.6g}" for symbol in sorted(components)]
        kind = "weighted_average"
    else:
        symbols = [normalize_ticker(leg) for leg in legs]
        unique = []
        for symbol in symbols:
            if symbol not in unique:
                unique.append(symbol)
        weight = 1.0 / len(unique)
        components = {symbol: weight for symbol in unique}
        canonical_legs = unique
        kind = "single_ticker" if len(unique) == 1 else "equal_weight_average"
    canonical = "/".join(canonical_legs)
    label = canonical if kind != "equal_weight_average" else f"{canonical} equal average"
    return {
        "id": canonical,
        "spec": canonical,
        "label": label,
        "kind": kind,
        "components": components,
    }


def parse_benchmark_list(values: Iterable[str]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        item = parse_benchmark_spec(value)
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        parsed.append(item)
    if not parsed:
        raise ValueError("At least one benchmark is required")
    if len(parsed) > 12:
        raise ValueError("Use no more than 12 simultaneous comparison benchmarks")
    return parsed


def benchmark_symbols(definition: dict[str, Any]) -> set[str]:
    return {str(symbol).upper() for symbol in (definition.get("components") or {})}


def build_benchmark_returns(
    histories: dict[str, pd.DataFrame],
    definition: dict[str, Any],
    index: pd.DatetimeIndex,
) -> pd.Series:
    result = pd.Series(0.0, index=index, dtype=float)
    components = definition.get("components") or {}
    if not components:
        raise ValueError("Benchmark definition has no components")
    for symbol, weight in components.items():
        symbol = str(symbol).upper()
        frame = histories.get(symbol)
        if frame is None or "Close" not in frame:
            raise ValueError(f"Benchmark history is missing {symbol}")
        close = frame.reindex(index)["Close"]
        if close.isna().any():
            raise ValueError(f"Benchmark {definition.get('spec', '')} has missing {symbol} sessions")
        leg = close.pct_change(fill_method=None).fillna(0.0)
        result = result.add(leg * float(weight), fill_value=0.0)
    result.name = str(definition.get("id") or definition.get("spec") or "benchmark")
    return result


def evaluate_many(
    strategy_returns: pd.Series,
    benchmark_returns: dict[str, pd.Series],
    risk_free: pd.Series,
    definitions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Regress on disclosed, finite daily pairs using the shared CAPM estimator.

    QPO has an independently assembled market panel. A missing comparison date
    must not erase every otherwise valid observation, nor be filled with a fake
    zero. All Control models use this same complete-session convention.
    """
    rows: list[dict[str, Any]] = []
    def daily(values: pd.Series) -> pd.Series:
        series = pd.Series(values, dtype=float).copy()
        index = pd.DatetimeIndex(series.index)
        if index.tz is not None:
            index = index.tz_localize(None)
        series.index = index.normalize()
        return series.sort_index()

    strategy = daily(strategy_returns)
    rf = daily(risk_free)
    for i, definition in enumerate(definitions, start=1):
        key = str(definition["id"])
        bench = daily(benchmark_returns[key])
        requested = len(strategy)
        duplicate_inputs = [name for name, values in (("strategy", strategy), ("benchmark", bench), ("risk_free", rf)) if values.index.has_duplicates]
        frame = pd.DataFrame(columns=["s", "b", "rf"], dtype=float)
        excluded_dates: list[str] = []
        missing_counts: dict[str, int] = {}
        warning = None
        if duplicate_inputs:
            fit = {"status": "ambiguous_daily_sessions"}
            reason = "Duplicate daily sessions in " + ", ".join(duplicate_inputs) + "; alpha/beta were not estimated."
        else:
            aligned = pd.DataFrame({"s": strategy, "b": bench.reindex(strategy.index), "rf": rf.reindex(strategy.index)})
            finite = np.isfinite(aligned.to_numpy(dtype=float))
            complete = finite.all(axis=1)
            missing_counts = {name: int((~finite[:, j]).sum()) for j, name in enumerate(("strategy", "benchmark", "risk_free"))}
            excluded_dates = [str(day.date()) for day in aligned.index[~complete]]
            frame = aligned.loc[complete]
            fit = regression_alpha(frame.s, frame.b, frame.rf)
            if fit.get("status") == "available":
                reason = None
            elif fit.get("status") == "degenerate_benchmark":
                reason = "Benchmark excess returns have zero or near-zero variance; alpha and beta cannot be identified."
            else:
                reason = f"Only {len(frame)} finite aligned daily observations; at least 63 are required for alpha/beta."
            if excluded_dates:
                warning = f"Excluded {len(excluded_dates)} of {requested} requested sessions with missing/non-finite strategy, benchmark, or risk-free returns; no values were imputed."
        active = frame.s - frame.b
        tracking = float(active.std(ddof=0)) if len(frame) else 0.0
        information_ratio = float(np.sqrt(252) * active.mean() / tracking) if tracking > 1e-12 else None
        row = {
            "index": i,
            "benchmark": definition,
            "alpha_key": f"alpha_{i}",
            "beta_key": f"beta_{i}",
            "benchmark_key": f"benchmark_{i}",
            "alpha_pct": fit.get("alpha_pct"),
            "beta": fit.get("beta"),
            "alpha_ci95_pct": fit.get("alpha_ci95_pct"),
            "alpha_t_hac": fit.get("alpha_t_hac"),
            "observations": int(len(frame)),
            "requested_observations": int(requested),
            "minimum_observations": 63,
            "excluded_observations": int(len(excluded_dates)),
            "excluded_sessions": excluded_dates[:20],
            "excluded_sessions_truncated": len(excluded_dates) > 20,
            "missing_observations_by_input": missing_counts,
            "sample_start": str(frame.index[0].date()) if len(frame) else None,
            "sample_end": str(frame.index[-1].date()) if len(frame) else None,
            "sample_policy": "finite aligned daily strategy/benchmark/risk-free observations; no imputation",
            "strategy_return_pct": fit.get("strategy_return_pct"),
            "benchmark_return_pct": fit.get("benchmark_return_pct"),
            "information_ratio": information_ratio,
            "status": fit.get("status"),
            "reason": reason,
            "warning": warning,
        }
        rows.append(row)
    return rows
