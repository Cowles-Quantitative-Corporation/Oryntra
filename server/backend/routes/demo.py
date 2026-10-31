from __future__ import annotations

import asyncio
import math
import random
from datetime import date, timedelta
from functools import lru_cache

from fastapi import APIRouter, HTTPException

from ..public_payload import assert_no_raw_market_data, public_analysis_payload
from .analysis import (
    ScanRequest,
    _build_result,
    _compute_scan_artifacts,
    _period_to_timeframe,
    browser_bars_to_history,
)

router = APIRouter()

# Public demo mode intentionally uses a fixed, synthetic market-data snapshot.
# It is not live market data and must never be presented as such.
_DEMO_END_DATE = date(2026, 8, 28)
_DEMO_BARS = 380

_DEMO_SYMBOLS = {
    "AAPL": {"company": "Apple Inc. · demo", "seed": 1101, "base": 162.0, "drift": 0.00055, "cycle": 0.010, "volume": 52_000_000},
    "NVDA": {"company": "NVIDIA Corp. · demo", "seed": 2202, "base": 104.0, "drift": 0.00100, "cycle": 0.017, "volume": 185_000_000},
    "TSLA": {"company": "Tesla Inc. · demo", "seed": 3303, "base": 218.0, "drift": 0.00010, "cycle": 0.024, "volume": 108_000_000},
    "MSFT": {"company": "Microsoft Corp. · demo", "seed": 4404, "base": 386.0, "drift": 0.00048, "cycle": 0.008, "volume": 25_000_000},
    "SPY": {"company": "SPDR S&P 500 ETF Trust · demo", "seed": 5505, "base": 498.0, "drift": 0.00036, "cycle": 0.006, "volume": 71_000_000},
}


def _business_days(end: date, count: int) -> list[date]:
    rows: list[date] = []
    cursor = end
    while len(rows) < count:
        if cursor.weekday() < 5:
            rows.append(cursor)
        cursor -= timedelta(days=1)
    rows.reverse()
    return rows


def _build_bars(symbol: str) -> list[dict]:
    cfg = _DEMO_SYMBOLS[symbol]
    rng = random.Random(int(cfg["seed"]))
    dates = _business_days(_DEMO_END_DATE, _DEMO_BARS)
    close = float(cfg["base"])
    rows: list[dict] = []

    for index, day in enumerate(dates):
        cycle = math.sin(index / 13.0) * float(cfg["cycle"])
        slower = math.sin(index / 47.0) * float(cfg["cycle"]) * 0.65
        shock = rng.gauss(0.0, 0.011 if symbol != "TSLA" else 0.018)
        daily_return = float(cfg["drift"]) + (cycle + slower) * 0.075 + shock
        next_close = max(10.0, close * (1.0 + daily_return))
        overnight = rng.gauss(0.0, 0.0035)
        open_price = max(1.0, close * (1.0 + overnight))
        spread = abs(rng.gauss(0.009, 0.0035))
        high = max(open_price, next_close) * (1.0 + spread)
        low = min(open_price, next_close) * max(0.80, 1.0 - spread * 0.92)
        volume_wave = 1.0 + 0.18 * math.sin(index / 9.0) + rng.gauss(0.0, 0.08)
        volume = max(100_000.0, float(cfg["volume"]) * max(0.45, volume_wave))
        rows.append(
            {
                "timestamp": day.isoformat(),
                "open": round(open_price, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "close": round(next_close, 4),
                "volume": round(volume, 2),
            }
        )
        close = next_close
    return rows


@lru_cache(maxsize=len(_DEMO_SYMBOLS))
def _demo_payload_sync(symbol: str) -> dict:
    cfg = _DEMO_SYMBOLS[symbol]
    bars = _build_bars(symbol)
    hist = browser_bars_to_history(bars, minimum_bars=320, maximum_bars=500)
    analysis_hist, indicators, setup, pattern_report, plan = _compute_scan_artifacts(hist, symbol, "v8")
    raw = _build_result(
        symbol,
        {"company_name": cfg["company"], "exchange": "DEMO"},
        "fixed_illustrative_demo",
        _period_to_timeframe("1y"),
        indicators,
        setup,
        plan,
        pattern_report,
        {"status": "demo_only", "reason": "Illustrative fixed dataset. Nothing is persisted."},
        analysis_hist,
    )
    raw["pattern_engine_mode"] = "v8"
    raw["response_cache"] = True

    payload = public_analysis_payload(
        raw,
        quota={"date_utc": None, "used": 0, "limit": 5, "remaining": 5, "resets_at": None},
        policy={
            "license_mode": "fixed_demo",
            "public_derived_analysis_enabled": True,
        },
    )
    payload["demo_mode"] = True
    payload["demo_notice"] = (
        "Illustrative fixed demo dataset. This is not live or historical vendor market data "
        "and must not be used for an investment decision."
    )
    payload["demo_snapshot_date"] = _DEMO_END_DATE.isoformat()
    payload["data_policy"]["demo_dataset"] = "fixed_synthetic_illustration"
    payload["data_policy"]["live_market_data"] = False
    assert_no_raw_market_data(payload)
    return payload


@router.get("/status")
async def demo_status():
    return {
        "enabled": True,
        "symbols": list(_DEMO_SYMBOLS),
        "snapshot_date": _DEMO_END_DATE.isoformat(),
        "notice": "Fixed illustrative dataset; no account, provider key, or live market data required.",
    }


@router.get("/scan/{ticker}")
async def demo_scan(ticker: str):
    symbol = str(ticker or "").upper().strip()
    if symbol not in _DEMO_SYMBOLS:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "DEMO_SYMBOL_NOT_AVAILABLE",
                "message": f"Demo mode supports only: {', '.join(_DEMO_SYMBOLS)}.",
            },
        )
    try:
        return await asyncio.to_thread(_demo_payload_sync, symbol)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "DEMO_ANALYSIS_FAILED",
                "message": f"Demo analysis could not be generated: {type(exc).__name__}.",
            },
        ) from exc
