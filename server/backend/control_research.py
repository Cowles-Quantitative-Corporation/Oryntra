"""Interactive proprietary research runners for Oryntra Control.

This module is intentionally distinct from frozen confirmatory protocols. It
uses the selected current/cached market-data provider, runs one or more models
through identical construction/risk/benchmark settings, and can report an
explicit equal-weight *strategy ensemble* without altering any underlying model.
"""
from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any

import numpy as np
import pandas as pd

from .control_benchmarks import (
    benchmark_symbols,
    build_benchmark_returns,
    evaluate_many,
    parse_benchmark_list,
    parse_benchmark_spec,
)
from .market_repository import get_market_repository, normalize_ticker
from .control_risk_free import build_control_risk_free
from .corporate_repository import get_corporate_repository
from .models.registry import get_model_spec
from .phase4_control import build_control_config
from .provider_credentials import decrypted_credentials
from .universal_research import run_universal

_EVAL_SESSIONS = {"3mo": 63, "6mo": 126, "1y": 252, "2y": 504, "5y": 1260, "10y": 2520}
_FETCH_PERIOD = {"3mo": "2y", "6mo": "2y", "1y": "5y", "2y": "5y", "5y": "10y", "10y": "all"}
_MAX_MODELS = 12


def _clean_universe(values: list[str]) -> list[str]:
    symbols: list[str] = []
    for value in values:
        symbol = normalize_ticker(value)
        if symbol not in symbols:
            symbols.append(symbol)
    if len(symbols) < 2:
        raise ValueError("Use at least two research symbols")
    if len(symbols) > 60:
        raise ValueError("Interactive desktop studies are limited to 60 symbols per run")
    return symbols


def _selected_models(spec: dict[str, Any]) -> list[str]:
    raw = spec.get("models")
    if raw is None:
        raw = [spec.get("alpha_model") or "tba8"]
    if isinstance(raw, str):
        raw = [raw]
    selected: list[str] = []
    for value in raw:
        key = str(value or "").strip().lower()
        if not key or key in selected:
            continue
        get_model_spec(key)
        selected.append(key)
    if not selected:
        raise ValueError("Select at least one registered model")
    if len(selected) > _MAX_MODELS:
        raise ValueError(f"Use no more than {_MAX_MODELS} models in one interactive comparison")
    return selected


def _risk_free(index: pd.DatetimeIndex, annual_pct: float) -> pd.Series:
    """Legacy constant helper retained for tests/older callers."""
    return build_control_risk_free(index, mode="manual_constant", annual_pct=annual_pct)[0]


