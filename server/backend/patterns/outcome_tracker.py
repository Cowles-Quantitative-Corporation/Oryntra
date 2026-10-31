from __future__ import annotations

from typing import Any

import pandas as pd

from ..database import evaluate_and_store_pattern_outcomes


def persist_pattern_scan(
    ticker: str,
    timeframe: str,
    hist: pd.DataFrame,
    pattern_report: dict[str, Any],
) -> dict[str, int | str]:


    patterns = _select_persisted_patterns(pattern_report)
    if not patterns:
        return {"events_saved": 0, "outcomes_saved": 0, "mode": "none"}
    outcomes = evaluate_and_store_pattern_outcomes(ticker, timeframe, hist, patterns, horizons=(1, 3, 5, 10))
    return {"events_saved": len(patterns), "outcomes_saved": outcomes, "mode": "light"}


def _select_persisted_patterns(pattern_report: dict[str, Any] | None, max_items: int = 60) -> list[dict[str, Any]]:
    if not pattern_report:
        return []
    raw = pattern_report.get("patterns") or []
    recent = pattern_report.get("recent") or []
    top = pattern_report.get("top_pattern")

    selected: list[dict[str, Any]] = []
    seen: set[tuple] = set()

    def add(p: dict[str, Any] | None) -> None:
        if not isinstance(p, dict):
            return
        key = (p.get("pattern_name"), p.get("timestamp"), p.get("zone_low"), p.get("zone_high"), p.get("candle_index"))
        if key in seen:
            return
        seen.add(key)
        selected.append(p)

    for p in recent:
        add(p)
    add(top)

    historical = sorted(raw, key=lambda p: float((p or {}).get("confidence") or 0), reverse=True)
    for p in historical:
        if len(selected) >= max_items:
            break
        conf = float((p or {}).get("confidence") or 0)
        if conf >= 75:
            add(p)

    return selected[:max_items]

