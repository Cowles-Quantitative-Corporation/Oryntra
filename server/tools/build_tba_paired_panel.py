#!/usr/bin/env python3
"""Download and freeze an adjusted daily panel for paired TBA engineering tests.

The default universe is predeclared and deliberately contains long-history
current survivors. That makes the output useful for same-data A/B engineering,
but not a point-in-time investable-universe performance claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
import yfinance as yf


DEFAULT_STOCKS = (
    "AAPL", "MSFT", "AMZN", "CSCO", "INTC", "IBM", "ORCL", "QCOM", "TXN",
    "JPM", "BAC", "C", "GS", "MS", "WFC", "AXP", "BLK", "XOM", "CVX",
    "SLB", "JNJ", "PFE", "MRK", "AMGN", "GILD", "WMT", "COST", "HD",
    "MCD", "KO", "PEP", "PG", "CAT", "BA", "HON", "GE", "UPS", "FDX",
    "UNP", "TGT", "LOW", "DIS", "NKE",
)
BENCHMARKS = ("SPY", "QQQ")
RISK_FREE_PROXY = "^IRX"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars-output", type=Path, required=True)
    parser.add_argument("--risk-free-output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    parser.add_argument("--start", default="1999-01-01")
    parser.add_argument("--end", default="2026-01-01", help="Exclusive end date")
    parser.add_argument("--stocks", default=",".join(DEFAULT_STOCKS))
    args = parser.parse_args()
    outputs = (args.bars_output, args.risk_free_output, args.manifest_output)
    if any(path.exists() for path in outputs):
        parser.error("An output already exists; choose new paths to preserve the frozen panel")
    stocks = tuple(dict.fromkeys(symbol.strip().upper() for symbol in args.stocks.split(",") if symbol.strip()))
    requested = stocks + BENCHMARKS + (RISK_FREE_PROXY,)
    downloaded = yf.download(
        list(requested), start=args.start, end=args.end, auto_adjust=True,
        actions=False, group_by="ticker", threads=True, progress=False,
    )
    if downloaded.empty:
        raise RuntimeError("No market data were downloaded")
    records = []
    coverage = {}
    for symbol in stocks + BENCHMARKS:
        if symbol not in downloaded.columns.get_level_values(0):
            coverage[symbol] = 0
            continue
        frame = downloaded[symbol].rename_axis("date")
        columns = {name.lower(): name for name in frame.columns}
        required = ("open", "high", "low", "close", "volume")
        if not set(required).issubset(columns):
            coverage[symbol] = 0
            continue
        clean = frame[[columns[name] for name in required]].copy()
        clean.columns = required
        clean = clean.dropna()
        clean = clean[(clean[["open", "high", "low", "close"]] > 0).all(axis=1) & (clean["volume"] >= 0)]
        # Adjustment multiplication can leave High/Low a few floating-point
        # ulps inside Open/Close. Restore the candle identity without changing
        # any open, close, return, date, or volume observation.
        clean["high"] = clean[["high", "open", "close"]].max(axis=1)
        clean["low"] = clean[["low", "open", "close"]].min(axis=1)
        coverage[symbol] = len(clean)
        for date, row in clean.iterrows():
            records.append({"date": str(pd.Timestamp(date).date()), "ticker": symbol, **{name: float(row[name]) for name in required}})
    bars = pd.DataFrame(records).sort_values(["date", "ticker"])
    if not set(BENCHMARKS).issubset(set(bars["ticker"])):
        raise RuntimeError("The downloaded panel lacks SPY or QQQ")
    risk_source = downloaded[RISK_FREE_PROXY]["Close"].dropna().astype(float)
    benchmark_dates = pd.DatetimeIndex(sorted(pd.to_datetime(bars.loc[bars.ticker == "SPY", "date"]).unique()))
    daily_cash = (risk_source.reindex(benchmark_dates).ffill() / 100 / 252).dropna()
    risk_free = pd.DataFrame({"date": [str(date.date()) for date in daily_cash.index], "return_daily": daily_cash.to_numpy()})
    for path in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
    bars.to_csv(args.bars_output, index=False)
    risk_free.to_csv(args.risk_free_output, index=False)
    manifest = {
        "status": "frozen_current_survivor_engineering_panel",
        "downloaded_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "source": "Yahoo Finance via yfinance",
        "price_adjustment": "auto_adjust=True",
        "candle_normalization": "High=max(High,Open,Close); Low=min(Low,Open,Close) for floating-point envelope identity",
        "requested_start": args.start,
        "requested_end_exclusive": args.end,
        "predeclared_stocks": list(stocks),
        "benchmarks": list(BENCHMARKS),
        "risk_free_proxy": RISK_FREE_PROXY,
        "coverage_rows": coverage,
        "bars_sha256": hashlib.sha256(args.bars_output.read_bytes()).hexdigest(),
        "risk_free_sha256": hashlib.sha256(args.risk_free_output.read_bytes()).hexdigest(),
        "qualification": "Current-survivor panel for identical-data paired engineering tests; not point-in-time membership or release evidence.",
    }
    args.manifest_output.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
