"""Point-in-time validation shared by the downstream research state gates."""
from __future__ import annotations

from typing import Any
import pandas as pd


def validate_state_time(state_time: Any, decision_time: Any, *, prefix: str,
                        maximum_age_sessions: int) -> tuple[int | None, list[str]]:
    """Reject absent, malformed and future timestamps before legacy age policy.

    The inherited staleness policy is still calendar days <= sessions + 3.
    This helper deliberately does not pretend to implement an exchange calendar.
    Naive timestamps are UTC. Intraday cutoffs are compared without truncation.
    """
    blockers: list[str] = []
    parsed: list[pd.Timestamp | None] = []
    for value, label in ((state_time, "state"), (decision_time, "decision")):
        try:
            if value is None or isinstance(value, (int, float, bool)):
                raise ValueError("timestamp missing or numeric")
            timestamp = pd.Timestamp(value)
            if pd.isna(timestamp):
                raise ValueError("timestamp missing")
            timestamp = timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")
            parsed.append(timestamp)
        except (ValueError, TypeError, OverflowError):
            parsed.append(None)
            blockers.append(f"{prefix}_{label}_timestamp_invalid")
    state, decision = parsed
    if state is None or decision is None:
        return None, blockers
    if state > decision:
        return None, [f"{prefix}_state_future"]
    age = (decision.date() - state.date()).days
    if age > int(maximum_age_sessions) + 3:
        blockers.append(f"{prefix}_state_stale")
    return age, blockers
