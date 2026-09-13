#!/usr/bin/env python3
"""Hold cached Qlib scores fixed and compare native, legacy, and supervised risk."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.alpha_evaluation import regression_alpha
from backend.minerva import minerva_baseline, tba2_qlib_risk_candidate
from backend.portfolio_execution import simulate_book
from backend.universal_engine import portfolio_targets


ROOT = Path("/private/tmp")


def risk_targets(predicted, histories, benchmark, config):
    dates, symbols = next(iter(histories.values())).index, list(histories)
    closes = pd.DataFrame({s: histories[s]["Close"] for s in symbols})
    opens = pd.DataFrame({s: histories[s]["Open"] for s in symbols})
    volumes = pd.DataFrame({s: histories[s]["Volume"] for s in symbols})
    score = pd.DataFrame(0.0, index=dates, columns=symbols)
    score.loc[predicted.index] = predicted.rank(axis=1, pct=True, method="first").mul(2).sub(1).reindex(columns=symbols).fillna(0.0)
    vol = closes.pct_change(fill_method=None).rolling(63, min_periods=63).std(ddof=0).clip(lower=.003)
    return portfolio_targets(closes, config, benchmark.reindex(dates), opens=opens, volumes=volumes,
                             precomputed_panel={"score": score, "volatility": vol})


def native_topk_targets(predicted, dates, symbols):
    result = pd.DataFrame(0.0, index=dates, columns=symbols)
    periods = predicted.index.to_period("W-FRI")
    current = pd.Series(0.0, index=symbols)
    for index, day in enumerate(predicted.index):
        if index and periods[index] == periods[index - 1]:
            result.loc[day] = current
            continue
        selected = predicted.loc[day].dropna().nlargest(24).index
        current = pd.Series(0.0, index=symbols)
        current.loc[selected] = 1 / len(selected)
        result.loc[day] = current
    return result


def excess_sharpe(returns, cash):
    excess = (returns - cash.reindex(returns.index)).dropna()
    volatility = float(excess.std(ddof=0))
    return float(np.sqrt(252) * excess.mean() / volatility) if volatility > 0 else None


def measurements(returns, benchmark, cash):
    return {
        "alpha_pct": regression_alpha(returns, benchmark.reindex(returns.index), cash.reindex(returns.index))["alpha_pct"],
        "excess_sharpe": excess_sharpe(returns, cash),
    }


def mean_available(values):
    available = [value for value in values if value is not None]
    return float(np.mean(available)) if available else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "minerva_ohlcv_vs_baseline_20260912.json")
    parser.add_argument("--bars", type=Path, default=ROOT / "minerva_ohlcv_sp500_holdout.pkl")
    parser.add_argument("--score-directory", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=ROOT / "tba_risk_supervisor_oos.json")
    args = parser.parse_args()
    missing = [str(path) for path in (args.manifest, args.bars) if not path.is_file()]
    if missing:
        raise RuntimeError(f"Missing frozen out-of-sample inputs: {', '.join(missing)}")
    source = json.loads(args.manifest.read_text())
    bars = pd.read_pickle(args.bars).sort_index()
    bars.index = pd.DatetimeIndex(bars.index).tz_localize(None) if getattr(bars.index, "tz", None) else pd.DatetimeIndex(bars.index)
    benchmark = (bars["Close"]["SPY"].pct_change().fillna(0) + bars["Close"]["QQQ"].pct_change().fillna(0)) / 2
    cash = (bars["Close"]["^IRX"] / 100 / 252).reindex(benchmark.index).ffill()
    config, legacy_config, trials = tba2_qlib_risk_candidate(), minerva_baseline(), []
    declared_trials = source["baseline"]["trials"]
    missing_scores = [
        str(args.score_directory / f"tba2_qlib_alpha158_scores_{trial['year']}.pkl")
        for trial in declared_trials
        if not (args.score_directory / f"tba2_qlib_alpha158_scores_{trial['year']}.pkl").is_file()
    ]
    if missing_scores:
        raise RuntimeError("Missing scores for declared out-of-sample years: " + ", ".join(missing_scores))
    for trial in declared_trials:
        year, symbols = trial["year"], trial["symbols"]
        score_path = args.score_directory / f"tba2_qlib_alpha158_scores_{year}.pkl"
        annual = benchmark.loc[f"{year}-01-01":f"{year}-12-31"].index
        warmup = benchmark.loc[benchmark.index < pd.Timestamp(year, 1, 1)].tail(756).index
        dates = warmup.append(annual)
        histories = {}
        for symbol in symbols:
            frame = bars.loc[dates, pd.IndexSlice[["Open", "High", "Low", "Close", "Volume"], symbol]].copy()
            frame.columns = frame.columns.droplevel(1)
            histories[symbol] = frame
        scores = pd.read_pickle(score_path).reindex(index=annual, columns=symbols)
        native = simulate_book(histories, native_topk_targets(scores, dates, symbols), legacy_config, cash.reindex(dates))["net"].reindex(annual)
        legacy = simulate_book(histories, risk_targets(scores, histories, benchmark, legacy_config), legacy_config, cash.reindex(dates))["net"].reindex(annual)
        supervised = simulate_book(histories, risk_targets(scores, histories, benchmark, config), config, cash.reindex(dates))["net"].reindex(annual)
        trials.append({
            "year": year,
            "qlib_native_top24": measurements(native, benchmark, cash),
            "legacy_oryntra_risk": measurements(legacy, benchmark, cash),
            "tba_supervised_risk": measurements(supervised, benchmark, cash),
        })
    if not trials:
        raise RuntimeError("No frozen Qlib score years were available; out-of-sample results cannot be reported")
    systems = ("qlib_native_top24", "legacy_oryntra_risk", "tba_supervised_risk")
    result = {"matched_cached_years": [row["year"] for row in trials], "trials": trials,
              "summary": {name: {
                  "mean_alpha_pct": float(np.mean([row[name]["alpha_pct"] for row in trials])),
                  "mean_excess_sharpe": mean_available(row[name]["excess_sharpe"] for row in trials),
                  "minimum_annual_excess_sharpe": min((row[name]["excess_sharpe"] for row in trials if row[name]["excess_sharpe"] is not None), default=None),
                  "positive_sharpe_years": sum((row[name]["excess_sharpe"] or 0) > 0 for row in trials),
              } for name in systems},
              "selection_rule": "TBA supervisor advances only if its mean out-of-sample excess Sharpe exceeds both Qlib native top-24 and legacy Oryntra risk, without worse minimum annual Sharpe.",
              "qualification": "This isolates portfolio construction on frozen Qlib scores. The supervisor parameters must be frozen before these years are scored; missing score years are never imputed."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
