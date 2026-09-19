"""Prospective Phase 4 shadow/paper ledger and automatic reconciliation.

The ledger is deliberately broker-agnostic.  It freezes a research manifest,
records completed-close decisions, creates next-session paper orders, records
simulated fills, marks paper positions, and explains deviations between the
approved target and the observed paper portfolio.  No function in this module
can submit a real order.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .database import get_connection
from .phase4_control import code_fingerprint, control_manifest
from .universal_engine import UniversalConfig, portfolio_targets
from .universal_research import run_universal


CANDIDATE_STATUSES = {"SHADOW", "PAPER", "PAUSED", "ARCHIVED"}
ORDER_STATUSES = {"PENDING", "FILLED", "PARTIAL", "CANCELLED"}


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _next_business_day(value: str | pd.Timestamp) -> str:
    return str((pd.Timestamp(value).normalize() + pd.offsets.BDay(1)).date())


def create_candidate(*, user_id: int, label: str, alpha_model: str, construction: str,
                     risk_model: str, phase3_enabled: bool, benchmark: str,
                     universe: Iterable[str], initial_nav: float = 1_000_000.0,
                     overrides: dict[str, Any] | None = None, status: str = "SHADOW") -> dict[str, Any]:
    clean_status = str(status).upper().strip()
    if clean_status not in CANDIDATE_STATUSES:
        raise ValueError("Unknown Phase 4 candidate status")
    symbols = sorted({str(symbol).upper().strip() for symbol in universe if str(symbol).strip()})
    if len(symbols) < 2:
        raise ValueError("Prospective candidates require at least two symbols")
    if not math.isfinite(float(initial_nav)) or float(initial_nav) < 100:
        raise ValueError("initial_nav must be at least 100")
    manifest = control_manifest(
        alpha_model=alpha_model, construction=construction, risk_model=risk_model,
        phase3_enabled=phase3_enabled, benchmark=benchmark, overrides=overrides,
    )
    manifest["universe"] = symbols
    manifest["initial_nav"] = float(initial_nav)
    manifest["code_fingerprint"] = code_fingerprint()
    conn = get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO phase4_candidates
               (user_id, label, alpha_model_id, construction_id, risk_model_id, phase3_enabled,
                benchmark_id, universe_json, manifest_json, config_fingerprint, code_fingerprint,
                initial_nav, status, frozen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(user_id), str(label).strip()[:120], alpha_model, construction, risk_model,
                1 if phase3_enabled else 0, benchmark, _json(symbols), _json(manifest),
                manifest["config_fingerprint"], manifest["code_fingerprint"], float(initial_nav),
                clean_status, _utc_now(),
            ),
        )
        candidate_id = int(cur.lastrowid)
        conn.commit()
        return get_candidate(candidate_id, user_id=user_id, conn=conn)
    finally:
        conn.close()


def get_candidate(candidate_id: int, *, user_id: int | None = None, conn=None) -> dict[str, Any]:
    own = conn is None
    conn = conn or get_connection()
    try:
        params: list[Any] = [int(candidate_id)]
        clause = "id=?"
        if user_id is not None:
            clause += " AND user_id=?"
            params.append(int(user_id))
        row = conn.execute(f"SELECT * FROM phase4_candidates WHERE {clause}", params).fetchone()
        if not row:
            raise ValueError("Phase 4 candidate not found")
        out = dict(row)
        out["phase3_enabled"] = bool(out["phase3_enabled"])
        out["universe"] = _loads(out.pop("universe_json"), [])
        out["manifest"] = _loads(out.pop("manifest_json"), {})
        return out
    finally:
        if own:
            conn.close()


def list_candidates(user_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT id FROM phase4_candidates WHERE user_id=? ORDER BY id DESC", (int(user_id),)).fetchall()
        return [get_candidate(int(row["id"]), user_id=user_id, conn=conn) for row in rows]
    finally:
        conn.close()


def set_candidate_status(candidate_id: int, user_id: int, status: str) -> dict[str, Any]:
    clean = str(status).upper().strip()
    if clean not in CANDIDATE_STATUSES:
        raise ValueError("Unknown Phase 4 candidate status")
    conn = get_connection()
    try:
        cur = conn.execute("UPDATE phase4_candidates SET status=? WHERE id=? AND user_id=?", (clean, int(candidate_id), int(user_id)))
        if cur.rowcount != 1:
            raise ValueError("Phase 4 candidate not found")
        conn.commit()
        return get_candidate(candidate_id, user_id=user_id, conn=conn)
    finally:
        conn.close()


def latest_position_weights(candidate_id: int, *, conn=None) -> dict[str, float]:
    own = conn is None
    conn = conn or get_connection()
    try:
        row = conn.execute("SELECT MAX(as_of_date) AS d FROM phase4_positions WHERE candidate_id=?", (int(candidate_id),)).fetchone()
        if not row or not row["d"]:
            return {}
        rows = conn.execute("SELECT symbol, weight FROM phase4_positions WHERE candidate_id=? AND as_of_date=?", (int(candidate_id), row["d"])).fetchall()
        return {str(item["symbol"]): float(item["weight"]) for item in rows}
    finally:
        if own:
            conn.close()


def latest_target_weights(candidate_id: int, *, conn=None) -> dict[str, float]:
    own = conn is None
    conn = conn or get_connection()
    try:
        row = conn.execute("SELECT approved_target_json FROM phase4_decisions WHERE candidate_id=? ORDER BY as_of_date DESC, id DESC LIMIT 1", (int(candidate_id),)).fetchone()
        return {k: float(v) for k, v in _loads(row["approved_target_json"], {}).items()} if row else {}
    finally:
        if own:
            conn.close()


def record_decision(*, candidate_id: int, user_id: int, as_of_date: str, dataset_fingerprint: str,
                    approved_weights: dict[str, float], reference_closes: dict[str, float],
                    nav_before: float, diagnostics: dict[str, Any] | None = None,
                    scheduled_for: str | None = None) -> dict[str, Any]:
    candidate = get_candidate(candidate_id, user_id=user_id)
    if candidate["status"] in {"PAUSED", "ARCHIVED"}:
        raise ValueError("Candidate is not active")
    weights = {str(k).upper(): max(0.0, float(v)) for k, v in approved_weights.items() if float(v) > 1e-12}
    if sum(weights.values()) > 1.000001:
        raise ValueError("Phase 4 remains long-only with gross exposure <= 1")
    closes = {str(k).upper(): float(v) for k, v in reference_closes.items()}
    if any(not math.isfinite(v) or v <= 0 for v in closes.values()):
        raise ValueError("Reference closes must be positive finite prices")
    conn = get_connection()
    try:
        prior = latest_target_weights(candidate_id, conn=conn)
        schedule = scheduled_for or _next_business_day(as_of_date)
        cur = conn.execute(
            """INSERT INTO phase4_decisions
               (candidate_id, as_of_date, dataset_fingerprint, config_fingerprint, nav_before,
                prior_target_json, approved_target_json, diagnostics_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (int(candidate_id), str(as_of_date)[:10], str(dataset_fingerprint), candidate["config_fingerprint"],
             float(nav_before), _json(prior), _json(weights), _json(diagnostics or {})),
        )
        decision_id = int(cur.lastrowid)
        symbols = sorted(set(prior) | set(weights))
        orders: list[dict[str, Any]] = []
        for symbol in symbols:
            before, target = float(prior.get(symbol, 0.0)), float(weights.get(symbol, 0.0))
            delta = target - before
            if abs(delta) <= 1e-8:
                continue
            ref = closes.get(symbol)
            if ref is None:
                raise ValueError(f"Missing reference close for {symbol}")
            notional = abs(delta) * float(nav_before)
            shares = notional / ref
            side = "BUY" if delta > 0 else "SELL"
            order_cur = conn.execute(
                """INSERT INTO phase4_orders
                   (decision_id, symbol, side, prior_weight, target_weight, weight_delta,
                    reference_close, intended_notional, intended_shares, scheduled_for, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')""",
                (decision_id, symbol, side, before, target, delta, ref, notional, shares, schedule),
            )
            orders.append({
                "id": int(order_cur.lastrowid), "symbol": symbol, "side": side,
                "prior_weight": before, "target_weight": target, "weight_delta": delta,
                "reference_close": ref, "intended_notional": notional,
                "intended_shares": shares, "scheduled_for": schedule, "status": "PENDING",
            })
        conn.execute("UPDATE phase4_candidates SET last_cycle_at=? WHERE id=?", (_utc_now(), int(candidate_id)))
        conn.commit()
        return {
            "decision_id": decision_id, "candidate_id": int(candidate_id), "as_of_date": str(as_of_date)[:10],
            "approved_weights": weights, "prior_weights": prior, "orders": orders,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def pending_orders(candidate_id: int, *, through_date: str | None = None) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        params: list[Any] = [int(candidate_id)]
        date_clause = ""
        if through_date:
            date_clause = " AND o.scheduled_for <= ?"
            params.append(str(through_date)[:10])
        rows = conn.execute(
            f"""SELECT o.*, d.candidate_id FROM phase4_orders o
                  JOIN phase4_decisions d ON d.id=o.decision_id
                 WHERE d.candidate_id=? AND o.status IN ('PENDING','PARTIAL'){date_clause}
                 ORDER BY o.scheduled_for, o.id""", params,
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def record_simulated_fill(*, order_id: int, fill_date: str, fill_price: float,
                          filled_shares: float | None = None, fees: float = 0.0,
                          source: str = "next_open_simulated") -> dict[str, Any]:
    if not math.isfinite(float(fill_price)) or float(fill_price) <= 0:
        raise ValueError("fill_price must be positive")
    conn = get_connection()
    try:
        order = conn.execute("SELECT * FROM phase4_orders WHERE id=?", (int(order_id),)).fetchone()
        if not order:
            raise ValueError("Order not found")
        fill_day = str(fill_date)[:10]
        if fill_day < str(order["scheduled_for"])[:10]:
            raise ValueError("Fill date cannot precede the order's scheduled next-open date")
        candidate_row = conn.execute(
            """SELECT d.candidate_id FROM phase4_decisions d
                 JOIN phase4_orders o ON o.decision_id=d.id WHERE o.id=?""",
            (int(order_id),),
        ).fetchone()
        latest_mark = conn.execute(
            "SELECT MAX(as_of_date) AS d FROM phase4_nav WHERE candidate_id=?",
            (int(candidate_row["candidate_id"]),),
        ).fetchone()["d"] if candidate_row else None
        if latest_mark and fill_day <= str(latest_mark)[:10]:
            raise ValueError("Cannot backdate a fill into an already-frozen portfolio mark")
        already = conn.execute("SELECT COALESCE(SUM(filled_shares),0) AS n FROM phase4_fills WHERE order_id=?", (int(order_id),)).fetchone()["n"]
        remaining = max(0.0, float(order["intended_shares"]) - float(already or 0.0))
        quantity = remaining if filled_shares is None else min(remaining, max(0.0, float(filled_shares)))
        if quantity <= 1e-12:
            raise ValueError("Order has no remaining quantity")
        ref = float(order["reference_close"])
        sign = 1.0 if order["side"] == "BUY" else -1.0
        slippage_bps = sign * (float(fill_price) / ref - 1.0) * 10_000.0
        conn.execute(
            """INSERT INTO phase4_fills
               (order_id, fill_date, fill_price, filled_shares, fees, slippage_bps, fill_source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (int(order_id), str(fill_date)[:10], float(fill_price), quantity, float(fees), slippage_bps, str(source)[:80]),
        )
        total = float(already or 0.0) + quantity
        status = "FILLED" if total >= float(order["intended_shares"]) - 1e-8 else "PARTIAL"
        conn.execute("UPDATE phase4_orders SET status=? WHERE id=?", (status, int(order_id)))
        conn.commit()
        return {"order_id": int(order_id), "status": status, "filled_shares": quantity, "fill_price": float(fill_price), "slippage_bps": slippage_bps}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _shares_before(candidate_id: int, as_of_date: str, conn) -> tuple[dict[str, float], float]:
    row = conn.execute(
        "SELECT MAX(as_of_date) AS d FROM phase4_positions WHERE candidate_id=? AND as_of_date < ?",
        (int(candidate_id), str(as_of_date)[:10]),
    ).fetchone()
    shares: dict[str, float] = {}
    cash_row = conn.execute(
        "SELECT cash FROM phase4_nav WHERE candidate_id=? AND as_of_date < ? ORDER BY as_of_date DESC LIMIT 1",
        (int(candidate_id), str(as_of_date)[:10]),
    ).fetchone()
    candidate = conn.execute("SELECT initial_nav FROM phase4_candidates WHERE id=?", (int(candidate_id),)).fetchone()
    cash = float(cash_row["cash"]) if cash_row else float(candidate["initial_nav"])
    if row and row["d"]:
        for item in conn.execute("SELECT symbol, shares FROM phase4_positions WHERE candidate_id=? AND as_of_date=?", (int(candidate_id), row["d"])).fetchall():
            shares[str(item["symbol"])] = float(item["shares"])
    return shares, cash


def mark_portfolio(*, candidate_id: int, as_of_date: str, close_prices: dict[str, float],
                   benchmark_return: float | None = None) -> dict[str, Any]:
    """Apply fills through ``as_of_date`` and persist one immutable paper mark."""
    closes = {str(k).upper(): float(v) for k, v in close_prices.items() if math.isfinite(float(v)) and float(v) > 0}
    conn = get_connection()
    try:
        existing_nav = conn.execute(
            "SELECT * FROM phase4_nav WHERE candidate_id=? AND as_of_date=?",
            (int(candidate_id), str(as_of_date)[:10]),
        ).fetchone()
        if existing_nav:
            rows = conn.execute(
                "SELECT symbol,weight FROM phase4_positions WHERE candidate_id=? AND as_of_date=?",
                (int(candidate_id), str(as_of_date)[:10]),
            ).fetchall()
            return {
                "candidate_id": int(candidate_id),
                "as_of_date": str(as_of_date)[:10],
                "nav": float(existing_nav["nav"]),
                "cash": float(existing_nav["cash"]),
                "gross_exposure": float(existing_nav["gross_exposure"]),
                "net_return": float(existing_nav["net_return"]),
                "weights": {str(row["symbol"]): float(row["weight"]) for row in rows},
                "immutable_existing": True,
            }
        shares, cash = _shares_before(candidate_id, as_of_date, conn)
        # Apply only fills not already represented by an earlier mark.
        previous = conn.execute("SELECT MAX(as_of_date) AS d FROM phase4_nav WHERE candidate_id=? AND as_of_date < ?", (int(candidate_id), str(as_of_date)[:10])).fetchone()["d"]
        clauses = ["d.candidate_id=?", "f.fill_date<=?"]
        params: list[Any] = [int(candidate_id), str(as_of_date)[:10]]
        if previous:
            clauses.append("f.fill_date>?")
            params.append(str(previous)[:10])
        fills = conn.execute(
            f"""SELECT f.*, o.symbol, o.side FROM phase4_fills f
                  JOIN phase4_orders o ON o.id=f.order_id
                  JOIN phase4_decisions d ON d.id=o.decision_id
                 WHERE {' AND '.join(clauses)} ORDER BY f.fill_date, f.id""", params,
        ).fetchall()
        for fill in fills:
            symbol = str(fill["symbol"])
            qty = float(fill["filled_shares"])
            px = float(fill["fill_price"])
            fee = float(fill["fees"] or 0.0)
            if fill["side"] == "BUY":
                shares[symbol] = shares.get(symbol, 0.0) + qty
                cash -= qty * px + fee
            else:
                shares[symbol] = shares.get(symbol, 0.0) - qty
                if shares[symbol] < 1e-8:
                    shares[symbol] = 0.0
                cash += qty * px - fee
        values = {symbol: qty * closes[symbol] for symbol, qty in shares.items() if qty > 1e-12 and symbol in closes}
        nav = cash + sum(values.values())
        if nav <= 0:
            raise ValueError("Paper portfolio NAV is nonpositive")
        gross = sum(values.values()) / nav
        prior_nav_row = conn.execute("SELECT nav FROM phase4_nav WHERE candidate_id=? AND as_of_date < ? ORDER BY as_of_date DESC LIMIT 1", (int(candidate_id), str(as_of_date)[:10])).fetchone()
        net_return = nav / float(prior_nav_row["nav"]) - 1.0 if prior_nav_row else 0.0
        conn.execute("DELETE FROM phase4_positions WHERE candidate_id=? AND as_of_date=?", (int(candidate_id), str(as_of_date)[:10]))
        for symbol, market_value in values.items():
            conn.execute(
                """INSERT INTO phase4_positions(candidate_id, as_of_date, symbol, shares, mark_price, market_value, weight)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (int(candidate_id), str(as_of_date)[:10], symbol, shares[symbol], closes[symbol], market_value, market_value / nav),
            )
        conn.execute(
            """INSERT INTO phase4_nav(candidate_id, as_of_date, nav, cash, gross_exposure, net_return, benchmark_return)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (int(candidate_id), str(as_of_date)[:10], nav, cash, gross, net_return, benchmark_return),
        )
        conn.commit()
        return {"candidate_id": int(candidate_id), "as_of_date": str(as_of_date)[:10], "nav": nav, "cash": cash, "gross_exposure": gross, "net_return": net_return, "weights": {s: v / nav for s, v in values.items()}}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reconcile_latest(*, candidate_id: int, tolerance: float = 0.0025) -> dict[str, Any]:
    conn = get_connection()
    try:
        decision = conn.execute("SELECT * FROM phase4_decisions WHERE candidate_id=? ORDER BY as_of_date DESC,id DESC LIMIT 1", (int(candidate_id),)).fetchone()
        if not decision:
            return {"candidate_id": int(candidate_id), "status": "no_decision", "rows": []}
        nav = conn.execute("SELECT * FROM phase4_nav WHERE candidate_id=? ORDER BY as_of_date DESC LIMIT 1", (int(candidate_id),)).fetchone()
        if not nav:
            return {"candidate_id": int(candidate_id), "status": "awaiting_mark", "decision_id": int(decision["id"]), "rows": []}
        existing_rows = conn.execute(
            """SELECT symbol,approved_weight,actual_weight,weight_error,status,reason_json
                 FROM phase4_reconciliations
                WHERE candidate_id=? AND decision_id=? AND as_of_date=? ORDER BY symbol""",
            (int(candidate_id), int(decision["id"]), nav["as_of_date"]),
        ).fetchall()
        if existing_rows:
            rows = [
                {
                    "symbol": str(row["symbol"]),
                    "approved_weight": float(row["approved_weight"]),
                    "actual_weight": float(row["actual_weight"]),
                    "weight_error": float(row["weight_error"]),
                    "status": str(row["status"]),
                    **_loads(row["reason_json"], {}),
                }
                for row in existing_rows
            ]
            return {
                "candidate_id": int(candidate_id),
                "decision_id": int(decision["id"]),
                "decision_as_of": decision["as_of_date"],
                "portfolio_as_of": nav["as_of_date"],
                "status": "matched" if all(row["status"] == "MATCH" for row in rows) else "differences_explained",
                "rows": rows,
                "immutable_existing": True,
            }
        positions = conn.execute("SELECT symbol,weight FROM phase4_positions WHERE candidate_id=? AND as_of_date=?", (int(candidate_id), nav["as_of_date"])).fetchall()
        actual = {str(row["symbol"]): float(row["weight"]) for row in positions}
        approved = {k: float(v) for k, v in _loads(decision["approved_target_json"], {}).items()}
        rows: list[dict[str, Any]] = []
        for symbol in sorted(set(approved) | set(actual)):
            intended = float(approved.get(symbol, 0.0))
            observed = float(actual.get(symbol, 0.0))
            error = observed - intended
            orders = conn.execute("SELECT id,status FROM phase4_orders WHERE decision_id=? AND symbol=?", (int(decision["id"]), symbol)).fetchall()
            reasons: list[str] = []
            if any(row["status"] == "PENDING" for row in orders):
                reasons.append("unfilled_order")
            if any(row["status"] == "PARTIAL" for row in orders):
                reasons.append("partial_fill")
            if not reasons and abs(error) > tolerance:
                reasons.append("market_move_cash_or_execution_drift")
            if abs(error) <= tolerance:
                reasons.append("within_tolerance")
            status = "MATCH" if abs(error) <= tolerance else "EXPLAIN"
            reason = {"reasons": reasons, "tolerance": tolerance}
            conn.execute(
                """INSERT INTO phase4_reconciliations
                   (candidate_id, decision_id, as_of_date, symbol, intended_weight, approved_weight,
                    actual_weight, weight_error, reason_json, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (int(candidate_id), int(decision["id"]), nav["as_of_date"], symbol, intended, intended, observed, error, _json(reason), status),
            )
            rows.append({"symbol": symbol, "approved_weight": intended, "actual_weight": observed, "weight_error": error, "status": status, **reason})
        conn.commit()
        return {
            "candidate_id": int(candidate_id), "decision_id": int(decision["id"]),
            "decision_as_of": decision["as_of_date"], "portfolio_as_of": nav["as_of_date"],
            "status": "matched" if all(row["status"] == "MATCH" for row in rows) else "differences_explained",
            "rows": rows,
        }
    finally:
        conn.close()


def run_completed_close_decision(*, candidate_id: int, user_id: int, histories: dict[str, pd.DataFrame],
                                 benchmark_returns: pd.Series | None = None,
                                 risk_free: pd.Series | None = None,
                                 factor_quality_scores: pd.DataFrame | None = None) -> dict[str, Any]:
    """Run the frozen candidate on completed bars and record the next-open intent."""
    candidate = get_candidate(candidate_id, user_id=user_id)
    # Prospective evidence is valid only while the frozen code fingerprint is
    # unchanged.  A new code version requires a new prospective candidate.
    current_code = code_fingerprint()
    if current_code != str(candidate.get("code_fingerprint") or ""):
        raise ValueError(
            "Frozen candidate code fingerprint differs from the current research engine; "
            "freeze a new candidate before recording more prospective evidence."
        )
    manifest = candidate["manifest"]
    configuration = UniversalConfig(**manifest["configuration"])
    if configuration.fingerprint != str(candidate.get("config_fingerprint") or ""):
        raise ValueError("Frozen candidate configuration fingerprint mismatch")
    report = run_universal(histories, configuration, benchmark_returns, risk_free,
                           factor_quality_scores=factor_quality_scores)
    common = None
    for frame in histories.values():
        common = frame.index if common is None else common.intersection(frame.index)
    if common is None or len(common) < 2:
        raise ValueError("Prospective decision requires aligned completed histories")
    common = common.sort_values()
    prices = pd.DataFrame({symbol: histories[symbol].reindex(common)["Close"] for symbol in candidate["universe"]}, index=common)
    opens = pd.DataFrame({symbol: histories[symbol].reindex(common)["Open"] for symbol in candidate["universe"]}, index=common)
    highs = pd.DataFrame({symbol: histories[symbol].reindex(common)["High"] for symbol in candidate["universe"]}, index=common)
    lows = pd.DataFrame({symbol: histories[symbol].reindex(common)["Low"] for symbol in candidate["universe"]}, index=common)
    volumes = pd.DataFrame({symbol: histories[symbol].reindex(common)["Volume"] for symbol in candidate["universe"]}, index=common)
    target = portfolio_targets(prices, configuration, benchmark_returns=benchmark_returns, opens=opens, volumes=volumes,
                               learning_highs=highs, learning_lows=lows,
                               factor_quality_scores=factor_quality_scores,
                               alpha_quality_scores=factor_quality_scores)
    latest = {str(k): float(v) for k, v in target.iloc[-1].items() if float(v) > 1e-12}
    as_of = str(common[-1].date())
    closes = {symbol: float(prices.loc[common[-1], symbol]) for symbol in candidate["universe"]}
    conn = get_connection()
    try:
        nav_row = conn.execute("SELECT nav FROM phase4_nav WHERE candidate_id=? ORDER BY as_of_date DESC LIMIT 1", (int(candidate_id),)).fetchone()
        nav_before = float(nav_row["nav"]) if nav_row else float(candidate["initial_nav"])
    finally:
        conn.close()
    diagnostics = {
        "engine": report.get("engine"), "engine_version": report.get("engine_version"),
        "risk_supervisor": report.get("risk_supervisor", {}),
        "factor_model": report.get("factor_model", {}),
        "portfolio_optimizer": report.get("portfolio_optimizer", {}),
        "phase3": report.get("phase3", {}),
    }
    decision = record_decision(
        candidate_id=candidate_id, user_id=user_id, as_of_date=as_of,
        dataset_fingerprint=report["dataset_fingerprint"], approved_weights=latest,
        reference_closes=closes, nav_before=nav_before, diagnostics=diagnostics,
    )
    return {"report": report, "decision": decision}
