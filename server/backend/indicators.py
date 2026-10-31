import pandas as pd
import numpy as np
from typing import Dict, Any


def calculate_all_indicators(hist: pd.DataFrame) -> Dict[str, Any]:
    close  = hist["Close"]
    high   = hist["High"]
    low    = hist["Low"]
    open_  = hist["Open"]
    volume = hist["Volume"]
    n      = len(hist)

    indicators = {}

    indicators["ma20"]  = _sma(close, 20)
    indicators["ma50"]  = _sma(close, 50)
    indicators["ma200"] = _sma(close, 200)
    indicators["ema9"]  = _ema(close, 9)
    indicators["ema21"] = _ema(close, 21)
    indicators["ema50"] = _ema(close, 50)

    indicators["price"]       = float(close.iloc[-1])
    indicators["prev_close"]  = float(close.iloc[-2]) if n > 1 else float(close.iloc[-1])
    indicators["day_change"]  = round((indicators["price"] - indicators["prev_close"]) / indicators["prev_close"] * 100, 2)
    indicators["high_52w"]    = float(high.iloc[-min(260, n):].max())
    indicators["low_52w"]     = float(low.iloc[-min(260, n):].min())
    indicators["high_20d"]    = float(high.iloc[-20:].max())
    indicators["low_20d"]     = float(low.iloc[-20:].min())
    indicators["high_5d"]     = float(high.iloc[-5:].max())
    indicators["low_5d"]      = float(low.iloc[-5:].min())
    indicators["daily_range_pct"] = round((float(high.iloc[-1]) - float(low.iloc[-1])) / float(close.iloc[-1]) * 100, 2)

    indicators["rsi14"] = _rsi(close, 14)
    indicators["rsi7"]  = _rsi(close, 7)

    macd_result = _macd(close)
    indicators.update(macd_result)

    bb = _bollinger_bands(close, 20, 2.0)
    indicators.update(bb)

    indicators["atr14"]   = _atr(high, low, close, 14)
    indicators["atr_pct"] = round(indicators["atr14"] / indicators["price"] * 100, 2) if indicators["price"] > 0 else 0
    indicators["atr_percentile_252"] = _atr_percentile(high, low, close, 14, 252)

    adx_result = _adx(high, low, close, 14)
    indicators.update(adx_result)

    indicators["williams_r"] = _williams_r(high, low, close, 14)

    obv_result = _obv(close, volume)
    indicators.update(obv_result)

    indicators["vwap_20d"] = _vwap(high, low, close, volume, 20)

    ichi = _ichimoku(high, low, close)
    indicators.update(ichi)

    indicators["vol_current"] = int(volume.iloc[-1])
    indicators["vol_ma20"]    = int(volume.iloc[-20:].mean()) if n >= 20 else int(volume.mean())
    indicators["vol_ratio"]   = round(indicators["vol_current"] / indicators["vol_ma20"], 2) if indicators["vol_ma20"] > 0 else 1.0
    indicators["rvol_20d"]    = indicators["vol_ratio"]
    indicators["vol_trend"]   = _volume_trend(volume)

    indicators["ema_cross"] = _ema_cross_signal(indicators)

    indicators["trend"]          = _classify_trend(indicators)
    indicators["trend_strength"] = _trend_strength(close)
    indicators["adx_trend"]      = _adx_trend_label(indicators.get("adx14", 0))
    indicators["above_ma20"]     = indicators["price"] > indicators["ma20"]  if indicators["ma20"]  else None
    indicators["above_ma50"]     = indicators["price"] > indicators["ma50"]  if indicators["ma50"]  else None
    indicators["above_ma200"]    = indicators["price"] > indicators["ma200"] if indicators["ma200"] else None
    indicators["above_vwap"]     = indicators["price"] > indicators["vwap_20d"] if indicators["vwap_20d"] else None

    indicators["momentum_5d"]  = _pct_change(close, 5)
    indicators["momentum_20d"] = _pct_change(close, 20)
    indicators["momentum_60d"] = _pct_change(close, 60)

    if indicators["ma50"]:
        indicators["pct_from_ma50"]  = round((indicators["price"] - indicators["ma50"])  / indicators["ma50"]  * 100, 2)
    else:
        indicators["pct_from_ma50"] = None

    if indicators["high_52w"]:
        indicators["pct_from_52w_high"] = round((indicators["price"] - indicators["high_52w"]) / indicators["high_52w"] * 100, 2)
    else:
        indicators["pct_from_52w_high"] = None

    stoch = _stochastic(high, low, close, 14, 3)
    indicators.update(stoch)

    for key, level in (
        ("pct_from_sma20", indicators.get("ma20")),
        ("pct_from_sma50", indicators.get("ma50")),
        ("pct_from_sma200", indicators.get("ma200")),
        ("pct_from_ema9", indicators.get("ema9")),
        ("pct_from_ema21", indicators.get("ema21")),
        ("pct_from_ema50", indicators.get("ema50")),
        ("pct_from_vwap", indicators.get("vwap_20d")),
    ):
        indicators[key] = _distance_pct(indicators["price"], level)

    pivots = _pivot_points(hist)
    indicators.update(pivots)

    indicators["volume_price_divergence"] = _volume_price_divergence(close, volume)

    return indicators


