from typing import Dict, Any
from .pattern_analyzer import analyze_patterns, normalize_pattern_engine_mode
import pandas as pd


SETUP_TYPES = [
    "BREAKOUT",
    "PULLBACK",
    "TREND_CONTINUATION",
    "REVERSAL_ATTEMPT",
    "OVEREXTENDED",
    "NO_TRADE",
]


def detect_setup(ind: Dict[str, Any], hist: pd.DataFrame = None, pattern_mode: str = "new", patterns_override: Dict[str, Any] | None = None) -> Dict[str, Any]:
    engine_mode = normalize_pattern_engine_mode(pattern_mode)
    if engine_mode == "universal_v2":
        from .universal_engine import scanner_setup
        return scanner_setup(hist)
    patterns = patterns_override if isinstance(patterns_override, dict) else {}
    if patterns_override is None and hist is not None:
        try:
            patterns = analyze_patterns(hist, ind, mode=engine_mode)
        except Exception:
            patterns = {}

    results = {}
    results["BREAKOUT"]           = _score_breakout(ind, patterns)
    results["PULLBACK"]           = _score_pullback(ind, patterns)
    results["TREND_CONTINUATION"] = _score_trend_continuation(ind, patterns)
    results["REVERSAL_ATTEMPT"]   = _score_reversal(ind, patterns)
    results["OVEREXTENDED"]       = _score_overextended(ind, patterns)
    results["NO_TRADE"]           = _score_no_trade(ind, patterns)

    if engine_mode == "risky":
        results = _apply_risky_engine_adjustments(results, ind, patterns)
    elif engine_mode == "selective":
        results = _apply_selective_engine_adjustments(results, ind, patterns)
    elif engine_mode == "balanced":
        results = _apply_balanced_shortfix_engine_adjustments(results, ind, patterns)
    elif engine_mode == "official":
        results = _apply_official_v7_engine_adjustments(results, ind, patterns)
    elif engine_mode == "v8":
        results = _apply_v8_engine_adjustments(results, ind, patterns)
    elif engine_mode == "vai":
        results = _apply_vai_1_0_engine_adjustments(results, ind, patterns)
    elif engine_mode == "vai2":
        results = _apply_vai_2_0_engine_adjustments(results, ind, patterns)

    best_setup = max(results, key=lambda k: results[k]["score"])
    best       = results[best_setup]

    no_trade_threshold = {
        "v8": 58,
        "vai": 58,
        "vai2": 58,
        "official": 60,
        "selective": 62,
        "balanced": 62,
    }.get(engine_mode, 70)
    if results["NO_TRADE"]["score"] >= no_trade_threshold:
        best_setup = "NO_TRADE"
        best       = results["NO_TRADE"]

    quality_thresholds = {
        "selective": 78,
        "balanced": 80,
        "official": 88,
        "v8": 65,
        "vai": 70,
        "vai2": 72,
    }
    if engine_mode in quality_thresholds and best_setup != "NO_TRADE" and best.get("score", 0) < quality_thresholds[engine_mode]:
        labels = {
            "selective": "V5 selective",
            "balanced": "V6 balanced",
            "official": "V7 official",
            "v8": "V8 analytics evidence",
            "vai": "VAI 1.0",
            "vai2": "VAI 2.2",
        }
        best_setup = "NO_TRADE"
        best = {
            "score": max(results["NO_TRADE"].get("score", 0), 78),
            "rules": list(results["NO_TRADE"].get("rules", [])) + [f"{labels[engine_mode]} gate: best setup did not clear quality threshold"],
            "direction": "NEUTRAL",
        }

    return {
        "setup_type":   best_setup,
        "confidence":   best["score"],
        "direction":    best.get("direction", "NEUTRAL"),
        "rules_fired":  best["rules"],
        "all_scores":   {k: v["score"] for k, v in results.items()},
        "patterns":     patterns,
    }


def _score_breakout(ind: dict, patterns: dict = None) -> dict:
    patterns  = patterns or {}
    score     = 0
    rules     = []

    price      = ind.get("price", 0)
    high_52w   = ind.get("high_52w", 0)
    high_20d   = ind.get("high_20d", 0)
    vol_ratio  = ind.get("vol_ratio", 1)
    rsi        = ind.get("rsi14", 50) or 50
    bb_pct     = ind.get("bb_pct") or 50
    above_ma20 = ind.get("above_ma20", False)
    above_ma50 = ind.get("above_ma50", False)
    above_vwap = ind.get("above_vwap", False)
    adx        = ind.get("adx14") or 0
    di_plus    = ind.get("di_plus") or 0
    di_minus   = ind.get("di_minus") or 0
    obv_sig    = ind.get("obv_signal", "")
    ema_cross  = ind.get("ema_cross", "")
    ichi_sig   = ind.get("ichi_signal", "")
    williams_r = ind.get("williams_r")

    if high_52w and price >= high_52w * 0.97:
        score += 28
        rules.append("Price within 3% of 52-week high")

    if high_20d and price >= high_20d * 0.99:
        score += 18
        rules.append("Price at or above 20-day high")

    if vol_ratio >= 2.0:
        score += 22
        rules.append(f"Volume surge: {vol_ratio:.1f}x average")
    elif vol_ratio >= 1.5:
        score += 13
        rules.append(f"Elevated volume: {vol_ratio:.1f}x average")

    if 55 <= rsi <= 75:
        score += 13
        rules.append(f"RSI in breakout zone ({rsi:.0f})")
    elif rsi > 75:
        score -= 8
        rules.append(f"RSI overbought ({rsi:.0f}) — reduces quality")

    if above_ma20 and above_ma50:
        score += 8
        rules.append("Price above MA20 and MA50")

    if bb_pct and bb_pct > 85:
        score += 5
        rules.append("Price at upper Bollinger Band")

    if adx >= 25 and di_plus > di_minus:
        score += 12
        rules.append(f"ADX {adx:.0f} confirms upside breakout momentum")
    elif adx >= 15 and di_plus > di_minus:
        score += 5
        rules.append(f"ADX {adx:.0f} — moderate trend developing")

    if obv_sig == "CONFIRMING":
        score += 8
        rules.append("OBV confirming price move — institutional buying")
    elif obv_sig == "DIVERGING":
        score -= 5
        rules.append("OBV diverging — volume not backing the breakout")

    if above_vwap:
        score += 6
        rules.append("Price above 20-day VWAP — bullish bias")

    if ichi_sig in ("STRONG_BULL", "BULL"):
        score += 7
        rules.append(f"Ichimoku: {ichi_sig.replace('_', ' ')} — price above cloud")

    if ema_cross == "BULLISH":
        score += 5
        rules.append("EMA 9/21 bullish cross — short-term momentum aligned")

    if williams_r is not None and -30 <= williams_r <= -10:
        score += 5
        rules.append(f"Williams %R ({williams_r:.0f}) — strong momentum zone")

    if patterns.get("recent_pattern", {}).get("confidence", 0) >= 60:
        score += 8
        rules.append(f"Bullish pattern: {patterns['recent_pattern']['pattern']}")

    if patterns.get("momentum_confirmation", {}).get("confirmation") == "BULLISH":
        score += 7
        rules.append("Momentum confirmed (MACD, RSI, Price aligned)")

    if patterns.get("sr_strength", 0) > 20:
        score += 4
        rules.append(f"Strong S/R level ({patterns['sr_strength']:.0f} touches)")

    p_bonus, p_rules = _pattern_direction_bonus(patterns, "BULLISH")
    score += p_bonus
    rules.extend(p_rules)

    return {"score": min(score, 100), "rules": rules, "direction": "LONG"}


