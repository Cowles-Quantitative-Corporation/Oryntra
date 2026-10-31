#!/usr/bin/env python3
"""Frozen-panel Alpha V1.2 comparison before Model B.

The five arms share the same Phase 2 construction, Risk V2.0.3, Phase 3,
execution, years and frozen datasets.  The intended difference is the alpha
candidate only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.phase4_control import build_control_config
from backend.universal_yearly_protocol import (
    SeededYearProtocol,
    eligible_years,
    run_seeded_yearly_trials,
    select_seeded_years,
)
from tools.run_v202_paired_risk_study import load_inputs, summarize


def _delta(a: dict, b: dict) -> dict:
    keys = (
        "mean_excess_sharpe",
        "minimum_annual_excess_sharpe",
        "mean_annual_alpha_pct",
        "mean_max_drawdown_pct",
        "mean_annualized_turnover",
        "mean_gross_exposure",
    )
    return {key: float(a[key] - b[key]) for key in keys}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--bars-csv", type=Path, required=True)
    p.add_argument("--risk-free-csv", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--seed", type=int, default=20260919)
    p.add_argument("--years", type=int, default=12)
    p.add_argument("--warmup-sessions", type=int, default=800)
    p.add_argument("--declared-years")
    p.add_argument("--evidence-label", default="development", choices=("development", "fresh_holdout"))
    a = p.parse_args()
    if a.output.exists():
        p.error("Output exists; preserve prior evidence and choose a new path")

    stocks, market_closes, benchmark, risk_free = load_inputs(a.bars_csv, a.risk_free_csv)
    declared = [int(v) for v in a.declared_years.split(",") if v.strip()] if a.declared_years else None
    protocol = SeededYearProtocol(
        seed=a.seed,
        selected_years=len(declared) if declared else a.years,
        warmup_sessions=a.warmup_sessions,
    )
    availability = eligible_years(stocks, benchmark, risk_free, protocol)
    if declared:
        available = {row["year"] for row in availability if row["eligible"]}
        missing = sorted(set(declared) - available)
        if missing:
            p.error("Declared years unavailable: " + ",".join(map(str, missing)))
        years = sorted(declared)
    else:
        years = [row["year"] for row in select_seeded_years(availability, protocol)]

    configs = {
        "tba8": build_control_config(alpha_model="tba8", construction="phase2", risk_model="v203", phase3_enabled=True),
        "alpha_v1": build_control_config(alpha_model="alpha_v1", construction="phase2", risk_model="v203", phase3_enabled=True),
        "alpha_v11": build_control_config(alpha_model="alpha_v11", construction="phase2", risk_model="v203", phase3_enabled=True),
        "alpha_v12": build_control_config(alpha_model="alpha_v12", construction="phase2", risk_model="v203", phase3_enabled=True),
        "alpha_v12_alt": build_control_config(alpha_model="alpha_v12_alt", construction="phase2", risk_model="v203", phase3_enabled=True),
    }
    reports = {
        name: run_seeded_yearly_trials(
            stocks, cfg, benchmark, risk_free,
            protocol=protocol, market_closes=market_closes, declared_years=years,
        )
        for name, cfg in configs.items()
    }
    fingerprints = {
        name: [trial["dataset_fingerprint"] for trial in report["trials"]]
        for name, report in reports.items()
    }
    reference = fingerprints["tba8"]
    if any(values != reference for values in fingerprints.values()):
        raise RuntimeError("Alpha V1.2 paired arms did not use identical datasets")

    summaries = {name: summarize(report) for name, report in reports.items()}
    result = {
        "status": "research_only",
        "evidence_label": a.evidence_label,
        "protocol": {
            "seed": a.seed,
            "declared_years": years,
            "construction": "phase2",
            "risk": "v203",
            "phase3": True,
            "only_intended_difference": "alpha model",
            "alpha_v12_main": "55% fixed TBA8 rank anchor + 45% stabilized robust Alpha-family rank before one causal calibration",
            "alpha_v12_alt": "same stabilized V1.2 Alpha-family architecture with zero TBA/TBA9 forecast input",
        },
        "summaries": summaries,
        "alpha_v12_minus_tba8": _delta(summaries["alpha_v12"], summaries["tba8"]),
        "alpha_v12_minus_alpha_v1": _delta(summaries["alpha_v12"], summaries["alpha_v1"]),
        "alpha_v12_minus_alpha_v11": _delta(summaries["alpha_v12"], summaries["alpha_v11"]),
        "alpha_v12_alt_minus_alpha_v1": _delta(summaries["alpha_v12_alt"], summaries["alpha_v1"]),
        "alpha_v12_main_minus_alt": _delta(summaries["alpha_v12"], summaries["alpha_v12_alt"]),
        "dataset_fingerprints": reference,
        "input_fingerprint": hashlib.sha256(a.bars_csv.read_bytes() + a.risk_free_csv.read_bytes()).hexdigest(),
        "promotion_question": "Does V1.2 retain TBA8 economics while materially improving risk efficiency, and does the no-anchor Alt show that the stabilization concept works independently?",
        "qualification": "Do not tune V1.2 to this development panel. Fix causal/mathematical defects only, freeze the specification, then require a fresh point-in-time holdout.",
    }
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
