from __future__ import annotations

from typing import Any

import pandas as pd

from .utils import candle, near, pattern, volume_ratio


def detect_structure_patterns(hist: pd.DataFrame, indicators: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if hist is None or len(hist) < 12:
        return []
    out: list[dict[str, Any]] = []

    swing_highs, swing_lows = _swings(hist)
    out.extend(_equal_high_low(hist, swing_highs, swing_lows))
    out.extend(_liquidity_sweeps(hist, swing_highs, swing_lows))
    out.extend(_bos_choch(hist, swing_highs, swing_lows))
    out.extend(_order_blocks(hist))
    out.extend(_displacement(hist, indicators or {}))
    return out


def _swings(hist: pd.DataFrame, left_right: int = 2) -> tuple[list[int], list[int]]:
    highs, lows = [], []
    for i in range(left_right, len(hist) - left_right):
        h = float(hist["High"].iloc[i])
        l = float(hist["Low"].iloc[i])
        if h == float(hist["High"].iloc[i-left_right:i+left_right+1].max()):
            highs.append(i)
        if l == float(hist["Low"].iloc[i-left_right:i+left_right+1].min()):
            lows.append(i)
    return highs, lows


def _equal_high_low(hist: pd.DataFrame, swing_highs: list[int], swing_lows: list[int]) -> list[dict[str, Any]]:
    out = []
    for arr, name, direction, col in ((swing_highs, "EQUAL_HIGHS", "BEARISH", "High"), (swing_lows, "EQUAL_LOWS", "BULLISH", "Low")):
        recent = arr[-8:]
        for a, b in zip(recent, recent[1:]):
            va = float(hist[col].iloc[a])
            vb = float(hist[col].iloc[b])
            if near(va, vb, 0.004):
                out.append(pattern(hist, b, name, "STRUCTURE", direction, 56, zone_low=min(va, vb), zone_high=max(va, vb), context={"first_swing_index": a, "second_swing_index": b}))
    return out


def _liquidity_sweeps(hist: pd.DataFrame, swing_highs: list[int], swing_lows: list[int]) -> list[dict[str, Any]]:
    out = []
    for i in range(5, len(hist)):
        c = candle(hist, i)
        prior_highs = [h for h in swing_highs if h < i][-5:]
        prior_lows = [l for l in swing_lows if l < i][-5:]
        if prior_highs:
            level = max(float(hist["High"].iloc[j]) for j in prior_highs)
            if c["high"] > level and c["close"] < level:
                out.append(pattern(hist, i, "LIQUIDITY_SWEEP_HIGH", "STRUCTURE", "BEARISH", 66, zone_low=level, zone_high=c["high"], context={"volume_ratio": volume_ratio(hist, i)}))
        if prior_lows:
            level = min(float(hist["Low"].iloc[j]) for j in prior_lows)
            if c["low"] < level and c["close"] > level:
                out.append(pattern(hist, i, "LIQUIDITY_SWEEP_LOW", "STRUCTURE", "BULLISH", 66, zone_low=c["low"], zone_high=level, context={"volume_ratio": volume_ratio(hist, i)}))
    return out


def _bos_choch(hist: pd.DataFrame, swing_highs: list[int], swing_lows: list[int]) -> list[dict[str, Any]]:
    out = []
    recent_trend = _trend(hist)
    for i in range(8, len(hist)):
        c = candle(hist, i)
        prev_highs = [h for h in swing_highs if h < i]
        prev_lows = [l for l in swing_lows if l < i]
        if prev_highs:
            last_high = float(hist["High"].iloc[prev_highs[-1]])
            if c["close"] > last_high:
                name = "CHOCH_BULLISH" if recent_trend == "DOWN" else "BOS_BULLISH"
                out.append(pattern(hist, i, name, "STRUCTURE", "BULLISH", 63 if "BOS" in name else 70, zone_low=last_high, zone_high=c["close"], context={"broken_swing_index": prev_highs[-1]}))
        if prev_lows:
            last_low = float(hist["Low"].iloc[prev_lows[-1]])
            if c["close"] < last_low:
                name = "CHOCH_BEARISH" if recent_trend == "UP" else "BOS_BEARISH"
                out.append(pattern(hist, i, name, "STRUCTURE", "BEARISH", 63 if "BOS" in name else 70, zone_low=c["close"], zone_high=last_low, context={"broken_swing_index": prev_lows[-1]}))
    return out


def _order_blocks(hist: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    if len(hist) < 8:
        return out
    avg_range = (hist["High"].astype(float) - hist["Low"].astype(float)).rolling(20, min_periods=5).mean()
    for i in range(3, len(hist)):
        c = candle(hist, i)
        prev = candle(hist, i - 1)
        ar = float(avg_range.iloc[i]) if not pd.isna(avg_range.iloc[i]) else c["range"]
        displacement = c["body"] > ar * 1.25 if ar else False
        if not displacement:
            continue
        if c["direction"] == "BULLISH" and prev["direction"] == "BEARISH":
            out.append(pattern(hist, i - 1, "BULLISH_ORDER_BLOCK", "STRUCTURE", "BULLISH", 58, zone_low=prev["low"], zone_high=prev["high"], trigger_price=c["close"], context={"displacement_index": i}))
        if c["direction"] == "BEARISH" and prev["direction"] == "BULLISH":
            out.append(pattern(hist, i - 1, "BEARISH_ORDER_BLOCK", "STRUCTURE", "BEARISH", 58, zone_low=prev["low"], zone_high=prev["high"], trigger_price=c["close"], context={"displacement_index": i}))
    return out


def _displacement(hist: pd.DataFrame, indicators: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    atr = float(indicators.get("atr14") or 0)
    if atr <= 0:
        atr = float((hist["High"] - hist["Low"]).tail(14).mean())
    for i in range(max(0, len(hist)-80), len(hist)):
        c = candle(hist, i)
        if atr and c["body"] >= atr * 1.0 and volume_ratio(hist, i) >= 1.2:
            direction = "BULLISH" if c["close"] > c["open"] else "BEARISH"
            out.append(pattern(hist, i, "DISPLACEMENT_CANDLE", "STRUCTURE", direction, 60, context={"body_atr_multiple": round(c["body"] / atr, 3), "volume_ratio": volume_ratio(hist, i)}))
    return out


def _trend(hist: pd.DataFrame, lookback: int = 20) -> str:
    close = hist["Close"].astype(float).tail(lookback)
    if len(close) < 3:
        return "SIDEWAYS"
    change = (float(close.iloc[-1]) - float(close.iloc[0])) / max(float(close.iloc[0]), 1e-9) * 100
    if change > 3:
        return "UP"
    if change < -3:
        return "DOWN"
    return "SIDEWAYS"

