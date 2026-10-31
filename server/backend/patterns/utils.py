from __future__ import annotations

from typing import Any

import pandas as pd


def num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def candle(hist: pd.DataFrame, i: int) -> dict[str, float]:
    row = hist.iloc[i]
    o = num(row.get("Open"))
    h = num(row.get("High"))
    l = num(row.get("Low"))
    c = num(row.get("Close"))
    v = num(row.get("Volume"))
    rng = max(h - l, 0.0)
    body = abs(c - o)
    upper = max(h - max(o, c), 0.0)
    lower = max(min(o, c) - l, 0.0)
    direction = "BULLISH" if c > o else "BEARISH" if c < o else "NEUTRAL"
    return {
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
        "range": rng,
        "body": body,
        "upper": upper,
        "lower": lower,
        "body_pct": body / rng if rng > 0 else 0.0,
        "upper_pct": upper / rng if rng > 0 else 0.0,
        "lower_pct": lower / rng if rng > 0 else 0.0,
        "direction": direction,
        "mid": (o + c) / 2,
    }


def ts(hist: pd.DataFrame, i: int) -> str:
    try:
        return pd.Timestamp(hist.index[i]).isoformat()
    except Exception:
        return str(i)


def pattern(
    hist: pd.DataFrame,
    i: int,
    name: str,
    family: str,
    direction: str,
    confidence: float,
    trigger_price: float | None = None,
    zone_low: float | None = None,
    zone_high: float | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    c = candle(hist, i)
    return {
        "pattern_name": name,
        "pattern_family": family,
        "direction": direction,
        "confidence": max(0, min(100, round(float(confidence), 2))),
        "timestamp": ts(hist, i),
        "zone_low": round(float(zone_low), 4) if zone_low is not None else None,
        "zone_high": round(float(zone_high), 4) if zone_high is not None else None,
        "trigger_price": round(float(trigger_price if trigger_price is not None else c["close"]), 4),
        "candle_index": int(i),
        "context": context or {},
    }


def is_doji(c: dict[str, float], threshold: float = 0.10) -> bool:
    return c["range"] > 0 and c["body_pct"] <= threshold


def is_long_body(c: dict[str, float], threshold: float = 0.58) -> bool:
    return c["range"] > 0 and c["body_pct"] >= threshold


def body_high(c: dict[str, float]) -> float:
    return max(c["open"], c["close"])


def body_low(c: dict[str, float]) -> float:
    return min(c["open"], c["close"])


def near(a: float, b: float, tolerance_pct: float = 0.0025) -> bool:
    if a == 0 or b == 0:
        return abs(a - b) <= tolerance_pct
    return abs(a - b) / max(abs(a), abs(b)) <= tolerance_pct


def trend_context(hist: pd.DataFrame, i: int, lookback: int = 8) -> str:
    if i <= 1:
        return "UNKNOWN"
    start = max(0, i - lookback)
    close = hist["Close"].astype(float).iloc[start:i+1]
    if len(close) < 3:
        return "UNKNOWN"
    change = (float(close.iloc[-1]) - float(close.iloc[0])) / max(float(close.iloc[0]), 1e-9) * 100
    if change > 2:
        return "UPTREND"
    if change < -2:
        return "DOWNTREND"
    return "SIDEWAYS"


def volume_ratio(hist: pd.DataFrame, i: int, window: int = 20) -> float:
    if "Volume" not in hist.columns or i <= 0:
        return 1.0
    start = max(0, i - window)
    avg = hist["Volume"].astype(float).iloc[start:i].mean()
    cur = float(hist["Volume"].iloc[i])
    if not avg or avg <= 0:
        return 1.0
    return float(round(float(cur / avg), 3))

