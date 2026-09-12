import copy
import pandas as pd
import numpy as np
from typing import Dict, Any, List

from .patterns import detect_all_patterns
from .research_governance import scanner_research_governance


VALID_PATTERN_ENGINE_MODES = {"old", "new", "experimental", "risky", "selective", "balanced", "official", "v8", "vai", "vai2", "universal_v2"}


def normalize_pattern_engine_mode(mode: str | None = None) -> str:
    raw = (mode or "new").strip().lower()
    if raw in {"legacy", "classic", "v1"}:
        return "old"
    if raw in {"research", "expanded", "v2"}:
        return "new"
    if raw in {"strict", "v3", "lab", "experimental"}:
        return "experimental"
    if raw in {"v4", "super", "super_experimental", "risky", "aggressive"}:
        return "risky"
    if raw in {"v5", "selective", "quality", "quality_selective", "selective_v5", "sniper"}:
        return "selective"
    if raw in {"v6", "balanced", "shortfix", "short_fix", "allrounder", "all_rounder", "balanced_shortfix"}:
        return "balanced"
    if raw in {"v7", "official", "official_beta", "momentum", "momentum_long", "v020"}:
        return "official"
    if raw in {"v8", "research", "research_engine", "official_research"}:
        return "v8"
    if raw in {"vai", "vai1", "vai1.0", "vai_1_0", "vai_10", "vai_experimental", "vai1_experimental"}:
        return "vai"
    if raw in {"vai2", "vai2.0", "vai_2_0", "vai20", "vai_20", "vai2_experimental", "vai_ai2", "vai2.1", "vai_2_1", "vai21", "vai2.2", "vai_2_2", "vai22"}:
        return "vai2"
    return raw if raw in VALID_PATTERN_ENGINE_MODES else "new"


def _legacy_advanced_patterns(hist: pd.DataFrame, ind: Dict[str, Any]) -> Dict[str, Any]:
    candle = _identify_candle_patterns(hist)
    pattern_name = candle.get("pattern", "NONE")
    conf = float(candle.get("confidence", 0) or 0)
    direction = candle.get("direction") or "NEUTRAL"
    if pattern_name in {"HAMMER", "ENGULFING"} and direction == "NEUTRAL":
        direction = "BULLISH"
    if pattern_name == "NONE" or conf <= 0:
        recent = []
        top = None
    else:
        idx = len(hist) - 1
        try:
            timestamp = str(hist.index[-1])
            trigger = float(hist["Close"].iloc[-1])
        except Exception:
            timestamp = ""
            trigger = None
        top = {
            "pattern_name": pattern_name,
            "pattern_family": "LEGACY_CANDLE",
            "direction": direction,
            "confidence": conf,
            "timestamp": timestamp,
            "zone_low": None,
            "zone_high": None,
            "trigger_price": trigger,
            "candle_index": idx,
            "context": {"engine": "old", "source": "original lightweight candle detector"},
        }
        recent = [top]
    return {
        "ticker": None,
        "timeframe": "1d",
        "engine_mode": "old",
        "patterns": recent,
        "recent": recent,
        "summary": {
            "total_patterns": len(recent),
            "displayed_patterns": len(recent),
            "by_family": {"LEGACY_CANDLE": len(recent)} if recent else {},
            "by_direction": {direction: len(recent)} if recent else {},
            "engine_mode": "old",
        },
        "top_pattern": top,
        "warnings": [],
        "disclaimer": "Old pattern engine for A/B testing only. Educational, not financial advice.",
    }


def _experimental_advanced_patterns(hist: pd.DataFrame, ind: Dict[str, Any]) -> Dict[str, Any]:
    advanced = detect_all_patterns(hist, ind, timeframe="1d")
    patterns_raw = advanced.get("patterns") or []
    scored = []
    trend = str(ind.get("trend") or "").upper()
    vol_ratio = float(ind.get("vol_ratio") or 1)
    rsi = float(ind.get("rsi14") or 50)
    price = float(ind.get("price") or 0)
    ma20 = float(ind.get("ma20") or 0)
    ma50 = float(ind.get("ma50") or 0)

    for p in patterns_raw:
        q = dict(p)
        name = str(q.get("pattern_name") or "").upper()
        family = str(q.get("pattern_family") or "").upper()
        direction = str(q.get("direction") or "NEUTRAL").upper()
        conf = float(q.get("confidence") or 0)
        original = conf

        if family == "FVG":
            conf += 8
        elif family == "STRUCTURE":
            conf += 10
        elif family == "CHART":
            conf += 6
        elif family in {"CANDLE", "CANDLESTICK", "LEGACY_CANDLE"}:
            conf -= 6

        if direction == "BULLISH":
            if "UPTREND" in trend or (ma20 and price > ma20) or (ma50 and price > ma50):
                conf += 6
            if rsi > 75:
                conf -= 5
        elif direction == "BEARISH":
            if "DOWNTREND" in trend or (ma20 and price < ma20) or (ma50 and price < ma50):
                conf += 6
            if rsi < 25:
                conf -= 5

        if vol_ratio >= 1.8:
            conf += 6
        elif vol_ratio >= 1.3:
            conf += 3
        elif vol_ratio < 0.75:
            conf -= 5

        obscure_fragments = ("TASUKI", "SIDE_BY_SIDE", "CONCEALING", "SHORT_LINE", "RICKSHAW", "UNIQUE_THREE_RIVER")
        if any(x in name for x in obscure_fragments) and family not in {"FVG", "STRUCTURE", "CHART"}:
            conf -= 10

        q["confidence"] = max(0, min(99, round(conf, 1)))
        ctx = dict(q.get("context") or {})
        ctx["engine"] = "experimental"
        ctx["original_confidence"] = original
        ctx["experimental_adjustment"] = round(q["confidence"] - original, 1)
        q["context"] = ctx
        scored.append(q)

    important_families = {"FVG", "STRUCTURE", "CHART"}
    keep = []
    latest_idx = max((int(p.get("candle_index") or 0) for p in scored), default=0)
    for q in scored:
        idx = int(q.get("candle_index") or 0)
        family = str(q.get("pattern_family") or "").upper()
        conf = float(q.get("confidence") or 0)
        if idx >= latest_idx - 1:
            keep.append(q)
        elif family in important_families and conf >= 62:
            keep.append(q)
        elif conf >= 82:
            keep.append(q)

    keep = sorted(keep, key=lambda p: (int(p.get("candle_index") or 0), float(p.get("confidence") or 0)), reverse=True)[:80]
    display = [p for p in keep if int(p.get("candle_index") or 0) >= latest_idx - 1 or float(p.get("confidence") or 0) >= 70][:20]
    top = max(display or keep, key=lambda p: float(p.get("confidence") or 0), default=None)
    summary = {
        "total_patterns": len(keep),
        "raw_patterns_before_v3_filter": len(patterns_raw),
        "displayed_patterns": len(display),
        "engine_mode": "experimental",
        "by_family": dict(__import__('collections').Counter(str(p.get("pattern_family") or "UNKNOWN") for p in keep)),
        "by_direction": dict(__import__('collections').Counter(str(p.get("direction") or "NEUTRAL") for p in keep)),
        "v3_policy": "Expanded detector with stricter confidence weighting, trend/volume alignment, and noisy candle filtering.",
    }
    return {
        "ticker": advanced.get("ticker"),
        "timeframe": advanced.get("timeframe", "1d"),
        "engine_mode": "experimental",
        "patterns": keep,
        "recent": display,
        "summary": summary,
        "top_pattern": top,
        "warnings": advanced.get("warnings", []),
        "disclaimer": "Experimental pattern engine for research/testing only. Educational, not financial advice.",
    }


