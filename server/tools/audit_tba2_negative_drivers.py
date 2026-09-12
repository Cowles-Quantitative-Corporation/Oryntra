#!/usr/bin/env python3
"""Evidence-led TBA 2 loss audit; flags code choices without overstating causality."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--tba2", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    old = json.loads(args.comparison.read_text())
    new = json.loads(args.tba2.read_text())
    baseline = {row["year"]: row["baseline_alpha_pct"] for row in old["matched_by_year"]}
    tba1 = {row["year"]: row["ohlcv_structure_alpha_pct"] for row in old["matched_by_year"]}
    tba2 = {row["year"]: row["alpha"]["alpha_pct"] for row in new["trials"]}
    losses = []
    for year in sorted(tba2):
        losses.append({
            "year": year,
            "tba2_alpha_pct": tba2[year],
            "delta_vs_minerva_pct": tba2[year] - baseline[year],
            "delta_vs_tba1_pct": tba2[year] - tba1[year],
        })
    report = {
        "evidence": "Matched annual alpha deltas; a negative delta means TBA 2 underperformed on the identical year and symbol basket.",
        "largest_tba2_relative_losses": sorted(losses, key=lambda row: row["delta_vs_tba1_pct"])[:4],
        "largest_tba2_relative_gains": sorted(losses, key=lambda row: row["delta_vs_tba1_pct"], reverse=True)[:4],
        "verified_tuning_and_code_paths": [
            {"setting": "fixed 756-session pre-year training window", "source": "/private/tmp/run_qlib_alpha158_holdout.py:51-64", "risk": "No within-year retraining; score relationships can become stale after a regime shift.", "causal_status": "candidate; needs a matched retraining ablation"},
            {"setting": "30 boosting-round cap and 10-round early stop", "source": "/private/tmp/run_qlib_alpha158_holdout.py:65-72", "risk": "Several logged fits stopped at 1-5 rounds, which can underfit the score model.", "causal_status": "candidate; needs a fixed-seed round-cap ablation"},
            {"setting": "Qlib raw-score percentile rank mapped to [-1, 1]", "source": "/private/tmp/run_qlib_alpha158_holdout.py:25-29", "risk": "It discards prediction magnitude and makes selection depend only on ordering.", "causal_status": "candidate; needs a rank-versus-magnitude ablation"},
            {"setting": "Minerva absolute entry threshold and 24-position risk construction", "source": "/private/tmp/run_qlib_alpha158_holdout.py:42,82-85 and backend/universal_engine.py:316-398", "risk": "The risk layer can block, resize, or concentrate Qlib-selected names; it is not the same as Qlib's native top-K portfolio rule.", "causal_status": "candidate; needs a same-score native-top-K versus risk-layer ablation"},
        ],
        "not_supported": "This report does not claim any setting caused a loss. A cause requires changing one setting while holding the same cached Qlib scores, years, symbols, costs, and execution constant.",
        "next_ablation_order": ["native top-K versus TBA 1 risk layer", "fixed pre-year fit versus 21-session retraining", "30-round cap versus a predeclared larger cap", "rank-only calibration versus magnitude-aware calibration"],
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
