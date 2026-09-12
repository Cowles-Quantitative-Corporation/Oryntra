#!/usr/bin/env python3
"""Fetch a declared CIK manifest from the SEC Company Facts API politely.

The tool does not infer tickers, overwrite data, or import facts into Oryntra.
It leaves a raw, source-addressable acquisition manifest for the separate
normalizer and CorporateRepository import step.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"


def _read_mapping(path: Path) -> list[dict[str, object]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    result = []
    for row in rows:
        ticker = str(row.get("ticker") or "").upper().strip()
        try:
            cik = int(str(row.get("cik") or "").strip())
        except ValueError as exc:
            raise ValueError("Mapping requires numeric ticker,cik rows") from exc
        if not ticker or cik <= 0:
            raise ValueError("Mapping requires nonempty ticker and positive cik")
        result.append({"ticker": ticker, "cik": cik})
    if not result or len({(row["ticker"], row["cik"]) for row in result}) != len(result):
        raise ValueError("Mapping must be nonempty and have unique ticker/cik rows")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mapping-csv", required=True, help="Declared ticker,cik mapping; never inferred by this tool")
    parser.add_argument("--output-dir", required=True, help="New or resumable raw JSON directory")
    parser.add_argument("--user-agent", required=True, help="Identifying contact string required by SEC fair-access guidance")
    parser.add_argument("--minimum-delay-seconds", type=float, default=.2)
    args = parser.parse_args()
    if not args.user_agent.strip() or not (.1 <= args.minimum_delay_seconds <= 60):
        parser.error("Supply an identifying user agent and a delay from 0.1 to 60 seconds")
    mapping = _read_mapping(Path(args.mapping_csv))
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    prior = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"files": []}
    prior_files = {entry["ticker"]: entry for entry in prior.get("files", [])}
    files = []
    for item in mapping:
        ticker, cik = str(item["ticker"]), int(item["cik"])
        destination = output / f"{ticker}-{cik:010d}.json"
        if destination.exists():
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            files.append({"ticker": ticker, "cik": cik, "path": destination.name, "sha256": digest, "status": "existing"})
            continue
        # Do not request compressed content: urllib's basic response reader
        # does not transparently decompress it before JSON validation.
        request = Request(URL.format(cik=cik), headers={"User-Agent": args.user_agent})
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read()
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"SEC fetch failed for {ticker} CIK{cik:010d}: {exc}") from exc
        payload = json.loads(body)
        if int(payload.get("cik", 0)) != cik:
            raise RuntimeError(f"SEC response CIK mismatch for {ticker}")
        destination.write_bytes(body)
        files.append({"ticker": ticker, "cik": cik, "path": destination.name,
                      "sha256": hashlib.sha256(body).hexdigest(), "status": "downloaded"})
        time.sleep(args.minimum_delay_seconds)
    manifest = {"source": "SEC Company Facts API", "source_url_template": URL,
                "mapping_sha256": hashlib.sha256(Path(args.mapping_csv).read_bytes()).hexdigest(),
                "minimum_delay_seconds": args.minimum_delay_seconds, "files": files}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "files": len(files),
                      "downloaded": sum(row["status"] == "downloaded" for row in files),
                      "existing": sum(row["status"] == "existing" for row in files)}))


if __name__ == "__main__":
    main()
