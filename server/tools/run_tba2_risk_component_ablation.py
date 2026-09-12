#!/usr/bin/env python3
"""Measure individual portfolio-risk controls on fixed Qlib prediction files.

This is a research diagnostic, not an optimizer.  It never trains a model and
it changes precisely one construction control per variant.  Re-run it only
after all score files are generated from a fixed model seed.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.alpha_evaluation import regression_alpha
from backend.minerva import minerva_baseline
from backend.portfolio_execution import simulate_book
from backend.universal_engine import portfolio_targets


ROOT = Path("/private/tmp")


def targets(predicted: pd.DataFrame, histories: dict[str, pd.DataFrame], benchmark: pd.Series, config, *, equal_weight: bool = False):
    dates, symbols = next(iter(histories.values())).index, list(histories)
    closes = pd.DataFrame({symbol: histories[symbol]["Close"] for symbol in symbols})
    score = pd.DataFrame(0.0, index=dates, columns=symbols)
    score.loc[predicted.index] = (
        predicted.rank(axis=1, pct=True, method="first").mul(2).sub(1).reindex(columns=symbols).fillna(0.0)
    )
    volatility = closes.pct_change(fill_method=None).rolling(63, min_periods=63).std(ddof=0).clip(lower=.003)
    if equal_weight:
        # Only score-to-weight conversion changes; the covariance calculation
        # in portfolio_targets still uses observed returns.
        volatility.loc[:, :] = 1.0
    return portfolio_targets(
        closes,
        config,
        benchmark.reindex(dates),
        precomputed_panel={"score": score, "volatility": volatility},
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--score-prefix", default="tba2_qlib", help="e.g. tba2_lgb for fixed-seed Qlib scores")
    parser.add_argument("--years", default="", help="comma-separated frozen years; blank means all cached years")
    parser.add_argument("--output", type=Path, default=ROOT / "tba2_risk_component_ablation_20260912.json")
    args = parser.parse_args()
    requested_years = {int(value) for value in args.years.split(",") if value.strip()}

    source = json.loads((ROOT / "minerva_ohlcv_vs_baseline_20260912.json").read_text())
    bars = pd.read_pickle(ROOT / "minerva_ohlcv_sp500_holdout.pkl").sort_index()
    bars.index = pd.DatetimeIndex(bars.index).tz_localize(None) if getattr(bars.index, "tz", None) else pd.DatetimeIndex(bars.index)
    benchmark = (bars["Close"]["SPY"].pct_change().fillna(0.0) + bars["Close"]["QQQ"].pct_change().fillna(0.0)) / 2
    cash = (bars["Close"]["^IRX"] / 100 / 252).reindex(benchmark.index).ffill()
    base = minerva_baseline()
    variants = {
        "full_tba1_risk": base,
        "no_correlation_stress": replace(base, correlation_stress=0.0),
        "no_covariance_shrinkage": replace(base, covariance_shrinkage=0.0),
        # .50 is the validated maximum and means target scaling cannot bind for
        # a long-only portfolio with the supplied daily covariance estimate.
        "no_volatility_target_scaling": replace(base, vol_target=0.50),
        "equal_weight_before_portfolio_risk": base,
    }
    rows: list[dict[str, object]] = []
    for trial in source["baseline"]["trials"]:
        year, symbols = trial["year"], trial["symbols"]
        if requested_years and year not in requested_years:
            continue
        score_path = ROOT / f"{args.score_prefix}_alpha158_scores_{year}.pkl"
        if not score_path.exists():
            continue
        annual = benchmark.loc[f"{year}-01-01":f"{year}-12-31"].index
        warmup = benchmark.loc[benchmark.index < pd.Timestamp(year, 1, 1)].tail(756).index
        dates = warmup.append(annual)
        histories: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            frame = bars.loc[dates, pd.IndexSlice[["Open", "High", "Low", "Close", "Volume"], symbol]].copy()
            frame.columns = frame.columns.droplevel(1)
            histories[symbol] = frame
        predicted = pd.read_pickle(score_path).reindex(index=annual, columns=symbols)
        result: dict[str, object] = {"year": year}
        for name, config in variants.items():
            if name == "equal_weight_before_portfolio_risk":
                # Constant signal-volatility produces equal raw weights while
                # leaving caps, covariance stress and target scaling intact.
                raw_targets = targets(predicted, histories, benchmark, config, equal_weight=True)
                result[name] = regression_alpha(
                    simulate_book(histories, raw_targets, config, cash.reindex(dates))["net"].reindex(annual),
                    benchmark.reindex(annual), cash.reindex(annual),
                )["alpha_pct"]
                continue
            result[name] = regression_alpha(
                simulate_book(histories, targets(predicted, histories, benchmark, config), config, cash.reindex(dates))["net"].reindex(annual),
                benchmark.reindex(annual), cash.reindex(annual),
            )["alpha_pct"]
        rows.append(result)

    if not rows:
        raise RuntimeError(f"No score files matched {args.score_prefix!r}")
    summary = {}
    baseline = np.array([float(row["full_tba1_risk"]) for row in rows])
    for name in variants:
        values = np.array([float(row[name]) for row in rows])
        summary[name] = {
            "mean_alpha_pct": float(values.mean()),
            "mean_delta_vs_full_tba1_pct": float((values - baseline).mean()),
            "years_better_than_full_tba1": int((values > baseline).sum()),
        }
    output = {
        "score_prefix": args.score_prefix,
        "requested_years": sorted(requested_years) if requested_years else "all_cached_years",
        "matched_cached_years": [row["year"] for row in rows],
        "trials": rows,
        "summary": summary,
        "qualification": "One-control-at-a-time diagnostic on held-fixed score files. It does not establish causality for a fresh model run and is incomplete until all ten fixed-seed score files are available.",
    }
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