def _fetch_histories(*, user_id: int, symbols: list[str], provider: str, period: str,
                     minimum_bars: int, force_refresh: bool = False) -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, Any]]]:
    repository = get_market_repository()
    keys = {} if provider == "yfinance" else decrypted_credentials(user_id)
    histories: dict[str, pd.DataFrame] = {}
    metadata: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        try:
            item = repository.get_history(
                symbol, period=period, minimum_bars=minimum_bars,
                allow_api=provider != "cache_only", force_refresh=force_refresh,
                provider_preference=provider, provider_api_keys=keys,
                allow_platform_provider_keys=False,
            )
        except Exception as exc:
            raise ValueError(f"{symbol}: {exc}") from exc
        frame = item.history.copy().sort_index()
        required = ["Open", "High", "Low", "Close", "Volume"]
        if not set(required).issubset(frame.columns):
            raise ValueError(f"{symbol}: market history is missing required OHLCV fields")
        idx = pd.DatetimeIndex(frame.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        frame.index = idx.normalize()
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        numeric = frame[required].apply(pd.to_numeric, errors="coerce")
        finite_mask = np.isfinite(numeric.to_numpy(dtype=float)).all(axis=1)
        complete_mask = numeric.notna().all(axis=1).to_numpy() & finite_mask
        raw_rows = int(len(frame))
        frame = frame.loc[complete_mask].copy()
        dropped_rows = raw_rows - int(len(frame))
        if frame.empty:
            raise ValueError(f"{symbol}: provider returned no complete OHLCV sessions")
        histories[symbol] = frame
        item_metadata = asdict(item.metadata)
        item_metadata["interactive_rows_received"] = raw_rows
        item_metadata["interactive_incomplete_rows_excluded"] = dropped_rows
        item_metadata["interactive_index_normalized"] = True
        metadata[symbol] = item_metadata
    return histories, metadata


def _aligned_index(histories: dict[str, pd.DataFrame], symbols: list[str]) -> pd.DatetimeIndex:
    common: pd.DatetimeIndex | None = None
    for symbol in symbols:
        idx = pd.DatetimeIndex(histories[symbol].index).tz_localize(None).normalize()
        common = idx if common is None else common.intersection(idx)
    if common is None:
        return pd.DatetimeIndex([])
    return common.sort_values().drop_duplicates()


def _compact_report(report: dict[str, Any]) -> dict[str, Any]:
    risk = report.get("risk_supervisor") or {}
    factor = report.get("factor_model") or {}
    optimizer = report.get("portfolio_optimizer") or {}
    phase3 = report.get("phase3") or {}
    return {
        "engine": report.get("engine"), "engine_version": report.get("engine_version"),
        "config_fingerprint": report.get("config_fingerprint"), "dataset_fingerprint": report.get("dataset_fingerprint"),
        "universe": report.get("universe"), "performance": (report.get("results") or [{}])[0],
        "execution": report.get("execution"), "portfolio_risk": report.get("portfolio_risk"),
        "latest_signals": report.get("latest_signals"),
        "risk_latest": (risk.get("target_audit") or [])[-1] if risk.get("target_audit") else None,
        "factor_latest": (factor.get("decision_audit") or [])[-1] if factor.get("decision_audit") else None,
        "optimizer_latest": (optimizer.get("decision_audit") or [])[-1] if optimizer.get("decision_audit") else None,
        "phase3_latest": (phase3.get("decision_audit") or [])[-1] if phase3.get("decision_audit") else None,
        "methodology": report.get("methodology"),
    }


def _strategy_returns(report: dict[str, Any]) -> pd.Series:
    return pd.Series(
        {pd.Timestamp(row["date"]): float(row["net_return"]) for row in report.get("daily_returns") or []},
        dtype=float,
    ).sort_index()


def _headline(strategy_returns: pd.Series, risk_free: pd.Series) -> dict[str, Any]:
    rf = risk_free.reindex(strategy_returns.index)
    excess = strategy_returns - rf
    vol = float(excess.std(ddof=0))
    sharpe = float(np.sqrt(252) * excess.mean() / vol) if vol > 1e-12 else None
    total = float((1.0 + strategy_returns).prod() - 1.0) if len(strategy_returns) else None
    annual = float((1.0 + total) ** (252.0 / len(strategy_returns)) - 1.0) if total is not None and len(strategy_returns) else None
    curve = (1.0 + strategy_returns).cumprod()
    drawdown = curve.div(curve.cummax()).sub(1.0) if len(curve) else pd.Series(dtype=float)
    return {
        "sessions": int(len(strategy_returns)),
        "total_return_pct": None if total is None else 100.0 * total,
        "annualized_return_pct": None if annual is None else 100.0 * annual,
        "strategy_excess_sharpe": sharpe,
        "max_drawdown_pct": None if drawdown.empty else 100.0 * float(drawdown.min()),
    }


def _average_positions(compacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not compacts:
        return []
    totals: dict[str, float] = {}
    for compact in compacts:
        for row in (compact.get("portfolio_risk") or {}).get("latest_positions") or []:
            totals[str(row["symbol"])] = totals.get(str(row["symbol"]), 0.0) + float(row["weight_pct"])
    n = float(len(compacts))
    return [
        {"symbol": symbol, "weight_pct": weight / n}
        for symbol, weight in sorted(totals.items(), key=lambda item: abs(item[1]), reverse=True)
        if abs(weight / n) > 1e-8
    ]


def run_interactive_backtest(user_id: int, spec: dict[str, Any]) -> dict[str, Any]:
    universe = _clean_universe(list(spec.get("universe") or []))
    selected_models = _selected_models(spec)
    evaluation_period = str(spec.get("evaluation_period") or "1y").strip().lower()
    if evaluation_period not in _EVAL_SESSIONS:
        raise ValueError("Choose 3mo, 6mo, 1y, 2y, 5y, or 10y")
    provider = str(spec.get("provider") or "auto").strip().lower()
    model_benchmark = parse_benchmark_spec(str(spec.get("model_benchmark") or "SPY/QQQ"))
    comparisons = parse_benchmark_list(spec.get("comparison_benchmarks") or [model_benchmark["spec"]])
    all_symbols = list(universe)
    for definition in [model_benchmark, *comparisons]:
        for symbol in sorted(benchmark_symbols(definition)):
            if symbol not in all_symbols:
                all_symbols.append(symbol)

    eval_sessions = _EVAL_SESSIONS[evaluation_period]
    minimum_bars = min(5200, max(420, eval_sessions + 380))
    histories, metadata = _fetch_histories(
        user_id=user_id, symbols=all_symbols, provider=provider,
        period=_FETCH_PERIOD[evaluation_period], minimum_bars=minimum_bars,
        force_refresh=bool(spec.get("force_refresh", False)),
    )
    common = _aligned_index(histories, all_symbols)
    required = eval_sessions + 253
    if len(common) < required:
        raise ValueError(
            f"Only {len(common)} aligned sessions are available; this {evaluation_period} study needs at least {required}. "
            "Try a shorter period or a universe with longer shared history."
        )
    evaluation_index = common[-eval_sessions:]
    evaluation_start, evaluation_end = str(evaluation_index[0].date()), str(evaluation_index[-1].date())
    aligned = {symbol: frame.reindex(common).copy() for symbol, frame in histories.items()}
    if any(frame[["Open", "High", "Low", "Close", "Volume"]].isna().any().any() for frame in aligned.values()):
        raise ValueError("Aligned market panel contains missing OHLCV values; no silent imputation is allowed")

    model_benchmark_returns = build_benchmark_returns(aligned, model_benchmark, common)
    # Older Control/API clients had only a manual annual-percent field. Preserve
    # that contract when risk_free_mode is omitted; Control 5.1 explicitly sends
    # fred_dgs3mo by default.
    risk_free_mode = str(spec.get("risk_free_mode") or "manual_constant").strip().lower()
    risk_free, risk_free_metadata = build_control_risk_free(
        common, mode=risk_free_mode, annual_pct=float(spec.get("risk_free_annual_pct", 0.0)),
        force_refresh=bool(spec.get("force_refresh_risk_free", False)),
    )
    comparison_returns_full = {definition["id"]: build_benchmark_returns(aligned, definition, common) for definition in comparisons}
    research_histories = {symbol: aligned[symbol] for symbol in universe}

    # Use only availability-dated corporate facts. Missing observations remain
    # NaN so Alpha/Phase 2 can distinguish unavailable data from a neutral score.
    corporate_quality, corporate_metadata = get_corporate_repository().factor_panel(
        universe, common, fill_missing=False,
    )
    quality_panel = corporate_quality if corporate_quality.notna().any().any() else None

    model_results: list[dict[str, Any]] = []
    series: dict[str, pd.Series] = {}
    compacts: list[dict[str, Any]] = []
    for model_id in selected_models:
        model_spec = get_model_spec(model_id)
        config = build_control_config(
            alpha_model=model_id,
            construction=str(spec.get("construction") or "phase15"),
            risk_model=str(spec.get("risk_model") or "v203"),
            phase3_enabled=bool(spec.get("phase3_enabled", True)),
        )
        report = run_universal(
            research_histories, config, model_benchmark_returns, risk_free,
            evaluation_start=evaluation_start, evaluation_end=evaluation_end,
            factor_quality_scores=quality_panel,
        )
        returns = _strategy_returns(report)
        series[model_id] = returns
        compact = _compact_report(report)
        compacts.append(compact)
        comparison_returns = {key: value.reindex(returns.index) for key, value in comparison_returns_full.items()}
        model_results.append({
            "model": model_spec.public_dict(),
            "headline": _headline(returns, risk_free),
            "benchmarks": evaluate_many(returns, comparison_returns, risk_free, comparisons),
            "report": compact,
        })

    ensemble: dict[str, Any] | None = None
    if len(model_results) > 1 and bool(spec.get("include_equal_weight_ensemble", True)):
        common_strategy = None
        for returns in series.values():
            common_strategy = returns.index if common_strategy is None else common_strategy.intersection(returns.index)
        if common_strategy is not None and len(common_strategy):
            matrix = pd.concat([returns.reindex(common_strategy) for returns in series.values()], axis=1)
            ensemble_returns = matrix.mean(axis=1)
            comparison_returns = {key: value.reindex(ensemble_returns.index) for key, value in comparison_returns_full.items()}
            ensemble = {
                "id": "equal_weight_strategy_ensemble",
                "label": "Equal-weight strategy ensemble",
                "constituent_models": selected_models,
                "headline": _headline(ensemble_returns, risk_free),
                "benchmarks": evaluate_many(ensemble_returns, comparison_returns, risk_free, comparisons),
                "latest_positions": _average_positions(compacts),
                "methodology": "Arithmetic mean of each selected model's realized daily strategy return under identical construction/risk settings; this does not alter or retrain the constituent models.",
            }

    result: dict[str, Any] = {
        "status": "completed",
        "mode": "interactive_multi_model_backtest" if len(selected_models) > 1 else "interactive_current_data_backtest",
        "configuration": {
            "models": selected_models,
            "alpha_model": selected_models[0],
            "construction": str(spec.get("construction") or "phase15"),
            "risk_model": str(spec.get("risk_model") or "v203"),
            "phase3_enabled": bool(spec.get("phase3_enabled", True)),
            "provider": provider, "evaluation_period": evaluation_period,
            "model_benchmark": model_benchmark,
            "risk_free_mode": risk_free_mode,
            "risk_free_annual_pct": float(spec.get("risk_free_annual_pct", 0.0)),
        },
        "evaluation": {"start": evaluation_start, "end": evaluation_end, "sessions": int(eval_sessions)},
        "models": model_results,
        "ensemble": ensemble,
        "data": metadata,
        "risk_free": risk_free_metadata,
        "point_in_time_corporate": corporate_metadata,
        "qualification": (
            "Convenience backtest on current/cached provider data. Static input universe and repeated operator use can introduce selection bias; "
            "use frozen point-in-time protocols for confirmatory evidence. Multi-model comparison shares one identical market panel and research settings."
        ),
    }
    # Backward-compatible single-model fields for Control 4.x clients.
    if len(model_results) == 1:
        single = model_results[0]
        result["benchmarks"] = single["benchmarks"]
        result["report"] = single["report"]
        result["evaluation"] = {**result["evaluation"], **single["headline"]}
    return result


def run_current_market_snapshot(user_id: int, spec: dict[str, Any]) -> dict[str, Any]:
    snapshot_spec = dict(spec)
    snapshot_spec["evaluation_period"] = str(spec.get("evaluation_period") or "3mo")
    result = run_interactive_backtest(user_id, snapshot_spec)
    snapshots: list[dict[str, Any]] = []
    for model in result["models"]:
        compact = model["report"]
        snapshots.append({
            "model": model["model"], "headline": model["headline"], "benchmarks": model["benchmarks"],
            "latest_positions": (compact.get("portfolio_risk") or {}).get("latest_positions") or [],
            "latest_signals": compact.get("latest_signals") or {}, "risk": compact.get("risk_latest"),
            "factor": compact.get("factor_latest"), "optimizer": compact.get("optimizer_latest"),
            "phase3": compact.get("phase3_latest"),
        })
    out = {
        "status": "completed", "mode": "current_market_snapshot", "as_of": result["evaluation"]["end"],
        "configuration": result["configuration"], "models": snapshots, "ensemble": result.get("ensemble"),
        "data": result.get("data"), "qualification": result["qualification"],
    }
    if len(snapshots) == 1:
        single = snapshots[0]
        out.update({
            "benchmarks": single["benchmarks"], "latest_positions": single["latest_positions"],
            "latest_signals": single["latest_signals"], "risk": single["risk"], "factor": single["factor"],
            "optimizer": single["optimizer"], "phase3": single["phase3"],
        })
    return out
