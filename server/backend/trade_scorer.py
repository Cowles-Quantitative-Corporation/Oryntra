from typing import Dict, Any, Optional

ACCOUNT_RISK_PCT = 1.0


def calculate_trade_plan(ind: Dict[str, Any], setup: Dict[str, Any]) -> Dict[str, Any]:
    setup_type = setup.get("setup_type", "NO_TRADE")
    direction  = setup.get("direction", "NEUTRAL")
    price      = ind.get("price", 0)
    atr        = ind.get("atr14") or (price * 0.02)

    if setup_type == "NO_TRADE" or direction == "NEUTRAL":
        return _no_trade_plan(price, atr, ind, setup)

    if direction == "LONG":
        plan = _long_plan(ind, setup)
    else:
        plan = _short_plan(ind, setup)

    plan["quality_score"] = _calculate_quality_score(ind, setup, plan)
    plan["quality_grade"] = _grade(plan["quality_score"])
    plan["conviction"]    = _conviction_label(plan["quality_score"])
    plan["signal"]        = _five_level_signal(ind, setup, plan)
    plan["position_size"] = _position_size_suggestion(plan, price)
    plan["predictions"]   = _generate_predictions(ind, setup, plan)

    return plan


def _long_plan(ind: dict, setup: dict) -> dict:
    price      = ind.get("price", 0)
    atr        = ind.get("atr14") or (price * 0.02)
    ma20       = ind.get("ma20") or price
    ma50       = ind.get("ma50") or price
    support_1  = ind.get("support_1") or (price - 2 * atr)
    vwap       = ind.get("vwap_20d") or price
    setup_type = setup.get("setup_type", "")

    if setup_type == "BREAKOUT":
        entry_low  = price * 0.998
        entry_high = price * 1.005
        stop       = max(price - 2.0 * atr, support_1, price * 0.94)
        target     = price + 3.0 * atr

    elif setup_type == "PULLBACK":
        support    = max(ma20, support_1, vwap, price - 1.5 * atr)
        entry_low  = support * 0.995
        entry_high = support * 1.01
        stop       = support - 1.5 * atr
        target     = price + 3.0 * atr

    elif setup_type == "TREND_CONTINUATION":
        entry_low  = price
        entry_high = price * 1.005
        stop       = max(price - 1.5 * atr, ma20 - atr * 0.5) if ma20 else price - 1.5 * atr
        target     = price + 2.5 * atr

    elif setup_type == "REVERSAL_ATTEMPT":
        entry_low  = price
        entry_high = price * 1.01
        stop       = price - 1.5 * atr
        target     = price + 2.0 * atr

    else:
        entry_low  = price
        entry_high = price * 1.005
        stop       = price - 1.5 * atr
        target     = price + 2.0 * atr

    entry_mid = (entry_low + entry_high) / 2
    risk      = entry_mid - stop
    reward    = target - entry_mid
    rr        = reward / risk if risk > 0 else 0

    return {
        "direction":   "LONG",
        "entry_low":   round(entry_low, 2),
        "entry_high":  round(entry_high, 2),
        "entry_ideal": round(entry_mid, 2),
        "stop":        round(max(stop, 0.01), 2),
        "target":      round(target, 2),
        "risk_amt":    round(risk, 2),
        "reward_amt":  round(reward, 2),
        "risk_pct":    round(risk / entry_mid * 100, 2) if entry_mid > 0 else 0,
        "reward_pct":  round(reward / entry_mid * 100, 2) if entry_mid > 0 else 0,
        "risk_reward": round(rr, 2),
    }


