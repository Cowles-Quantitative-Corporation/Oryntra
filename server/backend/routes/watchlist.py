from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from ..database import get_connection, init_db
from .auth import require_current_user

router = APIRouter()

class WatchlistItem(BaseModel):
    ticker: str
    notes: str = ""

@router.get("/")
async def get_watchlist(request: Request):
    init_db()
    user = require_current_user(request)
    conn = get_connection()
    try:
        rows = conn.execute("SELECT id, ticker, added_at, notes FROM user_watchlist WHERE user_id=? ORDER BY added_at DESC", (user["id"],)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

@router.post("/add")
async def add_to_watchlist(item: WatchlistItem, request: Request):
    init_db()
    ticker = item.ticker.upper().strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker is required.")
    user = require_current_user(request)
    conn = get_connection()
    try:
        conn.execute("INSERT OR REPLACE INTO user_watchlist (user_id, ticker, notes, added_at) VALUES (?, ?, ?, datetime('now'))", (user["id"], ticker, item.notes[:240]))
        conn.commit()
        return {"status": "ok", "ticker": ticker}
    finally:
        conn.close()

@router.delete("/{ticker}")
async def remove_from_watchlist(ticker: str, request: Request):
    ticker = ticker.upper().strip()
    user = require_current_user(request)
    conn = get_connection()
    try:
        conn.execute("DELETE FROM user_watchlist WHERE user_id=? AND ticker=?", (user["id"], ticker))
        conn.commit()
        return {"status": "ok", "ticker": ticker}
    finally:
        conn.close()

