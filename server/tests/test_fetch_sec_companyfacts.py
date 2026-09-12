from pathlib import Path

import pytest

from tools.fetch_sec_companyfacts import _read_mapping


def test_declared_cik_mapping_requires_unique_valid_ticker_cik_rows(tmp_path: Path):
    valid = tmp_path / "valid.csv"
    valid.write_text("ticker,cik\nAAPL,320193\nMSFT,789019\n", encoding="utf-8")
    assert _read_mapping(valid) == [{"ticker": "AAPL", "cik": 320193}, {"ticker": "MSFT", "cik": 789019}]
    duplicate = tmp_path / "duplicate.csv"
    duplicate.write_text("ticker,cik\nAAPL,320193\nAAPL,320193\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        _read_mapping(duplicate)