def _short_plan(ind: dict, setup: dict) -> dict:
    price      = ind.get("price", 0)
    atr        = ind.get("atr14") or (price * 0.02)
    ma20       = ind.get("ma20") or price
    resist_1   = ind.get("resist_1") or (price + 2 * atr)
    setup_type = setup.get("setup_type", "")

    if setup_type == "BREAKOUT":
        entry_low  = price * 0.995
        entry_high = price
        stop       = min(price + 1.5 * atr, resist_1)
        target     = price - 3.0 * atr

    elif setup_type == "PULLBACK":
        resistance = min(ma20, resist_1, price + 1.5 * atr)
        entry_low  = price * 0.99
        entry_high = resistance
        stop       = resistance + 1.5 * atr
        target     = price - 3.0 * atr

    elif setup_type == "REVERSAL_ATTEMPT":
        entry_low  = price * 0.995
        entry_high = price
        stop       = price + 1.5 * atr
        target     = price - 2.5 * atr

    else:
        entry_low  = price * 0.998
        entry_high = price
        stop       = price + 1.5 * atr
        target     = price - 2.0 * atr

    entry_mid = (entry_low + entry_high) / 2
    risk      = stop - entry_mid
    reward    = entry_mid - target
    rr        = reward / risk if risk > 0 else 0

    return {
        "direction":   "SHORT",
        "entry_low":   round(entry_low, 2),
        "entry_high":  round(entry_high, 2),
        "entry_ideal": round(entry_mid, 2),
        "stop":        round(stop, 2),
        "target":      round(max(target, 0.01), 2),
        "risk_amt":    round(risk, 2),
        "reward_amt":  round(reward, 2),
        "risk_pct":    round(risk / entry_mid * 100, 2) if entry_mid > 0 else 0,
        "reward_pct":  round(reward / entry_mid * 100, 2) if entry_mid > 0 else 0,
        "risk_reward": round(rr, 2),
    }


def _no_trade_plan(price: float, atr: float, ind: dict, setup: dict) -> dict:
    plan = {
        "direction":    "NEUTRAL",
        "entry_low":    None, "entry_high":  None, "entry_ideal": None,
        "stop":         None, "target":      None,
        "risk_amt":     None, "reward_amt":  None,
        "risk_pct":     None, "reward_pct":  None, "risk_reward": None,
        "quality_score": 0,   "quality_grade": "F",
        "conviction":   "AVOID", "signal": "HOLD",
        "position_size": None,
        "predictions":  _no_trade_predictions(),
    }
    plan["quality_score"] = _calculate_quality_score(ind, setup, plan)
    plan["quality_grade"] = _grade(plan["quality_score"])
    plan["conviction"]    = _conviction_label(plan["quality_score"])
    return plan


def _calculate_quality_score(ind: dict, setup: dict, plan: dict) -> float:
    score     = 0.0
    rr        = plan.get("risk_reward", 0) or 0
    conf      = setup.get("confidence", 0) or 0
    direction = plan.get("direction", "NEUTRAL")
    trend     = ind.get("trend", "") or ""
    rsi       = ind.get("rsi14", 50) or 50
    vol_ratio = ind.get("vol_ratio", 1) or 1
    strength  = ind.get("trend_strength", 0) or 0
    adx       = ind.get("adx14") or 0
    di_plus   = ind.get("di_plus") or 0
    di_minus  = ind.get("di_minus") or 0
    obv_trend = ind.get("obv_trend", "") or ""
    ichi_sig  = ind.get("ichi_signal", "") or ""
    ema_cross = ind.get("ema_cross", "") or ""
    vol_divg  = ind.get("volume_price_divergence", "") or ""
    mom60     = ind.get("momentum_60d") or 0

    score += conf * 0.28

    if rr >= 3.0:       score += 22
    elif rr >= 2.5:     score += 18
    elif rr >= 2.0:     score += 13
    elif rr >= 1.5:     score += 8
    elif rr >= 1.0:     score += 3
    else:               score -= 6

    aligned = (("UP" in trend and direction == "LONG") or
               ("DOWN" in trend and direction == "SHORT"))
    if "STRONG" in trend and aligned:   score += 18
    elif aligned:                        score += 11
    elif trend == "SIDEWAYS":            score += 0
    else:                                score -= 5

    if adx >= 40:                                   score += 12
    elif adx >= 25:                                 score += 8
    elif adx >= 15:                                 score += 3
    else:                                           score -= 4
    if direction == "LONG"  and di_plus > di_minus: score += 3
    if direction == "SHORT" and di_minus > di_plus: score += 3

    if vol_ratio >= 2.0:    score += 10
    elif vol_ratio >= 1.5:  score += 7
    elif vol_ratio >= 1.0:  score += 4

    if direction == "LONG":
        if 40 <= rsi <= 65:   score += 5
        elif rsi > 75:        score -= 4
    elif direction == "SHORT":
        if 35 <= rsi <= 60:   score += 5
        elif rsi < 25:        score -= 4

    if direction == "LONG":
        if ichi_sig in ("STRONG_BULL", "BULL"):     score += 2
        if ema_cross == "BULLISH":                   score += 1
        if obv_trend == "RISING":                    score += 1
        if vol_divg == "BEARISH_DIVERGENCE":         score -= 3
    elif direction == "SHORT":
        if ichi_sig in ("STRONG_BEAR", "BEAR"):     score += 2
        if ema_cross == "BEARISH":                   score += 1
        if obv_trend == "FALLING":                   score += 1
        if vol_divg == "BULLISH_DIVERGENCE":         score -= 3

    score += strength * 0.04

    if direction == "LONG"  and mom60 and mom60 > 5:  score += 2
    if direction == "SHORT" and mom60 and mom60 < -5:  score += 2

    return round(min(max(score, 0), 100), 1)