def _score_pullback(ind: dict, patterns: dict = None) -> dict:
    patterns   = patterns or {}
    score      = 0
    rules      = []

    price      = ind.get("price", 0)
    ma20       = ind.get("ma20") or 0
    ma50       = ind.get("ma50") or 0
    trend      = ind.get("trend", "")
    rsi        = ind.get("rsi14", 50) or 50
    vol_ratio  = ind.get("vol_ratio", 1)
    mom5       = ind.get("momentum_5d", 0) or 0
    mom20      = ind.get("momentum_20d", 0) or 0
    stoch_k    = ind.get("stoch_k") or 50
    above_ma50 = ind.get("above_ma50", False)
    above_vwap = ind.get("above_vwap", False)
    adx        = ind.get("adx14") or 0
    obv_sig    = ind.get("obv_signal", "")
    williams_r = ind.get("williams_r")
    vwap       = ind.get("vwap_20d")
    obv_trend  = ind.get("obv_trend", "")

    if "UPTREND" not in trend:
        return {"score": 5, "rules": ["Not in uptrend — pullback less applicable"], "direction": "LONG"}

    score += 20
    rules.append(f"Confirmed {trend.replace('_', ' ').title()}")

    if ma20 and abs(price - ma20) / ma20 <= 0.03:
        score += 23
        rules.append("Price testing MA20 support")
    elif ma20 and price > ma20 * 0.97:
        score += 9
        rules.append("Price above MA20 with minor pullback")

    if ma50 and abs(price - ma50) / ma50 <= 0.03:
        score += 18
        rules.append("Price testing MA50 as deeper pullback entry")

    if vwap and abs(price - vwap) / vwap <= 0.02:
        score += 8
        rules.append("Price testing VWAP — key institutional level")

    if 35 <= rsi <= 55:
        score += 18
        rules.append(f"RSI cooled to {rsi:.0f} — momentum reset")
    elif rsi < 35:
        score += 5
        rules.append(f"RSI oversold ({rsi:.0f}) — deep pullback")

    if williams_r is not None and williams_r <= -70:
        score += 8
        rules.append(f"Williams %R oversold ({williams_r:.0f}) — pullback exhaustion")

    if vol_ratio < 0.9:
        score += 10
        rules.append("Light pullback volume — sellers not in control")

    if mom5 < 0 and mom20 > 0:
        score += 9
        rules.append("Short-term dip in upward trend")

    if obv_sig == "DIVERGING" and mom5 < 0:
        score += 10
        rules.append("OBV rising while price dips — bullish accumulation signal")
    elif obv_trend == "RISING":
        score += 5
        rules.append("OBV trend rising — buyers accumulating on dip")

    if adx >= 25:
        score += 7
        rules.append(f"ADX {adx:.0f} — primary uptrend still strong")

    if stoch_k < 30:
        score += 5
        rules.append(f"Stochastic oversold ({stoch_k:.0f}) — bounce zone")

    p_bonus, p_rules = _pattern_direction_bonus(patterns, "BULLISH")
    score += p_bonus
    rules.extend(p_rules)

    return {"score": min(score, 100), "rules": rules, "direction": "LONG"}


def _score_trend_continuation(ind: dict, patterns: dict = None) -> dict:
    patterns    = patterns or {}
    score       = 0
    rules       = []

    trend       = ind.get("trend", "")
    strength    = ind.get("trend_strength", 0)
    macd_cross  = ind.get("macd_cross", "")
    bb_width    = ind.get("bb_width") or 10
    rsi         = ind.get("rsi14", 50) or 50
    above_ma20  = ind.get("above_ma20", False)
    above_ma50  = ind.get("above_ma50", False)
    above_ma200 = ind.get("above_ma200", False)
    adx         = ind.get("adx14") or 0
    di_plus     = ind.get("di_plus") or 0
    di_minus    = ind.get("di_minus") or 0
    ichi_sig    = ind.get("ichi_signal", "")
    ema_cross   = ind.get("ema_cross", "")
    obv_trend   = ind.get("obv_trend", "")
    vol_diverg  = ind.get("volume_price_divergence", "")

    direction = "LONG" if "UP" in trend else "SHORT" if "DOWN" in trend else "NEUTRAL"

    if "STRONG" in trend:
        score += 28
        rules.append(f"Strong confirmed trend: {trend.replace('_', ' ')}")
    elif trend in ("UPTREND", "DOWNTREND"):
        score += 14
        rules.append(f"Established trend: {trend}")
    else:
        return {"score": 10, "rules": ["No clear trend — continuation less likely"], "direction": direction}

    if adx >= 40 and ((direction == "LONG" and di_plus > di_minus) or (direction == "SHORT" and di_minus > di_plus)):
        score += 20
        rules.append(f"ADX {adx:.0f} — very strong trend momentum")
    elif adx >= 25:
        score += 12
        rules.append(f"ADX {adx:.0f} — confirmed trending conditions")
    elif adx < 15:
        score -= 8
        rules.append(f"ADX {adx:.0f} — very weak trend (ranging market)")

    if strength >= 70:
        score += 16
        rules.append(f"High trend linearity (R²={strength:.0f}%)")
    elif strength >= 40:
        score += 8
        rules.append(f"Moderate trend strength (R²={strength:.0f}%)")

    if above_ma20 and above_ma50 and above_ma200:
        score += 13
        rules.append("All MAs aligned bullishly")
    elif not above_ma20 and not above_ma50 and not above_ma200:
        score += 13
        rules.append("All MAs aligned bearishly")

    if direction == "LONG" and ichi_sig in ("STRONG_BULL", "BULL"):
        score += 8
        rules.append(f"Ichimoku {ichi_sig.replace('_', ' ')} — cloud confirms uptrend")
    elif direction == "SHORT" and ichi_sig in ("STRONG_BEAR", "BEAR"):
        score += 8
        rules.append(f"Ichimoku {ichi_sig.replace('_', ' ')} — cloud confirms downtrend")

    if "BULL" in macd_cross and "UP" in trend:
        score += 12
        rules.append("MACD histogram positive — momentum confirming")
    elif "BEAR" in macd_cross and "DOWN" in trend:
        score += 12
        rules.append("MACD histogram negative — confirms downtrend")

    if direction == "LONG" and ema_cross == "BULLISH":
        score += 6
        rules.append("EMA 9/21 bullish — short-term momentum aligned")
    elif direction == "SHORT" and ema_cross == "BEARISH":
        score += 6
        rules.append("EMA 9/21 bearish — short-term momentum aligned")

    if obv_trend == "RISING" and direction == "LONG":
        score += 6
        rules.append("OBV rising — volume supports the uptrend")
    elif obv_trend == "FALLING" and direction == "SHORT":
        score += 6
        rules.append("OBV falling — volume supports the downtrend")

    if direction == "LONG" and vol_diverg == "BEARISH_DIVERGENCE":
        score -= 7
        rules.append("Warning: price rising on declining volume — distribution risk")

    if "UP" in trend and 50 <= rsi <= 70:
        score += 8
        rules.append(f"RSI in bullish trend range ({rsi:.0f})")
    elif "DOWN" in trend and 30 <= rsi <= 50:
        score += 8
        rules.append(f"RSI in bearish trend range ({rsi:.0f})")

    if bb_width and bb_width < 8:
        score += 8
        rules.append("Tight Bollinger squeeze — breakout potential")

    desired = "BULLISH" if direction == "LONG" else "BEARISH" if direction == "SHORT" else "NEUTRAL"
    p_bonus, p_rules = _pattern_direction_bonus(patterns, desired)
    score += p_bonus
    rules.extend(p_rules)

    return {"score": min(score, 100), "rules": rules, "direction": direction}


