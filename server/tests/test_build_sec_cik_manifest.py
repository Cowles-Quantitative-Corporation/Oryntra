import json
from pathlib import Path

from tools.build_sec_cik_manifest import _index, _symbols


def test_sec_cik_manifest_helpers_preserve_exact_identity_and_reject_duplicates(tmp_path: Path):
    symbols = tmp_path / "symbols.txt"
    symbols.write_text("AAPL\nMSFT\n", encoding="utf-8")
    assert _symbols(symbols) == ["AAPL", "MSFT"]
    index = _index({"0": {"ticker": "AAPL", "cik_str": 320193}, "1": {"ticker": "MSFT", "cik_str": 789019}})
    assert index == {"AAPL": 320193, "MSFT": 789019}