def _risky_advanced_patterns(hist: pd.DataFrame, ind: Dict[str, Any]) -> Dict[str, Any]:
    advanced = detect_all_patterns(hist, ind, timeframe="1d")
    patterns_raw = advanced.get("patterns") or []
    scored = []
    trend = str(ind.get("trend") or "").upper()
    vol_ratio = float(ind.get("vol_ratio") or 1)
    rsi = float(ind.get("rsi14") or 50)
    price = float(ind.get("price") or 0)
    ma20 = float(ind.get("ma20") or 0)
    ma50 = float(ind.get("ma50") or 0)
    mom5 = float(ind.get("momentum_5d") or 0)
    mom20 = float(ind.get("momentum_20d") or 0)

    for p in patterns_raw:
        q = dict(p)
        name = str(q.get("pattern_name") or "").upper()
        family = str(q.get("pattern_family") or "").upper()
        direction = str(q.get("direction") or "NEUTRAL").upper()
        conf = float(q.get("confidence") or 0)
        original = conf

        if family == "FVG":
            conf += 15
        elif family == "STRUCTURE":
            conf += 18
        elif family == "CHART":
            conf += 14
        elif family in {"CANDLE", "CANDLESTICK", "LEGACY_CANDLE"}:
            conf += 2

        if direction == "BULLISH":
            if price and ma20 and price > ma20:
                conf += 7
            if price and ma50 and price > ma50:
                conf += 5
            if mom5 > 1.5 or mom20 > 4:
                conf += 8
            if rsi > 82:
                conf -= 6
        elif direction == "BEARISH":
            if price and ma20 and price < ma20:
                conf += 7
            if price and ma50 and price < ma50:
                conf += 5
            if mom5 < -1.5 or mom20 < -4:
                conf += 8
            if rsi < 18:
                conf -= 6

        if vol_ratio >= 2.0:
            conf += 10
        elif vol_ratio >= 1.3:
            conf += 5
        elif vol_ratio < 0.55:
            conf -= 4

        cluster_fragments = ("TASUKI", "GAP", "BREAKAWAY", "MAT_HOLD", "THREE", "WEDGE", "BROADENING", "RETEST")
        if any(x in name for x in cluster_fragments):
            conf += 5

        q["confidence"] = max(0, min(99, round(conf, 1)))
        ctx = dict(q.get("context") or {})
        ctx["engine"] = "risky"
        ctx["original_confidence"] = original
        ctx["v4_adjustment"] = round(q["confidence"] - original, 1)
        ctx["warning"] = "Super-experimental V4 weighting; high false-positive risk."
        q["context"] = ctx
        scored.append(q)

    latest_idx = max((int(p.get("candle_index") or 0) for p in scored), default=max(len(hist) - 1, 0))
    keep = []
    for q in scored:
        idx = int(q.get("candle_index") or 0)
        conf = float(q.get("confidence") or 0)
        if idx >= latest_idx - 3 or conf >= 58:
            keep.append(q)

    if not keep and len(hist) >= 60 and price > 0:
        direction = "NEUTRAL"
        name = "RISKY_NEUTRAL_OBSERVATION"
        conf = 42.0
        if (price > ma20 > 0 and price > ma50 > 0 and 52 <= rsi <= 78 and mom20 > 2):
            direction, name, conf = "BULLISH", "RISKY_MOMENTUM_CHASE", 68.0
        elif (price < ma20 and price < ma50 and 22 <= rsi <= 48 and mom20 < -2):
            direction, name, conf = "BEARISH", "RISKY_DOWNTREND_CONTINUATION", 68.0
        elif rsi <= 32 and mom5 < -3:
            direction, name, conf = "BULLISH", "RISKY_OVERSOLD_BOUNCE", 62.0
        elif rsi >= 68 and mom5 > 3:
            direction, name, conf = "BEARISH", "RISKY_OVERBOUGHT_FADE", 62.0
        if direction != "NEUTRAL":
            try:
                timestamp = str(hist.index[-1])
                trigger = float(hist["Close"].iloc[-1])
            except Exception:
                timestamp, trigger = "", price
            keep.append({
                "pattern_name": name,
                "pattern_family": "RISKY_SYNTHETIC",
                "direction": direction,
                "confidence": conf,
                "timestamp": timestamp,
                "zone_low": None,
                "zone_high": None,
                "trigger_price": trigger,
                "candle_index": len(hist) - 1,
                "context": {"engine": "risky", "warning": "Synthetic V4 marker for high-coverage testing only."},
            })

    keep = sorted(keep, key=lambda p: (int(p.get("candle_index") or 0), float(p.get("confidence") or 0)), reverse=True)[:120]
    display = [p for p in keep if int(p.get("candle_index") or 0) >= latest_idx - 3 or float(p.get("confidence") or 0) >= 60][:30]
    top = max(display or keep, key=lambda p: float(p.get("confidence") or 0), default=None)
    summary = {
        "total_patterns": len(keep),
        "raw_patterns_before_v4_filter": len(patterns_raw),
        "displayed_patterns": len(display),
        "engine_mode": "risky",
        "by_family": dict(__import__('collections').Counter(str(p.get("pattern_family") or "UNKNOWN") for p in keep)),
        "by_direction": dict(__import__('collections').Counter(str(p.get("direction") or "NEUTRAL") for p in keep)),
        "v4_policy": "Super-experimental high-coverage engine with aggressive momentum/reversal weighting. Hidden testing only.",
    }
    return {
        "ticker": advanced.get("ticker"),
        "timeframe": advanced.get("timeframe", "1d"),
        "engine_mode": "risky",
        "patterns": keep,
        "recent": display,
        "summary": summary,
        "top_pattern": top,
        "warnings": list(advanced.get("warnings", [])) + ["V4 risky engine is intentionally aggressive and may overfit / create false positives."],
        "disclaimer": "Super-experimental risky pattern engine for stress testing only. Educational, not financial advice.",
    }


