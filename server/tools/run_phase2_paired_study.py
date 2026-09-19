#!/usr/bin/env python3
"""Paired Phase 2 study with Risk V2.0.3 fully represented.

Arms:
- TBA8 + V1
- TBA8 + Risk V2.0.3
- Phase2 factor model + optimizer + V1
- Phase2 factor model + optimizer + Risk V2.0.3

All arms use the same frozen data, yearly schedule, symbols, costs, benchmark,
cash series and TBA8 alpha path. Phase 2 changes portfolio construction; the
V2.0.3 comparison then isolates the post-optimizer risk layer within Phase 2.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.minerva import (
    phase2_factor_optimizer_candidate,
    phase2_factor_optimizer_v203_candidate,
    risk_v203_candidate,
    tba8_institutional_risk_candidate,
)
from backend.universal_yearly_protocol import SeededYearProtocol, eligible_years, run_seeded_yearly_trials, select_seeded_years
from tools.run_v202_paired_risk_study import load_inputs, summarize


def _phase2_summary(report: dict) -> dict:
    base = summarize(report)
    decisions = np.asarray([trial.get("optimizer_decisions", 0) for trial in report["trials"]], dtype=float)
    accepted = np.asarray([trial.get("optimizer_accepted", 0) for trial in report["trials"]], dtype=float)
    return {
        **base,
        "optimizer_decisions": int(decisions.sum()),
        "optimizer_accepted": int(accepted.sum()),
        "optimizer_acceptance_rate": float(accepted.sum() / decisions.sum()) if decisions.sum() > 0 else None,
    }


def _delta(a: dict, b: dict) -> dict:
    keys = (
        "mean_excess_sharpe", "minimum_annual_excess_sharpe", "mean_annual_alpha_pct",
        "mean_max_drawdown_pct", "mean_annualized_turnover", "mean_gross_exposure",
    )
    return {key: float(a[key] - b[key]) for key in keys}


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
        parser.error("Output exists; preserve prior evidence and use a new path")

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
        years = [row["year"] for row in select_seeded_years(availability, protocol)]
    else:
        available = {row["year"] for row in availability if row["eligible"]}
        missing = sorted(set(declared) - available)
        if missing:
            parser.error("Declared years unavailable: " + ", ".join(map(str, missing)))
        years = sorted(declared)

    configs = {
        "tba8_v1": tba8_institutional_risk_candidate(),
        "tba8_v203": risk_v203_candidate(),
        "phase2_v1": phase2_factor_optimizer_candidate(),
        "phase2_v203": phase2_factor_optimizer_v203_candidate(),
    }
    reports = {
        name: run_seeded_yearly_trials(
            stocks, config, benchmark, risk_free,
            protocol=protocol, market_closes=market_closes, declared_years=years,
        )
        for name, config in configs.items()
    }
    fingerprints = {name: [trial["dataset_fingerprint"] for trial in report["trials"]] for name, report in reports.items()}
    first = fingerprints["tba8_v1"]
    if any(value != first for value in fingerprints.values()):
        raise RuntimeError("Paired Phase 2 arms did not use identical datasets")

    summary = {name: _phase2_summary(report) for name, report in reports.items()}
    result = {
        "status": "research_only",
        "evidence_label": args.evidence_label,
        "protocol": {
            "seed": args.seed,
            "declared_years": years,
            "same_alpha_path": "TBA8",
            "phase2_components": ["causal factor-risk model", "constrained portfolio optimizer"],
            "post_optimizer_risk_challenger": "Risk Supervisor V2.0.3",
        },
        "input_fingerprint": hashlib.sha256(args.bars_csv.read_bytes() + args.risk_free_csv.read_bytes()).hexdigest(),
        **summary,
        "phase2_v1_minus_tba8_v1": _delta(summary["phase2_v1"], summary["tba8_v1"]),
        "phase2_v203_minus_tba8_v203": _delta(summary["phase2_v203"], summary["tba8_v203"]),
        "v203_increment_within_phase2": _delta(summary["phase2_v203"], summary["phase2_v1"]),
        "dataset_fingerprints": first,
        "promotion_question": (
            "Does factor-aware construction improve the TBA8 portfolio after costs, and does V2.0.3 add downside protection "
            "after optimization without erasing alpha or creating excessive turnover?"
        ),
        "qualification": (
            "Development panels diagnose behavior but do not promote Phase 2. A fresh point-in-time-universe holdout is required. "
            "Optional sector/size/value/quality factors are absent unless separately supplied as availability-dated panels."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
