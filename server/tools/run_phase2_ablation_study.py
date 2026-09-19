#!/usr/bin/env python3
"""Predeclared Phase 2 component ablation.

This runner is intentionally small. It answers whether gains come from factor
risk measurement, optimizer reshaping, or the V2.0.3 post-optimizer layer rather
than searching a large configuration grid on already-seen data.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.minerva import phase2_factor_optimizer_candidate, phase2_factor_optimizer_v203_candidate, tba8_institutional_risk_candidate
from backend.universal_factor_model import FactorModelConfig
from backend.universal_optimizer import PortfolioOptimizerConfig
from backend.universal_yearly_protocol import SeededYearProtocol, run_seeded_yearly_trials
from tools.run_v202_paired_risk_study import load_inputs, summarize


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars-csv", type=Path, required=True)
    parser.add_argument("--risk-free-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--declared-years", required=True, help="Comma-separated years declared before running")
    parser.add_argument("--seed", type=int, default=20260918)
    parser.add_argument("--minimum-symbols", type=int, default=20)
    parser.add_argument("--maximum-symbols-per-trial", type=int, default=30)
    parser.add_argument("--warmup-sessions", type=int, default=756)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists")
    years = sorted(int(value) for value in args.declared_years.split(",") if value.strip())
    stocks, market_closes, benchmark, risk_free = load_inputs(args.bars_csv, args.risk_free_csv)
    protocol = SeededYearProtocol(
        seed=args.seed, selected_years=len(years), minimum_year_spacing=1,
        minimum_symbols=args.minimum_symbols, maximum_symbols_per_trial=args.maximum_symbols_per_trial,
        warmup_sessions=args.warmup_sessions, earliest_year=1900,
    )
    base = tba8_institutional_risk_candidate()
    diagnostics_only = replace(
        base,
        research_profile="phase2_factor_optimizer",
        factor_model=FactorModelConfig(enabled=True, feed_supervisor_covariance=False),
        portfolio_optimizer=PortfolioOptimizerConfig(enabled=False),
    )
    factor_risk_only = replace(
        base,
        research_profile="phase2_factor_optimizer",
        factor_model=FactorModelConfig(enabled=True, feed_supervisor_covariance=True),
        portfolio_optimizer=PortfolioOptimizerConfig(enabled=False),
    )
    configs = {
        "tba8_v1": base,
        "factor_diagnostics_only": diagnostics_only,
        "factor_covariance_v1": factor_risk_only,
        "phase2_optimizer_v1": phase2_factor_optimizer_candidate(),
        "phase2_optimizer_v203": phase2_factor_optimizer_v203_candidate(),
    }
    reports = {
        name: run_seeded_yearly_trials(
            stocks, config, benchmark, risk_free,
            protocol=protocol, market_closes=market_closes, declared_years=years,
        )
        for name, config in configs.items()
    }
    result = {
        "status": "research_only_component_ablation",
        "declared_years": years,
        "arms": {name: summarize(report) for name, report in reports.items()},
        "interpretation_order": [
            "factor_diagnostics_only should be behaviorally identical to TBA8; otherwise integration leaked into construction",
            "factor_covariance_v1 isolates factor covariance as the supervisor risk matrix",
            "phase2_optimizer_v1 adds constrained construction",
            "phase2_optimizer_v203 adds the V2.0.3 post-optimizer safety layer",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