def _grade(score: float) -> str:
    if score >= 85: return "A+"
    if score >= 75: return "A"
    if score >= 65: return "B"
    if score >= 55: return "C"
    if score >= 40: return "D"
    return "F"


def _conviction_label(score: float) -> str:
    if score >= 80: return "HIGH CONVICTION"
    if score >= 65: return "GOOD SETUP"
    if score >= 50: return "WATCHABLE"
    if score >= 35: return "MARGINAL"
    return "AVOID"


def _position_size_suggestion(plan: dict, price: float) -> dict | None:
    risk_amt = plan.get("risk_amt")
    entry    = plan.get("entry_ideal")
    if not risk_amt or not entry or risk_amt <= 0 or entry <= 0:
        return None
    account      = 10_000
    dollar_risk  = account * (ACCOUNT_RISK_PCT / 100)
    shares       = int(dollar_risk / risk_amt)
    if shares < 1:
        shares = 1
    position_val = round(shares * entry, 2)
    return {
        "shares":         shares,
        "position_value": position_val,
        "dollar_risk":    round(shares * risk_amt, 2),
        "account_basis":  account,
        "risk_pct_used":  ACCOUNT_RISK_PCT,
        "note":           f"Sizing for ${account:,} account at {ACCOUNT_RISK_PCT}% risk — adjust to your actual account size"
    }


def _five_level_signal(ind: dict, setup: dict, plan: dict) -> str:
    direction  = plan.get("direction", "NEUTRAL")
    score      = plan.get("quality_score", 0) or 0
    rr         = plan.get("risk_reward", 0) or 0
    trend      = ind.get("trend", "") or ""
    rsi        = ind.get("rsi14", 50) or 50
    adx        = ind.get("adx14") or 0
    setup_type = setup.get("setup_type", "NO_TRADE")

    if setup_type == "NO_TRADE" or direction == "NEUTRAL":
        return "HOLD"

    if setup_type == "OVEREXTENDED":
        return "SELL" if rsi > 70 else "BUY"

    if direction == "LONG":
        if score >= 78 and rr >= 2.5 and "UP" in trend and adx >= 20:
            return "STRONG_BUY"
        elif score >= 55 and rr >= 1.5:
            return "BUY"
        elif score >= 65 and rr >= 1.2 and "UP" in trend:
            return "BUY"
        else:
            return "HOLD"

    elif direction == "SHORT":
        if score >= 78 and rr >= 2.5 and "DOWN" in trend and adx >= 20:
            return "STRONG_SELL"
        elif score >= 55 and rr >= 1.5:
            return "SELL"
        elif score >= 65 and rr >= 1.2 and "DOWN" in trend:
            return "SELL"
        else:
            return "HOLD"

    return "HOLD"


