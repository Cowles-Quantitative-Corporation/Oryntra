"""Private current-data, same-panel model comparisons for Oryntra Control.

This is exploratory research only.  It does not alter a model, create a Phase
4 candidate, or turn an equal-weight comparison into an executable strategy.
"""
from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any

import numpy as np
import pandas as pd

from .control_benchmarks import benchmark_symbols, build_benchmark_returns, evaluate_many, parse_benchmark_list, parse_benchmark_spec
from .market_repository import get_market_repository, normalize_ticker
from .models.registry import get_model_spec
from .phase4_control import build_control_config
from .provider_credentials import decrypted_credentials
from .universal_research import run_universal

_SESSIONS = {"3mo": 63, "6mo": 126, "1y": 252, "2y": 504, "5y": 1260, "10y": 2520}
_PERIOD = {"3mo": "2y", "6mo": "2y", "1y": "5y", "2y": "5y", "5y": "10y", "10y": "all"}


def _symbols(values: list[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        symbol = normalize_ticker(value)
        if symbol not in output:
            output.append(symbol)
    if not 2 <= len(output) <= 60:
        raise ValueError("Use between two and 60 research symbols")
    return output


def _selected_models(spec: dict[str, Any]) -> list[str]:
    raw = spec.get("models") or [spec.get("alpha_model") or "tba8"]
    raw = [raw] if isinstance(raw, str) else raw
    result: list[str] = []
    for value in raw:
        model_id = str(value or "").strip().lower()
        if model_id and model_id not in result:
            get_model_spec(model_id)
            result.append(model_id)
    if not result or len(result) > 12:
        raise ValueError("Select between one and 12 registered models")
    return result


def _fetch_histories(*, user_id: int, symbols: list[str], provider: str, period: str, minimum_bars: int, force_refresh: bool = False) -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, Any]]]:
    repository, histories, metadata = get_market_repository(), {}, {}
    keys = {} if provider == "yfinance" else decrypted_credentials(user_id)
    for symbol in symbols:
        try:
            item = repository.get_history(symbol, period=period, minimum_bars=minimum_bars, allow_api=provider != "cache_only", force_refresh=force_refresh, provider_preference=provider, provider_api_keys=keys, allow_platform_provider_keys=False)
        except Exception as exc:
            raise ValueError(f"{symbol}: {exc}") from exc
        frame = item.history.copy().sort_index()
        required = ["Open", "High", "Low", "Close", "Volume"]
        if not set(required).issubset(frame):
            raise ValueError(f"{symbol}: market history is missing required OHLCV fields")
        index = pd.DatetimeIndex(frame.index)
        if index.tz is not None:
            index = index.tz_localize(None)
        frame.index = index.normalize()
        frame = frame[~frame.index.duplicated(keep="last")]
        values = frame[required].apply(pd.to_numeric, errors="coerce")
        complete = values.notna().all(axis=1) & np.isfinite(values.to_numpy(dtype=float)).all(axis=1)
        original = len(frame)
        frame = frame.loc[complete].copy()
        if frame.empty:
            raise ValueError(f"{symbol}: provider returned no complete OHLCV sessions")
        histories[symbol] = frame
        metadata[symbol] = {**asdict(item.metadata), "interactive_rows_received": original, "interactive_incomplete_rows_excluded": original - len(frame), "interactive_index_normalized": True}
    return histories, metadata


def _common_index(histories: dict[str, pd.DataFrame], symbols: list[str]) -> pd.DatetimeIndex:
    index: pd.DatetimeIndex | None = None
    for symbol in symbols:
        current = pd.DatetimeIndex(histories[symbol].index).tz_localize(None).normalize()
        index = current if index is None else index.intersection(current)
    return (index or pd.DatetimeIndex([])).sort_values().drop_duplicates()


def _returns(report: dict[str, Any]) -> pd.Series:
    return pd.Series({pd.Timestamp(row["date"]): float(row["net_return"]) for row in report.get("daily_returns") or []}, dtype=float).sort_index()


def _headline(returns: pd.Series, risk_free: pd.Series) -> dict[str, Any]:
    excess = returns - risk_free.reindex(returns.index)
    volatility = float(excess.std(ddof=0))
    total = float((1 + returns).prod() - 1) if len(returns) else None
    curve = (1 + returns).cumprod()
    return {"sessions": len(returns), "total_return_pct": None if total is None else 100 * total, "annualized_return_pct": None if total is None else 100 * ((1 + total) ** (252 / len(returns)) - 1), "strategy_excess_sharpe": float(np.sqrt(252) * excess.mean() / volatility) if volatility > 1e-12 else None, "max_drawdown_pct": None if curve.empty else 100 * float((curve / curve.cummax() - 1).min())}


