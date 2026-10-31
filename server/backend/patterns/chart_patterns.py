from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .utils import near, pattern


def detect_chart_patterns(hist: pd.DataFrame, indicators: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if hist is None or len(hist) < 30:
        return []
    out: list[dict[str, Any]] = []
    highs, lows = _swings(hist, 2)
    out.extend(_double_triple(hist, highs, lows))
    out.extend(_channels_and_wedges(hist))
    out.extend(_triangles(hist, highs, lows))
    out.extend(_head_shoulders(hist, highs, lows))
    out.extend(_flags(hist))
    out.extend(_cup_and_rounding(hist))
    out.extend(_breakout_retest_patterns(hist, highs, lows))
    out.extend(_broadening_patterns(hist, highs, lows))
    return out


def _swings(hist: pd.DataFrame, lr: int = 2) -> tuple[list[int], list[int]]:
    highs, lows = [], []
    for i in range(lr, len(hist) - lr):
        h = float(hist["High"].iloc[i])
        l = float(hist["Low"].iloc[i])
        if h == float(hist["High"].iloc[i-lr:i+lr+1].max()):
            highs.append(i)
        if l == float(hist["Low"].iloc[i-lr:i+lr+1].min()):
            lows.append(i)
    return highs, lows


def _double_triple(hist: pd.DataFrame, highs: list[int], lows: list[int]) -> list[dict[str, Any]]:
    out = []
    recent_highs = highs[-6:]
    for count, name in ((2, "DOUBLE_TOP"), (3, "TRIPLE_TOP")):
        if len(recent_highs) >= count:
            pts = recent_highs[-count:]
            vals = [float(hist["High"].iloc[p]) for p in pts]
            if max(vals) and (max(vals) - min(vals)) / max(vals) <= 0.015:
                out.append(pattern(hist, pts[-1], name, "CHART", "BEARISH", 62 + count * 3, zone_low=min(vals), zone_high=max(vals), context={"swing_indexes": pts}))
    recent_lows = lows[-6:]
    for count, name in ((2, "DOUBLE_BOTTOM"), (3, "TRIPLE_BOTTOM")):
        if len(recent_lows) >= count:
            pts = recent_lows[-count:]
            vals = [float(hist["Low"].iloc[p]) for p in pts]
            if max(vals) and (max(vals) - min(vals)) / max(vals) <= 0.015:
                out.append(pattern(hist, pts[-1], name, "CHART", "BULLISH", 62 + count * 3, zone_low=min(vals), zone_high=max(vals), context={"swing_indexes": pts}))
    return out


def _channels_and_wedges(hist: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    window = min(40, len(hist))
    recent = hist.tail(window)
    closes = recent["Close"].astype(float).values
    highs = recent["High"].astype(float).values
    lows = recent["Low"].astype(float).values
    x = np.arange(window)
    if window < 20:
        return out
    close_slope = float(np.polyfit(x, closes, 1)[0])
    high_slope = float(np.polyfit(x, highs, 1)[0])
    low_slope = float(np.polyfit(x, lows, 1)[0])
    price = max(float(closes[-1]), 1e-9)
    slope_pct = close_slope / price * 100
    idx = len(hist) - 1
    width_start = highs[:10].mean() - lows[:10].mean()
    width_end = highs[-10:].mean() - lows[-10:].mean()
    contracting = width_end < width_start * 0.75 if width_start > 0 else False

    if slope_pct > 0.10 and high_slope > 0 and low_slope > 0:
        out.append(pattern(hist, idx, "CHANNEL_UP", "CHART", "BULLISH", 54, context={"slope_pct_per_bar": round(slope_pct, 4)}))
    elif slope_pct < -0.10 and high_slope < 0 and low_slope < 0:
        out.append(pattern(hist, idx, "CHANNEL_DOWN", "CHART", "BEARISH", 54, context={"slope_pct_per_bar": round(slope_pct, 4)}))

    if contracting and high_slope > 0 and low_slope > 0 and high_slope < low_slope:
        out.append(pattern(hist, idx, "RISING_WEDGE", "CHART", "BEARISH", 58, context={"width_change_pct": round((width_end - width_start) / width_start * 100, 2) if width_start else None}))
    if contracting and high_slope < 0 and low_slope < 0 and high_slope < low_slope:
        out.append(pattern(hist, idx, "FALLING_WEDGE", "CHART", "BULLISH", 58, context={"width_change_pct": round((width_end - width_start) / width_start * 100, 2) if width_start else None}))
    return out


def _triangles(hist: pd.DataFrame, highs: list[int], lows: list[int]) -> list[dict[str, Any]]:
    out = []
    if len(highs) < 3 or len(lows) < 3:
        return out
    hs = highs[-4:]
    ls = lows[-4:]
    high_vals = [float(hist["High"].iloc[i]) for i in hs]
    low_vals = [float(hist["Low"].iloc[i]) for i in ls]
    idx = max(hs[-1], ls[-1])

    high_flat = (max(high_vals) - min(high_vals)) / max(high_vals) <= 0.018 if max(high_vals) else False
    low_flat = (max(low_vals) - min(low_vals)) / max(low_vals) <= 0.018 if max(low_vals) else False
    lows_rising = low_vals[-1] > low_vals[0]
    highs_falling = high_vals[-1] < high_vals[0]

    if high_flat and lows_rising:
        out.append(pattern(hist, idx, "ASCENDING_TRIANGLE", "CHART", "BULLISH", 60, zone_low=min(low_vals), zone_high=max(high_vals), context={"high_swings": hs, "low_swings": ls}))
    if low_flat and highs_falling:
        out.append(pattern(hist, idx, "DESCENDING_TRIANGLE", "CHART", "BEARISH", 60, zone_low=min(low_vals), zone_high=max(high_vals), context={"high_swings": hs, "low_swings": ls}))
    if highs_falling and lows_rising:
        out.append(pattern(hist, idx, "SYMMETRICAL_TRIANGLE", "CHART", "NEUTRAL", 56, zone_low=min(low_vals), zone_high=max(high_vals), context={"high_swings": hs, "low_swings": ls}))
    if high_flat and low_flat:
        out.append(pattern(hist, idx, "RECTANGLE_CONSOLIDATION", "CHART", "NEUTRAL", 52, zone_low=min(low_vals), zone_high=max(high_vals), context={"high_swings": hs, "low_swings": ls}))
    return out


def _head_shoulders(hist: pd.DataFrame, highs: list[int], lows: list[int]) -> list[dict[str, Any]]:
    out = []
    if len(highs) >= 3:
        pts = highs[-3:]
        vals = [float(hist["High"].iloc[p]) for p in pts]
        if vals[1] > vals[0] and vals[1] > vals[2] and near(vals[0], vals[2], 0.035):
            out.append(pattern(hist, pts[-1], "HEAD_AND_SHOULDERS", "CHART", "BEARISH", 64, zone_low=min(vals[0], vals[2]), zone_high=vals[1], context={"swing_indexes": pts}))
    if len(lows) >= 3:
        pts = lows[-3:]
        vals = [float(hist["Low"].iloc[p]) for p in pts]
        if vals[1] < vals[0] and vals[1] < vals[2] and near(vals[0], vals[2], 0.035):
            out.append(pattern(hist, pts[-1], "INVERSE_HEAD_AND_SHOULDERS", "CHART", "BULLISH", 64, zone_low=vals[1], zone_high=max(vals[0], vals[2]), context={"swing_indexes": pts}))
    return out


def _flags(hist: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    if len(hist) < 30:
        return out
    closes = hist["Close"].astype(float)
    idx = len(hist) - 1
    impulse = (float(closes.iloc[-12]) - float(closes.iloc[-25])) / max(float(closes.iloc[-25]), 1e-9) * 100
    consolidation = (float(closes.iloc[-1]) - float(closes.iloc[-12])) / max(float(closes.iloc[-12]), 1e-9) * 100
    recent_range = (hist["High"].tail(12).max() - hist["Low"].tail(12).min()) / max(float(closes.iloc[-1]), 1e-9) * 100

    if impulse > 8 and -5 <= consolidation <= 2 and recent_range < 9:
        out.append(pattern(hist, idx, "BULL_FLAG", "CHART", "BULLISH", 58, context={"impulse_pct": round(impulse, 2), "consolidation_pct": round(consolidation, 2)}))
    if impulse < -8 and -2 <= consolidation <= 5 and recent_range < 9:
        out.append(pattern(hist, idx, "BEAR_FLAG", "CHART", "BEARISH", 58, context={"impulse_pct": round(impulse, 2), "consolidation_pct": round(consolidation, 2)}))
    if impulse > 10 and recent_range < 5:
        out.append(pattern(hist, idx, "BULL_PENNANT", "CHART", "BULLISH", 56, context={"impulse_pct": round(impulse, 2)}))
    if impulse < -10 and recent_range < 5:
        out.append(pattern(hist, idx, "BEAR_PENNANT", "CHART", "BEARISH", 56, context={"impulse_pct": round(impulse, 2)}))
    return out


def _cup_and_rounding(hist: pd.DataFrame) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if len(hist) < 70:
        return out
    closes = hist["Close"].astype(float).values
    highs = hist["High"].astype(float).values
    lows = hist["Low"].astype(float).values
    idx = len(hist) - 1

    window = min(90, len(hist))
    start = len(hist) - window
    segment = closes[-window:]
    if len(segment) < 60:
        return out

    left_rim_i = int(np.argmax(segment[: max(10, window // 3)]))
    bottom_i = int(np.argmin(segment[left_rim_i + 5: max(left_rim_i + 10, window - 15)])) + left_rim_i + 5 if left_rim_i + 10 < window - 15 else -1
    right_rim_i = int(np.argmax(segment[max(window // 2, bottom_i + 5):])) + max(window // 2, bottom_i + 5) if bottom_i > 0 else -1

    if bottom_i > 0 and right_rim_i > bottom_i:
        left_rim = float(segment[left_rim_i])
        bottom = float(segment[bottom_i])
        right_rim = float(segment[right_rim_i])
        depth = (min(left_rim, right_rim) - bottom) / max(min(left_rim, right_rim), 1e-9)
        rims_near = abs(left_rim - right_rim) / max(left_rim, right_rim, 1e-9) <= 0.08
        rounded = bottom_i > left_rim_i + 8 and right_rim_i > bottom_i + 8
        handle = closes[-1] <= right_rim and closes[-1] >= right_rim * 0.88 and right_rim_i < window - 3
        if rims_near and rounded and 0.08 <= depth <= 0.45:
            name = "CUP_AND_HANDLE" if handle else "CUP_FORMATION"
            conf = 66 if handle else 58
            out.append(pattern(hist, idx, name, "CHART", "BULLISH", conf, zone_low=bottom, zone_high=max(left_rim, right_rim), context={"depth_pct": round(depth * 100, 2), "left_rim_index": start + left_rim_i, "bottom_index": start + bottom_i, "right_rim_index": start + right_rim_i}))

    for w in (50, 80):
        if len(hist) < w:
            continue
        y = closes[-w:]
        x = np.linspace(-1, 1, w)
        try:
            a, b, c = np.polyfit(x, y, 2)
        except Exception:
            continue
        curve_depth = abs(a) / max(float(np.mean(y)), 1e-9)
        if curve_depth < 0.01:
            continue
        if a > 0 and y[-1] > y[w // 2] * 1.04:
            out.append(pattern(hist, idx, "ROUNDED_BOTTOM", "CHART", "BULLISH", 54, zone_low=float(y.min()), zone_high=float(y.max()), context={"window": w, "curvature": round(float(a), 4)}))
        if a < 0 and y[-1] < y[w // 2] * 0.96:
            out.append(pattern(hist, idx, "ROUNDED_TOP", "CHART", "BEARISH", 54, zone_low=float(y.min()), zone_high=float(y.max()), context={"window": w, "curvature": round(float(a), 4)}))
    return out


def _breakout_retest_patterns(hist: pd.DataFrame, highs: list[int], lows: list[int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if len(hist) < 25:
        return out
    idx = len(hist) - 1
    close = float(hist["Close"].iloc[-1])
    high = float(hist["High"].iloc[-1])
    low = float(hist["Low"].iloc[-1])
    recent_highs = [h for h in highs if h < idx][-8:]
    recent_lows = [l for l in lows if l < idx][-8:]

    if recent_highs:
        resistance = max(float(hist["High"].iloc[j]) for j in recent_highs)
        prior_close = float(hist["Close"].iloc[-2])
        if prior_close <= resistance and close > resistance * 1.003:
            out.append(pattern(hist, idx, "RESISTANCE_BREAKOUT", "CHART", "BULLISH", 62, zone_low=resistance, zone_high=close, context={"level": round(resistance, 4)}))
        elif low <= resistance <= close and close > resistance:
            out.append(pattern(hist, idx, "BREAKOUT_RETEST_SUPPORT", "CHART", "BULLISH", 58, zone_low=resistance, zone_high=high, context={"retested_level": round(resistance, 4)}))

    if recent_lows:
        support = min(float(hist["Low"].iloc[j]) for j in recent_lows)
        prior_close = float(hist["Close"].iloc[-2])
        if prior_close >= support and close < support * 0.997:
            out.append(pattern(hist, idx, "SUPPORT_BREAKDOWN", "CHART", "BEARISH", 62, zone_low=close, zone_high=support, context={"level": round(support, 4)}))
        elif high >= support >= close and close < support:
            out.append(pattern(hist, idx, "BREAKDOWN_RETEST_RESISTANCE", "CHART", "BEARISH", 58, zone_low=low, zone_high=support, context={"retested_level": round(support, 4)}))
    return out


def _broadening_patterns(hist: pd.DataFrame, highs: list[int], lows: list[int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if len(highs) < 3 or len(lows) < 3:
        return out
    hs = highs[-4:]
    ls = lows[-4:]
    high_vals = [float(hist["High"].iloc[i]) for i in hs]
    low_vals = [float(hist["Low"].iloc[i]) for i in ls]
    idx = max(hs[-1], ls[-1])
    if high_vals[-1] > high_vals[0] and low_vals[-1] < low_vals[0]:
        out.append(pattern(hist, idx, "BROADENING_FORMATION", "CHART", "NEUTRAL", 55, zone_low=min(low_vals), zone_high=max(high_vals), context={"high_swings": hs, "low_swings": ls}))
    if high_vals[-1] > high_vals[0] and min(low_vals[-2:]) > min(low_vals[:2]):
        out.append(pattern(hist, idx, "ASCENDING_BROADENING_WEDGE", "CHART", "BEARISH", 53, zone_low=min(low_vals), zone_high=max(high_vals), context={"high_swings": hs, "low_swings": ls}))
    if low_vals[-1] < low_vals[0] and max(high_vals[-2:]) < max(high_vals[:2]):
        out.append(pattern(hist, idx, "DESCENDING_BROADENING_WEDGE", "CHART", "BULLISH", 53, zone_low=min(low_vals), zone_high=max(high_vals), context={"high_swings": hs, "low_swings": ls}))
    return out