def _sma(series: pd.Series, period: int) -> float | None:
    if len(series) < period:
        return None
    return round(float(series.iloc[-period:].mean()), 4)


def _ema(series: pd.Series, period: int) -> float | None:
    if len(series) < period:
        return None
    ema = series.ewm(span=period, adjust=False).mean()
    return round(float(ema.iloc[-1]), 4)


def _rsi(close: pd.Series, period: int = 14) -> float | None:
    if len(close) < period + 1:
        return None
    delta  = close.diff()
    gain   = delta.clip(lower=0)
    loss   = (-delta).clip(lower=0)
    avg_g  = gain.ewm(com=period - 1, adjust=False).mean()
    avg_l  = loss.ewm(com=period - 1, adjust=False).mean()
    rs     = avg_g / avg_l.replace(0, np.nan)
    rsi    = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 2)


def _macd(close: pd.Series) -> dict:
    if len(close) < 26:
        return {"macd_line": None, "macd_signal": None, "macd_hist": None, "macd_cross": "UNKNOWN"}
    ema12   = close.ewm(span=12, adjust=False).mean()
    ema26   = close.ewm(span=26, adjust=False).mean()
    line    = ema12 - ema26
    signal  = line.ewm(span=9, adjust=False).mean()
    hist    = line - signal
    cross   = "BULLISH" if float(hist.iloc[-1]) > 0 and float(hist.iloc[-2]) <= 0 else \
              "BEARISH"  if float(hist.iloc[-1]) < 0 and float(hist.iloc[-2]) >= 0 else \
              "BULL"     if float(hist.iloc[-1]) > 0 else "BEAR"
    return {
        "macd_line":   round(float(line.iloc[-1]),   4),
        "macd_signal": round(float(signal.iloc[-1]), 4),
        "macd_hist":   round(float(hist.iloc[-1]),   4),
        "macd_cross":  cross,
    }


def _bollinger_bands(close: pd.Series, period: int = 20, std: float = 2.0) -> dict:
    if len(close) < period:
        return {"bb_upper": None, "bb_mid": None, "bb_lower": None, "bb_pct": None, "bb_width": None}
    sma   = close.rolling(period).mean()
    sigma = close.rolling(period).std()
    upper = sma + std * sigma
    lower = sma - std * sigma
    mid   = sma
    bb_pct = (close - lower) / (upper - lower).replace(0, np.nan) * 100
    bb_width = (upper - lower) / mid * 100
    return {
        "bb_upper": round(float(upper.iloc[-1]), 4),
        "bb_mid":   round(float(mid.iloc[-1]),   4),
        "bb_lower": round(float(lower.iloc[-1]), 4),
        "bb_pct":   round(float(bb_pct.iloc[-1]), 2),
        "bb_width": round(float(bb_width.iloc[-1]), 2),
    }


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1:
        return float((high - low).mean())
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(com=period - 1, adjust=False).mean()
    return round(float(atr.iloc[-1]), 4)


