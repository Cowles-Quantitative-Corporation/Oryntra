from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

try:
    import exchange_calendars as xcals
except Exception:  # pragma: no cover - fallback is tested through behavior
    xcals = None


ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


class SessionResolver:
    """Resolve an information timestamp to the first NYSE open at which it is usable.

    Beta is a daily state model evaluated for the regular-session open. Information
    published before 09:30 ET can be used at that session's open. Information at or
    after 09:30 ET is admitted at the next regular NYSE session.
    """

    def __init__(self, calendar_name: str = "XNYS") -> None:
        self.calendar_name = calendar_name
        self._calendar = xcals.get_calendar(calendar_name) if xcals is not None else None

    def _is_session(self, d: date) -> bool:
        if self._calendar is None:
            return d.weekday() < 5
        ts = pd.Timestamp(d)
        return bool(self._calendar.is_session(ts))

    def next_session(self, d: date, *, include_today: bool = True) -> date:
        x = d
        if not include_today:
            x += timedelta(days=1)
        for _ in range(14):
            if self._is_session(x):
                return x
            x += timedelta(days=1)
        raise RuntimeError(f"Could not resolve a trading session near {d}")

    def eligible_session(self, available_at: datetime) -> date:
        if available_at.tzinfo is None:
            raise ValueError("available_at must be timezone-aware")
        local = available_at.astimezone(ET)
        same_day_is_session = self._is_session(local.date())
        if same_day_is_session and local.timetz().replace(tzinfo=None) < time(9, 30):
            return local.date()
        return self.next_session(local.date(), include_today=False)

    def release_timestamp(self, d: date, hh: int, mm: int) -> datetime:
        return datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET)
