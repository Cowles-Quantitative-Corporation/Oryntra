from __future__ import annotations

from typing import Any

import pandas as pd

from .utils import (
    body_high,
    body_low,
    candle,
    is_doji,
    is_long_body,
    near,
    pattern,
    trend_context,
    volume_ratio,
)


CANDLE_FAMILY = "CANDLE"


def detect_candle_patterns(hist: pd.DataFrame, indicators: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if hist is None or len(hist) < 1:
        return []

    patterns: list[dict[str, Any]] = []
    for i in range(len(hist)):
        patterns.extend(_single_candle(hist, i))
        if i >= 1:
            patterns.extend(_two_candle(hist, i))
        if i >= 2:
            patterns.extend(_three_candle(hist, i))
        if i >= 3:
            patterns.extend(_four_candle(hist, i))
        if i >= 4:
            patterns.extend(_five_candle(hist, i))

    best: dict[tuple[str, str], dict[str, Any]] = {}
    for p in patterns:
        key = (p["pattern_name"], p["timestamp"])
        if key not in best or p["confidence"] > best[key]["confidence"]:
            best[key] = p
    return sorted(best.values(), key=lambda x: (x["candle_index"], x["confidence"]), reverse=False)


def _ctx(hist: pd.DataFrame, i: int, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    data = {
        "trend": trend_context(hist, i),
        "volume_ratio": volume_ratio(hist, i),
        "detector": "expanded_rule_set",
    }
    if extra:
        data.update(extra)
    return data


def _gap_up(a: dict[str, float], b: dict[str, float]) -> bool:
    return b["low"] > a["high"] or b["open"] > a["high"]


def _gap_down(a: dict[str, float], b: dict[str, float]) -> bool:
    return b["high"] < a["low"] or b["open"] < a["low"]


def _body_inside(inner: dict[str, float], outer: dict[str, float]) -> bool:
    return body_low(inner) >= body_low(outer) and body_high(inner) <= body_high(outer)


def _body_engulfs(engulfer: dict[str, float], engulfed: dict[str, float]) -> bool:
    return body_low(engulfer) <= body_low(engulfed) and body_high(engulfer) >= body_high(engulfed)


def _small(c: dict[str, float], max_body: float = 0.35) -> bool:
    return c["range"] > 0 and c["body_pct"] <= max_body


def _long(c: dict[str, float], min_body: float = 0.50) -> bool:
    return is_long_body(c, min_body)


def _upper_tail_dominant(c: dict[str, float]) -> bool:
    return c["upper"] >= max(c["body"] * 2.0, c["range"] * 0.45) and c["lower_pct"] <= 0.25


def _lower_tail_dominant(c: dict[str, float]) -> bool:
    return c["lower"] >= max(c["body"] * 2.0, c["range"] * 0.45) and c["upper_pct"] <= 0.25


def _single_candle(hist: pd.DataFrame, i: int) -> list[dict[str, Any]]:
    c = candle(hist, i)
    out: list[dict[str, Any]] = []
    trend = trend_context(hist, i)
    vr = volume_ratio(hist, i)

    if c["range"] <= 0:
        return out

    if is_doji(c, 0.10):
        conf = 45 + min(15, c["range"] / max(c["close"], 1e-9) * 1000)
        if c["upper_pct"] >= 0.35 and c["lower_pct"] >= 0.35:
            out.append(pattern(hist, i, "LONG_LEGGED_DOJI", CANDLE_FAMILY, "NEUTRAL", conf + 8, context=_ctx(hist, i)))
            if c["upper_pct"] >= 0.42 and c["lower_pct"] >= 0.42:
                out.append(pattern(hist, i, "RICKSHAW_MAN", CANDLE_FAMILY, "NEUTRAL", conf + 6, context=_ctx(hist, i)))
        elif c["lower_pct"] >= 0.60 and c["upper_pct"] <= 0.20:
            out.append(pattern(hist, i, "DRAGONFLY_DOJI", CANDLE_FAMILY, "BULLISH", conf + 12, context=_ctx(hist, i)))
            if c["lower_pct"] >= 0.72:
                out.append(pattern(hist, i, "TAKURI", CANDLE_FAMILY, "BULLISH", conf + 14, context=_ctx(hist, i)))
        elif c["upper_pct"] >= 0.60 and c["lower_pct"] <= 0.20:
            out.append(pattern(hist, i, "GRAVESTONE_DOJI", CANDLE_FAMILY, "BEARISH", conf + 12, context=_ctx(hist, i)))
        else:
            out.append(pattern(hist, i, "DOJI", CANDLE_FAMILY, "NEUTRAL", conf, context=_ctx(hist, i)))

    if 0.10 < c["body_pct"] <= 0.35 and c["upper_pct"] >= 0.25 and c["lower_pct"] >= 0.25:
        out.append(pattern(hist, i, "SPINNING_TOP", CANDLE_FAMILY, "NEUTRAL", 48, context=_ctx(hist, i)))
    if c["body_pct"] <= 0.25 and c["upper_pct"] >= 0.35 and c["lower_pct"] >= 0.35:
        out.append(pattern(hist, i, "HIGH_WAVE_CANDLE", CANDLE_FAMILY, "NEUTRAL", 50, context=_ctx(hist, i)))
    if 0.12 <= c["body_pct"] <= 0.28 and c["upper_pct"] < 0.25 and c["lower_pct"] < 0.25:
        out.append(pattern(hist, i, "SHORT_LINE_CANDLE", CANDLE_FAMILY, "NEUTRAL", 42, context=_ctx(hist, i)))

    if c["body_pct"] >= 0.86 and c["upper_pct"] <= 0.08 and c["lower_pct"] <= 0.08:
        if c["direction"] == "BULLISH":
            out.append(pattern(hist, i, "MARUBOZU_BULLISH", CANDLE_FAMILY, "BULLISH", 65, context=_ctx(hist, i)))
        elif c["direction"] == "BEARISH":
            out.append(pattern(hist, i, "MARUBOZU_BEARISH", CANDLE_FAMILY, "BEARISH", 65, context=_ctx(hist, i)))
    if c["body_pct"] >= 0.72:
        if c["direction"] == "BULLISH" and c["upper_pct"] <= 0.08:
            out.append(pattern(hist, i, "CLOSING_MARUBOZU_BULLISH", CANDLE_FAMILY, "BULLISH", 59, context=_ctx(hist, i)))
        if c["direction"] == "BEARISH" and c["lower_pct"] <= 0.08:
            out.append(pattern(hist, i, "CLOSING_MARUBOZU_BEARISH", CANDLE_FAMILY, "BEARISH", 59, context=_ctx(hist, i)))
    if c["body_pct"] >= 0.58:
        if c["direction"] == "BULLISH" and c["lower_pct"] <= 0.08:
            out.append(pattern(hist, i, "BELT_HOLD_BULLISH", CANDLE_FAMILY, "BULLISH", 56, context=_ctx(hist, i)))
        if c["direction"] == "BEARISH" and c["upper_pct"] <= 0.08:
            out.append(pattern(hist, i, "BELT_HOLD_BEARISH", CANDLE_FAMILY, "BEARISH", 56, context=_ctx(hist, i)))

    small_to_mid_body = 0.10 <= c["body_pct"] <= 0.42
    if small_to_mid_body and _lower_tail_dominant(c):
        if trend == "DOWNTREND":
            out.append(pattern(hist, i, "HAMMER", CANDLE_FAMILY, "BULLISH", 68, context=_ctx(hist, i)))
        elif trend == "UPTREND":
            out.append(pattern(hist, i, "HANGING_MAN", CANDLE_FAMILY, "BEARISH", 58, context=_ctx(hist, i)))
        else:
            out.append(pattern(hist, i, "LONG_LOWER_SHADOW", CANDLE_FAMILY, "BULLISH", 45, context=_ctx(hist, i)))

    if small_to_mid_body and _upper_tail_dominant(c):
        if trend == "DOWNTREND":
            out.append(pattern(hist, i, "INVERTED_HAMMER", CANDLE_FAMILY, "BULLISH", 58, context=_ctx(hist, i)))
        elif trend == "UPTREND":
            out.append(pattern(hist, i, "SHOOTING_STAR", CANDLE_FAMILY, "BEARISH", 68, context=_ctx(hist, i)))
        else:
            out.append(pattern(hist, i, "LONG_UPPER_SHADOW", CANDLE_FAMILY, "BEARISH", 45, context=_ctx(hist, i)))

    return out


def _two_candle(hist: pd.DataFrame, i: int) -> list[dict[str, Any]]:
    p = candle(hist, i - 1)
    c = candle(hist, i)
    out: list[dict[str, Any]] = []
    trend = trend_context(hist, i)
    vr = volume_ratio(hist, i)

    if p["direction"] == "BEARISH" and c["direction"] == "BULLISH" and _body_engulfs(c, p):
        out.append(pattern(hist, i, "BULLISH_ENGULFING", CANDLE_FAMILY, "BULLISH", 72, context=_ctx(hist, i)))
    if p["direction"] == "BULLISH" and c["direction"] == "BEARISH" and _body_engulfs(c, p):
        out.append(pattern(hist, i, "BEARISH_ENGULFING", CANDLE_FAMILY, "BEARISH", 72, context=_ctx(hist, i)))

    if p["direction"] == "BEARISH" and c["direction"] == "BULLISH" and _body_inside(c, p):
        out.append(pattern(hist, i, "BULLISH_HARAMI", CANDLE_FAMILY, "BULLISH", 56, context=_ctx(hist, i)))
    if p["direction"] == "BULLISH" and c["direction"] == "BEARISH" and _body_inside(c, p):
        out.append(pattern(hist, i, "BEARISH_HARAMI", CANDLE_FAMILY, "BEARISH", 56, context=_ctx(hist, i)))
    if p["direction"] == "BEARISH" and c["direction"] == "BEARISH" and _body_inside(c, p):
        out.append(pattern(hist, i, "HOMING_PIGEON", CANDLE_FAMILY, "BULLISH", 52, context=_ctx(hist, i)))
    if is_doji(c, 0.10) and _body_inside(c, p):
        out.append(pattern(hist, i, "HARAMI_CROSS", CANDLE_FAMILY, "NEUTRAL", 54, context=_ctx(hist, i)))
        if p["direction"] == "BEARISH":
            out.append(pattern(hist, i, "BULLISH_HARAMI_CROSS", CANDLE_FAMILY, "BULLISH", 58, context=_ctx(hist, i)))
        if p["direction"] == "BULLISH":
            out.append(pattern(hist, i, "BEARISH_HARAMI_CROSS", CANDLE_FAMILY, "BEARISH", 58, context=_ctx(hist, i)))

    if p["direction"] == "BEARISH" and c["direction"] == "BULLISH":
        midpoint = (p["open"] + p["close"]) / 2
        if c["open"] < p["close"] and c["close"] > midpoint and c["close"] < p["open"]:
            out.append(pattern(hist, i, "PIERCING_LINE", CANDLE_FAMILY, "BULLISH", 64, context=_ctx(hist, i)))
    if p["direction"] == "BULLISH" and c["direction"] == "BEARISH":
        midpoint = (p["open"] + p["close"]) / 2
        if c["open"] > p["close"] and c["close"] < midpoint and c["close"] > p["open"]:
            out.append(pattern(hist, i, "DARK_CLOUD_COVER", CANDLE_FAMILY, "BEARISH", 64, context=_ctx(hist, i)))

    if near(c["low"], p["low"], 0.004) and p["direction"] == "BEARISH" and c["direction"] == "BULLISH":
        out.append(pattern(hist, i, "TWEEZER_BOTTOM", CANDLE_FAMILY, "BULLISH", 58, zone_low=min(c["low"], p["low"]), zone_high=max(c["low"], p["low"]), context=_ctx(hist, i)))
    if near(c["high"], p["high"], 0.004) and p["direction"] == "BULLISH" and c["direction"] == "BEARISH":
        out.append(pattern(hist, i, "TWEEZER_TOP", CANDLE_FAMILY, "BEARISH", 58, zone_low=min(c["high"], p["high"]), zone_high=max(c["high"], p["high"]), context=_ctx(hist, i)))
    if p["direction"] == "BEARISH" and c["direction"] == "BEARISH" and near(c["close"], p["close"], 0.003):
        out.append(pattern(hist, i, "MATCHING_LOW", CANDLE_FAMILY, "BULLISH", 50, zone_low=min(c["close"], p["close"]), zone_high=max(c["close"], p["close"]), context=_ctx(hist, i)))

    gap_up = c["open"] > p["high"]
    gap_down = c["open"] < p["low"]
    if p["direction"] == "BEARISH" and c["direction"] == "BULLISH" and gap_up:
        out.append(pattern(hist, i, "BULLISH_KICKER", CANDLE_FAMILY, "BULLISH", 78, context=_ctx(hist, i)))
    if p["direction"] == "BULLISH" and c["direction"] == "BEARISH" and gap_down:
        out.append(pattern(hist, i, "BEARISH_KICKER", CANDLE_FAMILY, "BEARISH", 78, context=_ctx(hist, i)))
    if near(c["close"], p["close"], 0.003) and p["direction"] != c["direction"]:
        out.append(pattern(hist, i, f"MEETING_LINES_{c['direction']}", CANDLE_FAMILY, c["direction"], 50, context=_ctx(hist, i)))
        out.append(pattern(hist, i, f"COUNTERATTACK_{c['direction']}", CANDLE_FAMILY, c["direction"], 55, context=_ctx(hist, i)))
    if near(c["open"], p["open"], 0.003) and p["direction"] == c["direction"] and _long(c, 0.50):
        out.append(pattern(hist, i, f"SEPARATING_LINES_{c['direction']}", CANDLE_FAMILY, c["direction"], 52, context=_ctx(hist, i)))

    if p["direction"] == "BEARISH" and c["direction"] == "BEARISH" and c["close"] > p["low"] and c["close"] < p["close"]:
        out.append(pattern(hist, i, "ON_NECK", CANDLE_FAMILY, "BEARISH", 46, context=_ctx(hist, i)))
    if p["direction"] == "BEARISH" and c["direction"] == "BULLISH" and c["close"] < (p["open"] + p["close"]) / 2:
        out.append(pattern(hist, i, "THRUSTING_PATTERN", CANDLE_FAMILY, "NEUTRAL", 44, context=_ctx(hist, i)))
    if p["direction"] == "BEARISH" and c["direction"] == "BEARISH" and near(c["close"], p["low"], 0.004):
        out.append(pattern(hist, i, "IN_NECK", CANDLE_FAMILY, "BEARISH", 44, context=_ctx(hist, i)))

    return out


def _three_candle(hist: pd.DataFrame, i: int) -> list[dict[str, Any]]:
    a = candle(hist, i - 2)
    b = candle(hist, i - 1)
    c = candle(hist, i)
    out: list[dict[str, Any]] = []
    trend = trend_context(hist, i)
    vr = volume_ratio(hist, i)

    if a["direction"] == "BEARISH" and _long(a, 0.50) and c["direction"] == "BULLISH":
        midpoint = (a["open"] + a["close"]) / 2
        if b["body_pct"] <= 0.35 and c["close"] > midpoint:
            name = "MORNING_DOJI_STAR" if is_doji(b, 0.12) else "MORNING_STAR"
            out.append(pattern(hist, i, name, CANDLE_FAMILY, "BULLISH", 74 if is_doji(b, 0.12) else 70, context=_ctx(hist, i)))
        if _small(b, 0.30) and _gap_down(a, b):
            out.append(pattern(hist, i, "DOJI_STAR_BULLISH" if is_doji(b, 0.12) else "STAR_BULLISH", CANDLE_FAMILY, "BULLISH", 57, context=_ctx(hist, i)))
    if a["direction"] == "BULLISH" and _long(a, 0.50) and c["direction"] == "BEARISH":
        midpoint = (a["open"] + a["close"]) / 2
        if b["body_pct"] <= 0.35 and c["close"] < midpoint:
            name = "EVENING_DOJI_STAR" if is_doji(b, 0.12) else "EVENING_STAR"
            out.append(pattern(hist, i, name, CANDLE_FAMILY, "BEARISH", 74 if is_doji(b, 0.12) else 70, context=_ctx(hist, i)))
        if _small(b, 0.30) and _gap_up(a, b):
            out.append(pattern(hist, i, "DOJI_STAR_BEARISH" if is_doji(b, 0.12) else "STAR_BEARISH", CANDLE_FAMILY, "BEARISH", 57, context=_ctx(hist, i)))

    if is_doji(b, 0.10):
        if a["high"] < b["low"] and c["low"] > b["high"] and c["direction"] == "BULLISH":
            out.append(pattern(hist, i, "ABANDONED_BABY_BULLISH", CANDLE_FAMILY, "BULLISH", 82, context=_ctx(hist, i)))
        if a["low"] > b["high"] and c["high"] < b["low"] and c["direction"] == "BEARISH":
            out.append(pattern(hist, i, "ABANDONED_BABY_BEARISH", CANDLE_FAMILY, "BEARISH", 82, context=_ctx(hist, i)))

    if all(x["direction"] == "BULLISH" and _long(x, 0.45) for x in (a, b, c)) and a["close"] < b["close"] < c["close"]:
        out.append(pattern(hist, i, "THREE_WHITE_SOLDIERS", CANDLE_FAMILY, "BULLISH", 76, context=_ctx(hist, i)))
        if a["body"] > b["body"] > c["body"] or c["upper_pct"] > 0.35:
            out.append(pattern(hist, i, "ADVANCE_BLOCK", CANDLE_FAMILY, "BEARISH", 54, context=_ctx(hist, i, {"note": "three rising candles losing body strength"})))
        if b["body"] < a["body"] * 0.75 and c["body"] < b["body"] * 0.75:
            out.append(pattern(hist, i, "STALLED_PATTERN", CANDLE_FAMILY, "BEARISH", 52, context=_ctx(hist, i)))
    if all(x["direction"] == "BEARISH" and _long(x, 0.45) for x in (a, b, c)) and a["close"] > b["close"] > c["close"]:
        out.append(pattern(hist, i, "THREE_BLACK_CROWS", CANDLE_FAMILY, "BEARISH", 76, context=_ctx(hist, i)))
        if near(a["open"], b["open"], 0.006) and near(b["open"], c["open"], 0.006):
            out.append(pattern(hist, i, "IDENTICAL_THREE_CROWS", CANDLE_FAMILY, "BEARISH", 80, context=_ctx(hist, i)))

    if a["direction"] == "BEARISH" and _body_inside(b, a) and c["close"] > a["open"]:
        out.append(pattern(hist, i, "THREE_INSIDE_UP", CANDLE_FAMILY, "BULLISH", 64, context=_ctx(hist, i)))
    if a["direction"] == "BULLISH" and _body_inside(b, a) and c["close"] < a["open"]:
        out.append(pattern(hist, i, "THREE_INSIDE_DOWN", CANDLE_FAMILY, "BEARISH", 64, context=_ctx(hist, i)))
    if a["direction"] == "BEARISH" and b["direction"] == "BULLISH" and _body_engulfs(b, a) and c["close"] > b["close"]:
        out.append(pattern(hist, i, "THREE_OUTSIDE_UP", CANDLE_FAMILY, "BULLISH", 68, context=_ctx(hist, i)))
    if a["direction"] == "BULLISH" and b["direction"] == "BEARISH" and _body_engulfs(b, a) and c["close"] < b["close"]:
        out.append(pattern(hist, i, "THREE_OUTSIDE_DOWN", CANDLE_FAMILY, "BEARISH", 68, context=_ctx(hist, i)))

    if a["direction"] == "BULLISH" and b["direction"] == "BEARISH" and c["direction"] == "BEARISH" and _gap_up(a, b) and _body_engulfs(c, b) and c["close"] > a["close"]:
        out.append(pattern(hist, i, "UPSIDE_GAP_TWO_CROWS", CANDLE_FAMILY, "BEARISH", 63, context=_ctx(hist, i)))
    if a["direction"] == "BULLISH" and b["direction"] == "BEARISH" and c["direction"] == "BEARISH" and b["open"] > a["close"] and c["open"] > b["open"] and c["close"] < b["close"]:
        out.append(pattern(hist, i, "TWO_CROWS", CANDLE_FAMILY, "BEARISH", 58, context=_ctx(hist, i)))

    if is_doji(a, 0.12) and is_doji(b, 0.12) and is_doji(c, 0.12):
        if trend == "DOWNTREND":
            out.append(pattern(hist, i, "TRI_STAR_DOJI_BULLISH", CANDLE_FAMILY, "BULLISH", 70, context=_ctx(hist, i)))
        elif trend == "UPTREND":
            out.append(pattern(hist, i, "TRI_STAR_DOJI_BEARISH", CANDLE_FAMILY, "BEARISH", 70, context=_ctx(hist, i)))
        else:
            out.append(pattern(hist, i, "TRI_STAR_DOJI", CANDLE_FAMILY, "NEUTRAL", 58, context=_ctx(hist, i)))

    if all(x["direction"] == "BEARISH" for x in (a, b, c)) and a["lower"] > a["body"] and b["low"] > a["low"] and c["low"] > b["low"]:
        out.append(pattern(hist, i, "THREE_STARS_IN_THE_SOUTH", CANDLE_FAMILY, "BULLISH", 66, context=_ctx(hist, i)))
    if a["direction"] == "BEARISH" and b["direction"] == "BEARISH" and c["direction"] == "BULLISH" and b["low"] < a["low"] and c["close"] < b["open"]:
        out.append(pattern(hist, i, "UNIQUE_THREE_RIVER", CANDLE_FAMILY, "BULLISH", 56, context=_ctx(hist, i)))
    if a["direction"] == "BEARISH" and b["direction"] == "BULLISH" and c["direction"] == "BEARISH" and near(a["close"], c["close"], 0.004):
        out.append(pattern(hist, i, "STICK_SANDWICH", CANDLE_FAMILY, "BULLISH", 55, context=_ctx(hist, i)))
    if a["direction"] == "BEARISH" and b["direction"] == "BEARISH" and c["direction"] == "BULLISH" and _lower_tail_dominant(c) and c["close"] > b["close"]:
        out.append(pattern(hist, i, "LADDER_BOTTOM", CANDLE_FAMILY, "BULLISH", 60, context=_ctx(hist, i)))

    if _gap_up(a, b) and a["direction"] == "BULLISH" and b["direction"] == "BULLISH" and c["direction"] == "BEARISH" and c["close"] > a["high"]:
        out.append(pattern(hist, i, "UPSIDE_TASUKI_GAP", CANDLE_FAMILY, "BULLISH", 58, zone_low=a["high"], zone_high=b["low"], context=_ctx(hist, i)))
    if _gap_down(a, b) and a["direction"] == "BEARISH" and b["direction"] == "BEARISH" and c["direction"] == "BULLISH" and c["close"] < a["low"]:
        out.append(pattern(hist, i, "DOWNSIDE_TASUKI_GAP", CANDLE_FAMILY, "BEARISH", 58, zone_low=b["high"], zone_high=a["low"], context=_ctx(hist, i)))
    if _gap_up(a, b) and b["direction"] == "BULLISH" and c["direction"] == "BULLISH" and near(b["open"], c["open"], 0.01):
        out.append(pattern(hist, i, "UPSIDE_GAP_SIDE_BY_SIDE_WHITE_LINES", CANDLE_FAMILY, "BULLISH", 57, context=_ctx(hist, i)))
    if _gap_down(a, b) and b["direction"] == "BULLISH" and c["direction"] == "BULLISH" and near(b["open"], c["open"], 0.01):
        out.append(pattern(hist, i, "DOWNSIDE_GAP_SIDE_BY_SIDE_WHITE_LINES", CANDLE_FAMILY, "BEARISH", 57, context=_ctx(hist, i)))
    if _gap_up(a, b) and c["direction"] == "BEARISH" and c["low"] <= b["low"] and c["close"] > a["high"]:
        out.append(pattern(hist, i, "UPSIDE_GAP_THREE_METHODS", CANDLE_FAMILY, "BULLISH", 56, context=_ctx(hist, i)))
    if _gap_down(a, b) and c["direction"] == "BULLISH" and c["high"] >= b["high"] and c["close"] < a["low"]:
        out.append(pattern(hist, i, "DOWNSIDE_GAP_THREE_METHODS", CANDLE_FAMILY, "BEARISH", 56, context=_ctx(hist, i)))

    if i >= 3:
        d0 = candle(hist, i - 3)
        seq = [d0, a, b, c]
        if all(x["direction"] == "BEARISH" for x in seq[:3]) and c["direction"] == "BULLISH" and c["close"] > d0["open"]:
            out.append(pattern(hist, i, "THREE_LINE_STRIKE_BULLISH", CANDLE_FAMILY, "BULLISH", 62, context=_ctx(hist, i)))
        if all(x["direction"] == "BULLISH" for x in seq[:3]) and c["direction"] == "BEARISH" and c["close"] < d0["open"]:
            out.append(pattern(hist, i, "THREE_LINE_STRIKE_BEARISH", CANDLE_FAMILY, "BEARISH", 62, context=_ctx(hist, i)))

    return out


def _four_candle(hist: pd.DataFrame, i: int) -> list[dict[str, Any]]:
    a, b, c, d = [candle(hist, j) for j in range(i - 3, i + 1)]
    out: list[dict[str, Any]] = []

    if all(x["direction"] == "BEARISH" for x in (a, b, c, d)) and _long(a, 0.55) and _long(b, 0.55) and c["high"] > b["high"] and d["open"] > c["close"] and d["close"] < c["open"]:
        out.append(pattern(hist, i, "CONCEALING_BABY_SWALLOW", CANDLE_FAMILY, "BULLISH", 62, context=_ctx(hist, i)))

    return out


def _five_candle(hist: pd.DataFrame, i: int) -> list[dict[str, Any]]:
    seq = [candle(hist, j) for j in range(i - 4, i + 1)]
    first, mid1, mid2, mid3, last = seq
    out: list[dict[str, Any]] = []

    if first["direction"] == "BULLISH" and last["direction"] == "BULLISH" and all(m["high"] < first["high"] and m["low"] > first["low"] for m in (mid1, mid2, mid3)) and last["close"] > first["close"]:
        out.append(pattern(hist, i, "RISING_THREE_METHODS", CANDLE_FAMILY, "BULLISH", 67, context=_ctx(hist, i)))
    if first["direction"] == "BEARISH" and last["direction"] == "BEARISH" and all(m["high"] < first["high"] and m["low"] > first["low"] for m in (mid1, mid2, mid3)) and last["close"] < first["close"]:
        out.append(pattern(hist, i, "FALLING_THREE_METHODS", CANDLE_FAMILY, "BEARISH", 67, context=_ctx(hist, i)))

    if first["direction"] == "BULLISH" and _long(first, 0.55) and _gap_up(first, mid1) and all(m["close"] > first["close"] for m in (mid1, mid2, mid3)) and last["direction"] == "BULLISH" and last["close"] > mid1["high"]:
        out.append(pattern(hist, i, "MAT_HOLD_BULLISH", CANDLE_FAMILY, "BULLISH", 70, context=_ctx(hist, i)))
    if first["direction"] == "BEARISH" and _long(first, 0.55) and _gap_down(first, mid1) and all(m["close"] < first["close"] for m in (mid1, mid2, mid3)) and last["direction"] == "BEARISH" and last["close"] < mid1["low"]:
        out.append(pattern(hist, i, "MAT_HOLD_BEARISH", CANDLE_FAMILY, "BEARISH", 70, context=_ctx(hist, i)))

    if first["direction"] == "BEARISH" and _gap_down(first, mid1) and last["direction"] == "BULLISH" and last["close"] > mid2["close"]:
        out.append(pattern(hist, i, "BREAKAWAY_BULLISH", CANDLE_FAMILY, "BULLISH", 60, context=_ctx(hist, i)))
    if first["direction"] == "BULLISH" and _gap_up(first, mid1) and last["direction"] == "BEARISH" and last["close"] < mid2["close"]:
        out.append(pattern(hist, i, "BREAKAWAY_BEARISH", CANDLE_FAMILY, "BEARISH", 60, context=_ctx(hist, i)))

    return out

