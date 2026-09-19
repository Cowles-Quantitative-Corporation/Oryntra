#!/usr/bin/env python3
"""Fair research comparison for Risk Supervisor V2.0.3.

All arms share the same frozen data, yearly schedule, alpha path, benchmark,
risk-free input, costs and warm-up.  Only risk supervision changes.

The already-inspected 12-year panel is development evidence.  A favorable rerun
there does not promote V2.0.3; promotion still requires a fresh point-in-time
holdout.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.minerva import (
    minerva_baseline,
    risk_v203_candidate,
    risk_v202_candidate,
    tba8_institutional_risk_candidate,
    tba9_fragility_risk_candidate,
)
from backend.universal_risk_supervisor import RiskSupervisorConfig
from backend.universal_yearly_protocol import SeededYearProtocol, eligible_years, run_seeded_yearly_trials, select_seeded_years
from tools.run_v202_paired_risk_study import load_inputs, summarize


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars-csv", type=Path, required=True)
    parser.add_argument("--risk-free-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260918)
    parser.add_argument("--years", type=int, default=12)
    parser.add_argument("--minimum-year-spacing", type=int, default=1)
    parser.add_argument("--minimum-symbols", type=int, default=20)
    parser.add_argument("--maximum-symbols-per-trial", type=int, default=30)
    parser.add_argument("--warmup-sessions", type=int, default=756)
    parser.add_argument("--earliest-year", type=int, default=2000)
    parser.add_argument("--declared-years", help="Comma-separated years frozen before running this comparison")
    parser.add_argument("--evidence-label", default="development", choices=("development", "fresh_holdout"))
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; use a new path so prior evidence is preserved")

    stocks, market_closes, benchmark, risk_free = load_inputs(args.bars_csv, args.risk_free_csv)
    declared = None if not args.declared_years else [int(v) for v in args.declared_years.split(",") if v.strip()]
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
        available = {row["year"] for row in availability if row["eligible"]}
        missing = sorted(set(declared) - available)
        if missing:
            parser.error("Declared years are unavailable: " + ", ".join(map(str, missing)))
        declared_years = sorted(declared)

    v1 = tba8_institutional_risk_candidate()
    configs = {
        "minerva": minerva_baseline(),
        "no_supervisor": replace(v1, risk_supervisor=RiskSupervisorConfig(enabled=False)),
        "v1": v1,
        "v201": tba9_fragility_risk_candidate(),
        "v202": risk_v202_candidate(),
        "v203": risk_v203_candidate(),
    }

    reports = {}
    fingerprints = None
    for name, config in configs.items():
        report = run_seeded_yearly_trials(
            stocks, config, benchmark, risk_free,
            protocol=protocol, market_closes=market_closes, declared_years=declared_years,
        )
        fps = [trial["dataset_fingerprint"] for trial in report["trials"]]
        if fingerprints is None:
            fingerprints = fps
        elif fps != fingerprints:
            raise RuntimeError(f"Dataset mismatch in {name}")
        reports[name] = report

    summary = {name: summarize(report) for name, report in reports.items()}
    v1s, v203s = summary["v1"], summary["v203"]
    result = {
        "status": "research_only",
        "evidence_label": args.evidence_label,
        "protocol": {
            "seed": args.seed,
            "declared_years": declared_years,
            "paired_change": "same TBA8 alpha path for no/V1/V2 branches; risk supervisor only",
            "v203_defaults_predeclared_before_run": True,
        },
        "input_fingerprint": hashlib.sha256(args.bars_csv.read_bytes() + args.risk_free_csv.read_bytes()).hexdigest(),
        **summary,
        "v203_minus_v1": {
            "mean_excess_sharpe": v203s["mean_excess_sharpe"] - v1s["mean_excess_sharpe"],
            "minimum_annual_excess_sharpe": v203s["minimum_annual_excess_sharpe"] - v1s["minimum_annual_excess_sharpe"],
            "mean_annual_alpha_pct": v203s["mean_annual_alpha_pct"] - v1s["mean_annual_alpha_pct"],
            "mean_max_drawdown_pct": v203s["mean_max_drawdown_pct"] - v1s["mean_max_drawdown_pct"],
            "mean_annualized_turnover": v203s["mean_annualized_turnover"] - v1s["mean_annualized_turnover"],
            "mean_gross_exposure": v203s["mean_gross_exposure"] - v1s["mean_gross_exposure"],
        },
        "dataset_fingerprints": fingerprints,
        "promotion_question": (
            "Does V2.0.3 improve V1 on downside and alpha/Sharpe without paying unacceptable turnover, "
            "and are gains broad rather than concentrated in one year?"
        ),
        "qualification": (
            "If evidence_label=development, this panel may diagnose V2.0.3 but cannot promote it. "
            "Promotion requires a fresh point-in-time-universe holdout whose dates/universe were not used to choose defaults."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
