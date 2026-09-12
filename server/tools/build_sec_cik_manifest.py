#!/usr/bin/env python3
"""Build a declared ticker/CIK manifest from a saved SEC ticker snapshot.

Exact ticker matching is intentional.  A missing or renamed historical symbol
is evidence that a point-in-time identity resolver is needed, not permission
to guess a successor CIK.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def _symbols(path: Path) -> list[str]:
    values = [line.strip().upper() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not values or len(set(values)) != len(values):
        raise ValueError("Symbols file must be nonempty with one unique ticker per line")
    return values


def _index(payload: dict) -> dict[str, int]:
    if not isinstance(payload, dict):
        raise ValueError("SEC ticker snapshot must be a JSON object")
    result: dict[str, int] = {}
    for row in payload.values():
        try:
            ticker, cik = str(row["ticker"]).upper().strip(), int(row["cik_str"])
        except (KeyError, TypeError, ValueError):
            continue
        if ticker and cik > 0 and ticker not in result:
            result[ticker] = cik
    if not result:
        raise ValueError("SEC ticker snapshot contains no valid ticker/CIK records")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols-file", required=True)
    parser.add_argument("--sec-tickers-json", required=True, help="Saved SEC company_tickers.json snapshot")
    parser.add_argument("--output", required=True, help="New ticker,cik CSV; refuses overwrite")
    parser.add_argument("--missing-output", required=True, help="New unresolved-symbol text file; refuses overwrite")
    args = parser.parse_args()
    output, missing_output = Path(args.output), Path(args.missing_output)
    if output.exists() or missing_output.exists():
        parser.error("Manifest or missing-symbol output exists; preserve prior identity evidence")
    symbols = _symbols(Path(args.symbols_file))
    lookup = _index(json.loads(Path(args.sec_tickers_json).read_text(encoding="utf-8")))
    matched = [{"ticker": symbol, "cik": lookup[symbol]} for symbol in symbols if symbol in lookup]
    missing = [symbol for symbol in symbols if symbol not in lookup]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ticker", "cik"])
        writer.writeheader()
        writer.writerows(matched)
    missing_output.parent.mkdir(parents=True, exist_ok=True)
    missing_output.write_text("\n".join(missing) + ("\n" if missing else ""), encoding="utf-8")
    print(json.dumps({"symbols": len(symbols), "matched": len(matched), "missing": len(missing),
                      "manifest": str(output), "missing_output": str(missing_output)}))


if __name__ == "__main__":
    main()
