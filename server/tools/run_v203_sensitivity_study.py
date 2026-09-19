#!/usr/bin/env python3
"""Predeclared one-factor-at-a-time sensitivity study for V2.0.3.

This is deliberately NOT a Cartesian parameter search.  The purpose is to ask
whether V2.0.3 behaves stably around its declared defaults, not to select the
best in-sample configuration from hundreds of combinations.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.minerva import risk_v203_candidate, tba8_institutional_risk_candidate
from backend.universal_risk_supervisor import v203_risk_config
from backend.universal_yearly_protocol import SeededYearProtocol, eligible_years, run_seeded_yearly_trials, select_seeded_years
from tools.run_v202_paired_risk_study import load_inputs, summarize


def _candidate(**risk_overrides):
    return risk_v203_candidate(risk_supervisor=v203_risk_config(**risk_overrides))


def declared_variants() -> dict[str, object]:
    # One factor at a time around the declared V2.0.3 base.
    return {
        "v1_control": tba8_institutional_risk_candidate(),
        "v203_base": _candidate(),
        "trigger_078": _candidate(v203_systemic_trigger_percentile=.78),
        "trigger_086": _candidate(v203_systemic_trigger_percentile=.86),
        "persistence_1": _candidate(v203_persistence_rebalances=1),
        "persistence_3": _candidate(v203_persistence_rebalances=3),
        "maxcut_005": _candidate(v203_maximum_additional_gross_reduction=.05),
        "maxcut_015": _candidate(v203_maximum_additional_gross_reduction=.15),
        "structural_off": _candidate(v203_structural_intervention_enabled=False),
        "structural_strength_035": _candidate(v203_structural_strength=.35),
        "structural_strength_065": _candidate(v203_structural_strength=.65),
        "structural_band_010": _candidate(v203_structural_no_trade_band=.010),
        "structural_band_020": _candidate(v203_structural_no_trade_band=.020),
    }


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
    parser.add_argument("--declared-years", help="Comma-separated years frozen before running the study")
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
        years = [row["year"] for row in select_seeded_years(availability, protocol)]
    else:
        available = {row["year"] for row in availability if row["eligible"]}
        missing = sorted(set(declared) - available)
        if missing:
            parser.error("Declared years are unavailable: " + ", ".join(map(str, missing)))
        years = sorted(declared)

    output = {
        "status": "research_only",
        "method": "predeclared_one_factor_at_a_time_sensitivity",
        "declared_years": years,
        "variants": {},
    }
    fingerprint = None
    for name, config in declared_variants().items():
        report = run_seeded_yearly_trials(
            stocks, config, benchmark, risk_free,
            protocol=protocol, market_closes=market_closes, declared_years=years,
        )
        fps = [trial["dataset_fingerprint"] for trial in report["trials"]]
        if fingerprint is None:
            fingerprint = fps
        elif fps != fingerprint:
            raise RuntimeError(f"Dataset mismatch in {name}")
        output["variants"][name] = summarize(report)

    output["dataset_fingerprints"] = fingerprint
    output["interpretation_rule"] = (
        "Do not choose the numerically best setting from this evidence set.  A professional result is a broad stable region "
        "around the predeclared base.  Any changed default requires a separately frozen fresh holdout."
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, allow_nan=False) + "\n")
    print(json.dumps(output, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