def _generate_predictions(ind: dict, setup: dict, plan: dict) -> dict:
    direction  = plan.get("direction", "NEUTRAL")
    quality    = plan.get("quality_score", 50) or 50
    atr_pct    = ind.get("atr_pct", 1.5) or 1.5
    setup_type = setup.get("setup_type", "NO_TRADE")
    trend      = ind.get("trend", "") or ""
    rsi        = ind.get("rsi14", 50) or 50
    mom60      = ind.get("momentum_60d") or 0
    adx        = ind.get("adx14") or 0

    pattern_adjustment    = _pattern_projection_modifier(setup, direction)
    pattern_multiplier    = pattern_adjustment.get("expected_move_multiplier", 1.0)
    confidence_adjustment = pattern_adjustment.get("confidence_adjustment", 0)

    if direction == "LONG"  and mom60 and mom60 > 10:  regime_mult = 1.15
    elif direction == "LONG"  and mom60 and mom60 < -10: regime_mult = 0.85
    elif direction == "SHORT" and mom60 and mom60 < -10: regime_mult = 1.15
    elif direction == "SHORT" and mom60 and mom60 > 10:  regime_mult = 0.85
    else:                                                  regime_mult = 1.0

    adx_mult = 1.0
    if adx >= 40:   adx_mult = 1.12
    elif adx >= 25: adx_mult = 1.05
    elif adx < 15:  adx_mult = 0.88

    base = {
        "BREAKOUT":           {"5d": 2.0, "10d": 3.5, "20d": 5.5},
        "PULLBACK":           {"5d": 1.5, "10d": 2.5, "20d": 4.0},
        "TREND_CONTINUATION": {"5d": 1.2, "10d": 2.0, "20d": 3.5},
        "REVERSAL_ATTEMPT":   {"5d": 1.0, "10d": 2.0, "20d": 3.0},
        "OVEREXTENDED":       {"5d": -1.5, "10d": -2.5, "20d": -2.0},
        "NO_TRADE":           {"5d": 0,   "10d": 0,   "20d": 0},
    }.get(setup_type, {"5d": 0, "10d": 0, "20d": 0})

    sign = 1 if direction == "LONG" else -1 if direction == "SHORT" else 0

    def pred(mult: float) -> dict:
        raw_pct    = sign * atr_pct * mult * (quality / 100) * pattern_multiplier * regime_mult * adx_mult
        confidence = min(max(int(quality * 0.88 + confidence_adjustment), 0), 90)

        if raw_pct >= 3.0:    sig = "STRONG_BUY"
        elif raw_pct >= 1.0:  sig = "BUY"
        elif raw_pct <= -3.0: sig = "STRONG_SELL"
        elif raw_pct <= -1.0: sig = "SELL"
        else:                 sig = "HOLD"

        return {"expected_pct": round(raw_pct, 2), "signal": sig, "confidence": confidence}

    return {
        "5d":  pred(base["5d"]),
        "10d": pred(base["10d"]),
        "20d": pred(base["20d"]),
        "pattern_adjustment": pattern_adjustment,
        "disclaimer": "Rule-based estimate using setup, ATR, trend, ADX, 60d momentum, and pattern alignment. Not financial advice."
    }


def _pattern_projection_modifier(setup: dict, direction: str) -> dict:
    neutral = {
        "expected_move_multiplier": 1.0, "confidence_adjustment": 0,
        "bias_score": 0.0, "net_pattern_intensity": 0.0,
        "supporting_intensity": 0.0, "opposing_intensity": 0.0,
        "avg_supporting_confidence": 0.0, "avg_opposing_confidence": 0.0,
        "label": "No direct pattern adjustment",
        "supporting_patterns": [], "opposing_patterns": [], "pattern_effect_details": [],
    }
    if direction not in {"LONG", "SHORT"}:
        return neutral

    pattern_bundle = setup.get("patterns") or {}
    advanced       = pattern_bundle.get("advanced_patterns") or {}
    recent         = advanced.get("recent") or pattern_bundle.get("detected_patterns") or []
    if not recent:
        return neutral

    desired  = "BULLISH" if direction == "LONG" else "BEARISH"
    opposite = "BEARISH" if desired == "BULLISH" else "BULLISH"

    signed_intensity = supporting_intensity = opposing_intensity = 0.0
    supporting_conf_total = opposing_conf_total = 0.0
    supporting_count = opposing_count = 0
    supporting: list[str] = []
    opposing:   list[str] = []
    details:    list[dict] = []

    for p in recent[:30]:
        p_dir = str(p.get("direction") or "NEUTRAL").upper()
        if p_dir not in {desired, opposite}:
            continue
        conf = _safe_float(p.get("confidence"), 0.0)
        if conf <= 0:
            continue
        family  = str(p.get("pattern_family") or "UNKNOWN").upper()
        name    = str(p.get("pattern_name") or "PATTERN").replace("_", " ")
        context = p.get("context") if isinstance(p.get("context"), dict) else {}

        cs = _confidence_strength(conf)
        fw = _pattern_family_weight(family)
        cw = _pattern_context_intensity(family, context)
        rw = 1.18 if p.get("display_reason") == "LAST TRADING DAY" else 1.0

        contrib = max(0.0, min(cs * fw * cw * rw, 1.75))
        label   = f"{name} ({conf:.0f}%, impact {contrib:.2f})"
        signed  = contrib if p_dir == desired else -contrib
        signed_intensity += signed

        if p_dir == desired:
            supporting_intensity  += contrib
            supporting_conf_total += conf
            supporting_count      += 1
            if len(supporting) < 5: supporting.append(label)
        else:
            opposing_intensity  += contrib
            opposing_conf_total += conf
            opposing_count      += 1
            if len(opposing) < 5:   opposing.append(label)

        if len(details) < 8:
            details.append({
                "pattern": name, "direction": p_dir, "confidence": round(conf, 1),
                "confidence_strength": round(cs, 3), "family_weight": round(fw, 3),
                "context_intensity": round(cw, 3), "recency_weight": round(rw, 3),
                "signed_impact": round(signed, 3),
            })

    if supporting_count == 0 and opposing_count == 0:
        return neutral

    capped_bias           = max(-0.35, min(0.35, signed_intensity * 0.085))
    multiplier            = round(1.0 + capped_bias, 3)
    confidence_adjustment = int(round(capped_bias * 40))

    if capped_bias > 0.06:    lbl = "High-confidence patterns increase the estimate"
    elif capped_bias < -0.06: lbl = "High-confidence opposing patterns reduce the estimate"
    else:                      lbl = "Pattern confidence is mixed/low impact"

    return {
        "expected_move_multiplier": multiplier,
        "confidence_adjustment":    confidence_adjustment,
        "bias_score":               round(capped_bias, 3),
        "net_pattern_intensity":    round(signed_intensity, 3),
        "supporting_intensity":     round(supporting_intensity, 3),
        "opposing_intensity":       round(opposing_intensity, 3),
        "avg_supporting_confidence": round(supporting_conf_total / supporting_count, 1) if supporting_count else 0.0,
        "avg_opposing_confidence":   round(opposing_conf_total / opposing_count, 1)   if opposing_count   else 0.0,
        "label":                    lbl,
        "supporting_patterns":      supporting,
        "opposing_patterns":        opposing,
        "pattern_effect_details":   details,
    }


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None and value != "" else default
    except Exception:
        return default


