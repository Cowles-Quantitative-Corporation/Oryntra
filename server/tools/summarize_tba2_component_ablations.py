#!/usr/bin/env python3
"""Combine resumable one-year TBA2 risk-ablation outputs without re-running them."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    rows = []
    for path in args.inputs:
        payload = json.loads(path.read_text())
        rows.extend(payload["trials"])
    rows = sorted({int(row["year"]): row for row in rows}.values(), key=lambda row: int(row["year"]))
    if not rows:
        raise ValueError("No ablation trials supplied")
    names = [name for name in rows[0] if name != "year"]
    baseline = np.array([float(row["full_tba1_risk"]) for row in rows])
    summary = {}
    for name in names:
        values = np.array([float(row[name]) for row in rows])
        summary[name] = {
            "mean_alpha_pct": float(values.mean()),
            "median_alpha_pct": float(np.median(values)),
            "positive_years": int((values > 0).sum()),
            "mean_delta_vs_full_tba1_pct": float((values - baseline).mean()),
            "years_better_than_full_tba1": int((values > baseline).sum()),
        }
    output = {
        "matched_years": [row["year"] for row in rows],
        "trials": rows,
        "summary": summary,
        "qualification": "This summarizes cached-score diagnostics only. None of these variants may be promoted until the score files have fixed-seed provenance and the prescribed unseen evaluation passes.",
    }
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output["summary"], indent=2))


if __name__ == "__main__":
    main()