def _atr_percentile(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14, lookback: int = 252) -> float:
    if len(close) < period + 2:
        return 50.0
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    atr_pct = (atr / close.replace(0, np.nan) * 100).dropna().iloc[-max(period + 2, lookback):]
    if atr_pct.empty:
        return 50.0
    current = float(atr_pct.iloc[-1])
    return round(float((atr_pct <= current).mean() * 100.0), 2)


def _distance_pct(price: float | None, level: float | None) -> float | None:
    try:
        p = float(price)
        l = float(level)
    except Exception:
        return None
    if not np.isfinite(p) or not np.isfinite(l) or l == 0:
        return None
    return round((p - l) / l * 100.0, 4)


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> dict:
    if len(close) < period * 2:
        return {"adx14": None, "di_plus": None, "di_minus": None, "adx_signal": "WEAK"}

    prev_high  = high.shift(1)
    prev_low   = low.shift(1)
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    dm_plus  = ((high - prev_high).clip(lower=0)).where(
        (high - prev_high) > (prev_low - low), 0)
    dm_minus = ((prev_low - low).clip(lower=0)).where(
        (prev_low - low) > (high - prev_high), 0)

    atr_s    = tr.ewm(alpha=1/period, adjust=False).mean()
    dmp_s    = dm_plus.ewm(alpha=1/period, adjust=False).mean()
    dmm_s    = dm_minus.ewm(alpha=1/period, adjust=False).mean()

    di_plus  = (dmp_s / atr_s.replace(0, np.nan) * 100).fillna(0)
    di_minus = (dmm_s / atr_s.replace(0, np.nan) * 100).fillna(0)

    dx       = ((di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan) * 100).fillna(0)
    adx      = dx.ewm(alpha=1/period, adjust=False).mean()

    adx_val  = round(float(adx.iloc[-1]), 2)
    dip_val  = round(float(di_plus.iloc[-1]), 2)
    dim_val  = round(float(di_minus.iloc[-1]), 2)

    if adx_val >= 40:   sig = "STRONG"
    elif adx_val >= 25: sig = "TRENDING"
    elif adx_val >= 15: sig = "WEAK"
    else:               sig = "NO_TREND"

    return {"adx14": adx_val, "di_plus": dip_val, "di_minus": dim_val, "adx_signal": sig}


def _williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float | None:
    if len(close) < period:
        return None
    h = high.iloc[-period:].max()
    l = low.iloc[-period:].min()
    if h == l:
        return None
    wr = (h - float(close.iloc[-1])) / (h - l) * -100
    return round(wr, 2)


def _obv(close: pd.Series, volume: pd.Series) -> dict:
    if len(close) < 5:
        return {"obv": None, "obv_signal": "UNKNOWN", "obv_trend": "UNKNOWN"}

    direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    obv       = (direction * volume).cumsum()
    obv_now   = float(obv.iloc[-1])
    obv_ma5   = float(obv.iloc[-5:].mean())
    obv_ma20  = float(obv.iloc[-20:].mean()) if len(obv) >= 20 else float(obv.mean())

    obv_trend = "RISING" if obv_now > obv_ma20 else "FALLING"

    price_up  = float(close.iloc[-1]) > float(close.iloc[-5])
    obv_up    = obv_now > float(obv.iloc[-6]) if len(obv) > 6 else True

    if price_up and obv_up:     sig = "CONFIRMING"
    elif price_up and not obv_up: sig = "DIVERGING"
    elif not price_up and obv_up: sig = "DIVERGING"
    else:                         sig = "CONFIRMING"

    return {"obv": round(obv_now, 0), "obv_signal": sig, "obv_trend": obv_trend}


