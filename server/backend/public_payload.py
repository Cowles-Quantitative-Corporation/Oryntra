from __future__ import annotations

import copy
import math
from numbers import Real
from typing import Any

_BLOCKED_KEYS = {
    "open", "high", "low", "close", "adj_close", "volume", "vol_current",
    "vol_ma20", "bars", "bar", "candles", "candle", "history", "price_history",
    "ohlcv", "raw", "raw_data", "provider_response", "market_data_metadata",
    "timestamps", "timestamp_series", "results", "next_url", "request_id",
    "prev_close", "day_change", "daily_range", "high_52w", "low_52w",
    "high_20d", "low_20d", "data_provider",
}


def _public_release_label(value: str) -> str:
    """Keep historical internal engine identifiers out of user-facing analysis text."""
    return (
        value.replace("V7 Official", "V1.0 Official")
        .replace("V7", "V1.0")
        .replace("V8", "V1.0")
        .replace("VAI 2.2", "V1.0 Quant")
        .replace("VAI 2.1", "V1.0 Quant")
        .replace("VAI 2.2", "V1.0 Quant")
    )


def _clean(value: Any) -> Any:
    if isinstance(value, Real) and not isinstance(value, bool):
        numeric = float(value)
        if not math.isfinite(numeric):
            return None
    if isinstance(value, str):
        return _public_release_label(value)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in _BLOCKED_KEYS:
                continue
            if normalized.startswith("raw_") or normalized.endswith("_bars"):
                continue
            result[str(key)] = _clean(item)
        return result
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def public_analysis_payload(raw: dict[str, Any], *, quota: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    source = copy.deepcopy(raw)
    ticker = str(source.get("ticker") or "").upper()
    trade_plan = _clean(source.get("trade_plan") or {})
    setup = _clean(source.get("setup") or {})
    patterns = _clean(source.get("patterns") or {})

    volume = source.get("volume") if isinstance(source.get("volume"), dict) else {}
    volume_context = {
        "relative_ratio": volume.get("ratio"),
        "trend": volume.get("trend"),
        "price_divergence": source.get("volume_price_divergence"),
    }

    payload = {
        "ticker": ticker,
        "company_name": source.get("company_name", ticker),
        "exchange": source.get("exchange", ""),
        "scanned_at": source.get("scanned_at"),
        "timeframe": source.get("timeframe", "1d"),
        "pattern_engine_mode": source.get("pattern_engine_mode", "official"),
        "response_cache": bool(source.get("response_cache")),
        "search_counter": source.get("search_counter"),
        "trend": source.get("trend"),
        "trend_strength": source.get("trend_strength"),
        "ma20": source.get("ma20"),
        "ma50": source.get("ma50"),
        "ma200": source.get("ma200"),
        "ema9": source.get("ema9"),
        "ema21": source.get("ema21"),
        "ema50": source.get("ema50"),
        "ema_cross": source.get("ema_cross"),
        "rsi14": source.get("rsi14"),
        "rsi7": source.get("rsi7"),
        "macd": _clean(source.get("macd") or {}),
        "stochastic": _clean(source.get("stochastic") or {}),
        "bollinger": _clean(source.get("bollinger") or {}),
        "atr14": source.get("atr14"),
        "atr_pct": source.get("atr_pct"),
        "adx": _clean(source.get("adx") or {}),
        "williams_r": source.get("williams_r"),
        "obv": _clean(source.get("obv") or {}),
        "vwap_20d": source.get("vwap_20d"),
        "above_vwap": source.get("above_vwap"),
        "ichimoku": _clean(source.get("ichimoku") or {}),
        "momentum": _clean(source.get("momentum") or {}),
        "volume_context": _clean(volume_context),
        "levels": _clean(source.get("levels") or {}),
        "setup": setup,
        "confluence": _clean(source.get("confluence") or {}),
        "patterns": patterns,
        "lab_based_grade": _clean(source.get("lab_based_grade") or {}),
        "trade_plan": trade_plan,
        "predictions": _clean(source.get("predictions") or {}),
        "corporate_context": _clean(source.get("corporate_context") or {}),
        "chart": {
            "provider": "TradingView",
            "symbol": ticker,
            "interval": "D",
            "embedded": True,
        },
        "quota": quota,
        "data_policy": {
            "analysis_location": "server_side",
            "market_history_included": False,
            "ohlcv_arrays_included": False,
            "downloadable_market_history": False,
            "chart_provider": "TradingView",
            "market_data_license_mode": policy.get("license_mode"),
            "public_distribution_enabled": policy.get("public_derived_analysis_enabled"),
        },
    }
    return _clean(payload)


def assert_no_raw_market_data(payload: dict[str, Any]) -> None:
    def walk(value: Any, path: str = "root") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key).lower()
                if normalized in _BLOCKED_KEYS or normalized.startswith("raw_") or normalized.endswith("_bars"):
                    raise AssertionError(f"Blocked market-data field at {path}.{key}")
                walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")
    walk(payload)