def _score_reversal(ind: dict, patterns: dict = None) -> dict:
    patterns   = patterns or {}
    score      = 0
    rules      = []

    rsi        = ind.get("rsi14", 50) or 50
    stoch_k    = ind.get("stoch_k") or 50
    trend      = ind.get("trend", "")
    bb_pct     = ind.get("bb_pct") or 50
    vol_ratio  = ind.get("vol_ratio", 1)
    macd_cross = ind.get("macd_cross", "")
    mom5       = ind.get("momentum_5d", 0) or 0
    williams_r = ind.get("williams_r")
    adx        = ind.get("adx14") or 0
    obv_sig    = ind.get("obv_signal", "")
    ichi_sig   = ind.get("ichi_signal", "")
    vol_diverg = ind.get("volume_price_divergence", "")

    direction = "LONG" if "DOWN" in trend else "SHORT" if "UP" in trend else "NEUTRAL"

    if rsi <= 25:
        score += 24
        rules.append(f"RSI deeply oversold ({rsi:.0f}) — exhaustion signal")
    elif rsi >= 78:
        score += 20
        rules.append(f"RSI severely overbought ({rsi:.0f}) — exhaustion signal")
    else:
        return {"score": 5, "rules": ["No extreme RSI — reversal conditions weak"], "direction": direction}

    if williams_r is not None:
        if williams_r <= -85 and direction == "LONG":
            score += 12
            rules.append(f"Williams %R deeply oversold ({williams_r:.0f}) — reversal zone")
        elif williams_r >= -15 and direction == "SHORT":
            score += 12
            rules.append(f"Williams %R deeply overbought ({williams_r:.0f}) — reversal zone")

    if stoch_k < 20:
        score += 13
        rules.append(f"Stochastic oversold ({stoch_k:.0f})")
    elif stoch_k > 80:
        score += 13
        rules.append(f"Stochastic overbought ({stoch_k:.0f})")

    if bb_pct is not None and bb_pct <= 5:
        score += 13
        rules.append("Price at lower Bollinger Band extreme")
    elif bb_pct is not None and bb_pct >= 95:
        score += 13
        rules.append("Price at upper Bollinger Band extreme")

    if "BULLISH" in macd_cross and "DOWN" in trend:
        score += 18
        rules.append("Bullish MACD cross in downtrend — early reversal signal")
    elif "BEARISH" in macd_cross and "UP" in trend:
        score += 18
        rules.append("Bearish MACD cross in uptrend — early reversal signal")

    if direction == "LONG" and vol_diverg == "BULLISH_DIVERGENCE":
        score += 12
        rules.append("Bullish volume-price divergence — smart money accumulating")
    elif direction == "SHORT" and vol_diverg == "BEARISH_DIVERGENCE":
        score += 12
        rules.append("Bearish volume-price divergence — distribution at top")

    if vol_ratio >= 2.0:
        score += 9
        rules.append(f"High volume at extreme ({vol_ratio:.1f}x) — capitulation / buying climax")

    if adx < 18:
        score += 7
        rules.append(f"ADX {adx:.0f} — trend losing momentum, reversal more likely")

    if direction == "LONG" and ichi_sig in ("BEAR", "NEUTRAL"):
        score += 5
        rules.append("Ichimoku neutral/bear — potential floor for reversal")

    rsi_div = patterns.get("rsi_divergence", {}).get("type", "NONE")
    if rsi_div != "NONE":
        div_strength = patterns.get("rsi_divergence", {}).get("strength", 0)
        score += div_strength
        rules.append(f"RSI Divergence detected: {rsi_div} (+{div_strength:.0f})")

    macd_div = patterns.get("macd_divergence", {}).get("type", "NONE")
    if macd_div != "NONE":
        score += 14
        rules.append(f"MACD Divergence: {macd_div}")

    desired = "BULLISH" if direction == "LONG" else "BEARISH" if direction == "SHORT" else "NEUTRAL"
    p_bonus, p_rules = _pattern_direction_bonus(patterns, desired)
    score += p_bonus
    rules.extend(p_rules)

    return {"score": min(score, 82), "rules": rules, "direction": direction}


def _score_overextended(ind: dict, patterns: dict = None) -> dict:
    patterns  = patterns or {}
    score     = 0
    rules     = []

    rsi       = ind.get("rsi14", 50) or 50
    bb_pct    = ind.get("bb_pct") or 50
    mom5      = ind.get("momentum_5d", 0) or 0
    vol_ratio = ind.get("vol_ratio", 1)
    pct_ma50  = ind.get("pct_from_ma50") or 0
    atr_pct   = ind.get("atr_pct") or 1
    williams_r= ind.get("williams_r")
    adx       = ind.get("adx14") or 0
    vol_diverg= ind.get("volume_price_divergence", "")

    direction = "SHORT" if rsi > 70 else "LONG"

    if rsi >= 80:
        score += 33
        rules.append(f"RSI extremely overbought ({rsi:.0f})")
    elif rsi <= 20:
        score += 33
        rules.append(f"RSI extremely oversold ({rsi:.0f})")

    if williams_r is not None and williams_r >= -5:
        score += 10
        rules.append(f"Williams %R extremely overbought ({williams_r:.0f})")
    elif williams_r is not None and williams_r <= -95:
        score += 10
        rules.append(f"Williams %R extremely oversold ({williams_r:.0f})")

    if bb_pct is not None and (bb_pct >= 98 or bb_pct <= 2):
        score += 22
        rules.append(f"Price beyond Bollinger Band (BB%={bb_pct:.0f})")

    if abs(mom5) >= 10:
        score += 18
        rules.append(f"5-day move of {mom5:+.1f}% — extended")
    elif abs(mom5) >= 6:
        score += 9
        rules.append(f"5-day move of {mom5:+.1f}% — getting stretched")

    if pct_ma50 and abs(pct_ma50) >= 20:
        score += 14
        rules.append(f"Price {pct_ma50:+.1f}% from MA50 — mean-reversion risk")
    elif pct_ma50 and abs(pct_ma50) >= 12:
        score += 7
        rules.append(f"Price {pct_ma50:+.1f}% from MA50")

    if direction == "SHORT" and vol_diverg == "BEARISH_DIVERGENCE":
        score += 8
        rules.append("Price rising on falling volume — classic distribution top")

    return {"score": min(score, 100), "rules": rules, "direction": direction}