def run_interactive_backtest(user_id: int, spec: dict[str, Any]) -> dict[str, Any]:
    universe, models = _symbols(list(spec.get("universe") or [])), _selected_models(spec)
    period = str(spec.get("evaluation_period") or "1y").lower()
    if period not in _SESSIONS:
        raise ValueError("Choose 3mo, 6mo, 1y, 2y, 5y, or 10y")
    primary = parse_benchmark_spec(str(spec.get("model_benchmark") or "SPY/QQQ"))
    comparisons = parse_benchmark_list(spec.get("comparison_benchmarks") or [primary["spec"]])
    symbols = list(universe)
    for definition in [primary, *comparisons]:
        symbols.extend(symbol for symbol in benchmark_symbols(definition) if symbol not in symbols)
    sessions = _SESSIONS[period]
    histories, metadata = _fetch_histories(user_id=user_id, symbols=symbols, provider=str(spec.get("provider") or "auto").lower(), period=_PERIOD[period], minimum_bars=max(420, sessions + 380), force_refresh=bool(spec.get("force_refresh", False)))
    common = _common_index(histories, symbols)
    if len(common) < sessions + 253:
        raise ValueError(f"Only {len(common)} aligned sessions are available; this study needs at least {sessions + 253}")
    aligned = {symbol: frame.reindex(common).copy() for symbol, frame in histories.items()}
    if any(frame[["Open", "High", "Low", "Close", "Volume"]].isna().any().any() for frame in aligned.values()):
        raise ValueError("Aligned panel contains missing OHLCV values; no silent imputation is allowed")
    start, end = common[-sessions], common[-1]
    risk_free_annual = float(spec.get("risk_free_annual_pct", 0.0)) / 100
    if not math.isfinite(risk_free_annual) or not -.99 < risk_free_annual <= 2:
        raise ValueError("Risk-free annual percent must be between -99% and 200%")
    risk_free = pd.Series((1 + risk_free_annual) ** (1 / 252) - 1, index=common)
    benchmark = build_benchmark_returns(aligned, primary, common)
    comparison_returns = {item["id"]: build_benchmark_returns(aligned, item, common) for item in comparisons}
    results, strategy_series = [], []
    for model_id in models:
        report = run_universal({symbol: aligned[symbol] for symbol in universe}, build_control_config(alpha_model=model_id, construction=str(spec.get("construction") or "phase15"), risk_model=str(spec.get("risk_model") or "v203"), phase3_enabled=bool(spec.get("phase3_enabled", True))), benchmark, risk_free, evaluation_start=str(start.date()), evaluation_end=str(end.date()))
        returns = _returns(report)
        strategy_series.append(returns)
        results.append({"model": get_model_spec(model_id).public_dict(), "headline": _headline(returns, risk_free), "benchmarks": evaluate_many(returns, comparison_returns, risk_free, comparisons), "report": report})
    ensemble = None
    if len(results) > 1 and bool(spec.get("include_equal_weight_ensemble", True)):
        shared = strategy_series[0].index
        for series in strategy_series[1:]:
            shared = shared.intersection(series.index)
        returns = pd.concat([series.reindex(shared) for series in strategy_series], axis=1).mean(axis=1)
        ensemble = {"id": "equal_weight_strategy_ensemble", "label": "Equal-weight strategy ensemble", "constituent_models": models, "headline": _headline(returns, risk_free), "benchmarks": evaluate_many(returns, comparison_returns, risk_free, comparisons), "methodology": "Arithmetic mean of realized daily strategy returns; the constituent models are not retrained or altered."}
    return {"status": "completed", "mode": "interactive_multi_model_backtest" if len(models) > 1 else "interactive_current_data_backtest", "configuration": {"models": models, "model_benchmark": primary, "comparison_benchmarks": comparisons}, "evaluation": {"start": str(start.date()), "end": str(end.date()), "sessions": sessions}, "models": results, "ensemble": ensemble, "data": metadata, "qualification": "Exploratory comparison on one identical market panel. Repeated operator use can introduce selection bias; use frozen point-in-time protocols for confirmatory evidence."}


def run_current_market_snapshot(user_id: int, spec: dict[str, Any]) -> dict[str, Any]:
    current = dict(spec)
    current["evaluation_period"] = "3mo"
    result = run_interactive_backtest(user_id, current)
    return {**result, "mode": "current_market_snapshot", "as_of": result["evaluation"]["end"]}