def _selective_advanced_patterns(hist: pd.DataFrame, ind: Dict[str, Any]) -> Dict[str, Any]:
    advanced = detect_all_patterns(hist, ind, timeframe="1d")
    patterns_raw = advanced.get("patterns") or []

    scored = []
    trend = str(ind.get("trend") or "").upper()
    vol_ratio = float(ind.get("vol_ratio") or 1)
    rsi = float(ind.get("rsi14") or 50)
    price = float(ind.get("price") or 0)
    ma20 = float(ind.get("ma20") or 0)
    ma50 = float(ind.get("ma50") or 0)
    ma200 = float(ind.get("ma200") or 0)
    adx = float(ind.get("adx14") or 0)
    di_plus = float(ind.get("di_plus") or 0)
    di_minus = float(ind.get("di_minus") or 0)
    mom5 = float(ind.get("momentum_5d") or 0)
    mom20 = float(ind.get("momentum_20d") or 0)
    bb_width = float(ind.get("bb_width") or 10)
    vol_diverg = str(ind.get("volume_price_divergence") or "").upper()

    strong_fragments = (
        "FVG", "BOS", "CHOCH", "BREAKOUT", "BREAKDOWN", "RETEST",
        "CUP", "DOUBLE_BOTTOM", "DOUBLE_TOP", "HEAD", "SHOULDERS",
        "ENGULFING", "MORNING_STAR", "EVENING_STAR", "THREE_WHITE",
        "THREE_BLACK", "HAMMER", "SHOOTING_STAR", "BREAKAWAY"
    )
    noisy_fragments = (
        "TASUKI", "SIDE_BY_SIDE", "RICKSHAW", "SHORT_LINE", "DOJI",
        "SPINNING", "HARAMI", "UNIQUE_THREE_RIVER", "CONCEALING",
        "STICK_SANDWICH"
    )

    for p in patterns_raw:
        q = dict(p)
        name = str(q.get("pattern_name") or "").upper()
        family = str(q.get("pattern_family") or "").upper()
        direction = str(q.get("direction") or "NEUTRAL").upper()
        conf = float(q.get("confidence") or 0)
        original = conf

        alignment = 0
        vetoes = []

        if family == "STRUCTURE":
            conf += 12
        elif family == "FVG":
            conf += 10
        elif family == "CHART":
            conf += 8
        elif family in {"CANDLE", "CANDLESTICK", "LEGACY_CANDLE"}:
            conf -= 10

        if any(x in name for x in strong_fragments):
            conf += 6
        if any(x in name for x in noisy_fragments) and family not in {"FVG", "STRUCTURE", "CHART"}:
            conf -= 14

        if direction == "BULLISH":
            if price and ma50 and price > ma50:
                conf += 7; alignment += 1
            else:
                conf -= 7; vetoes.append("below_or_not_above_ma50")
            if price and ma200 and price > ma200:
                conf += 7; alignment += 1
            if mom20 > 0:
                conf += 6; alignment += 1
            else:
                conf -= 6; vetoes.append("negative_20d_momentum")
            if adx >= 18 and di_plus >= di_minus:
                conf += 7; alignment += 1
            elif adx < 14:
                conf -= 10; vetoes.append("weak_adx")
            if 38 <= rsi <= 68:
                conf += 6; alignment += 1
            elif rsi > 74 or rsi < 28:
                conf -= 10; vetoes.append("bad_rsi_for_long")
            if vol_diverg == "BEARISH_DIVERGENCE":
                conf -= 12; vetoes.append("bearish_volume_divergence")

        elif direction == "BEARISH":
            if price and ma50 and price < ma50:
                conf += 7; alignment += 1
            else:
                conf -= 7; vetoes.append("above_or_not_below_ma50")
            if price and ma200 and price < ma200:
                conf += 7; alignment += 1
            if mom20 < 0:
                conf += 6; alignment += 1
            else:
                conf -= 6; vetoes.append("positive_20d_momentum")
            if adx >= 18 and di_minus >= di_plus:
                conf += 7; alignment += 1
            elif adx < 14:
                conf -= 10; vetoes.append("weak_adx")
            if 32 <= rsi <= 62:
                conf += 6; alignment += 1
            elif rsi < 22 or rsi > 76:
                conf -= 10; vetoes.append("bad_rsi_for_short")
            if vol_diverg == "BULLISH_DIVERGENCE":
                conf -= 12; vetoes.append("bullish_volume_divergence")

        if vol_ratio >= 1.25:
            conf += 7; alignment += 1
        elif vol_ratio >= 0.9:
            conf += 2
        elif vol_ratio < 0.7:
            conf -= 12; vetoes.append("low_volume")

        if bb_width < 3.5:
            conf -= 8; vetoes.append("tight_chop")
        if trend == "SIDEWAYS":
            conf -= 9; vetoes.append("sideways_market")

        q["confidence"] = max(0, min(99, round(conf, 1)))
        ctx = dict(q.get("context") or {})
        ctx["engine"] = "selective"
        ctx["original_confidence"] = original
        ctx["v5_adjustment"] = round(q["confidence"] - original, 1)
        ctx["alignment_score"] = alignment
        ctx["vetoes"] = vetoes
        ctx["policy"] = "V5 keeps only higher-confidence patterns with trend, volume, and momentum context."
        q["context"] = ctx
        q["_v5_alignment"] = alignment
        q["_v5_vetoes"] = vetoes
        scored.append(q)

    latest_idx = max((int(p.get("candle_index") or 0) for p in scored), default=max(len(hist) - 1, 0))
    keep = []
    for q in scored:
        idx = int(q.get("candle_index") or 0)
        conf = float(q.get("confidence") or 0)
        family = str(q.get("pattern_family") or "").upper()
        alignment = int(q.get("_v5_alignment") or 0)
        direction = str(q.get("direction") or "NEUTRAL").upper()

        if direction not in {"BULLISH", "BEARISH"}:
            continue

        if conf >= 82 and alignment >= 3:
            keep.append(q)
        elif family in {"FVG", "STRUCTURE", "CHART"} and conf >= 76 and alignment >= 3:
            keep.append(q)
        elif idx >= latest_idx - 1 and conf >= 86 and alignment >= 4:
            keep.append(q)

    keep = sorted(
        keep,
        key=lambda p: (int(p.get("_v5_alignment") or 0), float(p.get("confidence") or 0), int(p.get("candle_index") or 0)),
        reverse=True
    )[:50]
    display = [p for p in keep if int(p.get("candle_index") or 0) >= latest_idx - 2 or float(p.get("confidence") or 0) >= 82][:15]
    top = max(display or keep, key=lambda p: (int(p.get("_v5_alignment") or 0), float(p.get("confidence") or 0)), default=None)

    summary = {
        "total_patterns": len(keep),
        "raw_patterns_before_v5_filter": len(patterns_raw),
        "displayed_patterns": len(display),
        "engine_mode": "selective",
        "by_family": dict(__import__('collections').Counter(str(p.get("pattern_family") or "UNKNOWN") for p in keep)),
        "by_direction": dict(__import__('collections').Counter(str(p.get("direction") or "NEUTRAL") for p in keep)),
        "v5_policy": "Selective engine: lower coverage, higher no-trade rate, stronger trend/volume/momentum gating.",
    }

    return {
        "ticker": advanced.get("ticker"),
        "timeframe": advanced.get("timeframe", "1d"),
        "engine_mode": "selective",
        "patterns": keep,
        "recent": display,
        "summary": summary,
        "top_pattern": top,
        "warnings": list(advanced.get("warnings", [])) + ["V5 selective engine is experimental and may skip many setups."],
        "disclaimer": "Selective V5 pattern engine for testing only. Educational, not financial advice.",
    }


