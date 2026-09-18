"""Reproducible, research-only Quant Lab experiment helpers.

This module deliberately accepts a small, declarative strategy manifest instead
of arbitrary user code.  A saved result can therefore say exactly which fixed
rules, data, limits, and assumptions produced it.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from .quant_research import MODEL_PROFILES, STRATEGIES, QuantConfig, evaluate_strategies
from .research_experiments import fingerprint, record_experiment, record_experiment_partitions


CONFIG_FIELDS = {
    "trend_lookback", "momentum_lookback", "reversal_lookback", "cost_bps",
    "borrow_bps_annual", "long_short", "model", "strategy_weights",
    "target_annual_volatility", "max_gross_exposure", "max_single_name_weight",
    "rebalance_frequency", "walk_forward_folds", "regime_conditioned_weights",
    "liquidity_aware_costs", "portfolio_value_assumption", "impact_coefficient_bps",
    "max_adv_participation_pct",
}


def quant_config_from_manifest(manifest: dict[str, Any]) -> QuantConfig:
    """Validate a declarative experiment manifest and return its fixed config."""
    raw_strategies = manifest.get("strategies") or []
    if not isinstance(raw_strategies, list):
        raise ValueError("Manifest strategies must be a list of supported strategy identifiers.")
    strategies = tuple(str(item) for item in raw_strategies if str(item) in STRATEGIES)
    if not strategies:
        raise ValueError("Manifest must select at least one supported Quant Lab strategy.")
    unknown = set(manifest) - {"label", "hypothesis", "notes", "strategies", *CONFIG_FIELDS}
    if unknown:
        raise ValueError(f"Manifest has unsupported fields: {', '.join(sorted(unknown))}.")
    config_values = {key: manifest[key] for key in CONFIG_FIELDS if key in manifest}
    if "model" in config_values and config_values["model"] not in MODEL_PROFILES:
        raise ValueError("Manifest model must be a supported Quant Lab model profile.")
    if "strategy_weights" in config_values:
        weights = config_values["strategy_weights"]
        if not isinstance(weights, dict) or any(key not in STRATEGIES or not 0 <= float(value) <= 100 for key, value in weights.items()):
            raise ValueError("Manifest strategy_weights must use supported identifiers with values from 0 to 100.")
    return QuantConfig(strategies=strategies, **config_values)


def histories_from_rows(rows: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Convert normalized CSV rows into the daily close/volume histories Quant Lab needs."""
    normalized = {str(column).strip().lower(): column for column in rows.columns}
    required = {"date", "ticker", "close", "volume"}
    missing = required - set(normalized)
    if missing:
        raise ValueError(f"CSV is missing required columns: {', '.join(sorted(missing))}.")
    frame = rows.rename(columns={normalized[key]: key for key in required})[["date", "ticker", "close", "volume"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], utc=True, errors="coerce").dt.tz_localize(None)
    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
    frame = frame.dropna(subset=["date", "ticker", "close", "volume"])
    frame = frame[(frame["ticker"] != "") & (frame["close"] > 0) & (frame["volume"] >= 0)]
    frame = frame.drop_duplicates(["date", "ticker"], keep="last").sort_values(["ticker", "date"])
    histories: dict[str, pd.DataFrame] = {}
    for ticker, group in frame.groupby("ticker", sort=True):
        history = group.set_index("date")[["close", "volume"]].rename(columns={"close": "Close", "volume": "Volume"})
        if len(history) >= 2:
            histories[str(ticker)] = history
    if len(histories) < 2:
        raise ValueError("CSV needs at least two symbols with usable daily rows.")
    return histories


def run_manifest_experiment(
    histories: dict[str, pd.DataFrame],
    manifest: dict[str, Any],
    *,
    corporate_scores: pd.DataFrame | None = None,
    macro_features: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Evaluate and durably record one fixed strategy hypothesis."""
    config = quant_config_from_manifest(manifest)
    required_bars = max(config.trend_lookback, config.momentum_lookback, 63) + 5
    eligible = {ticker: history for ticker, history in histories.items() if len(history) >= required_bars}
    if len(eligible) < 2:
        raise ValueError(f"At least two symbols need {required_bars} daily rows for this manifest.")
    report = evaluate_strategies(eligible, config, corporate_scores, macro_features)
    dataset = [
        {"ticker": ticker, "date": str(day.date()), "close": round(float(row.Close), 8), "volume": round(float(row.Volume), 3)}
        for ticker, history in sorted(eligible.items())
        for day, row in history[["Close", "Volume"]].iterrows()
    ]
    dataset_fingerprint = fingerprint(dataset)
    overall = next((item for item in report.get("results", []) if item.get("id") == "strategy_ensemble"), report.get("results", [{}])[0])
    experiment_id = record_experiment(
        experiment_type="quant_strategy",
        status="done",
        config={"code_version": "quant-manifest-v1", "manifest": manifest, "quant_config": config.as_dict()},
        dataset_fingerprint=dataset_fingerprint,
        dataset_start=report["universe"]["start"],
        dataset_end=report["universe"]["end"],
        symbols=list(eligible),
        sample_count=sum(len(history) for history in eligible.values()),
        metrics={"primary_result": overall, "validation": report.get("validation", {}), "benchmark": report.get("benchmark", {})},
        notes=str(manifest.get("hypothesis") or manifest.get("notes") or ""),
    )
    common_sessions = sorted(set.intersection(*[{str(day.date()) for day in history.index} for history in eligible.values()]))
    holdout_sessions = max(63, len(common_sessions) // 5)
    if len(common_sessions) > holdout_sessions:
        record_experiment_partitions(experiment_id, {
            "development": common_sessions[:-holdout_sessions],
            "holdout": common_sessions[-holdout_sessions:],
        })
    return {**report, "experiment_id": experiment_id, "dataset_fingerprint": dataset_fingerprint, "manifest": manifest, "configuration": config.as_dict()}