def _vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, period: int = 20) -> float | None:
    if len(close) < period:
        return None
    typical  = (high + low + close) / 3
    tp_vol   = typical * volume
    vwap_val = tp_vol.iloc[-period:].sum() / volume.iloc[-period:].sum()
    return round(float(vwap_val), 4) if volume.iloc[-period:].sum() > 0 else None


def _ichimoku(high: pd.Series, low: pd.Series, close: pd.Series) -> dict:
    n = len(close)
    result = {
        "ichi_tenkan": None, "ichi_kijun": None,
        "ichi_senkou_a": None, "ichi_senkou_b": None,
        "ichi_signal": "UNKNOWN",
    }
    if n < 52:
        return result

    def mid(series_h, series_l, period):
        return ((series_h.rolling(period).max() + series_l.rolling(period).min()) / 2).iloc[-1]

    tenkan  = mid(high, low, 9)
    kijun   = mid(high, low, 26)
    senk_a  = (tenkan + kijun) / 2
    senk_b  = mid(high, low, 52)

    result["ichi_tenkan"]   = round(float(tenkan), 4)
    result["ichi_kijun"]    = round(float(kijun), 4)
    result["ichi_senkou_a"] = round(float(senk_a), 4)
    result["ichi_senkou_b"] = round(float(senk_b), 4)

    price      = float(close.iloc[-1])
    cloud_top  = max(senk_a, senk_b)
    cloud_bot  = min(senk_a, senk_b)

    if price > cloud_top and tenkan > kijun:
        result["ichi_signal"] = "STRONG_BULL"
    elif price > cloud_top:
        result["ichi_signal"] = "BULL"
    elif price < cloud_bot and tenkan < kijun:
        result["ichi_signal"] = "STRONG_BEAR"
    elif price < cloud_bot:
        result["ichi_signal"] = "BEAR"
    else:
        result["ichi_signal"] = "NEUTRAL"

    return result


def _ema_cross_signal(ind: dict) -> str:
    e9  = ind.get("ema9")
    e21 = ind.get("ema21")
    if e9 is None or e21 is None:
        return "UNKNOWN"
    if e9 > e21:  return "BULLISH"
    if e9 < e21:  return "BEARISH"
    return "NEUTRAL"


def _stochastic(high: pd.Series, low: pd.Series, close: pd.Series, k: int = 14, d: int = 3) -> dict:
    if len(close) < k:
        return {"stoch_k": None, "stoch_d": None, "stoch_signal": "UNKNOWN"}
    lowest_low   = low.rolling(k).min()
    highest_high = high.rolling(k).max()
    stoch_k = (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan) * 100
    stoch_d = stoch_k.rolling(d).mean()
    k_val = round(float(stoch_k.iloc[-1]), 2)
    d_val = round(float(stoch_d.iloc[-1]), 2)
    sig = "OVERBOUGHT" if k_val > 80 else "OVERSOLD" if k_val < 20 else "NEUTRAL"
    prev_k = float(stoch_k.iloc[-2]) if len(stoch_k.dropna()) >= 2 else k_val
    prev_d = float(stoch_d.iloc[-2]) if len(stoch_d.dropna()) >= 2 else d_val
    cross = "BULLISH" if k_val > d_val and prev_k <= prev_d else "BEARISH" if k_val < d_val and prev_k >= prev_d else "BULL" if k_val > d_val else "BEAR"
    return {"stoch_k": k_val, "stoch_d": d_val, "stoch_signal": sig, "stoch_cross": cross}


def _volume_trend(volume: pd.Series, window: int = 5) -> str:
    if len(volume) < window * 2:
        return "UNKNOWN"
    recent = volume.iloc[-window:].mean()
    prior  = volume.iloc[-window * 2:-window].mean()
    if prior == 0:
        return "UNKNOWN"
    ratio = recent / prior
    if ratio > 1.3:  return "INCREASING"
    if ratio < 0.7:  return "DECLINING"
    return "STABLE"