def _balanced_advanced_patterns(hist: pd.DataFrame, ind: Dict[str, Any], base: Dict[str, Any] | None = None) -> Dict[str, Any]:
    base = copy.deepcopy(base) if base is not None else _selective_advanced_patterns(hist, ind)
    patterns_raw = list(base.get("patterns") or [])

    price = float(ind.get("price") or 0)
    ma50 = float(ind.get("ma50") or 0)
    ma200 = float(ind.get("ma200") or 0)
    above_ma50 = bool(ind.get("above_ma50"))
    above_ma200 = bool(ind.get("above_ma200"))
    trend = str(ind.get("trend") or "").upper()
    mom20 = float(ind.get("momentum_20d") or 0)
    mom5 = float(ind.get("momentum_5d") or 0)
    adx = float(ind.get("adx14") or 0)
    di_plus = float(ind.get("di_plus") or 0)
    di_minus = float(ind.get("di_minus") or 0)
    rsi = float(ind.get("rsi14") or 50)
    vol_ratio = float(ind.get("vol_ratio") or 1)
    vol_diverg = str(ind.get("volume_price_divergence") or "").upper()

    bearish_context_points = 0
    if not above_ma50:
        bearish_context_points += 1
    if not above_ma200:
        bearish_context_points += 1
    if mom20 < -1.0:
        bearish_context_points += 1
    if adx >= 20 and di_minus > di_plus:
        bearish_context_points += 1
    if rsi < 50:
        bearish_context_points += 1
    if vol_diverg == "BEARISH_DIVERGENCE":
        bearish_context_points += 1

    bull_context = (
        above_ma50 and above_ma200
        or mom20 > 1.0
        or trend in {"UP", "UPTREND", "BULL", "BULLISH", "STRONG_UP"}
        or (adx >= 18 and di_plus > di_minus and above_ma50)
    )

    keep = []
    removed_bearish = 0
    downgraded_bearish = 0

    for p in patterns_raw:
        q = dict(p)
        direction = str(q.get("direction") or "NEUTRAL").upper()
        name = str(q.get("pattern_name") or "").upper()
        family = str(q.get("pattern_family") or "").upper()
        conf = float(q.get("confidence") or 0)
        ctx = dict(q.get("context") or {})
        ctx["engine"] = "balanced"
        ctx["v6_short_context_points"] = bearish_context_points
        ctx["v6_bull_context"] = bool(bull_context)

        if direction == "BEARISH":
            if bull_context:
                conf -= 28
                downgraded_bearish += 1
                ctx.setdefault("v6_notes", []).append("Bearish pattern penalized because broader context is bullish/up-momentum.")
            if bearish_context_points < 4:
                conf -= 22
                downgraded_bearish += 1
                ctx.setdefault("v6_notes", []).append("Bearish pattern lacks enough bearish confirmation points.")
            if "BEARISH_FAIR_VALUE_GAP" in name or name == "BEARISH_FVG":
                conf -= 10
                ctx.setdefault("v6_notes", []).append("Bearish FVG penalty from lab results.")
            if family in {"CANDLE", "CANDLESTICK", "LEGACY_CANDLE"}:
                conf -= 8
            if vol_ratio < 0.85:
                conf -= 8

            q["confidence"] = max(0, min(99, round(conf, 1)))
            q["context"] = ctx

            if q["confidence"] >= 82 and bearish_context_points >= 4 and not bull_context:
                keep.append(q)
            else:
                removed_bearish += 1
            continue

        if direction == "BULLISH":
            if above_ma50 and above_ma200 and mom20 > 0:
                conf += 4
            if rsi > 78 and mom5 > 7:
                conf -= 8
                ctx.setdefault("v6_notes", []).append("Long reduced for stretched short-term move.")
            q["confidence"] = max(0, min(99, round(conf, 1)))
            q["context"] = ctx
            if q["confidence"] >= 74:
                keep.append(q)
            continue

        q["context"] = ctx
        if conf >= 80:
            keep.append(q)

    latest_idx = max((int(p.get("candle_index") or 0) for p in keep), default=max(len(hist) - 1, 0))
    keep = sorted(
        keep,
        key=lambda p: (float(p.get("confidence") or 0), int(p.get("candle_index") or 0)),
        reverse=True,
    )[:50]
    display = [p for p in keep if int(p.get("candle_index") or 0) >= latest_idx - 2 or float(p.get("confidence") or 0) >= 82][:15]
    top = max(display or keep, key=lambda p: float(p.get("confidence") or 0), default=None)

    summary = dict(base.get("summary") or {})
    summary.update({
        "engine_mode": "balanced",
        "total_patterns": len(keep),
        "displayed_patterns": len(display),
        "v6_shortfix_policy": "Bearish patterns require real bearish context; weak/bull-market shorts are filtered.",
        "v6_bearish_context_points": bearish_context_points,
        "v6_bull_context": bool(bull_context),
        "v6_removed_bearish_patterns": removed_bearish,
        "v6_downgraded_bearish_patterns": downgraded_bearish,
        "by_family": dict(__import__('collections').Counter(str(p.get("pattern_family") or "UNKNOWN") for p in keep)),
        "by_direction": dict(__import__('collections').Counter(str(p.get("direction") or "NEUTRAL") for p in keep)),
    })

    return {
        "ticker": base.get("ticker"),
        "timeframe": base.get("timeframe", "1d"),
        "engine_mode": "balanced",
        "patterns": keep,
        "recent": display,
        "summary": summary,
        "top_pattern": top,
        "warnings": list(base.get("warnings", [])) + ["V6 Balanced / Short-Fix is experimental. It filters many weak short signals."],
        "disclaimer": "V6 Balanced / Short-Fix is for testing only. Educational, not financial advice.",
    }