def _score_no_trade(ind: dict, patterns: dict = None) -> dict:
    patterns  = patterns or {}
    score     = 0
    rules     = []

    trend     = ind.get("trend", "")
    strength  = ind.get("trend_strength", 0)
    vol_ratio = ind.get("vol_ratio", 1)
    bb_width  = ind.get("bb_width") or 10
    rsi       = ind.get("rsi14", 50) or 50
    vol_trend = ind.get("vol_trend", "")
    adx       = ind.get("adx14") or 0
    ichi_sig  = ind.get("ichi_signal", "")

    if trend == "SIDEWAYS":
        score += 28
        rules.append("No clear trend — choppy / sideways action")

    if adx < 12:
        score += 18
        rules.append(f"ADX {adx:.0f} — extremely low momentum, ranging market")
    elif adx < 18:
        score += 8
        rules.append(f"ADX {adx:.0f} — weak trend, proceed cautiously")

    if strength < 20:
        score += 18
        rules.append(f"Very low trend linearity (R²={strength:.0f}%)")

    if vol_ratio < 0.5:
        score += 14
        rules.append(f"Very low volume ({vol_ratio:.2f}x avg) — low conviction")

    if 45 <= rsi <= 55:
        score += 13
        rules.append(f"RSI neutral zone ({rsi:.0f}) — no edge signal")

    if bb_width and bb_width < 3:
        score += 13
        rules.append("Extremely tight BB — potential volatility squeeze, wait for break")

    if vol_trend == "DECLINING":
        score += 9
        rules.append("Declining volume trend — participation drying up")

    if ichi_sig == "NEUTRAL":
        score += 7
        rules.append("Price inside Ichimoku cloud — indecisive zone")

    return {"score": min(score, 100), "rules": rules, "direction": "NEUTRAL"}


def _pattern_direction_bonus(patterns: dict, desired_direction: str) -> tuple[int, list[str]]:
    if not patterns or desired_direction == "NEUTRAL":
        return 0, []

    recent = patterns.get("detected_patterns") or []
    if not recent:
        advanced = patterns.get("advanced_patterns") or {}
        recent = advanced.get("recent") or []

    agreeing = [
        p for p in recent
        if p.get("direction") == desired_direction and (p.get("confidence") or 0) >= 55
    ]
    if not agreeing:
        return 0, []

    top  = max(agreeing, key=lambda p: p.get("confidence") or 0)
    name = str(top.get("pattern_name", "PATTERN")).replace("_", " ")
    conf = float(top.get("confidence") or 0)
    fam  = top.get("pattern_family", "PATTERN")

    bonus = 0
    if conf >= 80:  bonus = 13
    elif conf >= 70: bonus = 9
    elif conf >= 60: bonus = 6
    else:           bonus = 3

    if fam in ("FVG", "STRUCTURE"):  bonus += 3
    elif fam == "CHART":             bonus += 2

    return min(bonus, 16), [f"Advanced pattern confirmation: {name} ({conf:.0f}%)"]


def _apply_risky_engine_adjustments(results: dict, ind: dict, patterns: dict) -> dict:
    adjusted = {k: dict(v) for k, v in (results or {}).items()}
    for v in adjusted.values():
        v["rules"] = list(v.get("rules") or [])

    adv = (patterns or {}).get("advanced_patterns") or {}
    top = adv.get("top_pattern") or {}
    top_dir = str(top.get("direction") or "NEUTRAL").upper()
    top_conf = float(top.get("confidence") or 0)
    rsi = float(ind.get("rsi14") or 50)
    mom5 = float(ind.get("momentum_5d") or 0)
    mom20 = float(ind.get("momentum_20d") or 0)
    vol_ratio = float(ind.get("vol_ratio") or 1)
    above_ma20 = bool(ind.get("above_ma20"))
    above_ma50 = bool(ind.get("above_ma50"))

    if "NO_TRADE" in adjusted:
        adjusted["NO_TRADE"]["score"] = max(0, adjusted["NO_TRADE"].get("score", 0) - 28)
        adjusted["NO_TRADE"]["rules"].append("V4 risky engine: lowered no-trade threshold for high-coverage testing")

    if top_dir == "BULLISH" and top_conf >= 55:
        for key in ("BREAKOUT", "PULLBACK", "TREND_CONTINUATION", "REVERSAL_ATTEMPT"):
            if key in adjusted:
                adjusted[key]["score"] = min(100, adjusted[key].get("score", 0) + 10)
                adjusted[key]["rules"].append(f"V4 risky bullish pattern boost ({top_conf:.0f}%)")
    elif top_dir == "BEARISH" and top_conf >= 55:
        for key in ("OVEREXTENDED", "REVERSAL_ATTEMPT"):
            if key in adjusted:
                adjusted[key]["score"] = min(100, adjusted[key].get("score", 0) + 12)
                adjusted[key]["direction"] = "SHORT"
                adjusted[key]["rules"].append(f"V4 risky bearish pattern boost ({top_conf:.0f}%)")

    if above_ma20 and above_ma50 and 52 <= rsi <= 82 and mom20 > 1.5:
        for key in ("BREAKOUT", "TREND_CONTINUATION"):
            if key in adjusted:
                adjusted[key]["score"] = min(100, adjusted[key].get("score", 0) + 9)
                adjusted[key]["rules"].append("V4 risky momentum-chase boost")

    if rsi <= 33 and mom5 < -2.0:
        if "REVERSAL_ATTEMPT" in adjusted:
            adjusted["REVERSAL_ATTEMPT"]["score"] = min(100, adjusted["REVERSAL_ATTEMPT"].get("score", 0) + 10)
            adjusted["REVERSAL_ATTEMPT"]["direction"] = "LONG"
            adjusted["REVERSAL_ATTEMPT"]["rules"].append("V4 risky oversold-bounce boost")
    elif rsi >= 67 and mom5 > 2.0:
        if "OVEREXTENDED" in adjusted:
            adjusted["OVEREXTENDED"]["score"] = min(100, adjusted["OVEREXTENDED"].get("score", 0) + 10)
            adjusted["OVEREXTENDED"]["direction"] = "SHORT"
            adjusted["OVEREXTENDED"]["rules"].append("V4 risky overbought-fade boost")

    if vol_ratio >= 1.5:
        for key in ("BREAKOUT", "REVERSAL_ATTEMPT", "TREND_CONTINUATION"):
            if key in adjusted:
                adjusted[key]["score"] = min(100, adjusted[key].get("score", 0) + 4)
                adjusted[key]["rules"].append(f"V4 risky volume participation boost ({vol_ratio:.1f}x)")

    return adjusted