def _adx_trend_label(adx: float | None) -> str:
    if adx is None:  return "UNKNOWN"
    if adx >= 40:    return "STRONG TREND"
    if adx >= 25:    return "TRENDING"
    if adx >= 15:    return "WEAK TREND"
    return "RANGING"


def _classify_trend(ind: dict) -> str:
    price   = ind.get("price", 0)
    ma20    = ind.get("ma20")
    ma50    = ind.get("ma50")
    ma200   = ind.get("ma200")
    adx     = ind.get("adx14") or 0
    di_plus = ind.get("di_plus") or 0
    di_minus= ind.get("di_minus") or 0
    ema9    = ind.get("ema9")
    ema21   = ind.get("ema21")
    mom20   = ind.get("momentum_20d", 0) or 0

    if adx >= 25 and di_plus > di_minus:
        if ma20 and ma50 and price > ma20 > ma50:
            return "STRONG_UPTREND"
        return "UPTREND"
    if adx >= 25 and di_minus > di_plus:
        if ma20 and ma50 and price < ma20 < ma50:
            return "STRONG_DOWNTREND"
        return "DOWNTREND"

    if ma20 and ma50 and ma200:
        if price > ma20 > ma50 > ma200:
            return "STRONG_UPTREND"
        if price > ma20 and price > ma50:
            return "UPTREND"
        if price < ma20 < ma50 < ma200:
            return "STRONG_DOWNTREND"
        if price < ma20 and price < ma50:
            return "DOWNTREND"
    if ma20 and ma50:
        if price > ma20 > ma50:
            return "UPTREND"
        if price < ma20 < ma50:
            return "DOWNTREND"

    if abs(mom20) < 3:
        return "SIDEWAYS"
    return "SIDEWAYS"


def _trend_strength(close: pd.Series, period: int = 20) -> float:
    if len(close) < period:
        return 0.0
    y = close.iloc[-period:].values
    x = np.arange(period)
    slope, intercept = np.polyfit(x, y, 1)
    y_hat = slope * x + intercept
    ss_res = np.sum((y - y_hat) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    r2     = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0
    return round(max(0.0, r2) * 100, 1)


def _pct_change(close: pd.Series, periods: int) -> float | None:
    if len(close) < periods + 1:
        return None
    start = float(close.iloc[-periods - 1])
    end   = float(close.iloc[-1])
    if start == 0:
        return None
    return round((end - start) / start * 100, 2)


def _pivot_points(hist: pd.DataFrame) -> dict:
    prev = hist.iloc[-2] if len(hist) >= 2 else hist.iloc[-1]
    H, L, C = float(prev["High"]), float(prev["Low"]), float(prev["Close"])
    pivot  = (H + L + C) / 3
    r1     = 2 * pivot - L
    s1     = 2 * pivot - H
    r2     = pivot + (H - L)
    s2     = pivot - (H - L)
    return {
        "pivot":    round(pivot, 4),
        "resist_1": round(r1, 4),
        "resist_2": round(r2, 4),
        "support_1": round(s1, 4),
        "support_2": round(s2, 4),
    }


def _volume_price_divergence(close: pd.Series, volume: pd.Series, lookback: int = 10) -> str:
    if len(close) < lookback + 1:
        return "UNKNOWN"
    price_change  = float(close.iloc[-1]) - float(close.iloc[-lookback])
    vol_change    = float(volume.iloc[-lookback:].mean()) - float(volume.iloc[-lookback*2:-lookback].mean()) if len(close) >= lookback*2 else 0

    if price_change < 0 and vol_change > 0:   return "BULLISH_DIVERGENCE"
    if price_change > 0 and vol_change < 0:   return "BEARISH_DIVERGENCE"
    return "NONE"

