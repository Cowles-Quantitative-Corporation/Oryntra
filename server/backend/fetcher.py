from __future__ import annotations

import threading
import time
import hashlib
from typing import Any

from .market_repository import get_market_repository, normalize_period, normalize_ticker

_CACHE_TTL_SECONDS = 60.0
_MEMORY_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_MEMORY_LOCK = threading.Lock()


def fetch_ticker_data(
    ticker: str,
    period: str = "6mo",
    *,
    provider_api_keys: dict[str, str] | None = None,
    allow_platform_provider_keys: bool = True,
) -> dict[str, Any]:
    symbol = normalize_ticker(ticker)
    clean_period = normalize_period(period)
    secret_marker = ""
    if provider_api_keys:
        secret_marker = hashlib.sha256("|".join(f"{name}:{value}" for name, value in sorted(provider_api_keys.items())).encode("utf-8")).hexdigest()[:16]
    key = (symbol, clean_period, secret_marker, bool(allow_platform_provider_keys))
    now = time.monotonic()

    with _MEMORY_LOCK:
        cached = _MEMORY_CACHE.get(key)
        if cached and now - cached[0] <= _CACHE_TTL_SECONDS:
            payload = dict(cached[1])
            payload["from_cache"] = True
            payload.setdefault("data_source", "memory_response_cache")
            metadata = dict(payload.get("market_data_metadata") or {})
            metadata["response_cache"] = True
            payload["market_data_metadata"] = metadata
            return payload

    minimum_bars = 5 if clean_period in {"5m", "1mo"} else 20
    result = get_market_repository().get_history(
        symbol,
        period=clean_period,
        minimum_bars=minimum_bars,
        allow_api=True,
        provider_api_keys=provider_api_keys,
        allow_platform_provider_keys=allow_platform_provider_keys,
    ).as_fetcher_dict()

    with _MEMORY_LOCK:
        _MEMORY_CACHE[key] = (now, dict(result))
    return result


def clear_fetcher_memory_cache(ticker: str | None = None) -> None:
    with _MEMORY_LOCK:
        if ticker is None:
            _MEMORY_CACHE.clear()
            return
        symbol = normalize_ticker(ticker)
        for key in list(_MEMORY_CACHE):
            if key[0] == symbol:
                _MEMORY_CACHE.pop(key, None)
