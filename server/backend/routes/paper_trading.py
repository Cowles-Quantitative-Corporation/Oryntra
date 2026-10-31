from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..database import get_connection
from .auth import require_current_user

router = APIRouter()


class OpenTradeRequest(BaseModel):
    ticker: str
    direction: str
    entry_price: float
    stop_price: float
    target_price: float
    size: float = 100
    notes: str = ""
    setup_type: Optional[str] = None
    quality_score: Optional[float] = None


class CloseTradeRequest(BaseModel):
    trade_id: int
    close_price: float
    notes: str = ""


async def _augment_trade_rows(rows) -> list[dict]:
    output: list[dict] = []
    for row in rows:
        trade = dict(row)
        status = str(trade.get("status") or "").upper()
        trade["current_price"] = float(trade["close_price"]) if status == "CLOSED" and trade.get("close_price") is not None else None
        trade["current_price_at"] = None
        trade["snapshot_source"] = "user_entered_simulation"
        trade["current_pnl"] = trade.get("pnl") if status == "CLOSED" else None
        trade["current_pnl_pct"] = trade.get("pnl_pct") if status == "CLOSED" else None
        if status == "CLOSED":
            final_pnl = trade.get("pnl")
            trade["success"] = None if final_pnl is None else float(final_pnl) > 0
            trade["success_label"] = "UNKNOWN" if final_pnl is None else ("YES" if trade["success"] else "NO")
        else:
            trade["success"] = None
            trade["success_label"] = "IN PROGRESS"
        output.append(trade)
    return output


@router.get("/trades")
async def get_open_trades(request: Request):
    user = require_current_user(request)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, ticker, direction, entry_price, stop_price, target_price, size, status, opened_at, closed_at, close_price, pnl, pnl_pct, substr(notes,1,240) AS notes, setup_type, quality_score FROM paper_trades WHERE user_id = ? AND status = 'OPEN' ORDER BY opened_at DESC LIMIT 100",
            (user["id"],),
        ).fetchall()
    finally:
        conn.close()
    return await _augment_trade_rows(rows)


@router.get("/trades/all")
async def get_all_trades(request: Request):
    user = require_current_user(request)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, ticker, direction, entry_price, stop_price, target_price, size, status, opened_at, closed_at, close_price, pnl, pnl_pct, substr(notes,1,240) AS notes, setup_type, quality_score FROM paper_trades WHERE user_id = ? ORDER BY opened_at DESC LIMIT 100",
            (user["id"],),
        ).fetchall()
    finally:
        conn.close()
    return await _augment_trade_rows(rows)


@router.post("/open")
async def open_trade(req: OpenTradeRequest, request: Request):
    ticker = req.ticker.upper().strip()
    direction = req.direction.upper().strip()
    if direction not in ("LONG", "SHORT"):
        raise HTTPException(status_code=400, detail="direction must be LONG or SHORT")
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker is required")

    user = require_current_user(request)
    safe_notes = (req.notes or "")[:240]
    conn = get_connection()
    try:
        cursor = conn.execute(
            """INSERT INTO paper_trades
               (user_id, ticker, direction, entry_price, stop_price, target_price, size, notes, setup_type, quality_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user["id"], ticker, direction, req.entry_price, req.stop_price,
                req.target_price, req.size, safe_notes, req.setup_type,
                req.quality_score,
            ),
        )
        trade_id = cursor.lastrowid
        conn.commit()
    finally:
        conn.close()
    return {"status": "opened", "trade_id": trade_id, "ticker": ticker}


@router.post("/close")
async def close_trade(req: CloseTradeRequest, request: Request):
    user = require_current_user(request)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM paper_trades WHERE id = ? AND user_id = ? AND status = 'OPEN'",
            (req.trade_id, user["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Open trade not found")
        trade = dict(row)
        entry_price = float(trade["entry_price"])
        size = float(trade["size"])
        if trade["direction"] == "LONG":
            pnl = (req.close_price - entry_price) * size
            pnl_pct = (req.close_price - entry_price) / entry_price * 100
        else:
            pnl = (entry_price - req.close_price) * size
            pnl_pct = (entry_price - req.close_price) / entry_price * 100
        conn.execute(
            """UPDATE paper_trades
               SET status='CLOSED', closed_at=datetime('now'), close_price=?, pnl=?, pnl_pct=?, notes=?
               WHERE id=? AND user_id=?""",
            (
                req.close_price, round(pnl, 2), round(pnl_pct, 2),
                (req.notes or trade["notes"] or "")[:240], req.trade_id,
                user["id"],
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "status": "closed",
        "trade_id": req.trade_id,
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "outcome": "WIN" if pnl > 0 else "LOSS",
    }


@router.delete("/trades/{trade_id}")
async def delete_trade(trade_id: int, request: Request):
    user = require_current_user(request)
    conn = get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM paper_trades WHERE id = ? AND user_id = ?",
            (trade_id, user["id"]),
        )
        if cursor.rowcount != 1:
            raise HTTPException(status_code=404, detail="Paper trade not found")
        conn.commit()
    finally:
        conn.close()
    return {"status": "deleted", "trade_id": trade_id}


@router.get("/stats")
async def get_paper_stats(request: Request):
    user = require_current_user(request)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT pnl, pnl_pct FROM paper_trades WHERE user_id = ? AND status = 'CLOSED'",
            (user["id"],),
        ).fetchall()
    finally:
        conn.close()
    trades = [dict(row) for row in rows]
    if not trades:
        return {"total_trades": 0, "win_rate": 0, "total_pnl": 0, "expectancy": 0}
    wins = [trade for trade in trades if (trade["pnl"] or 0) > 0]
    losses = [trade for trade in trades if (trade["pnl"] or 0) <= 0]
    total = len(trades)
    win_rate = len(wins) / total * 100
    total_pnl = sum(trade["pnl"] or 0 for trade in trades)
    avg_win = sum(trade["pnl"] for trade in wins) / len(wins) if wins else 0
    avg_loss = sum(trade["pnl"] for trade in losses) / len(losses) if losses else 0
    expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)
    return {
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "best_trade": max((trade["pnl"] or 0 for trade in trades), default=0),
        "worst_trade": min((trade["pnl"] or 0 for trade in trades), default=0),
    }
