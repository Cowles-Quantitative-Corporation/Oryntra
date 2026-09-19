#!/usr/bin/env python3
"""Paired Phase 3 branch study.

Requested arms:
1) Phase 1.5 = TBA8 + Risk Supervisor V2.0.3
2) Phase 1.5 + Phase 3 diagnostics
3) Phase 1.5 + Phase 2 factor/optimizer + Phase 3 diagnostics

Phase 3 is observational by contract, so arms 1 and 2 MUST have identical
portfolio performance on the same frozen data.  If not, the runner fails rather
than allowing a diagnostic layer to leak into construction.
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
    phase15_phase2_phase3_candidate,
    phase15_phase3_candidate,
    phase15_v203_candidate,
)
from backend.universal_yearly_protocol import (
    SeededYearProtocol,
    eligible_years,
    run_seeded_yearly_trials,
    select_seeded_years,
)
from tools.run_v202_paired_risk_study import load_inputs, summarize


def _diagnostic_summary(report: dict) -> dict:
    trials = report["trials"]
    decisions = int(sum(int(row.get("phase3_decisions", 0)) for row in trials))
    critical = int(sum(int(row.get("phase3_critical_alerts", 0)) for row in trials))
    high = int(sum(int(row.get("phase3_high_alerts", 0)) for row in trials))
    return {
        "phase3_decisions": decisions,
        "critical_alerts": critical,
        "high_alerts": high,
        "critical_alerts_per_decision": float(critical / decisions) if decisions else None,
        "high_alerts_per_decision": float(high / decisions) if decisions else None,
    }


def _performance_vector(summary: dict) -> np.ndarray:
    return np.asarray([
        summary["mean_annual_alpha_pct"],
        summary["mean_excess_sharpe"],
        summary["minimum_annual_excess_sharpe"],
        summary["mean_max_drawdown_pct"],
        summary["mean_annualized_turnover"],
        summary["mean_gross_exposure"],
    ], dtype=float)


def _delta(a: dict, b: dict) -> dict:
    keys = (
        "mean_annual_alpha_pct",
        "mean_excess_sharpe",
        "minimum_annual_excess_sharpe",
        "mean_max_drawdown_pct",
        "mean_annualized_turnover",
        "mean_gross_exposure",
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
    parser.add_argument("--declared-years", help="Comma-separated years frozen before running")
    parser.add_argument("--evidence-label", default="development", choices=("development", "fresh_holdout"))
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; preserve previous evidence and choose a new output path")

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
        years = [row["year"] for row in select_seeded_years(availability, protocol)]
    else:
        available = {row["year"] for row in availability if row["eligible"]}
        missing = sorted(set(declared) - available)
        if missing:
            parser.error("Declared years unavailable: " + ", ".join(map(str, missing)))
        years = sorted(declared)

    configs = {
        "phase15_v203": phase15_v203_candidate(),
        "phase15_phase3": phase15_phase3_candidate(),
        "phase15_phase2_phase3": phase15_phase2_phase3_candidate(),
    }
    reports = {
        name: run_seeded_yearly_trials(
            stocks, config, benchmark, risk_free,
            protocol=protocol, market_closes=market_closes, declared_years=years,
        )
        for name, config in configs.items()
    }
    fingerprints = {
        name: [trial["dataset_fingerprint"] for trial in report["trials"]]
        for name, report in reports.items()
    }
    first = fingerprints["phase15_v203"]
    if any(value != first for value in fingerprints.values()):
        raise RuntimeError("Phase 3 paired arms did not use identical frozen datasets")

    performance = {name: summarize(report) for name, report in reports.items()}
    # Phase 3 is observational. An exact performance mismatch is an integration bug.
    if not np.allclose(
        _performance_vector(performance["phase15_v203"]),
        _performance_vector(performance["phase15_phase3"]),
        rtol=0.0,
        atol=1e-12,
    ):
        raise RuntimeError("Phase 3 diagnostics changed Phase 1.5 portfolio performance")

    result = {
        "status": "research_only",
        "evidence_label": args.evidence_label,
        "protocol": {
            "seed": args.seed,
            "declared_years": years,
            "same_alpha_path": "TBA8",
            "branches": [
                "Phase 1.5: Risk V2.0.3 only",
                "Phase 1.5 + Phase 3 diagnostics",
                "Phase 1.5 + Phase 2 factor/optimizer + Phase 3 diagnostics",
            ],
            "phase3_invariant": "diagnostic-only; cannot change target weights",
        },
        "input_fingerprint": hashlib.sha256(
            args.bars_csv.read_bytes() + args.risk_free_csv.read_bytes()
        ).hexdigest(),
        "performance": performance,
        "phase3_diagnostics": {
            name: _diagnostic_summary(report) for name, report in reports.items()
        },
        "phase15_plus_phase3_performance_identity_verified": True,
        "phase2_increment_with_phase3_present": _delta(
            performance["phase15_phase2_phase3"], performance["phase15_phase3"]
        ),
        "dataset_fingerprints": first,
        "promotion_question": (
            "Does Phase 2 improve portfolio economics while Phase 3 adds decision-useful stress, liquidity, "
            "capacity, attribution and exception diagnostics without leaking into construction?"
        ),
        "qualification": (
            "Phase 3 is not an alpha or risk-control claim. Development-panel diagnostics are descriptive. "
            "Phase 2 still requires independent economic validation and a fresh point-in-time holdout."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