def _apply_selective_engine_adjustments(results: dict, ind: dict, patterns: dict) -> dict:
    adjusted = {k: dict(v) for k, v in (results or {}).items()}
    for v in adjusted.values():
        v["rules"] = list(v.get("rules") or [])

    adv = (patterns or {}).get("advanced_patterns") or {}
    top = adv.get("top_pattern") or {}
    top_dir = str(top.get("direction") or "NEUTRAL").upper()
    top_conf = float(top.get("confidence") or 0)
    top_family = str(top.get("pattern_family") or "").upper()

    trend = str(ind.get("trend") or "").upper()
    rsi = float(ind.get("rsi14") or 50)
    adx = float(ind.get("adx14") or 0)
    di_plus = float(ind.get("di_plus") or 0)
    di_minus = float(ind.get("di_minus") or 0)
    vol_ratio = float(ind.get("vol_ratio") or 1)
    mom5 = float(ind.get("momentum_5d") or 0)
    mom20 = float(ind.get("momentum_20d") or 0)
    above_ma20 = bool(ind.get("above_ma20"))
    above_ma50 = bool(ind.get("above_ma50"))
    above_ma200 = bool(ind.get("above_ma200"))
    bb_width = float(ind.get("bb_width") or 10)
    vol_diverg = str(ind.get("volume_price_divergence") or "").upper()

    no_trade_add = 0
    if trend == "SIDEWAYS":
        no_trade_add += 18
    if adx < 14:
        no_trade_add += 18
    elif adx < 18:
        no_trade_add += 8
    if vol_ratio < 0.70:
        no_trade_add += 16
    elif vol_ratio < 0.90:
        no_trade_add += 7
    if 46 <= rsi <= 54:
        no_trade_add += 8
    if bb_width < 3.5:
        no_trade_add += 12
    if not top or top_conf < 72:
        no_trade_add += 16

    if "NO_TRADE" in adjusted:
        adjusted["NO_TRADE"]["score"] = min(100, adjusted["NO_TRADE"].get("score", 0) + no_trade_add)
        adjusted["NO_TRADE"]["rules"].append(f"V5 selective no-trade filter added {no_trade_add} points")

    for key, item in adjusted.items():
        if key == "NO_TRADE":
            continue
        direction = str(item.get("direction") or "NEUTRAL").upper()
        score = float(item.get("score") or 0)
        rules = item.get("rules") or []

        if direction == "LONG":
            long_ok = 0
            if above_ma50:
                score += 5; long_ok += 1
            else:
                score -= 11; rules.append("V5 penalty: long not above MA50")
            if above_ma200:
                score += 6; long_ok += 1
            elif key in {"BREAKOUT", "TREND_CONTINUATION"}:
                score -= 9; rules.append("V5 penalty: long continuation below MA200")
            if mom20 > 0:
                score += 5; long_ok += 1
            else:
                score -= 9; rules.append("V5 penalty: long has weak 20-day momentum")
            if 38 <= rsi <= 68:
                score += 4; long_ok += 1
            else:
                score -= 6; rules.append("V5 penalty: RSI not in preferred long range")
            if adx >= 18 and di_plus >= di_minus:
                score += 6; long_ok += 1
            elif adx < 18:
                score -= 8; rules.append("V5 penalty: weak ADX for long")
            if top_dir == "BULLISH" and top_conf >= 76:
                score += 10; long_ok += 1; rules.append(f"V5 bullish pattern confirmation ({top_conf:.0f}%)")
                if top_family in {"FVG", "STRUCTURE", "CHART"}:
                    score += 5
            elif top_dir == "BEARISH" and top_conf >= 68:
                score -= 16; rules.append("V5 veto: bearish top pattern conflicts with long")
            if vol_diverg == "BEARISH_DIVERGENCE":
                score -= 12; rules.append("V5 veto: bearish volume divergence")
            if long_ok < 4:
                score -= 10; rules.append("V5 penalty: not enough long confluence")

        elif direction == "SHORT":
            short_ok = 0
            if not above_ma50:
                score += 5; short_ok += 1
            else:
                score -= 11; rules.append("V5 penalty: short still above MA50")
            if not above_ma200:
                score += 6; short_ok += 1
            elif key in {"TREND_CONTINUATION", "OVEREXTENDED"}:
                score -= 9; rules.append("V5 penalty: short setup above MA200")
            if mom20 < 0:
                score += 5; short_ok += 1
            else:
                score -= 9; rules.append("V5 penalty: short has positive 20-day momentum")
            if 32 <= rsi <= 62:
                score += 4; short_ok += 1
            else:
                score -= 6; rules.append("V5 penalty: RSI not in preferred short range")
            if adx >= 18 and di_minus >= di_plus:
                score += 6; short_ok += 1
            elif adx < 18:
                score -= 8; rules.append("V5 penalty: weak ADX for short")
            if top_dir == "BEARISH" and top_conf >= 76:
                score += 10; short_ok += 1; rules.append(f"V5 bearish pattern confirmation ({top_conf:.0f}%)")
                if top_family in {"FVG", "STRUCTURE", "CHART"}:
                    score += 5
            elif top_dir == "BULLISH" and top_conf >= 68:
                score -= 16; rules.append("V5 veto: bullish top pattern conflicts with short")
            if vol_diverg == "BULLISH_DIVERGENCE":
                score -= 12; rules.append("V5 veto: bullish volume divergence")
            if short_ok < 4:
                score -= 10; rules.append("V5 penalty: not enough short confluence")

        else:
            score -= 18

        if vol_ratio >= 1.25:
            score += 5; rules.append(f"V5 volume confirmation ({vol_ratio:.1f}x)")
        elif vol_ratio < 0.80:
            score -= 9; rules.append("V5 penalty: low participation")

        if key != "OVEREXTENDED" and abs(mom5) > 9:
            score -= 7; rules.append("V5 penalty: 5-day move already stretched")

        item["score"] = max(0, min(100, round(score, 1)))
        item["rules"] = rules

    directional = [(k, v) for k, v in adjusted.items() if k != "NO_TRADE"]
    best_key, best_val = max(directional, key=lambda kv: kv[1].get("score", 0)) if directional else ("", {"score": 0})
    if best_val.get("score", 0) < 78:
        adjusted["NO_TRADE"]["score"] = max(adjusted["NO_TRADE"].get("score", 0), 76)
        adjusted["NO_TRADE"]["rules"].append(f"V5 hard gate: best directional setup {best_key} scored below 78")

    return adjusted


