#!/usr/bin/env python3
"""Hold cached Qlib scores fixed and measure TBA 1 risk-layer impact."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from backend.alpha_evaluation import regression_alpha
from backend.minerva import minerva_baseline
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


def main():
    source = json.loads((ROOT / "minerva_ohlcv_vs_baseline_20260912.json").read_text())
    bars = pd.read_pickle(ROOT / "minerva_ohlcv_sp500_holdout.pkl").sort_index()
    bars.index = pd.DatetimeIndex(bars.index).tz_localize(None) if getattr(bars.index, "tz", None) else pd.DatetimeIndex(bars.index)
    benchmark = (bars["Close"]["SPY"].pct_change().fillna(0) + bars["Close"]["QQQ"].pct_change().fillna(0)) / 2
    cash = (bars["Close"]["^IRX"] / 100 / 252).reindex(benchmark.index).ffill()
    config, trials = minerva_baseline(), []
    for trial in source["baseline"]["trials"]:
        year, symbols = trial["year"], trial["symbols"]
        score_path = ROOT / f"tba2_qlib_alpha158_scores_{year}.pkl"
        if not score_path.exists():
            continue
        annual = benchmark.loc[f"{year}-01-01":f"{year}-12-31"].index
        warmup = benchmark.loc[benchmark.index < pd.Timestamp(year, 1, 1)].tail(756).index
        dates = warmup.append(annual)
        histories = {}
        for symbol in symbols:
            frame = bars.loc[dates, pd.IndexSlice[["Open", "High", "Low", "Close", "Volume"], symbol]].copy()
            frame.columns = frame.columns.droplevel(1)
            histories[symbol] = frame
        scores = pd.read_pickle(score_path).reindex(index=annual, columns=symbols)
        native = simulate_book(histories, native_topk_targets(scores, dates, symbols), config, cash.reindex(dates))["net"].reindex(annual)
        risk = simulate_book(histories, risk_targets(scores, histories, benchmark, config), config, cash.reindex(dates))["net"].reindex(annual)
        native_alpha = regression_alpha(native, benchmark.reindex(annual), cash.reindex(annual))["alpha_pct"]
        risk_alpha = regression_alpha(risk, benchmark.reindex(annual), cash.reindex(annual))["alpha_pct"]
        trials.append({"year": year, "native_top24_alpha_pct": native_alpha, "tba1_risk_alpha_pct": risk_alpha,
                       "risk_layer_delta_alpha_pct": risk_alpha - native_alpha})
    result = {"matched_cached_years": [row["year"] for row in trials], "trials": trials,
              "summary": {"mean_risk_layer_delta_alpha_pct": float(np.mean([r["risk_layer_delta_alpha_pct"] for r in trials])),
                          "risk_layer_wins": sum(r["risk_layer_delta_alpha_pct"] > 0 for r in trials),
                          "native_top24_wins": sum(r["risk_layer_delta_alpha_pct"] < 0 for r in trials)},
              "qualification": "This isolates portfolio construction only on cached Qlib scores. It is incomplete until all ten fixed-seed score caches exist."}
    (ROOT / "tba2_risk_layer_ablation_20260912.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