def _official_v7_advanced_patterns(hist: pd.DataFrame, ind: Dict[str, Any], base: Dict[str, Any] | None = None) -> Dict[str, Any]:
    base = copy.deepcopy(base) if base is not None else _selective_advanced_patterns(hist, ind)
    raw_patterns = list(base.get("patterns") or [])

    above50 = bool(ind.get("above_ma50"))
    above200 = bool(ind.get("above_ma200"))
    mom20 = float(ind.get("momentum_20d") or 0)
    mom60 = float(ind.get("momentum_60d") or 0)
    mom5 = float(ind.get("momentum_5d") or 0)
    rsi = float(ind.get("rsi14") or 50)
    adx = float(ind.get("adx14") or 0)
    di_plus = float(ind.get("di_plus") or 0)
    di_minus = float(ind.get("di_minus") or 0)
    vol_ratio = float(ind.get("vol_ratio") or 1)
    atr_pct = float(ind.get("atr_pct") or 0)
    bb_width = float(ind.get("bb_width") or 0)
    vol_diverg = str(ind.get("volume_price_divergence") or "").upper()

    momentum_points = 0
    if above50: momentum_points += 1
    if above200: momentum_points += 1
    if mom20 > 1.0: momentum_points += 1
    if mom60 > 0: momentum_points += 1
    if adx >= 18 and di_plus >= di_minus: momentum_points += 1
    if 42 <= rsi <= 70: momentum_points += 1
    if vol_ratio >= 0.65: momentum_points += 1

    keep = []
    blocked_bearish = 0
    long_boosted = 0
    risk_blocked = 0

    good_long_patterns = (
        "BULLISH_FAIR_VALUE_GAP", "INVERSION_FVG_BULLISH", "BOS_BULLISH",
        "CUP_AND_HANDLE", "CUP_FORMATION", "DOUBLE_BOTTOM", "BULL_FLAG",
        "BULLISH_ENGULFING", "THREE_WHITE_SOLDIERS", "MORNING_STAR", "MORNING_DOJI_STAR",
        "BREAKOUT", "RETEST"
    )

    for p in raw_patterns:
        q = dict(p)
        direction = str(q.get("direction") or "NEUTRAL").upper()
        name = str(q.get("pattern_name") or "").upper()
        family = str(q.get("pattern_family") or "").upper()
        conf = float(q.get("confidence") or 0)
        ctx = dict(q.get("context") or {})
        ctx["engine"] = "official"
        ctx["v7_momentum_points"] = momentum_points
        ctx["v7_policy"] = "Official V0.2.1 beta favors confirmed bullish momentum longs and blocks bearish trade candidates."

        if direction == "BEARISH":
            blocked_bearish += 1
            ctx.setdefault("v7_notes", []).append("Bearish pattern blocked from trade candidacy by V7 release policy.")
            q["confidence"] = max(0, min(45, round(conf * 0.45, 1)))
            q["context"] = ctx
            continue

        if direction != "BULLISH":
            continue

        if any(x in name for x in good_long_patterns):
            conf += 10
            long_boosted += 1
        elif family in {"FVG", "STRUCTURE", "CHART"}:
            conf += 4
        else:
            conf -= 8

        if momentum_points >= 6:
            conf += 14
        elif momentum_points == 5:
            conf += 8
        elif momentum_points == 4:
            conf -= 4
        else:
            conf -= 22
            ctx.setdefault("v7_notes", []).append("Insufficient bullish momentum confirmation.")

        if not above50:
            conf -= 16
            ctx.setdefault("v7_notes", []).append("Below MA50: V7 avoids bullish momentum trades without MA50 support.")
        if not above200:
            conf -= 10
            ctx.setdefault("v7_notes", []).append("Below MA200: release engine requires stronger trend support.")
        if mom20 <= 0:
            conf -= 18
            ctx.setdefault("v7_notes", []).append("20-day momentum is not bullish.")
        if adx < 14:
            conf -= 10
            ctx.setdefault("v7_notes", []).append("Weak trend strength.")
        if di_plus < di_minus:
            conf -= 12
            ctx.setdefault("v7_notes", []).append("DI- is stronger than DI+.")
        if rsi > 78 and mom5 > 7:
            conf -= 18
            ctx.setdefault("v7_notes", []).append("Avoids chasing overextended bullish moves.")
        elif rsi > 72:
            conf -= 6
        if atr_pct > 7.5:
            conf -= 16
            risk_blocked += 1
            ctx.setdefault("v7_notes", []).append("High ATR%: reduces trades with concerning stop-hit risk.")
        elif atr_pct > 5.5:
            conf -= 8
            ctx.setdefault("v7_notes", []).append("Elevated ATR%: grade/trade quality reduced.")
        if bb_width > 18:
            conf -= 8
            ctx.setdefault("v7_notes", []).append("Wide Bollinger range: risk control penalty.")
        if vol_diverg == "BEARISH_DIVERGENCE":
            conf -= 14
            ctx.setdefault("v7_notes", []).append("Bearish volume divergence conflicts with long.")

        q["confidence"] = max(0, min(99, round(conf, 1)))
        q["context"] = ctx

        if q["confidence"] >= 82 and momentum_points >= 5 and above50 and mom20 > 0:
            keep.append(q)

    latest_idx = max((int(p.get("candle_index") or 0) for p in keep), default=max(len(hist) - 1, 0))
    keep = sorted(keep, key=lambda p: (float(p.get("confidence") or 0), int(p.get("candle_index") or 0)), reverse=True)[:40]
    display = [p for p in keep if int(p.get("candle_index") or 0) >= latest_idx - 2 or float(p.get("confidence") or 0) >= 86][:12]
    top = max(display or keep, key=lambda p: float(p.get("confidence") or 0), default=None)

    summary = dict(base.get("summary") or {})
    summary.update({
        "engine_mode": "official",
        "release_version": "0.2.1-beta",
        "total_patterns": len(keep),
        "displayed_patterns": len(display),
        "v7_policy": "High-conviction bullish momentum longs; bearish trade candidates blocked/no-trade by default.",
        "v7_momentum_points": momentum_points,
        "v7_blocked_bearish_patterns": blocked_bearish,
        "v7_boosted_long_patterns": long_boosted,
        "v7_risk_blocked_patterns": risk_blocked,
        "by_family": dict(__import__('collections').Counter(str(p.get("pattern_family") or "UNKNOWN") for p in keep)),
        "by_direction": dict(__import__('collections').Counter(str(p.get("direction") or "NEUTRAL") for p in keep)),
    })
    return {
        "ticker": base.get("ticker"),
        "timeframe": base.get("timeframe", "1d"),
        "engine_mode": "official",
        "patterns": keep,
        "recent": display,
        "summary": summary,
        "top_pattern": top,
        "warnings": list(base.get("warnings", [])) + ["V7 Official Beta is long-biased and will no-trade most bearish setups."],
        "disclaimer": "V7 Official Beta is experimental and educational only, not financial advice.",
    }


