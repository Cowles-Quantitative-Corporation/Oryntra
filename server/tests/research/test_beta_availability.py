"""Synthetic availability checks; no provider calls or real macro data."""
from datetime import date, datetime
from zoneinfo import ZoneInfo
import pandas as pd
import pytest
from backend.models.beta.repository import MacroRepository
from backend.models.beta.session import SessionResolver
from backend.models.beta.types import MacroRevision


def test_equal_open_release_waits_for_the_next_session():
    resolver = SessionResolver()
    eastern = ZoneInfo("America/New_York")
    assert resolver.eligible_session(datetime(2024, 3, 5, 9, 29, 59, tzinfo=eastern)) == date(2024, 3, 5)
    assert resolver.eligible_session(datetime(2024, 3, 5, 9, 30, tzinfo=eastern)) == date(2024, 3, 6)
    with pytest.raises(ValueError, match="timezone-aware"):
        resolver.eligible_session(datetime(2024, 3, 5, 9))


def test_revision_cannot_change_an_earlier_session(tmp_path, monkeypatch):
    repo = MacroRepository(tmp_path / "synthetic.db", fred_api_key="INVENTED_TEST_ONLY")
    utc = ZoneInfo("UTC")
    def revision(value, day):
        at = datetime(2024, 3, day, 12, tzinfo=utc)
        return MacroRevision("INVENTED", date(2024, 1, 1), date(2024, 3, day), None,
                             value, at, at, date(2024, 3, day), source="SYNTHETIC_TEST_ONLY")
    monkeypatch.setattr(repo, "revisions", lambda series: [revision(10., 5), revision(999., 7)])
    frame = repo._state_series("INVENTED", pd.date_range("2024-03-04", periods=5), [1])
    assert pd.isna(frame.loc["2024-03-04", "level"])
    assert frame.loc["2024-03-05", "level"] == 10.
    assert frame.loc["2024-03-06", "level"] == 10.
    assert frame.loc["2024-03-07", "level"] == 999.
