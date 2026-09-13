#!/usr/bin/env python3
"""Compare each native TBA signal with and without the shared risk supervisor.

The paired sides use identical data, years, symbols, signals, costs, and model
settings. Only RiskSupervisorConfig.enabled changes. TBA 2 is evaluated by the
separate frozen-Qlib-score runner because its predictions are external.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.minerva import (
    tba1_ohlcv_structure_candidate,
    tba3_market_residual_target_candidate,
    tba4_completed_ic_gate_candidate,
    tba5_residual_momentum_candidate,
    tba6_residual_lifecycle_candidate,
    tba8_institutional_risk_candidate,
)
from backend.universal_yearly_protocol import (
    SeededYearProtocol,
    eligible_years,
    run_seeded_yearly_trials,
    select_seeded_years,
)


MODELS = {
    "tba1": tba1_ohlcv_structure_candidate,
    "tba3": tba3_market_residual_target_candidate,
    "tba4": tba4_completed_ic_gate_candidate,
    "tba5": tba5_residual_momentum_candidate,
    "tba6": tba6_residual_lifecycle_candidate,
    "tba8": tba8_institutional_risk_candidate,
}


def load_inputs(bars_path: Path, risk_free_path: Path):
    bars = pd.read_csv(bars_path)
    required = {"date", "ticker", "open", "high", "low", "close", "volume"}
    if not required.issubset(bars.columns):
        raise ValueError("Bars missing fields: " + ", ".join(sorted(required - set(bars.columns))))
    bars["date"] = pd.to_datetime(bars["date"])
    if bars.duplicated(["date", "ticker"]).any():
        raise ValueError("Bars contain duplicate symbol/session rows")
    histories = {
        symbol: group.set_index("date").sort_index().rename(
            columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"},
        )
        for symbol, group in bars.groupby("ticker")
    }
    if not {"SPY", "QQQ"}.issubset(histories):
        raise ValueError("Bars must include SPY and QQQ")
    benchmark = (
        histories["SPY"]["Close"].pct_change(fill_method=None)
        + histories["QQQ"]["Close"].pct_change(fill_method=None)
    ) / 2
    cash = pd.read_csv(risk_free_path)
    if not {"date", "return_daily"}.issubset(cash.columns):
        raise ValueError("Risk-free input requires date,return_daily")
    risk_free = pd.Series(cash["return_daily"].to_numpy(float), index=pd.to_datetime(cash["date"]), dtype=float)
    stocks = {symbol: history for symbol, history in histories.items() if symbol not in {"SPY", "QQQ"}}
    market_closes = pd.DataFrame({symbol: histories[symbol]["Close"] for symbol in ("SPY", "QQQ")})
    return stocks, market_closes, benchmark, risk_free


def summarize(report: dict) -> dict:
    sharpes = [trial["excess_return_sharpe"] for trial in report["trials"]]
    if any(value is None for value in sharpes):
        raise ValueError("A paired annual trial produced undefined excess-return Sharpe")
    values = np.asarray(sharpes, dtype=float)
    alphas = np.asarray([trial["alpha"]["alpha_pct"] for trial in report["trials"]], dtype=float)
    return {
        "mean_excess_sharpe": float(values.mean()),
        "median_excess_sharpe": float(np.median(values)),
        "minimum_annual_excess_sharpe": float(values.min()),
        "positive_sharpe_years": int((values > 0).sum()),
        "mean_annual_alpha_pct": float(alphas.mean()),
        "negative_alpha_years": int((alphas < 0).sum()),
        "annual_excess_sharpe": values.tolist(),
        "annual_alpha_pct": alphas.tolist(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars-csv", type=Path, required=True)
    parser.add_argument("--risk-free-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--models", default=",".join(MODELS), help="Comma-separated native TBA IDs")
    parser.add_argument("--seed", type=int, default=20260913)
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--minimum-year-spacing", type=int, default=2)
    parser.add_argument("--minimum-symbols", type=int, default=20)
    parser.add_argument("--maximum-symbols-per-trial", type=int, default=30)
    parser.add_argument("--warmup-sessions", type=int, default=756)
    parser.add_argument("--earliest-year", type=int, default=2000)
    parser.add_argument("--declared-years", help="Comma-separated years frozen before this comparison")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; use a new path so prior evidence is preserved")
    requested = [name.strip().lower() for name in args.models.split(",") if name.strip()]
    unknown = sorted(set(requested) - set(MODELS))
    if unknown:
        parser.error("Unknown native TBA models: " + ", ".join(unknown))

    stocks, market_closes, benchmark, risk_free = load_inputs(args.bars_csv, args.risk_free_csv)
    declared = None if not args.declared_years else [int(value) for value in args.declared_years.split(",") if value.strip()]
    protocol = SeededYearProtocol(
        seed=args.seed,
        selected_years=len(declared) if declared is not None else args.years,
        minimum_year_spacing=args.minimum_year_spacing,
        minimum_symbols=args.minimum_symbols,
        maximum_symbols_per_trial=args.maximum_symbols_per_trial,
        warmup_sessions=args.warmup_sessions,
        earliest_year=args.earliest_year,
    )
    availability = eligible_years(stocks, benchmark, risk_free, protocol)
    if declared is None:
        declared_years = [row["year"] for row in select_seeded_years(availability, protocol)]
    else:
        available_years = {row["year"] for row in availability if row["eligible"]}
        missing = sorted(set(declared) - available_years)
        if missing:
            parser.error("Declared years are unavailable: " + ", ".join(map(str, missing)))
        declared_years = sorted(declared)
    comparisons = {}
    for model_id in requested:
        supervised_config = MODELS[model_id]()
        control_config = replace(
            supervised_config,
            risk_supervisor=replace(supervised_config.risk_supervisor, enabled=False),
        )
        control = run_seeded_yearly_trials(
            stocks, control_config, benchmark, risk_free, protocol=protocol,
            market_closes=market_closes, declared_years=declared_years,
        )
        supervised = run_seeded_yearly_trials(
            stocks, supervised_config, benchmark, risk_free, protocol=protocol,
            market_closes=market_closes, declared_years=declared_years,
        )
        control_fingerprints = [trial["dataset_fingerprint"] for trial in control["trials"]]
        supervised_fingerprints = [trial["dataset_fingerprint"] for trial in supervised["trials"]]
        if control_fingerprints != supervised_fingerprints:
            raise RuntimeError(f"{model_id} paired runs did not use identical datasets")
        control_summary, supervised_summary = summarize(control), summarize(supervised)
        comparisons[model_id] = {
            "without_supervisor": control_summary,
            "with_supervisor": supervised_summary,
            "mean_sharpe_delta": supervised_summary["mean_excess_sharpe"] - control_summary["mean_excess_sharpe"],
            "minimum_annual_sharpe_delta": supervised_summary["minimum_annual_excess_sharpe"] - control_summary["minimum_annual_excess_sharpe"],
            "mean_alpha_delta_pct": supervised_summary["mean_annual_alpha_pct"] - control_summary["mean_annual_alpha_pct"],
            "dataset_fingerprints": supervised_fingerprints,
        }

    result = {
        "status": "research_only",
        "protocol": {"seed": args.seed, "declared_years": declared_years, "paired_change": "RiskSupervisorConfig.enabled only"},
        "input_fingerprint": hashlib.sha256(args.bars_csv.read_bytes() + args.risk_free_csv.read_bytes()).hexdigest(),
        "comparisons": comparisons,
        "tba2": "Use run_tba2_risk_ablation.py with frozen external Qlib scores; it cannot share the native ridge signal path.",
        "selection_rule": "Prefer the supervisor only when mean excess-return Sharpe improves without degrading minimum annual excess-return Sharpe.",
        "qualification": "Current-survivor data are a paired engineering study, not a point-in-time-universe performance claim.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
