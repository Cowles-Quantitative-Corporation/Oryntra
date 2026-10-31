from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import pandas as pd

from .candle_patterns import detect_candle_patterns
from .chart_patterns import detect_chart_patterns
from .fair_value_gaps import detect_fair_value_gaps
from .structure_patterns import detect_structure_patterns


def detect_all_patterns(
    hist: pd.DataFrame,
    indicators: dict[str, Any] | None = None,
    ticker: str | None = None,
    timeframe: str = "1d",
    max_events: int = 250,
) -> dict[str, Any]:


    indicators = indicators or {}
    if hist is None or hist.empty:
        return {
            "ticker": ticker,
            "timeframe": timeframe,
            "patterns": [],
            "recent": [],
            "summary": {},
            "top_pattern": None,
            "warnings": ["No historical candles supplied to pattern engine."],
        }

    all_patterns: list[dict[str, Any]] = []
    warnings: list[str] = []

    for name, detector in (
        ("candles", detect_candle_patterns),
        ("fair_value_gaps", detect_fair_value_gaps),
        ("structure", detect_structure_patterns),
        ("chart", detect_chart_patterns),
    ):
        try:
            all_patterns.extend(detector(hist, indicators))
        except Exception as exc:
            warnings.append(f"{name} detector failed: {exc}")

    all_patterns = [_clean_pattern(p) for p in _dedupe_patterns(all_patterns)]
    all_patterns = sorted(all_patterns, key=lambda p: (p.get("candle_index", -1), p.get("confidence", 0)), reverse=True)

    if max_events and len(all_patterns) > max_events:
        all_patterns = all_patterns[:max_events]

    display_patterns = _select_display_patterns(all_patterns, len(hist))
    top = _choose_top_pattern(display_patterns or all_patterns)
    summary = summarize_patterns(all_patterns)
    summary["displayed_patterns"] = len(display_patterns)
    summary["display_filter"] = {
        "last_day_any_confidence": True,
        "high_confidence": 70,
        "month_window_days": 30,
        "max_display": 20,
        "description": "Shows every pattern from the most recent trading day plus important high-confidence patterns from the last 30 days. Full raw pattern events can still be stored for stats."
    }

    return {
        "ticker": ticker,
        "timeframe": timeframe,
        "patterns": all_patterns,
        "recent": display_patterns,
        "summary": summary,
        "top_pattern": top,
        "warnings": warnings,
        "disclaimer": "Pattern detection is rule-based and educational only. It is not financial advice.",
    }


def _select_display_patterns(patterns: list[dict[str, Any]], candle_count: int) -> list[dict[str, Any]]:
    if not patterns:
        return []

    max_display = 20
    high_confidence = 70
    very_high_confidence = 85

    parsed_times = [_parse_pattern_timestamp(p) for p in patterns]
    valid_times = [t for t in parsed_times if t is not None]
    latest_ts = max(valid_times) if valid_times else None

    if latest_ts is not None:
        day_cutoff = latest_ts - pd.Timedelta(days=1)
        month_cutoff = latest_ts - pd.Timedelta(days=30)
    else:
        day_cutoff = None
        month_cutoff = None

    latest_index = max((int(p.get("candle_index") or 0) for p in patterns), default=max(0, candle_count - 1))
    fallback_day_index = max(0, latest_index - 1)
    fallback_month_index = max(0, latest_index - 23)

    selected_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}

    for p, ts in zip(patterns, parsed_times):
        conf = float(p.get("confidence") or 0)
        idx = int(p.get("candle_index") or 0)
        in_last_day = _is_in_window(ts, idx, day_cutoff, fallback_day_index)
        in_last_month = _is_in_window(ts, idx, month_cutoff, fallback_month_index)
        important = _is_important_display_pattern(p)

        keep = False
        reason = ""

        if in_last_day:
            keep = True
            reason = "LAST TRADING DAY"
        elif in_last_month and conf >= high_confidence and (important or conf >= very_high_confidence):
            keep = True
            reason = "30D HIGH CONF"

        if not keep:
            continue

        q = dict(p)
        q["display_reason"] = reason
        key = (q.get("pattern_name"), q.get("timestamp"), q.get("zone_low"), q.get("zone_high"))
        if key not in selected_by_key or conf > float(selected_by_key[key].get("confidence") or 0):
            selected_by_key[key] = q

    selected = list(selected_by_key.values())

    selected = sorted(
        selected,
        key=lambda p: (
            1 if p.get("display_reason") == "LAST TRADING DAY" else 0,
            int(p.get("candle_index") or 0),
            float(p.get("confidence") or 0),
        ),
        reverse=True,
    )

    if len(selected) <= max_display:
        return selected

    daily = [p for p in selected if p.get("display_reason") == "LAST TRADING DAY"]
    monthly = [p for p in selected if p.get("display_reason") != "LAST TRADING DAY"]

    compact_daily = _compact_display_duplicates(daily, max_items=max_display)
    remaining_slots = max(0, max_display - len(compact_daily))
    compact_monthly = _compact_display_duplicates(monthly, max_items=remaining_slots)
    return (compact_daily + compact_monthly)[:max_display]


def _parse_pattern_timestamp(pattern: dict[str, Any]) -> pd.Timestamp | None:
    try:
        raw = pattern.get("timestamp")
        if not raw:
            return None
        ts = pd.to_datetime(raw, errors="coerce")
        if pd.isna(ts):
            return None
        if getattr(ts, "tzinfo", None) is not None:
            ts = ts.tz_convert(None)
        return ts
    except Exception:
        return None


def _is_in_window(ts: pd.Timestamp | None, idx: int, cutoff: pd.Timestamp | None, fallback_index: int) -> bool:
    if cutoff is not None and ts is not None:
        return ts >= cutoff
    return idx >= fallback_index


