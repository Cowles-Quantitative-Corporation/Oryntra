#!/usr/bin/env python3
"""Run fixed-config, seeded nonconsecutive annual Universal V2 trials.

The input must include daily OHLCV for stocks plus SPY and QQQ.  It is kept
separate from tuning: this command never searches configurations after it
draws years.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.universal_engine import UniversalConfig
from backend.universal_yearly_protocol import SeededYearProtocol, run_seeded_yearly_trials


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars-csv", required=True, help="date,ticker,open,high,low,close,volume; must include SPY and QQQ")
    parser.add_argument("--risk-free-csv", required=True, help="date,return_daily in decimal units")
    parser.add_argument("--config-json", required=True, help="Frozen UniversalConfig JSON object")
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, default=20260906)
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--minimum-year-spacing", type=int, default=2)
    parser.add_argument("--minimum-symbols", type=int, default=100)
    parser.add_argument("--maximum-symbols-per-trial", type=int, default=200)
    parser.add_argument("--warmup-sessions", type=int, default=756)
    parser.add_argument("--earliest-year", type=int, default=2000)
    parser.add_argument("--declared-years", help="Comma-separated eligible years for a reproducible chunk of a previously recorded draw")
    parser.add_argument("--corporate-facts-json", help="Normalized SEC/other fact JSON containing a facts array")
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        parser.error("Output exists; preserve prior experiments and select a new path")
    bars = pd.read_csv(args.bars_csv)
    required = {"date", "ticker", "open", "high", "low", "close", "volume"}
    if not required.issubset(bars.columns):
        parser.error("Bars missing fields: " + ", ".join(sorted(required - set(bars.columns))))
    bars["date"] = pd.to_datetime(bars["date"])
    if bars.duplicated(["date", "ticker"]).any():
        parser.error("Bars contain duplicate symbol/session rows")
    histories = {symbol: group.set_index("date").sort_index().rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
                 for symbol, group in bars.groupby("ticker")}
    if not {"SPY", "QQQ"}.issubset(histories):
        parser.error("Bars must include SPY and QQQ for the fixed alpha benchmark")
    benchmark = (histories["SPY"]["Close"].pct_change(fill_method=None) + histories["QQQ"]["Close"].pct_change(fill_method=None)) / 2
    cash_frame = pd.read_csv(args.risk_free_csv)
    if not {"date", "return_daily"}.issubset(cash_frame.columns):
        parser.error("Risk-free input requires date,return_daily")
    risk_free = pd.Series(cash_frame.return_daily.to_numpy(dtype=float), index=pd.to_datetime(cash_frame.date))
    config = UniversalConfig(**json.loads(Path(args.config_json).read_text()))
    protocol = SeededYearProtocol(seed=args.seed, selected_years=args.years, minimum_year_spacing=args.minimum_year_spacing,
                                  minimum_symbols=args.minimum_symbols, maximum_symbols_per_trial=args.maximum_symbols_per_trial, warmup_sessions=args.warmup_sessions,
                                  earliest_year=args.earliest_year)
    stock_histories = {symbol: frame for symbol, frame in histories.items() if symbol not in {"SPY", "QQQ"}}
    market_closes = pd.DataFrame({symbol: histories[symbol]["Close"] for symbol in ("SPY", "QQQ")})
    declared_years = None if not args.declared_years else [int(year) for year in args.declared_years.split(",") if year.strip()]
    corporate_facts = None
    if args.corporate_facts_json:
        payload = json.loads(Path(args.corporate_facts_json).read_text())
        if not isinstance(payload.get("facts"), list):
            parser.error("Corporate fact JSON requires a facts array")
        corporate_facts = payload["facts"]
    report = run_seeded_yearly_trials(stock_histories, config, benchmark, risk_free, protocol=protocol,
                                      market_closes=market_closes, corporate_facts=corporate_facts,
                                      declared_years=declared_years)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"output": str(output), "selected_years": report["selected_years"], "summary": report["summary"]}, indent=2))


if __name__ == "__main__":
    main()