def _confidence_strength(confidence: float) -> float:
    x = max(0.0, min(1.0, (confidence - 35.0) / 65.0))
    return x ** 1.35


def _pattern_family_weight(family: str) -> float:
    return {"FVG": 1.28, "STRUCTURE": 1.22, "CHART": 1.12, "CANDLE": 0.88}.get(
        str(family or "").upper(), 0.80)


def _pattern_context_intensity(family: str, context: dict) -> float:
    if not isinstance(context, dict):
        context = {}
    weight = 1.0
    family = str(family or "").upper()
    volume_ratio = _safe_float(context.get("middle_volume_ratio", context.get("volume_ratio", 1.0)), 1.0)
    if volume_ratio >= 1.0:   weight += min(0.22, (volume_ratio - 1.0) * 0.12)
    elif volume_ratio < 0.75: weight -= 0.08
    gap_atr   = _safe_float(context.get("gap_atr_multiple"), 0.0)
    body_atr  = _safe_float(context.get("middle_body_atr_multiple", context.get("body_atr_multiple", 0.0)), 0.0)
    impulse   = abs(_safe_float(context.get("impulse_pct"), 0.0))
    if family == "FVG":
        weight += min(0.35, gap_atr * 0.45)
        weight += min(0.22, body_atr * 0.18)
        fs = str(context.get("fill_status") or "").upper()
        if fs == "UNFILLED":     weight += 0.10
        elif "PARTIAL" in fs:    weight += 0.03
        elif fs == "FILLED":     weight -= 0.15
    elif family == "STRUCTURE":
        weight += min(0.24, body_atr * 0.18)
        if context.get("broken_swing_index") is not None: weight += 0.08
        if context.get("displacement_index") is not None: weight += 0.08
    elif family == "CHART":
        if impulse: weight += min(0.20, impulse / 100.0)
        if context.get("swing_indexes"): weight += 0.06
    elif family == "CANDLE":
        trend = str(context.get("trend") or "").upper()
        if "DOWN" in trend or "UP" in trend: weight += 0.04
    return max(0.65, min(weight, 1.65))


def _no_trade_predictions() -> dict:
    return {
        "5d":  {"expected_pct": 0, "signal": "HOLD", "confidence": 0},
        "10d": {"expected_pct": 0, "signal": "HOLD", "confidence": 0},
        "20d": {"expected_pct": 0, "signal": "HOLD", "confidence": 0},
        "disclaimer": "No setup detected. Predictions unavailable."
    }

