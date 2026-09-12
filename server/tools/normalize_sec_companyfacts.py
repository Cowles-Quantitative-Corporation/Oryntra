#!/usr/bin/env python3
"""Normalize one downloaded SEC Company Facts JSON file for CorporateRepository."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.sec_companyfacts import normalize_quarterly_companyfacts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--companyfacts-json", required=True, help="Downloaded SEC Company Facts JSON; never a URL")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--output", required=True, help="New JSON output; refuses overwrite")
    args = parser.parse_args()
    source, output = Path(args.companyfacts_json), Path(args.output)
    if output.exists():
        parser.error("Output exists; choose a new path to preserve the prior normalization")
    payload = json.loads(source.read_text(encoding="utf-8"))
    facts = normalize_quarterly_companyfacts(payload, args.ticker)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"facts": facts}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ticker": args.ticker.upper(), "facts": len(facts), "output": str(output)}))


if __name__ == "__main__":
    main()
