#!/usr/bin/env python3
"""Ablation runner for Risk Supervisor V2.0.2.

Uses the same input/protocol across V1 and each V2.0.2 component variant. The
purpose is to reject components that do not add stable value, not to hunt the
best in-sample combination.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.minerva import risk_v202_candidate, tba8_institutional_risk_candidate
from backend.universal_risk_supervisor import v202_risk_config
from backend.universal_yearly_protocol import SeededYearProtocol, eligible_years, run_seeded_yearly_trials, select_seeded_years
from tools.run_v202_paired_risk_study import load_inputs, summarize


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars-csv", type=Path, required=True)
    parser.add_argument("--risk-free-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--declared-years", required=True, help="Comma-separated predeclared years")
    parser.add_argument("--seed", type=int, default=20260918)
    parser.add_argument("--minimum-symbols", type=int, default=20)
    parser.add_argument("--maximum-symbols-per-trial", type=int, default=30)
    parser.add_argument("--warmup-sessions", type=int, default=756)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; use a new path")

    years = [int(v) for v in args.declared_years.split(",") if v.strip()]
    stocks, market_closes, benchmark, risk_free = load_inputs(args.bars_csv, args.risk_free_csv)
    protocol = SeededYearProtocol(
        seed=args.seed,
        selected_years=len(years),
        minimum_year_spacing=1,
        minimum_symbols=args.minimum_symbols,
        maximum_symbols_per_trial=args.maximum_symbols_per_trial,
        warmup_sessions=args.warmup_sessions,
    )
    available = {row["year"] for row in eligible_years(stocks, benchmark, risk_free, protocol) if row["eligible"]}
    missing = sorted(set(years) - available)
    if missing:
        parser.error("Declared years unavailable: " + ", ".join(map(str, missing)))

    variants = {
        "v1": tba8_institutional_risk_candidate(),
        "v202_predictive_only": risk_v202_candidate(),
        "v202_diagnostics_only": risk_v202_candidate(risk_supervisor=v202_risk_config(v202_predictive_risk_enabled=False)),
        "v202_plus_structural": risk_v202_candidate(risk_supervisor=v202_risk_config(v202_structural_intervention_enabled=True)),
        "v202_plus_liquidity": risk_v202_candidate(risk_supervisor=v202_risk_config(v202_liquidity_intervention_enabled=True)),
        "v202_no_turnover_penalty": risk_v202_candidate(risk_supervisor=v202_risk_config(v202_optimizer_turnover_penalty=0.0)),
    }

    output = {"status": "research_only", "declared_years": sorted(years), "variants": {}}
    fingerprint = None
    for name, config in variants.items():
        report = run_seeded_yearly_trials(
            stocks, config, benchmark, risk_free,
            protocol=protocol, market_closes=market_closes, declared_years=sorted(years),
        )
        fps = [trial["dataset_fingerprint"] for trial in report["trials"]]
        if fingerprint is None:
            fingerprint = fps
        elif fps != fingerprint:
            raise RuntimeError(f"Dataset mismatch in {name}")
        output["variants"][name] = summarize(report)

    output["dataset_fingerprints"] = fingerprint
    output["interpretation_rule"] = (
        "Do not select the numerically best variant from this same evidence set. Use ablation to identify whether a component "
        "adds stable value; any promoted specification still requires a fresh unseen holdout."
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, allow_nan=False) + "\n")
    print(json.dumps(output, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