def _v8_advanced_patterns(hist: pd.DataFrame, ind: Dict[str, Any], base: Dict[str, Any] | None = None) -> Dict[str, Any]:
    from .v8_engine import canonical_direction, v8_candidate_score, V8_VERSION

    base = copy.deepcopy(base) if base is not None else _balanced_advanced_patterns(hist, ind)
    rescored: list[dict[str, Any]] = []
    rejected = 0
    for pattern in list(base.get("patterns") or []):
        item = dict(pattern)
        side = canonical_direction(item.get("direction"))
        if side not in {"LONG", "SHORT"}:
            rejected += 1
            continue
        scored = v8_candidate_score(
            float(item.get("confidence") or 0),
            ind,
            side,
            pattern_direction=item.get("direction"),
            pattern_confidence=item.get("confidence"),
        )
        context = dict(item.get("context") or {})
        context.update({
            "engine": "v8",
            "v8_version": V8_VERSION,
            "v8_policy": "V7-derived candidates judged by a symmetric analytics evidence model.",
            "v8_alignment": scored["alignment"],
        })
        item["confidence"] = scored["score"]
        item["context"] = context
        if item["confidence"] >= 60:
            rescored.append(item)
        else:
            rejected += 1

    rescored.sort(
        key=lambda pattern: (float(pattern.get("confidence") or 0), int(pattern.get("candle_index") or 0)),
        reverse=True,
    )
    rescored = rescored[:60]
    latest_index = max((int(pattern.get("candle_index") or 0) for pattern in rescored), default=max(len(hist) - 1, 0))
    display = [
        pattern for pattern in rescored
        if int(pattern.get("candle_index") or 0) >= latest_index - 3
        or float(pattern.get("confidence") or 0) >= 78
    ][:16]
    top = max(display or rescored, key=lambda pattern: float(pattern.get("confidence") or 0), default=None)
    summary = dict(base.get("summary") or {})
    summary.update({
        "engine_mode": "v8",
        "release_version": V8_VERSION,
        "total_patterns": len(rescored),
        "displayed_patterns": len(display),
        "v8_rejected_patterns": rejected,
        "v8_policy": "Symmetric evidence scoring; ATR is risk-only; volume confirms rather than creates direction.",
        "ticker_identity_used": False,
        "by_family": dict(__import__("collections").Counter(str(pattern.get("pattern_family") or "UNKNOWN") for pattern in rescored)),
        "by_direction": dict(__import__("collections").Counter(str(pattern.get("direction") or "NEUTRAL") for pattern in rescored)),
    })
    return {
        "ticker": base.get("ticker"),
        "timeframe": base.get("timeframe", "1d"),
        "engine_mode": "v8",
        "patterns": rescored,
        "recent": display,
        "summary": summary,
        "top_pattern": top,
        "warnings": list(base.get("warnings", [])) + [
            "V8 is a research engine. Validate it on untouched dates and symbols before promotion."
        ],
        "disclaimer": "V8 is deterministic educational research, not financial advice.",
    }


def _vai_1_0_advanced_patterns(hist: pd.DataFrame, ind: Dict[str, Any]) -> Dict[str, Any]:
    base = _official_v7_advanced_patterns(hist, ind)
    for p in base.get("patterns") or []:
        ctx = dict(p.get("context") or {})
        ctx["engine"] = "vai"
        ctx["vai_policy"] = "VAI 1.0 uses V7 candidate patterns, then applies a trained local model in setup scoring."
        p["context"] = ctx
    summary = dict(base.get("summary") or {})
    summary.update({
        "engine_mode": "vai",
        "release_version": "VAI-1.0-experimental",
        "vai_policy": "Trainable experimental model layer on top of V7 Official Momentum candidates.",
    })
    out = dict(base)
    out["engine_mode"] = "vai"
    out["summary"] = summary
    out["warnings"] = list(base.get("warnings", [])) + ["VAI 1.0 is experimental. Train/validate before trusting outputs."]
    out["disclaimer"] = "VAI 1.0 Experimental is educational only and not financial advice."
    return out


