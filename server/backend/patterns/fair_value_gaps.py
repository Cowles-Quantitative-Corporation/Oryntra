from __future__ import annotations

from typing import Any

import pandas as pd

from .utils import candle, pattern, volume_ratio


def detect_fair_value_gaps(hist: pd.DataFrame, indicators: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if hist is None or len(hist) < 3:
        return []

    indicators = indicators or {}
    atr = float(indicators.get("atr14") or _atr_proxy(hist) or 0)
    out: list[dict[str, Any]] = []

    for i in range(2, len(hist)):
        a = candle(hist, i - 2)
        b = candle(hist, i - 1)
        c = candle(hist, i)
        vr = volume_ratio(hist, i - 1)

        if a["high"] < c["low"]:
            gap_low = a["high"]
            gap_high = c["low"]
            gap_size = gap_high - gap_low
            if _valid_gap(gap_size, atr, b, vr):
                filled = _fvg_fill_status(hist, i, gap_low, gap_high, "BULLISH")
                conf = _gap_confidence(gap_size, atr, b, vr, filled)
                out.append(pattern(
                    hist, i, "BULLISH_FAIR_VALUE_GAP", "FVG", "BULLISH", conf,
                    zone_low=gap_low, zone_high=gap_high, trigger_price=c["close"],
                    context={
                        "gap_size": round(gap_size, 4),
                        "gap_atr_multiple": round(gap_size / atr, 3) if atr else None,
                        "middle_body_atr_multiple": round(b["body"] / atr, 3) if atr else None,
                        "middle_volume_ratio": vr,
                        "fill_status": filled,
                    },
                ))

        if a["low"] > c["high"]:
            gap_low = c["high"]
            gap_high = a["low"]
            gap_size = gap_high - gap_low
            if _valid_gap(gap_size, atr, b, vr):
                filled = _fvg_fill_status(hist, i, gap_low, gap_high, "BEARISH")
                conf = _gap_confidence(gap_size, atr, b, vr, filled)
                out.append(pattern(
                    hist, i, "BEARISH_FAIR_VALUE_GAP", "FVG", "BEARISH", conf,
                    zone_low=gap_low, zone_high=gap_high, trigger_price=c["close"],
                    context={
                        "gap_size": round(gap_size, 4),
                        "gap_atr_multiple": round(gap_size / atr, 3) if atr else None,
                        "middle_body_atr_multiple": round(b["body"] / atr, 3) if atr else None,
                        "middle_volume_ratio": vr,
                        "fill_status": filled,
                    },
                ))

    out.extend(_detect_recent_fvg_interactions(hist, out))
    return out


def _valid_gap(gap_size: float, atr: float, middle: dict[str, float], vr: float) -> bool:
    if gap_size <= 0:
        return False
    if atr and gap_size < atr * 0.10:
        return False
    if atr and middle["body"] < atr * 0.30 and vr < 1.05:
        return False
    return True


def _gap_confidence(gap_size: float, atr: float, middle: dict[str, float], vr: float, fill_status: str) -> float:
    conf = 48.0
    if atr:
        conf += min(22, (gap_size / atr) * 35)
        conf += min(16, (middle["body"] / atr) * 14)
    if vr >= 1.5:
        conf += 10
    elif vr >= 1.2:
        conf += 5
    if fill_status == "UNFILLED":
        conf += 6
    elif fill_status == "FILLED":
        conf -= 12
    elif fill_status == "PARTIAL_FILL":
        conf -= 4
    return min(92, max(35, conf))


def _fvg_fill_status(hist: pd.DataFrame, i: int, low_zone: float, high_zone: float, direction: str) -> str:
    if i + 1 >= len(hist):
        return "UNFILLED"
    future = hist.iloc[i + 1:]
    if future.empty:
        return "UNFILLED"
    if direction == "BULLISH":
        if float(future["Low"].min()) <= low_zone:
            return "FILLED"
        if float(future["Low"].min()) <= high_zone:
            return "PARTIAL_FILL"
    else:
        if float(future["High"].max()) >= high_zone:
            return "FILLED"
        if float(future["High"].max()) >= low_zone:
            return "PARTIAL_FILL"
    return "UNFILLED"


def _detect_recent_fvg_interactions(hist: pd.DataFrame, fvg_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not fvg_events or len(hist) < 5:
        return []
    interactions: list[dict[str, Any]] = []
    latest = candle(hist, len(hist) - 1)
    latest_i = len(hist) - 1
    for fvg in fvg_events[-40:]:
        zone_low = fvg.get("zone_low")
        zone_high = fvg.get("zone_high")
        direction = fvg.get("direction")
        if zone_low is None or zone_high is None:
            continue
        touched = latest["low"] <= zone_high and latest["high"] >= zone_low
        if not touched:
            continue
        if direction == "BULLISH" and latest["close"] < zone_low:
            interactions.append(pattern(hist, latest_i, "INVERSION_FVG_BEARISH", "FVG", "BEARISH", 62, zone_low=zone_low, zone_high=zone_high, context={"source_fvg": fvg.get("timestamp")}))
        elif direction == "BEARISH" and latest["close"] > zone_high:
            interactions.append(pattern(hist, latest_i, "INVERSION_FVG_BULLISH", "FVG", "BULLISH", 62, zone_low=zone_low, zone_high=zone_high, context={"source_fvg": fvg.get("timestamp")}))
        else:
            interactions.append(pattern(hist, latest_i, "FVG_RETEST", "FVG", direction or "NEUTRAL", 54, zone_low=zone_low, zone_high=zone_high, context={"source_fvg": fvg.get("timestamp")}))
    return interactions


def _atr_proxy(hist: pd.DataFrame, period: int = 14) -> float:
    if len(hist) < 2:
        return float((hist["High"] - hist["Low"]).mean()) if len(hist) else 0
    high = hist["High"].astype(float)
    low = hist["Low"].astype(float)
    close = hist["Close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return float(tr.tail(period).mean())

