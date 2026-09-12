#!/usr/bin/env python3
"""Evaluate a bounded config search on development dates, then freeze and confirm once."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.universal_engine import UniversalConfig
from backend.universal_research import run_universal
from backend.universal_market_context import build_completed_market_observations
from backend.universal_fundamentals import build_fundamental_score_panel


def manifest_configs(manifest):
    """Apply one explicitly shared market-context policy without hiding per-candidate knobs."""
    shared_context = manifest.get("shared_market_context")
    configs = []
    for item in manifest["candidates"]:
        spec = dict(item)
        if shared_context is not None and "market_context" not in spec:
            spec["market_context"] = shared_context
        configs.append(UniversalConfig(**spec))
    return configs


def select_configuration(histories, candidates, market, rf, start, end, market_observations=None,
                         fundamental_scores=None, learning_panels=None):
    """Only the caller's development histories and date interval enter selection."""
    if not 1 <= len(candidates) <= 24:
        raise ValueError("Declare 1–24 candidate configurations before evaluating returns")
    rows = []
    for config in candidates:
        report = run_universal(histories, config, market, rf, start, end, market_observations,
                               fundamental_scores, learning_panels)
        annual = [row for row in report["alpha"].get("annual", []) if row.get("complete") and row.get("status") == "available"]
        if len(annual) < 3:
            raise ValueError("Selection requires at least three complete years with benchmark and cash returns")
        alphas = [row["alpha_pct"] for row in annual]
        # Fixed consistency penalty prioritizes mean alpha and limits weak years.
        objective = sum(alphas) / len(alphas) - sum(max(0, 2.5 - a) for a in alphas) / len(alphas)
        rows.append({"configuration": asdict(config), "config_fingerprint": config.fingerprint,
                     "dataset_fingerprint": report["dataset_fingerprint"], "objective": objective,
                     "mean_alpha_pct": sum(alphas) / len(alphas), "annual": annual,
                     "negative_years": sum(a < 0 for a in alphas), "performance": report["results"][0]})
    winner = max(range(len(rows)), key=lambda i: rows[i]["objective"])
    return candidates[winner], rows


