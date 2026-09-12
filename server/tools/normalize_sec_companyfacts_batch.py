#!/usr/bin/env python3
"""Normalize a declared SEC Company Facts acquisition directory in one pass."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.sec_companyfacts import normalize_quarterly_companyfacts


def _mapping(path: Path) -> list[tuple[str, int]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = [(str(row["ticker"]).upper().strip(), int(row["cik"])) for row in csv.DictReader(handle)]
    if not rows or len(set(rows)) != len(rows):
        raise ValueError("Mapping must have unique ticker,cik rows")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mapping-csv", required=True)
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument("--output", required=True, help="New normalized JSON output; refuses overwrite")
    args = parser.parse_args()
    raw, output = Path(args.raw_dir), Path(args.output)
    if output.exists():
        parser.error("Output exists; preserve the prior normalization")
    facts, files = [], []
    for ticker, cik in _mapping(Path(args.mapping_csv)):
        source = raw / f"{ticker}-{cik:010d}.json"
        if not source.exists():
            raise ValueError(f"Missing raw Company Facts file for {ticker} CIK{cik:010d}")
        body = source.read_bytes()
        facts.extend(normalize_quarterly_companyfacts(json.loads(body), ticker))
        files.append({"ticker": ticker, "cik": cik, "path": source.name, "sha256": hashlib.sha256(body).hexdigest()})
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {"source": "SEC Company Facts API", "mapping_sha256": hashlib.sha256(Path(args.mapping_csv).read_bytes()).hexdigest(),
               "raw_files": files, "facts": facts}
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"symbols": len(files), "facts": len(facts), "output": str(output)}))


if __name__ == "__main__":
    main()
