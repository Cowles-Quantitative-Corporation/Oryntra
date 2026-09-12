#!/usr/bin/env python3
"""Summarize the fixed Minerva / TBA 1 / TBA 2 research holdout fairly."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean, median, pstdev


def _stats(values: list[float]) -> dict[str, float | int]:
    return {
        "mean_alpha_pct": mean(values),
        "median_alpha_pct": median(values),
        "best_alpha_pct": max(values),
        "worst_alpha_pct": min(values),
        "negative_years": sum(value < 0 for value in values),
        "positive_years": sum(value > 0 for value in values),
        "dispersion_pct": pstdev(values),
        "mean_negative_alpha_pct": mean([value for value in values if value < 0]),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minerva", type=Path, required=True)
    parser.add_argument("--tba2", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    old = json.loads(args.minerva.read_text())
    tba2 = json.loads(args.tba2.read_text())
    annual = {"Minerva": {}, "TBA 1": {}, "TBA 2": {}}
    for row in old["matched_by_year"]:
        annual["Minerva"][row["year"]] = row["baseline_alpha_pct"]
        annual["TBA 1"][row["year"]] = row["ohlcv_structure_alpha_pct"]
    for row in tba2["trials"]:
        annual["TBA 2"][row["year"]] = row["alpha"]["alpha_pct"]
    years = sorted(set.intersection(*(set(values) for values in annual.values())))
    if len(years) != 10:
        raise ValueError("Expected the same completed ten-year holdout for every model")
    summaries = {name: _stats([annual[name][year] for year in years]) for name in annual}
    winners = []
    for year in years:
        row = {name: annual[name][year] for name in annual}
        winner = max(row, key=row.get)
        winners.append({"year": year, "winner": winner, "alpha_pct": row[winner], "all_alphas_pct": row})
    for name in annual:
        summaries[name]["year_wins"] = sum(row["winner"] == name for row in winners)
        summaries[name]["wins_vs_minerva"] = sum(
            annual[name][year] > annual["Minerva"][year] for year in years if name != "Minerva"
        )
    report = {
        "protocol": "Same fixed ten-year draw, matched annual symbol baskets, benchmark, cash proxy, execution costs and next-open fills.",
        "interpretation": {
            "mean": "Average annual CAPM alpha; sensitive to tail years.",
            "median": "Typical annual alpha; less sensitive to tail years.",
            "dispersion": "Cross-year alpha variability; lower is more stable, not automatically better.",
            "downside": "Worst year and mean negative-year alpha reveal tail-loss severity.",
        },
        "years": years,
        "model_summaries": summaries,
        "year_winners": winners,
        "qualification": "Research-only retrospective comparison on a current-constituent universe. No model qualifies for release or a performance claim from this report.",
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
