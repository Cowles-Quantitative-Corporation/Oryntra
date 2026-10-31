from __future__ import annotations

from fastapi import APIRouter, Query

from ..database import get_pattern_stats, get_recent_pattern_events

router = APIRouter()


@router.get("/events")
async def pattern_events(ticker: str | None = None, limit: int = Query(50, ge=1, le=500)):
    return {
        "events": get_recent_pattern_events(ticker=ticker, limit=limit),
        "count": limit,
    }


@router.get("/stats")
async def pattern_stats(
    pattern_name: str | None = None,
    ticker: str | None = None,
    timeframe: str | None = None,
    limit: int = Query(100, ge=1, le=500),
):

    return {
        "stats": get_pattern_stats(pattern_name=pattern_name, ticker=ticker, timeframe=timeframe, limit=limit),
        "disclaimer": "Historical pattern stats are educational and are not financial advice.",
    }

