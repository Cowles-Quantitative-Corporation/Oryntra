#!/usr/bin/env python3
"""Fair three-arm study: no supervisor vs frozen V1 vs Risk Supervisor V2.0.2.

All arms use the same TBA8 alpha path, symbols, dates, costs, benchmark, cash
series and yearly protocol. Only the risk supervisor changes.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.minerva import risk_v202_candidate, tba8_institutional_risk_candidate
from backend.universal_risk_supervisor import RiskSupervisorConfig
from backend.universal_yearly_protocol import SeededYearProtocol, eligible_years, run_seeded_yearly_trials, select_seeded_years


def load_inputs(bars_path: Path, risk_free_path: Path):
    bars = pd.read_csv(bars_path)
    required = {"date", "ticker", "open", "high", "low", "close", "volume"}
    if not required.issubset(bars.columns):
        raise ValueError("Bars missing fields: " + ", ".join(sorted(required - set(bars.columns))))
    bars["date"] = pd.to_datetime(bars["date"])
    if bars.duplicated(["date", "ticker"]).any():
        raise ValueError("Bars contain duplicate symbol/session rows")
    histories = {
        symbol: group.set_index("date").sort_index().rename(
            columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"},
        )
        for symbol, group in bars.groupby("ticker")
    }
    if not {"SPY", "QQQ"}.issubset(histories):
        raise ValueError("Bars must include SPY and QQQ")
    benchmark = (histories["SPY"]["Close"].pct_change(fill_method=None) + histories["QQQ"]["Close"].pct_change(fill_method=None)) / 2
    cash = pd.read_csv(risk_free_path)
    if not {"date", "return_daily"}.issubset(cash.columns):
        raise ValueError("Risk-free input requires date,return_daily")
    risk_free = pd.Series(cash["return_daily"].to_numpy(float), index=pd.to_datetime(cash["date"]), dtype=float)
    stocks = {symbol: history for symbol, history in histories.items() if symbol not in {"SPY", "QQQ"}}
    market_closes = pd.DataFrame({symbol: histories[symbol]["Close"] for symbol in ("SPY", "QQQ")})
    return stocks, market_closes, benchmark, risk_free


def summarize(report: dict) -> dict:
    sharpes = np.asarray([trial["excess_return_sharpe"] for trial in report["trials"]], dtype=float)
    alphas = np.asarray([trial["alpha"]["alpha_pct"] for trial in report["trials"]], dtype=float)
    drawdowns = np.asarray([trial["performance"]["max_drawdown_pct"] for trial in report["trials"]], dtype=float)
    turnover = np.asarray([trial["performance"]["annualized_turnover"] for trial in report["trials"]], dtype=float)
    exposure = np.asarray([trial["performance"]["average_gross_exposure"] for trial in report["trials"]], dtype=float)
    return {
        "mean_excess_sharpe": float(np.nanmean(sharpes)),
        "minimum_annual_excess_sharpe": float(np.nanmin(sharpes)),
        "mean_annual_alpha_pct": float(np.nanmean(alphas)),
        "mean_max_drawdown_pct": float(np.nanmean(drawdowns)),
        "worst_max_drawdown_pct": float(np.nanmin(drawdowns)),
        "mean_annualized_turnover": float(np.nanmean(turnover)),
        "mean_gross_exposure": float(np.nanmean(exposure)),
        "annual_excess_sharpe": sharpes.tolist(),
        "annual_alpha_pct": alphas.tolist(),
        "annual_max_drawdown_pct": drawdowns.tolist(),
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
    parser.add_argument("--declared-years", help="Comma-separated years frozen before running this comparison")
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
        declared_years = [row["year"] for row in select_seeded_years(availability, protocol)]
    else:
        available = {row["year"] for row in availability if row["eligible"]}
        missing = sorted(set(declared) - available)
        if missing:
            parser.error("Declared years are unavailable: " + ", ".join(map(str, missing)))
        declared_years = sorted(declared)

    v1_config = tba8_institutional_risk_candidate()
    no_risk_config = replace(v1_config, risk_supervisor=RiskSupervisorConfig(enabled=False))
    v202_config = risk_v202_candidate()

    reports = {}
    for name, config in (("no_supervisor", no_risk_config), ("v1", v1_config), ("v202", v202_config)):
        reports[name] = run_seeded_yearly_trials(
            stocks, config, benchmark, risk_free,
            protocol=protocol, market_closes=market_closes, declared_years=declared_years,
        )

    fingerprints = {
        name: [trial["dataset_fingerprint"] for trial in report["trials"]]
        for name, report in reports.items()
    }
    if not (fingerprints["no_supervisor"] == fingerprints["v1"] == fingerprints["v202"]):
        raise RuntimeError("The three risk arms did not use identical datasets")

    summary = {name: summarize(report) for name, report in reports.items()}
    v1, v202 = summary["v1"], summary["v202"]
    result = {
        "status": "research_only",
        "protocol": {
            "seed": args.seed,
            "declared_years": declared_years,
            "paired_change": "same TBA8 alpha path; no supervisor vs frozen V1 vs V2.0.2",
        },
        "input_fingerprint": hashlib.sha256(args.bars_csv.read_bytes() + args.risk_free_csv.read_bytes()).hexdigest(),
        **summary,
        "v202_minus_v1": {
            "mean_excess_sharpe": v202["mean_excess_sharpe"] - v1["mean_excess_sharpe"],
            "minimum_annual_excess_sharpe": v202["minimum_annual_excess_sharpe"] - v1["minimum_annual_excess_sharpe"],
            "mean_annual_alpha_pct": v202["mean_annual_alpha_pct"] - v1["mean_annual_alpha_pct"],
            "mean_max_drawdown_pct": v202["mean_max_drawdown_pct"] - v1["mean_max_drawdown_pct"],
            "mean_annualized_turnover": v202["mean_annualized_turnover"] - v1["mean_annualized_turnover"],
            "mean_gross_exposure": v202["mean_gross_exposure"] - v1["mean_gross_exposure"],
        },
        "dataset_fingerprints": fingerprints["v202"],
        "promotion_question": (
            "Does V2.0.2 improve V1 on alpha, downside and/or Sharpe without materially increasing turnover, "
            "and are improvements stable rather than concentrated in one year?"
        ),
        "qualification": (
            "The previously inspected years are development evidence now. A favorable rerun is not an unseen holdout; "
            "promotion still requires a fresh point-in-time-universe holdout and parameter/component robustness."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