def _apply_balanced_shortfix_engine_adjustments(results: dict, ind: dict, patterns: dict) -> dict:
    adjusted = _apply_selective_engine_adjustments(results, ind, patterns)

    adv = (patterns or {}).get("advanced_patterns") or {}
    top = adv.get("top_pattern") or {}
    top_dir = str(top.get("direction") or "NEUTRAL").upper()
    top_name = str(top.get("pattern_name") or "").upper()
    top_conf = float(top.get("confidence") or 0)
    top_family = str(top.get("pattern_family") or "").upper()

    trend = str(ind.get("trend") or "").upper()
    rsi = float(ind.get("rsi14") or 50)
    adx = float(ind.get("adx14") or 0)
    di_plus = float(ind.get("di_plus") or 0)
    di_minus = float(ind.get("di_minus") or 0)
    vol_ratio = float(ind.get("vol_ratio") or 1)
    mom5 = float(ind.get("momentum_5d") or 0)
    mom20 = float(ind.get("momentum_20d") or 0)
    above_ma50 = bool(ind.get("above_ma50"))
    above_ma200 = bool(ind.get("above_ma200"))
    vol_diverg = str(ind.get("volume_price_divergence") or "").upper()

    bearish_context = 0
    if not above_ma50:
        bearish_context += 1
    if not above_ma200:
        bearish_context += 1
    if mom20 < -1.0:
        bearish_context += 1
    if adx >= 20 and di_minus > di_plus:
        bearish_context += 1
    if rsi < 50:
        bearish_context += 1
    if top_dir == "BEARISH" and top_conf >= 82:
        bearish_context += 1
    if vol_diverg == "BEARISH_DIVERGENCE":
        bearish_context += 1

    bullish_context = (
        (above_ma50 and above_ma200)
        or mom20 > 1.0
        or (adx >= 18 and di_plus > di_minus and above_ma50)
        or trend in {"UP", "UPTREND", "BULL", "BULLISH", "STRONG_UP"}
    )

    short_allowed = bearish_context >= 5 and not bullish_context
    short_soft_allowed = bearish_context >= 4 and not (above_ma50 and above_ma200)

    for key, item in adjusted.items():
        if key == "NO_TRADE":
            continue
        direction = str(item.get("direction") or "NEUTRAL").upper()
        score = float(item.get("score") or 0)
        rules = list(item.get("rules") or [])

        if direction == "SHORT":
            if not short_allowed:
                penalty = 38 if bullish_context else 24
                score -= penalty
                rules.append(f"V6 short-fix penalty: short lacks strong bearish context ({bearish_context}/7 points)")
            elif short_soft_allowed:
                score += 7
                rules.append(f"V6 short allowed: bearish context confirmed ({bearish_context}/7 points)")

            if top_dir != "BEARISH" or top_conf < 82:
                score -= 14
                rules.append("V6 short-fix: no high-confidence bearish top pattern")
            if "BEARISH_FAIR_VALUE_GAP" in top_name:
                if bearish_context < 6:
                    score -= 16
                    rules.append("V6 short-fix: bearish FVG needs stronger bearish backdrop")
            if top_family in {"CANDLE", "CANDLESTICK", "LEGACY_CANDLE"}:
                score -= 8
                rules.append("V6 short-fix: candle-only short reduced")
            if vol_ratio < 0.90:
                score -= 8
                rules.append("V6 short-fix: low-volume short reduced")
            if rsi < 24:
                score -= 10
                rules.append("V6 short-fix: avoids chasing oversold shorts")
            if mom5 < -9:
                score -= 8
                rules.append("V6 short-fix: avoids shorting after stretched 5-day drop")

        elif direction == "LONG":
            if above_ma50 and mom20 > 0 and top_dir == "BULLISH" and top_conf >= 74:
                score += 5
                rules.append("V6 long quality boost")
            if top_dir == "BEARISH" and top_conf >= 82:
                score -= 8
                rules.append("V6 long caution: strong bearish top pattern")
            if rsi > 80 and mom5 > 8:
                score -= 10
                rules.append("V6 long caution: stretched overbought move")

        item["score"] = max(0, min(100, round(score, 1)))
        item["rules"] = rules

    directional = [(k, v) for k, v in adjusted.items() if k != "NO_TRADE"]
    best_key, best_val = max(directional, key=lambda kv: kv[1].get("score", 0)) if directional else ("", {"score": 0, "direction": "NEUTRAL"})
    if str(best_val.get("direction") or "").upper() == "SHORT" and best_val.get("score", 0) < 88:
        adjusted["NO_TRADE"]["score"] = max(adjusted["NO_TRADE"].get("score", 0), 82)
        adjusted["NO_TRADE"]["rules"].append("V6 short-fix hard gate: short setup below 88 converted to no-trade")
    elif best_val.get("score", 0) < 80:
        adjusted["NO_TRADE"]["score"] = max(adjusted["NO_TRADE"].get("score", 0), 78)
        adjusted["NO_TRADE"]["rules"].append("V6 balanced hard gate: best setup below 80 converted to no-trade")

    return adjusted


def _apply_official_v7_engine_adjustments(results: dict, ind: dict, patterns: dict) -> dict:
    adjusted = _apply_balanced_shortfix_engine_adjustments(results, ind, patterns)

    adv = (patterns or {}).get("advanced_patterns") or {}
    top = adv.get("top_pattern") or {}
    top_dir = str(top.get("direction") or "NEUTRAL").upper()
    top_name = str(top.get("pattern_name") or "").upper()
    top_conf = float(top.get("confidence") or 0)
    top_family = str(top.get("pattern_family") or "").upper()

    rsi = float(ind.get("rsi14") or 50)
    adx = float(ind.get("adx14") or 0)
    di_plus = float(ind.get("di_plus") or 0)
    di_minus = float(ind.get("di_minus") or 0)
    vol_ratio = float(ind.get("vol_ratio") or 1)
    mom5 = float(ind.get("momentum_5d") or 0)
    mom20 = float(ind.get("momentum_20d") or 0)
    mom60 = float(ind.get("momentum_60d") or 0)
    above_ma20 = bool(ind.get("above_ma20"))
    above_ma50 = bool(ind.get("above_ma50"))
    above_ma200 = bool(ind.get("above_ma200"))
    atr_pct = float(ind.get("atr_pct") or 0)
    bb_width = float(ind.get("bb_width") or 0)
    vol_diverg = str(ind.get("volume_price_divergence") or "").upper()

    momentum_points = 0
    if above_ma20: momentum_points += 1
    if above_ma50: momentum_points += 1
    if above_ma200: momentum_points += 1
    if mom20 > 1.0: momentum_points += 1
    if mom60 > 0: momentum_points += 1
    if adx >= 18 and di_plus >= di_minus: momentum_points += 1
    if 42 <= rsi <= 70: momentum_points += 1
    if vol_ratio >= 0.65: momentum_points += 1
    if top_dir == "BULLISH" and top_conf >= 82: momentum_points += 1

    bullish_pattern_group = (
        "BULLISH_FAIR_VALUE_GAP", "INVERSION_FVG_BULLISH", "BOS_BULLISH",
        "CUP_AND_HANDLE", "CUP_FORMATION", "DOUBLE_BOTTOM", "BULL_FLAG",
        "BULLISH_ENGULFING", "THREE_WHITE_SOLDIERS", "MORNING", "BREAKOUT", "RETEST"
    )

    for key, item in adjusted.items():
        if key == "NO_TRADE":
            continue
        direction = str(item.get("direction") or "NEUTRAL").upper()
        score = float(item.get("score") or 0)
        rules = list(item.get("rules") or [])

        if direction == "SHORT":
            item["score"] = 0
            rules.append("V7 release policy: bearish/short trade converted to no-trade.")
            item["rules"] = rules
            continue

        if direction != "LONG":
            item["score"] = max(0, min(100, score - 25))
            item["rules"] = rules + ["V7 release policy: non-long setup reduced."]
            continue

        if momentum_points >= 8:
            score += 24; rules.append(f"V7 A+ momentum stack ({momentum_points}/9 confirmations)")
        elif momentum_points >= 7:
            score += 18; rules.append(f"V7 strong momentum stack ({momentum_points}/9 confirmations)")
        elif momentum_points >= 6:
            score += 8; rules.append(f"V7 acceptable momentum stack ({momentum_points}/9 confirmations)")
        else:
            score -= 30; rules.append(f"V7 no-trade pressure: insufficient bullish momentum stack ({momentum_points}/9)")

        if top_dir == "BULLISH" and top_conf >= 82:
            score += 12; rules.append(f"V7 bullish top pattern confirmation: {top_name or 'BULLISH'}")
            if any(x in top_name for x in bullish_pattern_group):
                score += 10; rules.append("V7 lab-favored bullish pattern boost")
        elif top_dir == "BEARISH" and top_conf >= 70:
            score -= 24; rules.append("V7 veto: bearish top pattern conflicts with release long policy")
        else:
            score -= 8; rules.append("V7 requires clearer bullish pattern confirmation")

        if atr_pct > 7.5:
            score -= 24; rules.append("V7 risk gate: ATR% too high")
        elif atr_pct > 5.5:
            score -= 12; rules.append("V7 risk caution: elevated ATR%")
        if abs(mom5) > 11:
            score -= 10; rules.append("V7 risk caution: 5-day move already stretched")
        if rsi > 78:
            score -= 12; rules.append("V7 avoids overbought chase risk")
        if bb_width > 18:
            score -= 8; rules.append("V7 risk caution: wide Bollinger range")
        if vol_diverg == "BEARISH_DIVERGENCE":
            score -= 18; rules.append("V7 veto pressure: bearish volume divergence")
        if not above_ma50 or mom20 <= 0:
            score -= 22; rules.append("V7 hard preference: above MA50 with positive 20-day momentum")

        item["score"] = max(0, min(100, round(score, 1)))
        item["rules"] = rules

    directional = [(k, v) for k, v in adjusted.items() if k != "NO_TRADE"]
    best_key, best_val = max(directional, key=lambda kv: kv[1].get("score", 0)) if directional else ("", {"score": 0, "direction": "NEUTRAL"})
    best_direction = str(best_val.get("direction") or "NEUTRAL").upper()
    if best_direction != "LONG" or best_val.get("score", 0) < 88 or momentum_points < 6:
        adjusted["NO_TRADE"]["score"] = max(adjusted["NO_TRADE"].get("score", 0), 92)
        adjusted["NO_TRADE"].setdefault("rules", []).append("V7 release gate: no A-grade bullish momentum long found")
    else:
        adjusted["NO_TRADE"]["score"] = min(adjusted["NO_TRADE"].get("score", 0), 45)
        adjusted["NO_TRADE"].setdefault("rules", []).append("V7 release gate passed for bullish momentum long")

    return adjusted