def _is_important_display_pattern(pattern: dict[str, Any]) -> bool:
    family = str(pattern.get("pattern_family") or "").upper()
    name = str(pattern.get("pattern_name") or "").upper()

    if family in {"FVG", "STRUCTURE", "CHART"}:
        return True

    important_name_fragments = (
        "ENGULFING",
        "MORNING_STAR",
        "EVENING_STAR",
        "DOJI_STAR",
        "ABANDONED_BABY",
        "THREE_WHITE_SOLDIERS",
        "THREE_BLACK_CROWS",
        "THREE_INSIDE",
        "THREE_OUTSIDE",
        "THREE_LINE_STRIKE",
        "RISING_THREE_METHODS",
        "FALLING_THREE_METHODS",
        "HAMMER",
        "HANGING_MAN",
        "SHOOTING_STAR",
        "INVERTED_HAMMER",
        "PIERCING_LINE",
        "DARK_CLOUD",
        "TWEEZER",
        "KICKER",
        "HARSKI",
        "HARAMI",
        "TRI_STAR",
        "THREE_STARS_IN_THE_SOUTH",
        "ABANDONED_BABY",
        "BELT_HOLD",
        "MARUBOZU",
        "COUNTERATTACK",
        "MATCHING_LOW",
        "HOMING_PIGEON",
        "UNIQUE_THREE_RIVER",
        "STICK_SANDWICH",
        "LADDER_BOTTOM",
        "TASUKI",
        "GAP_THREE_METHODS",
        "MAT_HOLD",
        "BREAKAWAY",
        "ADVANCE_BLOCK",
        "STALLED",
        "CUP",
        "ROUNDED",
        "BREAKOUT",
        "BREAKDOWN",
        "BROADENING",
    )
    return any(fragment in name for fragment in important_name_fragments)


def _compact_display_duplicates(patterns: list[dict[str, Any]], max_items: int) -> list[dict[str, Any]]:
    if max_items <= 0:
        return []

    selected: list[dict[str, Any]] = []
    seen_names: dict[str, int] = defaultdict(int)
    seen_families: dict[str, int] = defaultdict(int)

    for p in patterns:
        name = str(p.get("pattern_name") or "UNKNOWN")
        family = str(p.get("pattern_family") or "UNKNOWN")
        conf = float(p.get("confidence") or 0)
        name_limit = 3 if conf >= 70 else 2
        family_limit = 8 if family in {"FVG", "STRUCTURE", "CHART"} else 6
        if seen_names[name] >= name_limit:
            continue
        if seen_families[family] >= family_limit:
            continue
        selected.append(p)
        seen_names[name] += 1
        seen_families[family] += 1
        if len(selected) >= max_items:
            return selected

    used = {id(p) for p in selected}
    for p in patterns:
        if id(p) in used:
            continue
        selected.append(p)
        if len(selected) >= max_items:
            break

    return selected

def summarize_patterns(patterns: list[dict[str, Any]]) -> dict[str, Any]:
    by_family = Counter(p.get("pattern_family", "UNKNOWN") for p in patterns)
    by_direction = Counter(p.get("direction", "NEUTRAL") for p in patterns)
    by_name = Counter(p.get("pattern_name", "UNKNOWN") for p in patterns)

    high_conf = [p for p in patterns if (p.get("confidence") or 0) >= 70]
    recent_high = sorted(high_conf, key=lambda p: (p.get("candle_index", -1), p.get("confidence", 0)), reverse=True)[:10]

    return {
        "total_patterns": len(patterns),
        "high_confidence_count": len(high_conf),
        "by_family": dict(by_family),
        "by_direction": dict(by_direction),
        "most_common": dict(by_name.most_common(12)),
        "high_confidence_recent": recent_high,
    }


def _clean_pattern(p: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(p, dict):
        p = {}

    def _float_or_none(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return round(float(value), 4)
        except Exception:
            return None

    def _int_or_zero(value: Any) -> int:
        try:
            return int(value)
        except Exception:
            return 0

    def _confidence(value: Any) -> float:
        try:
            return max(0.0, min(100.0, round(float(value), 2)))
        except Exception:
            return 0.0

    direction = str(p.get("direction") or "NEUTRAL").upper()
    if direction not in {"BULLISH", "BEARISH", "NEUTRAL"}:
        direction = "NEUTRAL"

    context = p.get("context")
    if not isinstance(context, dict):
        context = {}

    return {
        "pattern_name": str(p.get("pattern_name") or "UNKNOWN_PATTERN").upper(),
        "pattern_family": str(p.get("pattern_family") or "UNKNOWN").upper(),
        "direction": direction,
        "confidence": _confidence(p.get("confidence", 0)),
        "timestamp": str(p.get("timestamp") or ""),
        "zone_low": _float_or_none(p.get("zone_low")),
        "zone_high": _float_or_none(p.get("zone_high")),
        "trigger_price": _float_or_none(p.get("trigger_price")),
        "candle_index": _int_or_zero(p.get("candle_index", 0)),
        "context": context,
    }


def _choose_top_pattern(patterns: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not patterns:
        return None
    return max(patterns, key=lambda p: (float(p.get("confidence") or 0), int(p.get("candle_index") or 0)))


def _dedupe_patterns(patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[tuple[Any, ...], dict[str, Any]] = {}
    for p in patterns:
        key = (
            p.get("pattern_name"),
            p.get("timestamp"),
            p.get("zone_low"),
            p.get("zone_high"),
        )
        if key not in best or float(p.get("confidence") or 0) > float(best[key].get("confidence") or 0):
            best[key] = p
    return list(best.values())