def _vai_2_0_advanced_patterns(hist: pd.DataFrame, ind: Dict[str, Any]) -> Dict[str, Any]:
    base = _official_v7_advanced_patterns(hist, ind)
    for p in base.get("patterns") or []:
        ctx = dict(p.get("context") or {})
        ctx["engine"] = "vai2"
        ctx["vai_policy"] = "VAI 2.2 uses V7 candidates plus promoted local models for accept/return/stop-risk/confidence-size decisions."
        p["context"] = ctx
    summary = dict(base.get("summary") or {})
    summary.update({
        "engine_mode": "vai2",
        "release_version": "VAI-2.2-chronological-pit-experimental",
        "vai_policy": "Trainable chronological, purge-gapped model layer with promotion gates; evaluates return quality, stop-risk, and confidence sizing rather than win rate alone.",
    })
    out = dict(base)
    out["engine_mode"] = "vai2"
    out["summary"] = summary
    out["warnings"] = list(base.get("warnings", [])) + ["VAI 2.2 is experimental. Train/validate before trusting outputs."]
    out["disclaimer"] = "VAI 2.2 Chronological PIT Experimental is educational only and not financial advice."
    return out


def _compose_pattern_report(
    hist: pd.DataFrame,
    ind: Dict[str, Any],
    advanced: Dict[str, Any],
    common: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if common is None:
        close = hist["Close"]
        high = hist["High"]
        low = hist["Low"]
        volume = hist["Volume"]
        common = {
            "rsi_divergence": _detect_rsi_divergence(close, hist),
            "macd_divergence": _detect_macd_divergence(ind),
            "volume_divergence": _detect_volume_divergence(close, volume),
            "sr_strength": _analyze_sr_strength(high, low, close),
            "volatility_regime": _analyze_volatility_regime(close, ind),
        }
    patterns = copy.deepcopy(common)
    top_pattern = advanced.get("top_pattern") or {}

    if top_pattern:
        patterns["recent_pattern"] = {
            "pattern": top_pattern.get("pattern_name", "NONE"),
            "confidence": top_pattern.get("confidence", 0),
            "direction": top_pattern.get("direction", "NEUTRAL"),
            "family": top_pattern.get("pattern_family", "UNKNOWN"),
        }
    else:
        patterns["recent_pattern"] = _identify_candle_patterns(hist)

    patterns["advanced_patterns"] = advanced
    patterns["detected_patterns"] = advanced.get("recent", [])
    patterns["pattern_summary"] = advanced.get("summary", {})

    patterns["momentum_confirmation"] = _confirm_momentum(ind)

    return patterns


def analyze_patterns_multi(
    hist: pd.DataFrame, ind: Dict[str, Any], modes: List[str]
) -> Dict[str, Dict[str, Any]]:


    normalized = []
    for mode in modes:
        clean = normalize_pattern_engine_mode(mode)
        if clean not in normalized:
            normalized.append(clean)
    close = hist["Close"]
    common = {
        "rsi_divergence": _detect_rsi_divergence(close, hist),
        "macd_divergence": _detect_macd_divergence(ind),
        "volume_divergence": _detect_volume_divergence(close, hist["Volume"]),
        "sr_strength": _analyze_sr_strength(hist["High"], hist["Low"], close),
        "volatility_regime": _analyze_volatility_regime(close, ind),
    }
    selective_base = None
    if any(mode in {"official", "v8"} for mode in normalized):
        selective_base = _selective_advanced_patterns(hist, ind)
    output: Dict[str, Dict[str, Any]] = {}
    for mode in normalized:
        if mode == "official":
            advanced = _official_v7_advanced_patterns(hist, ind, base=selective_base)
        elif mode == "v8":
            balanced = _balanced_advanced_patterns(hist, ind, base=selective_base)
            advanced = _v8_advanced_patterns(hist, ind, base=balanced)
        elif mode == "old":
            advanced = _legacy_advanced_patterns(hist, ind)
        elif mode == "experimental":
            advanced = _experimental_advanced_patterns(hist, ind)
        elif mode == "risky":
            advanced = _risky_advanced_patterns(hist, ind)
        elif mode == "selective":
            advanced = copy.deepcopy(selective_base) if selective_base is not None else _selective_advanced_patterns(hist, ind)
        elif mode == "balanced":
            advanced = _balanced_advanced_patterns(hist, ind, base=selective_base)
        elif mode == "vai":
            advanced = _vai_1_0_advanced_patterns(hist, ind)
        elif mode == "vai2":
            advanced = _vai_2_0_advanced_patterns(hist, ind)
        else:
            advanced = detect_all_patterns(hist, ind, timeframe="1d")
            advanced["engine_mode"] = "new"
            advanced.setdefault("summary", {})["engine_mode"] = "new"
        report = _compose_pattern_report(hist, ind, advanced, common=common)
        # A common contract makes Official, V8, VAI, and VAI2 outputs
        # comparable without altering their independent scoring policies.
        report["research_governance"] = scanner_research_governance(mode, hist)
        output[mode] = report
    return output


def analyze_patterns(hist: pd.DataFrame, ind: Dict[str, Any], mode: str | None = None) -> Dict[str, Any]:
    engine_mode = normalize_pattern_engine_mode(mode)
    return analyze_patterns_multi(hist, ind, [engine_mode])[engine_mode]


def _detect_rsi_divergence(close: pd.Series, hist: pd.DataFrame) -> Dict[str, Any]:
    if len(close) < 50:
        return {"type": "NONE", "strength": 0}

    recent = close.iloc[-40:]
    recent_high = hist["High"].iloc[-40:]
    recent_low = hist["Low"].iloc[-40:]

    delta = recent.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_g = gain.rolling(14).mean()
    avg_l = loss.rolling(14).mean()
    rs = avg_g / avg_l.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    if len(rsi) < 4:
        return {"type": "NONE", "strength": 0}

    price_low_1 = recent_low.iloc[-15:].min()
    price_low_2 = recent_low.iloc[-30:-15].min() if len(hist) >= 30 else price_low_1
    rsi_low_1 = rsi.iloc[-15:].min()
    rsi_low_2 = rsi.iloc[-30:-15].min() if len(rsi) >= 30 else rsi_low_1

    price_high_1 = recent_high.iloc[-15:].max()
    price_high_2 = recent_high.iloc[-30:-15].max() if len(hist) >= 30 else price_high_1
    rsi_high_1 = rsi.iloc[-15:].max()
    rsi_high_2 = rsi.iloc[-30:-15].max() if len(rsi) >= 30 else rsi_high_1

    if price_low_1 < price_low_2 and rsi_low_1 > rsi_low_2 and rsi_low_1 < 50:
        strength = (rsi_low_2 - rsi_low_1) * 1.5
        return {"type": "BULLISH", "strength": min(strength, 30)}

    if price_high_1 > price_high_2 and rsi_high_1 < rsi_high_2 and rsi_high_1 > 50:
        strength = (rsi_high_1 - rsi_high_2) * 1.5
        return {"type": "BEARISH", "strength": min(strength, 30)}

    return {"type": "NONE", "strength": 0}


def _detect_macd_divergence(ind: Dict[str, Any]) -> Dict[str, Any]:
    macd_hist = ind.get("macd_hist", 0) or 0
    macd_cross = ind.get("macd_cross", "")

    if "BULLISH" in macd_cross and macd_hist > 0:
        return {"type": "BULLISH_CROSS", "strength": 20}
    elif "BEARISH" in macd_cross and macd_hist < 0:
        return {"type": "BEARISH_CROSS", "strength": 20}

    return {"type": "NONE", "strength": 0}


def _detect_volume_divergence(close: pd.Series, volume: pd.Series) -> Dict[str, Any]:
    if len(close) < 10:
        return {"type": "NONE", "strength": 0}

    recent_moves = abs(close.iloc[-10:].pct_change()).mean()
    recent_vol = volume.iloc[-10:].mean()
    prior_vol = volume.iloc[-20:-10].mean()

    if recent_moves > 0.02 and recent_vol < prior_vol * 0.8:
        return {"type": "WEAK_MOVE", "strength": 15}

    return {"type": "NONE", "strength": 0}


def _analyze_sr_strength(high: pd.Series, low: pd.Series, close: pd.Series) -> float:
    if len(close) < 20:
        return 0

    recent = close.iloc[-40:]
    pivot_low = recent.min()
    pivot_high = recent.max()

    touches_low = sum(1 for p in recent if abs(p - pivot_low) / pivot_low < 0.005)
    touches_high = sum(1 for p in recent if abs(p - pivot_high) / pivot_high < 0.005)

    strength = min((touches_low + touches_high) * 3, 30)
    return round(strength, 1)


def _analyze_volatility_regime(close: pd.Series, ind: Dict[str, Any]) -> Dict[str, str]:
    atr_pct = ind.get("atr_pct", 2) or 2
    bb_width = ind.get("bb_width", 10) or 10

    if atr_pct < 1.0 or bb_width < 5:
        return {"regime": "LOW", "advice": "Tight range - wait for breakout"}
    elif atr_pct > 4.0 or bb_width > 20:
        return {"regime": "HIGH", "advice": "Wide swings - reduce size"}
    else:
        return {"regime": "NORMAL", "advice": "Normal volatility"}


def _identify_candle_patterns(hist: pd.DataFrame) -> Dict[str, Any]:
    if len(hist) < 3:
        return {"pattern": "NONE", "confidence": 0}

    recent = hist.iloc[-3:]

    for i in range(len(recent) - 1):
        o = float(recent.iloc[i]["Open"])
        c = float(recent.iloc[i]["Close"])
        h = float(recent.iloc[i]["High"])
        l = float(recent.iloc[i]["Low"])
        body = abs(c - o)
        wick_up = h - max(o, c)
        wick_down = min(o, c) - l
        range_ = h - l

        if wick_down > body * 2 and wick_up < body:
            return {"pattern": "HAMMER", "confidence": 60}

        if body / range_ < 0.1 if range_ > 0 else False:
            return {"pattern": "DOJI", "confidence": 50}

    o1, c1 = float(recent.iloc[-2]["Open"]), float(recent.iloc[-2]["Close"])
    o2, c2 = float(recent.iloc[-1]["Open"]), float(recent.iloc[-1]["Close"])
    h2, l2 = float(recent.iloc[-1]["High"]), float(recent.iloc[-1]["Low"])

    if (o2 < min(o1, c1) and c2 > max(o1, c1)) or \
       (c2 < min(o1, c1) and o2 > max(o1, c1)):
        return {"pattern": "ENGULFING", "confidence": 70}

    return {"pattern": "NONE", "confidence": 0}


def _confirm_momentum(ind: Dict[str, Any]) -> Dict[str, Any]:
    rsi       = ind.get("rsi14", 50) or 50
    macd_hist = ind.get("macd_hist", 0) or 0
    mom20     = ind.get("momentum_20d", 0) or 0
    di_plus   = ind.get("di_plus") or 0
    di_minus  = ind.get("di_minus") or 0
    ema_cross = ind.get("ema_cross", "") or ""
    ichi_sig  = ind.get("ichi_signal", "") or ""
    obv_trend = ind.get("obv_trend", "") or ""

    bullish_checks = [
        macd_hist > 0,
        rsi > 50,
        mom20 > 0,
        di_plus > di_minus,
        ema_cross == "BULLISH",
        ichi_sig in ("BULL", "STRONG_BULL"),
        obv_trend == "RISING",
    ]
    bearish_checks = [
        macd_hist < 0,
        rsi < 50,
        mom20 < 0,
        di_minus > di_plus,
        ema_cross == "BEARISH",
        ichi_sig in ("BEAR", "STRONG_BEAR"),
        obv_trend == "FALLING",
    ]

    bullish_signals = sum(bullish_checks)
    bearish_signals = sum(bearish_checks)
    total_factors   = len(bullish_checks)

    if bullish_signals > bearish_signals and bullish_signals >= 3:
        pct = round(bullish_signals / total_factors * 100)
        return {
            "confirmation": "BULLISH",
            "strength": bullish_signals * 8,
            "confluence_pct": pct,
            "agreeing": bullish_signals,
            "total": total_factors,
        }
    elif bearish_signals > bullish_signals and bearish_signals >= 3:
        pct = round(bearish_signals / total_factors * 100)
        return {
            "confirmation": "BEARISH",
            "strength": bearish_signals * 8,
            "confluence_pct": pct,
            "agreeing": bearish_signals,
            "total": total_factors,
        }
    else:
        return {
            "confirmation": "MIXED",
            "strength": 0,
            "confluence_pct": 0,
            "agreeing": max(bullish_signals, bearish_signals),
            "total": total_factors,
        }
