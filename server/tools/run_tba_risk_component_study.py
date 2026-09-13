#!/usr/bin/env python3
"""Ablate TBA risk controls on fixed data, years, symbols, and signals."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.minerva import (
    tba1_ohlcv_structure_candidate,
    tba3_market_residual_target_candidate,
    tba4_completed_ic_gate_candidate,
    tba5_residual_momentum_candidate,
    tba6_residual_lifecycle_candidate,
    tba8_institutional_risk_candidate,
)
from backend.universal_risk_supervisor import RiskSupervisorConfig
from backend.universal_yearly_protocol import SeededYearProtocol, run_seeded_yearly_trials
from tools.run_tba_paired_risk_study import load_inputs, summarize


MODELS = {
    "tba1": tba1_ohlcv_structure_candidate,
    "tba3": tba3_market_residual_target_candidate,
    "tba4": tba4_completed_ic_gate_candidate,
    "tba5": tba5_residual_momentum_candidate,
    "tba6": tba6_residual_lifecycle_candidate,
    "tba8": tba8_institutional_risk_candidate,
}
SWITCHES = (
    "component_risk_enabled", "correlation_cluster_enabled", "correlation_regime_enabled",
    "diversification_enabled", "realized_volatility_enabled", "volatility_shock_enabled",
    "tail_loss_enabled", "drawdown_enabled", "gradual_recovery_enabled",
)


def profile(*active: str) -> RiskSupervisorConfig:
    values = {name: name in active for name in SWITCHES}
    return RiskSupervisorConfig(enabled=bool(active), **values)


PROFILES = {
    "disabled": profile(),
    "component_risk": profile("component_risk_enabled"),
    "correlation_cluster": profile("correlation_cluster_enabled"),
    "correlation_regime": profile("correlation_regime_enabled"),
    "diversification": profile("diversification_enabled"),
    "realized_volatility": profile("realized_volatility_enabled", "gradual_recovery_enabled"),
    "volatility_shock": profile("volatility_shock_enabled", "gradual_recovery_enabled"),
    "tail_loss": profile("tail_loss_enabled", "gradual_recovery_enabled"),
    "drawdown": profile("drawdown_enabled", "gradual_recovery_enabled"),
    "cross_sectional_bundle": profile(
        "component_risk_enabled", "correlation_cluster_enabled", "correlation_regime_enabled", "diversification_enabled",
    ),
    "dynamic_bundle": profile(
        "realized_volatility_enabled", "volatility_shock_enabled", "tail_loss_enabled",
        "drawdown_enabled", "gradual_recovery_enabled",
    ),
    "full": RiskSupervisorConfig(enabled=True),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars-csv", type=Path, required=True)
    parser.add_argument("--risk-free-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", choices=MODELS, default="tba5")
    parser.add_argument("--declared-years", required=True)
    parser.add_argument("--seed", type=int, default=20260913)
    parser.add_argument("--minimum-symbols", type=int, default=30)
    parser.add_argument("--maximum-symbols-per-trial", type=int, default=30)
    parser.add_argument("--warmup-sessions", type=int, default=756)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; choose a new path")
    years = [int(value) for value in args.declared_years.split(",") if value.strip()]
    stocks, market_closes, benchmark, risk_free = load_inputs(args.bars_csv, args.risk_free_csv)
    protocol = SeededYearProtocol(
        seed=args.seed, selected_years=len(years), minimum_year_spacing=1,
        minimum_symbols=args.minimum_symbols, maximum_symbols_per_trial=args.maximum_symbols_per_trial,
        warmup_sessions=args.warmup_sessions, earliest_year=min(years),
    )
    base = MODELS[args.model]()
    results = {}
    reference_fingerprints = None
    for name, risk_config in PROFILES.items():
        report = run_seeded_yearly_trials(
            stocks, replace(base, risk_supervisor=risk_config), benchmark, risk_free,
            protocol=protocol, market_closes=market_closes, declared_years=years,
        )
        fingerprints = [trial["dataset_fingerprint"] for trial in report["trials"]]
        if reference_fingerprints is None:
            reference_fingerprints = fingerprints
        elif fingerprints != reference_fingerprints:
            raise RuntimeError(f"{name} did not use the reference datasets")
        results[name] = summarize(report)
    baseline = results["disabled"]
    for name, result in results.items():
        result["mean_sharpe_delta_vs_disabled"] = result["mean_excess_sharpe"] - baseline["mean_excess_sharpe"]
        result["minimum_sharpe_delta_vs_disabled"] = result["minimum_annual_excess_sharpe"] - baseline["minimum_annual_excess_sharpe"]
        result["mean_alpha_delta_pct_vs_disabled"] = result["mean_annual_alpha_pct"] - baseline["mean_annual_alpha_pct"]
    payload = {
        "status": "research_only_development_ablation",
        "model": args.model,
        "declared_years": years,
        "paired_invariant": "Identical signal configuration, data, symbols, costs, and execution; risk switches only",
        "results": results,
        "selection_rule": "A profile may enter confirmation only if mean Sharpe improves and minimum annual Sharpe does not decline.",
        "dataset_fingerprints": reference_fingerprints,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
    print(json.dumps(payload, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
