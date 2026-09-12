import csv
import json
from pathlib import Path

from tools.normalize_sec_companyfacts_batch import _mapping


def test_batch_mapping_reads_declared_unique_ticker_cik_rows(tmp_path: Path):
    mapping = tmp_path / "mapping.csv"
    with mapping.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ticker", "cik"])
        writer.writeheader()
        writer.writerow({"ticker": "aapl", "cik": 320193})
    assert _mapping(mapping) == [("AAPL", 320193)]
