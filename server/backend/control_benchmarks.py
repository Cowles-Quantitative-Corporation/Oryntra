"""Custom benchmark parsing and evaluation for private Control research."""
from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .alpha_evaluation import regression_alpha
from .market_repository import normalize_ticker


def parse_benchmark_spec(value: str) -> dict[str, Any]:
    legs = [part.strip() for part in str(value or "").split("/") if part.strip()]
    if not legs:
        raise ValueError("Enter a benchmark ticker or slash-separated basket")
    weighted = any(":" in leg for leg in legs)
    if weighted and not all(":" in leg for leg in legs):
        raise ValueError("Weighted benchmarks must give every leg a weight")
    components: dict[str, float] = {}
    if weighted:
        for leg in legs:
            symbol_text, weight_text = leg.rsplit(":", 1)
            symbol = normalize_ticker(symbol_text)
            try:
                weight = float(weight_text)
            except ValueError as exc:
                raise ValueError(f"Invalid weight in benchmark leg '{leg}'") from exc
            if not math.isfinite(weight) or weight <= 0:
                raise ValueError("Benchmark weights must be positive finite numbers")
            components[symbol] = components.get(symbol, 0.0) + weight
        total = sum(components.values())
        components = {symbol: weight / total for symbol, weight in components.items()}
        canonical = "/".join(f"{symbol}:{components[symbol]:.6g}" for symbol in sorted(components))
        kind = "weighted_average"
    else:
        symbols = list(dict.fromkeys(normalize_ticker(leg) for leg in legs))
        components = {symbol: 1.0 / len(symbols) for symbol in symbols}
        canonical = "/".join(symbols)
        kind = "single_ticker" if len(symbols) == 1 else "equal_weight_average"
    return {"id": canonical, "spec": canonical, "label": canonical, "kind": kind, "components": components}


def parse_benchmark_list(values: Iterable[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    known: set[str] = set()
    for value in values:
        parsed = parse_benchmark_spec(value)
        if parsed["id"] not in known:
            result.append(parsed)
            known.add(parsed["id"])
    if not result or len(result) > 12:
        raise ValueError("Use between one and 12 comparison benchmarks")
    return result


def benchmark_symbols(definition: dict[str, Any]) -> set[str]:
    return set(definition.get("components") or {})


def build_benchmark_returns(histories: dict[str, pd.DataFrame], definition: dict[str, Any], index: pd.DatetimeIndex) -> pd.Series:
    result = pd.Series(0.0, index=index, dtype=float)
    for symbol, weight in (definition.get("components") or {}).items():
        frame = histories.get(str(symbol).upper())
        if frame is None or "Close" not in frame:
            raise ValueError(f"Benchmark history is missing {symbol}")
        close = frame.reindex(index)["Close"]
        if close.isna().any():
            raise ValueError(f"Benchmark {definition.get('spec', '')} has missing {symbol} sessions")
        result += close.pct_change(fill_method=None).fillna(0.0) * float(weight)
    result.name = str(definition.get("id") or "benchmark")
    return result


def evaluate_many(strategy_returns: pd.Series, benchmark_returns: dict[str, pd.Series], risk_free: pd.Series, definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for index, definition in enumerate(definitions, start=1):
        benchmark = benchmark_returns[definition["id"]].reindex(strategy_returns.index)
        fit = regression_alpha(strategy_returns, benchmark, risk_free.reindex(strategy_returns.index))
        active = strategy_returns - benchmark
        tracking = float(active.std(ddof=0))
        rows.append({"index": index, "benchmark": definition, "alpha_pct": fit.get("alpha_pct"), "beta": fit.get("beta"), "alpha_ci95_pct": fit.get("alpha_ci95_pct"), "alpha_t_hac": fit.get("alpha_t_hac"), "observations": fit.get("observations"), "strategy_return_pct": fit.get("strategy_return_pct"), "benchmark_return_pct": fit.get("benchmark_return_pct"), "information_ratio": float(np.sqrt(252) * active.mean() / tracking) if tracking > 1e-12 else None, "status": fit.get("status")})
    return rows
