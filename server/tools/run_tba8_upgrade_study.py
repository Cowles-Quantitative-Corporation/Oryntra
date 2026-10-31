#!/usr/bin/env python3
"""Paired confirmation of TBA 8 against its TBA 5 signal ancestor.

Both sides use identical frozen bars, stock samples, dates, benchmark, cash
series, execution simulator, and shared risk supervisor.  The candidate adds
only score persistence and the institutional confidence/capacity layer.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.minerva import tba5_residual_momentum_candidate, tba8_institutional_risk_candidate
from backend.universal_yearly_protocol import SeededYearProtocol, eligible_years, run_seeded_yearly_trials
from tools.run_tba_paired_risk_study import load_inputs


def summarize(report: dict) -> dict:
    sharpes = np.asarray([trial["excess_return_sharpe"] for trial in report["trials"]], dtype=float)
    alphas = np.asarray([trial["alpha"]["alpha_pct"] for trial in report["trials"]], dtype=float)
    if not np.isfinite(sharpes).all() or not np.isfinite(alphas).all():
        raise ValueError("A confirmation trial produced a non-finite metric")
    performance = [trial["performance"] for trial in report["trials"]]
    alpha_rows = [trial["alpha"] for trial in report["trials"]]
    years = [trial["year"] for trial in report["trials"]]
    return {
        "mean_excess_sharpe": float(sharpes.mean()),
        "median_excess_sharpe": float(np.median(sharpes)),
        "minimum_annual_excess_sharpe": float(sharpes.min()),
        "positive_sharpe_years": int((sharpes > 0).sum()),
        "mean_annual_alpha_pct": float(alphas.mean()),
        "negative_alpha_years": int((alphas < 0).sum()),
        "annual_excess_sharpe": sharpes.tolist(),
        "annual_alpha_pct": alphas.tolist(),
        "mean_annualized_return_pct": float(np.mean([row["annualized_return_pct"] for row in performance])),
        "mean_annualized_volatility_pct": float(np.mean([row["annualized_volatility_pct"] for row in performance])),
        "mean_annualized_turnover": float(np.mean([row["annualized_turnover"] for row in performance])),
        "mean_gross_exposure": float(np.mean([row["average_gross_exposure"] for row in performance])),
        "mean_market_beta": float(np.mean([row["beta"] for row in alpha_rows])),
        "mean_benchmark_return_pct": float(np.mean([row["benchmark_return_pct"] for row in alpha_rows])),
        "worst_max_drawdown_pct": float(min(row["max_drawdown_pct"] for row in performance)),
        "annual_diagnostics": [
            {
                "year": year,
                "alpha_pct": float(alpha),
                "excess_sharpe": float(sharpe),
                "return_pct": row["annualized_return_pct"],
                "volatility_pct": row["annualized_volatility_pct"],
                "turnover": row["annualized_turnover"],
                "gross_exposure": row["average_gross_exposure"],
                "max_drawdown_pct": row["max_drawdown_pct"],
            }
            | {"beta": alpha_row["beta"], "benchmark_return_pct": alpha_row["benchmark_return_pct"]}
            for year, alpha, sharpe, row, alpha_row in zip(years, alphas, sharpes, performance, alpha_rows)
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars-csv", type=Path, required=True)
    parser.add_argument("--risk-free-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--declared-years", required=True, help="Comma-separated years frozen before execution")
    parser.add_argument("--seed", type=int, default=20260913)
    parser.add_argument("--minimum-symbols", type=int, default=20)
    parser.add_argument("--maximum-symbols-per-trial", type=int, default=30)
    parser.add_argument("--warmup-sessions", type=int, default=756)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; use a new path so prior evidence is preserved")

    years = sorted({int(value) for value in args.declared_years.split(",") if value.strip()})
    stocks, market_closes, benchmark, risk_free = load_inputs(args.bars_csv, args.risk_free_csv)
    protocol = SeededYearProtocol(
        seed=args.seed,
        selected_years=len(years),
        # Explicit declared years bypass random spacing selection, while the
        # shared protocol still requires a valid positive setting.
        minimum_year_spacing=1,
        minimum_symbols=args.minimum_symbols,
        maximum_symbols_per_trial=args.maximum_symbols_per_trial,
        warmup_sessions=args.warmup_sessions,
        earliest_year=min(years),
    )
    available = {row["year"] for row in eligible_years(stocks, benchmark, risk_free, protocol) if row["eligible"]}
    missing = sorted(set(years) - available)
    if missing:
        parser.error("Declared years are unavailable: " + ", ".join(map(str, missing)))

    baseline_config = tba5_residual_momentum_candidate()
    candidate_config = tba8_institutional_risk_candidate()
    reports = {}
    for model_id, config in (("tba5_control", baseline_config), ("tba8_candidate", candidate_config)):
        reports[model_id] = run_seeded_yearly_trials(
            stocks, config, benchmark, risk_free, protocol=protocol,
            market_closes=market_closes, declared_years=years,
        )
    fingerprints = {
        model_id: [trial["dataset_fingerprint"] for trial in report["trials"]]
        for model_id, report in reports.items()
    }
    if fingerprints["tba5_control"] != fingerprints["tba8_candidate"]:
        raise RuntimeError("Paired runs did not use identical datasets")

    control = summarize(reports["tba5_control"])
    candidate = summarize(reports["tba8_candidate"])
    mean_delta = candidate["mean_excess_sharpe"] - control["mean_excess_sharpe"]
    minimum_delta = candidate["minimum_annual_excess_sharpe"] - control["minimum_annual_excess_sharpe"]
    result = {
        "status": "research_only",
        "protocol": {
            "seed": args.seed,
            "declared_years": years,
            "maximum_symbols_per_trial": args.maximum_symbols_per_trial,
            "paired_change": "TBA 8 adds 0.25 score persistence plus confidence and causal liquidity-capacity controls to TBA 5; both retain the same risk supervisor",
        },
        "input_fingerprint": hashlib.sha256(args.bars_csv.read_bytes() + args.risk_free_csv.read_bytes()).hexdigest(),
        "configuration": {
            "tba5_control": asdict(baseline_config),
            "tba8_candidate": asdict(candidate_config),
        },
        "results": {"tba5_control": control, "tba8_candidate": candidate},
        "deltas": {
            "mean_excess_sharpe": mean_delta,
            "minimum_annual_excess_sharpe": minimum_delta,
            "mean_annual_alpha_pct": candidate["mean_annual_alpha_pct"] - control["mean_annual_alpha_pct"],
            "negative_alpha_years": candidate["negative_alpha_years"] - control["negative_alpha_years"],
        },
        "decision": "passes_pairwise_robustness_rule" if mean_delta > 0 and minimum_delta >= 0 else "does_not_pass_pairwise_robustness_rule",
        "dataset_fingerprints": fingerprints["tba8_candidate"],
        "qualification": f"Current-survivor data and {len(years)} declared years are a paired robustness check, not a point-in-time-universe or future-performance claim.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