def _apply_v7_symmetric_candidate_foundation(results: dict, ind: dict, patterns: dict) -> dict:
    adjusted = {
        key: {**value, "rules": list(value.get("rules") or [])}
        for key, value in results.items()
    }
    advanced = (patterns or {}).get("advanced_patterns") or {}
    top = advanced.get("top_pattern") or {}
    top_side = str(top.get("direction") or "NEUTRAL").upper()
    top_confidence = float(top.get("confidence") or 0)

    rsi = float(ind.get("rsi14") or 50)
    adx = float(ind.get("adx14") or 0)
    di_plus = float(ind.get("di_plus") or 0)
    di_minus = float(ind.get("di_minus") or 0)
    rvol = float(ind.get("rvol_20d") or ind.get("vol_ratio") or 1)
    momentum_5d = float(ind.get("momentum_5d") or 0)
    momentum_20d = float(ind.get("momentum_20d") or 0)
    momentum_60d = float(ind.get("momentum_60d") or 0)
    above_ma20 = bool(ind.get("above_ma20"))
    above_ma50 = bool(ind.get("above_ma50"))
    above_ma200 = bool(ind.get("above_ma200"))
    atr_pct = float(ind.get("atr_pct") or 0)
    bb_width = float(ind.get("bb_width") or 0)
    volume_divergence = str(ind.get("volume_price_divergence") or "").upper()

    for key, item in adjusted.items():
        if key == "NO_TRADE":
            continue
        direction = str(item.get("direction") or "NEUTRAL").upper()
        if direction not in {"LONG", "SHORT"}:
            item["score"] = max(0.0, min(100.0, float(item.get("score") or 0) - 20.0))
            item["rules"].append("V8 foundation: candidate has no directional side.")
            continue

        is_long = direction == "LONG"
        aligned_top = "BULLISH" if is_long else "BEARISH"
        opposing_top = "BEARISH" if is_long else "BULLISH"
        momentum_sign = 1.0 if is_long else -1.0
        confirmations = 0
        confirmations += int(above_ma20 == is_long)
        confirmations += int(above_ma50 == is_long)
        confirmations += int(above_ma200 == is_long)
        confirmations += int(momentum_sign * momentum_20d > 1.0)
        confirmations += int(momentum_sign * momentum_60d > 0.0)
        confirmations += int(adx >= 18 and ((di_plus >= di_minus) if is_long else (di_minus >= di_plus)))
        confirmations += int((42 <= rsi <= 70) if is_long else (30 <= rsi <= 58))
        confirmations += int(rvol >= 0.65)
        confirmations += int(top_side == aligned_top and top_confidence >= 82)

        score = float(item.get("score") or 0)
        rules = item["rules"]
        if confirmations >= 8:
            score += 24
        elif confirmations >= 7:
            score += 18
        elif confirmations >= 6:
            score += 8
        else:
            score -= 30
        rules.append(f"V8 V7-foundation: {confirmations}/9 directionally mirrored momentum confirmations.")

        if top_side == aligned_top and top_confidence >= 82:
            score += 12
            rules.append("V8 V7-foundation: high-confidence pattern agrees with direction.")
        elif top_side == opposing_top and top_confidence >= 70:
            score -= 24
            rules.append("V8 V7-foundation: high-confidence pattern conflicts with direction.")
        else:
            score -= 8
            rules.append("V8 V7-foundation: pattern confirmation is unclear.")


        if atr_pct > 7.5:
            score -= 24
        elif atr_pct > 5.5:
            score -= 12
        if abs(momentum_5d) > 11:
            score -= 10
        if (is_long and rsi > 78) or ((not is_long) and rsi < 22):
            score -= 12
        if bb_width > 18:
            score -= 8
        opposing_divergence = "BEARISH_DIVERGENCE" if is_long else "BULLISH_DIVERGENCE"
        if volume_divergence == opposing_divergence:
            score -= 18
        if (above_ma50 != is_long) or momentum_sign * momentum_20d <= 0:
            score -= 22

        item["score"] = max(0.0, min(100.0, round(score, 1)))
        item["v7_foundation_confirmations"] = confirmations

    return adjusted


def _apply_v8_engine_adjustments(results: dict, ind: dict, patterns: dict) -> dict:
    from .v8_engine import canonical_direction, v8_candidate_score

    adjusted = _apply_v7_symmetric_candidate_foundation(results, ind, patterns)
    advanced = (patterns or {}).get("advanced_patterns") or {}
    top = advanced.get("top_pattern") or {}
    top_direction = top.get("direction")
    top_confidence = top.get("confidence")

    for key, item in adjusted.items():
        if key == "NO_TRADE":
            continue
        side = canonical_direction(item.get("direction"))
        scored = v8_candidate_score(
            float(item.get("score") or 0),
            ind,
            side,
            pattern_direction=top_direction,
            pattern_confidence=top_confidence,
        )
        rules = list(item.get("rules") or [])
        alignment = scored["alignment"]
        rules.extend("V8 evidence: " + text for text in alignment.get("evidence", [])[:3])
        rules.extend("V8 caution: " + text for text in alignment.get("warnings", [])[:3])
        item["score"] = scored["score"]
        item["v8_alignment"] = alignment
        item["rules"] = rules

    directional = [(key, value) for key, value in adjusted.items() if key != "NO_TRADE"]
    _, best = max(directional, key=lambda pair: pair[1].get("score", 0)) if directional else ("", {})
    best_alignment = best.get("v8_alignment") or {}
    if (
        canonical_direction(best.get("direction")) not in {"LONG", "SHORT"}
        or float(best.get("score") or 0) < 65
        or float(best_alignment.get("weighted_alignment") or 0) < 0.10
        or int(best_alignment.get("positive_factor_count") or 0) < 3
    ):
        adjusted["NO_TRADE"]["score"] = max(float(adjusted["NO_TRADE"].get("score") or 0), 86)
        adjusted["NO_TRADE"].setdefault("rules", []).append(
            "V8 analytics gate: no symmetric directional candidate cleared the evidence threshold."
        )
    else:
        adjusted["NO_TRADE"]["score"] = min(float(adjusted["NO_TRADE"].get("score") or 0), 42)
        adjusted["NO_TRADE"].setdefault("rules", []).append(
            f"V8 analytics gate passed for {canonical_direction(best.get('direction'))}."
        )
    return adjusted