def run_study(histories, market, rf, manifest, market_observations=None, fundamental_scores=None,
              learning_panels=None):
    development, confirmation = manifest["development_symbols"], manifest["confirmation_symbols"]
    if not development or not confirmation or set(development) & set(confirmation):
        raise ValueError("Development and confirmation baskets must be nonempty and disjoint")
    if len(set(development)) != len(development) or len(set(confirmation)) != len(confirmation):
        raise ValueError("Duplicate symbols in manifest")
    train_start, train_end = manifest["development_start"], manifest["development_end"]
    test_start, test_end = manifest["forward_start"], manifest["forward_end"]
    if not (pd.Timestamp(train_start) <= pd.Timestamp(train_end) < pd.Timestamp(test_start) <= pd.Timestamp(test_end)):
        raise ValueError("Development dates must strictly precede forward dates")
    configs = manifest_configs(manifest)
    dev = {name: histories[name] for name in development}
    chosen, search = select_configuration(dev, configs, market, rf, train_start, train_end, market_observations,
                                           fundamental_scores, learning_panels)
    forward = run_universal(dev, chosen, market, rf, test_start, test_end, market_observations,
                            fundamental_scores, learning_panels)
    fresh = run_universal({name: histories[name] for name in confirmation}, chosen, market, rf,
                          manifest["confirmation_start"], manifest["confirmation_end"], market_observations,
                          fundamental_scores, learning_panels)
    return {"manifest": manifest, "selected_configuration": asdict(chosen),
            "selected_fingerprint": chosen.fingerprint, "candidate_count": len(configs),
            "development": search, "forward": forward, "fresh_confirmation": fresh,
            "qualification": "exploratory_cross_sectional_confirmation; current-survivor universe and shared market regimes prevent a claim of independent ten-year prospective evidence",
            "promotion": "research_only"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars-csv", required=True, help="date,ticker,open,close,volume; include SPY and QQQ")
    parser.add_argument("--risk-free-csv", required=True, help="date,return_daily in decimal units")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--fundamental-events-json", help="Availability-dated SEC events; required by fundamental candidates")
    parser.add_argument("--learning-bars-csv", help="Broad OHLCV training panel; required by walk-forward ridge candidates")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        parser.error("Output exists; preserve prior experiments and choose a new path")
    frame = pd.read_csv(args.bars_csv)
    frame["date"] = pd.to_datetime(frame["date"])
    if frame.duplicated(["ticker", "date"]).any():
        parser.error("Duplicate ticker/session rows")
    column_map = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
    histories = {name: group.set_index("date").sort_index().rename(columns=column_map) for name, group in frame.groupby("ticker")}
    market = pd.DataFrame({name: histories[name].Close for name in ("SPY", "QQQ")}).pct_change(fill_method=None).sum(axis=1, min_count=2) / 2
    cash = pd.read_csv(args.risk_free_csv)
    rf = pd.Series(cash.return_daily.to_numpy(), index=pd.to_datetime(cash.date))
    manifest = json.loads(Path(args.manifest).read_text())
    configs = manifest_configs(manifest)
    market_observations = None
    if any(config.market_context.enabled for config in configs):
        closes = pd.DataFrame({name: item.Close for name, item in histories.items()})
        market_observations = build_completed_market_observations(
            closes, configs[0].market_context, source_manifest_id=f"{manifest.get('experiment_id', 'universal-study')}-frozen-ohlcv",
        )
    fundamental_scores = None
    if any(config.fundamental_weight for config in configs):
        if not args.fundamental_events_json:
            parser.error("Fundamental candidates require --fundamental-events-json")
        payload = json.loads(Path(args.fundamental_events_json).read_text())
        fundamental_scores = build_fundamental_score_panel(payload.get("events", []),
                                                           pd.DatetimeIndex(frame.date.drop_duplicates().sort_values()),
                                                           [name for name in histories if name not in {"SPY", "QQQ"}])
    learning_panels = None
    if any(config.alpha_model == "walk_forward_ridge" for config in configs):
        if not args.learning_bars_csv:
            parser.error("Walk-forward ridge candidates require --learning-bars-csv")
        learning_frame = pd.read_csv(args.learning_bars_csv)
        required = {"date", "ticker", "open", "close", "volume"}
        if not required.issubset(learning_frame.columns):
            parser.error("Learning bars missing fields: " + ", ".join(sorted(required - set(learning_frame.columns))))
        learning_frame["date"] = pd.to_datetime(learning_frame["date"])
        if learning_frame.duplicated(["date", "ticker"]).any():
            parser.error("Learning bars contain duplicate ticker/session rows")
        learning_panels = tuple(learning_frame.pivot(index="date", columns="ticker", values=field).sort_index()
                                for field in ("close", "open", "volume"))
        complete = learning_panels[0].notna().all() & learning_panels[1].notna().all() & learning_panels[2].notna().all()
        learning_panels = tuple(panel.loc[:, complete] for panel in learning_panels)
        missing = sorted((set(manifest["development_symbols"]) | set(manifest["confirmation_symbols"])) - set(learning_panels[0].columns))
        if missing:
            parser.error("Learning panel does not cover evaluation symbols: " + ", ".join(missing))
    report = run_study(histories, market, rf, manifest, market_observations, fundamental_scores, learning_panels)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
    print(json.dumps({"output": str(output), "selected": report["selected_configuration"],
                      "forward_alpha": report["forward"]["alpha"].get("mean_annual_alpha_pct"),
                      "confirmation_alpha": report["fresh_confirmation"]["alpha"].get("mean_annual_alpha_pct"),
                      "passes": report["fresh_confirmation"]["alpha"].get("passes", False)}, indent=2))


if __name__ == "__main__":
    main()