def _apply_vai_1_0_engine_adjustments(results: dict, ind: dict, patterns: dict) -> dict:
    from .vai_model import predict_vai_setup

    adjusted = _apply_official_v7_engine_adjustments(results, ind, patterns)
    model_seen = False

    for key, item in adjusted.items():
        if key == "NO_TRADE":
            continue
        rules = list(item.get("rules") or [])
        direction = str(item.get("direction") or "NEUTRAL").upper()
        pred = predict_vai_setup(ind, {**item, "setup_type": key}, patterns)
        item["vai_prediction"] = pred
        if not pred.get("trained"):
            rules.append("VAI 1.0: no trained model found; using V7 Official fallback.")
            item["rules"] = rules
            continue
        model_seen = True
        if direction != "LONG":
            item["score"] = 0
            rules.append("VAI 1.0: non-long setup rejected by experimental model policy.")
        elif pred.get("decision") != "TRADE":
            item["score"] = min(float(item.get("score") or 0), 58)
            rules.append(f"VAI 1.0 rejected setup: probability {pred.get('probability')}% below threshold {pred.get('threshold')}%.")
        else:
            p = float(pred.get("probability") or 0)
            er = float(pred.get("expected_return_pct") or 0)
            blended = (float(item.get("score") or 0) * 0.35) + (p * 0.65) + (er * 5)
            if pred.get("grade") in {"A+", "A"}:
                blended += 5
            if float(pred.get("validation_stop_hit_pct") or 0) > 62:
                blended -= 5; rules.append("VAI 1.0 risk penalty: validation stop-hit risk still elevated.")
            item["score"] = max(0, min(100, round(blended, 1)))
            rules.append(f"VAI 1.0 accepted: probability {p:.2f}% / threshold {pred.get('threshold')}% / grade {pred.get('grade')}.")
            rules.append(f"VAI 1.0 expected return estimate: {er:.2f}%.")
        item["rules"] = rules

    directional = [(k, v) for k, v in adjusted.items() if k != "NO_TRADE"]
    best_key, best_val = max(directional, key=lambda kv: kv[1].get("score", 0)) if directional else ("", {"score": 0, "direction": "NEUTRAL"})
    if model_seen:
        pred = best_val.get("vai_prediction") or {}
        if str(best_val.get("direction") or "NEUTRAL").upper() != "LONG" or pred.get("decision") != "TRADE" or best_val.get("score", 0) < 70:
            adjusted["NO_TRADE"]["score"] = max(adjusted["NO_TRADE"].get("score", 0), 95)
            adjusted["NO_TRADE"].setdefault("rules", []).append("VAI 1.0 gate: trained model did not approve a strong long setup.")
        else:
            adjusted["NO_TRADE"]["score"] = min(adjusted["NO_TRADE"].get("score", 0), 40)
            adjusted["NO_TRADE"].setdefault("rules", []).append("VAI 1.0 gate passed.")
    else:
        adjusted["NO_TRADE"].setdefault("rules", []).append("VAI 1.0 untrained: V7 fallback active. Train VAI in the hidden developer lab.")
    return adjusted


def _apply_vai_2_0_engine_adjustments(results: dict, ind: dict, patterns: dict) -> dict:
    from .vai2_model import predict_vai2_setup

    adjusted = _apply_official_v7_engine_adjustments(results, ind, patterns)
    model_seen = False

    for key, item in adjusted.items():
        if key == "NO_TRADE":
            continue
        rules = list(item.get("rules") or [])
        direction = str(item.get("direction") or "NEUTRAL").upper()
        pred = predict_vai2_setup(ind, {**item, "setup_type": key}, patterns)
        item["vai2_prediction"] = pred

        if not pred.get("trained"):
            rules.append("VAI 2.2: no promoted model found; using V7 Official fallback until headless training promotes one.")
            item["rules"] = rules
            continue

        model_seen = True
        if direction != "LONG":
            item["score"] = 0
            rules.append("VAI 2.2: non-long setup rejected by experimental model policy.")
        elif pred.get("decision") != "TRADE":
            item["score"] = min(float(item.get("score") or 0), 54)
            rules.append(
                "VAI 2.2 rejected setup: "
                f"probability {pred.get('probability')}% / threshold {pred.get('threshold')}%, "
                f"expected return {pred.get('expected_return_pct')}%, "
                f"stop probability {pred.get('stop_probability_pct')}%, "
                f"decision edge {pred.get('decision_edge')} / minimum {pred.get('min_decision_edge')}."
            )
        else:
            p = float(pred.get("probability") or 0)
            er = float(pred.get("expected_return_pct") or 0)
            stop_p = float(pred.get("stop_probability_pct") or 0)
            edge = float(pred.get("decision_edge") or 0)
            size = float(pred.get("suggested_position_size_pct") or 0)

            blended = (float(item.get("score") or 0) * 0.22) + (p * 0.58) + (er * 6.5) + (edge * 18.0) + (size * 3.0) - (stop_p * 0.12)
            if pred.get("grade") in {"A+", "A"}:
                blended += 6
                rules.append("VAI 2.2 premium grade boost.")
            if size >= 2.0:
                blended += 3
                rules.append("VAI 2.2 confidence sizing boost: model says this is one of the stronger bets.")
            elif size <= 0.6:
                blended -= 3
                rules.append("VAI 2.2 low-size caution: accepted, but only as a small-confidence bet.")
            item["score"] = max(0, min(100, round(blended, 1)))
            rules.append(
                f"VAI 2.2 accepted: probability {p:.2f}% / expected return {er:.2f}% / "
                f"stop risk {stop_p:.2f}% / edge {edge:.3f} / grade {pred.get('grade')}."
            )
            rules.append(f"VAI 2.2 suggested position size: {size:.2f}% educational sizing hint.")
        item["rules"] = rules

    directional = [(k, v) for k, v in adjusted.items() if k != "NO_TRADE"]
    best_key, best_val = max(directional, key=lambda kv: kv[1].get("score", 0)) if directional else ("", {"score": 0, "direction": "NEUTRAL"})
    if model_seen:
        pred = best_val.get("vai2_prediction") or {}
        size = float(pred.get("suggested_position_size_pct") or 0)
        if str(best_val.get("direction") or "NEUTRAL").upper() != "LONG" or pred.get("decision") != "TRADE" or best_val.get("score", 0) < 72 or size <= 0:
            adjusted["NO_TRADE"]["score"] = max(adjusted["NO_TRADE"].get("score", 0), 96)
            adjusted["NO_TRADE"].setdefault("rules", []).append("VAI 2.2 gate: trained model did not approve a high-confidence long setup.")
        else:
            adjusted["NO_TRADE"]["score"] = min(adjusted["NO_TRADE"].get("score", 0), 36)
            adjusted["NO_TRADE"].setdefault("rules", []).append("VAI 2.2 gate passed; promoted long candidate approved.")
    else:
        adjusted["NO_TRADE"].setdefault("rules", []).append("VAI 2.2 untrained: V7 fallback active. Train VAI2.2 headless for experimental model filtering.")
    return adjusted
