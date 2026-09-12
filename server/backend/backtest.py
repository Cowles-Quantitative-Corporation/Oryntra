from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from typing import Any, Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter
from pydantic import BaseModel, Field

from .indicators import calculate_all_indicators
from .market_repository import get_market_repository, normalize_ticker
from .research_experiments import fingerprint, record_experiment
from .research_governance import scanner_research_governance
from .setup_detector import detect_setup
from .trade_scorer import calculate_trade_plan

router = APIRouter()


class BacktestRequest(BaseModel):
    ticker: str = ""
    tickers: list[str] = Field(default_factory=list)
    period: str = "2y"
    min_score: float = 55.0
    setups: list[str] = Field(default_factory=list)
    engine_mode: str = "official"
    data_source: str = "cache_first"
    min_history: int = 220
    max_hold_candles: int = 20
    commission_bps: float = 2.0
    slippage_bps: float = 4.0
    target_stop_policy: str = "stop_first"
    position_size_pct: float = 20.0
    max_concurrent_positions: int = 5
    bootstrap_samples: int = 500
    initial_equity: float = 10000.0


class BacktestResult(BaseModel):
    ticker: str
    period: str
    total_signals: int
    trades: list[dict]
    stats: dict


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except Exception:
        return default


def _clean_tickers(req: BacktestRequest) -> list[str]:
    values = list(req.tickers or [])
    if req.ticker:
        values.insert(0, req.ticker)
    output: list[str] = []
    for raw in values:
        ticker = normalize_ticker(raw)
        if ticker not in output:
            output.append(ticker)
    if not output:
        raise ValueError("Provide a ticker or tickers list.")
    return output


def _adjust_entry(open_price: float, direction: str, slippage_pct: float) -> float:
    if direction == "LONG":
        return open_price * (1.0 + slippage_pct / 100.0)
    return open_price * (1.0 - slippage_pct / 100.0)


def _adjust_exit(price: float, direction: str, slippage_pct: float) -> float:
    if direction == "LONG":
        return price * (1.0 - slippage_pct / 100.0)
    return price * (1.0 + slippage_pct / 100.0)


def _plan_levels(
    plan: dict[str, Any],
    *,
    direction: str,
    signal_price: float,
    entry_price: float,
) -> tuple[float, float] | None:
    raw_stop = _finite(plan.get("stop"))
    raw_target = _finite(plan.get("target"))
    if min(signal_price, entry_price, raw_stop, raw_target) <= 0:
        return None
    if direction == "LONG":
        stop_pct = max(0.1, (signal_price - raw_stop) / signal_price * 100.0)
        target_pct = max(0.1, (raw_target - signal_price) / signal_price * 100.0)
        return entry_price * (1.0 - stop_pct / 100.0), entry_price * (1.0 + target_pct / 100.0)
    stop_pct = max(0.1, (raw_stop - signal_price) / signal_price * 100.0)
    target_pct = max(0.1, (signal_price - raw_target) / signal_price * 100.0)
    return entry_price * (1.0 + stop_pct / 100.0), entry_price * (1.0 - target_pct / 100.0)


def _pnl_pct(trade: dict[str, Any], exit_price: float, commission_pct: float) -> float:
    entry = _finite(trade.get("entry"))
    if entry <= 0:
        return 0.0
    if trade.get("direction") == "LONG":
        gross = (exit_price - entry) / entry * 100.0
    else:
        gross = (entry - exit_price) / entry * 100.0
    return gross - 2 * commission_pct


def _check_exit(
    trade: dict[str, Any],
    *,
    row: pd.Series,
    date: Any,
    candle_idx: int,
    max_hold: int,
    slippage_pct: float,
    commission_pct: float,
    policy: str,
) -> Optional[dict[str, Any]]:
    direction = str(trade["direction"])
    stop = _finite(trade["stop"])
    target = _finite(trade["target"])
    high = _finite(row.get("High"))
    low = _finite(row.get("Low"))
    close = _finite(row.get("Close"))
    held = candle_idx - int(trade["candle_in"])
    if direction == "LONG":
        stop_hit = low <= stop
        target_hit = high >= target
    else:
        stop_hit = high >= stop
        target_hit = low <= target

    exit_reason: str | None = None
    raw_exit: float | None = None
    ambiguous = stop_hit and target_hit
    open_price = _finite(row.get("Open"), close)
    if direction == "LONG" and open_price <= stop or direction == "SHORT" and open_price >= stop:
        exit_reason, raw_exit = "STOP_GAP", open_price
    elif direction == "LONG" and open_price >= target or direction == "SHORT" and open_price <= target:
        exit_reason, raw_exit = "TARGET_GAP", target
    elif ambiguous:
        if policy == "target_first":
            exit_reason, raw_exit = "TARGET_HIT_AMBIGUOUS", target
        else:
            exit_reason, raw_exit = "STOP_HIT_AMBIGUOUS", stop
    elif stop_hit:
        exit_reason, raw_exit = "STOP_HIT", stop
    elif target_hit:
        exit_reason, raw_exit = "TARGET_HIT", target
    elif held >= max_hold:
        exit_reason, raw_exit = "TIME_EXIT", close

    if raw_exit is None:
        return None
    exit_price = _adjust_exit(raw_exit, direction, slippage_pct)
    pnl = _pnl_pct(trade, exit_price, commission_pct)
    result = {
        **trade,
        "date_out": pd.Timestamp(date).date().isoformat(),
        "exit_price": round(exit_price, 6),
        "raw_exit_price": round(raw_exit, 6),
        "exit_reason": exit_reason,
        "ambiguous_daily_bar": ambiguous,
        "pnl_pct": round(pnl, 6),
        "winner": pnl > 0,
        "candles_held": held,
    }
    return result


def _run_one(
    ticker: str,
    history: pd.DataFrame,
    req: BacktestRequest,
    *,
    source: str,
) -> dict[str, Any]:
    history = history.sort_index().copy()
    min_warmup = max(40, int(req.min_history))
    max_hold = max(1, int(req.max_hold_candles))
    commission_pct = max(0.0, float(req.commission_bps)) / 100.0
    slippage_pct = max(0.0, float(req.slippage_bps)) / 100.0
    min_score = max(0.0, float(req.min_score))
    setup_filter = {str(value).upper() for value in req.setups or []}
    trades: list[dict[str, Any]] = []
    open_trade: dict[str, Any] | None = None
    signal_count = 0

    for i in range(min_warmup - 1, len(history) - 1):
        if open_trade is not None:
            closed = _check_exit(
                open_trade,
                row=history.iloc[i],
                date=history.index[i],
                candle_idx=i,
                max_hold=max_hold,
                slippage_pct=slippage_pct,
                commission_pct=commission_pct,
                policy=req.target_stop_policy,
            )
            if closed is not None:
                trades.append(closed)
                open_trade = None
            continue

        signal_start = max(0, i + 1 - 280)
        signal_history = history.iloc[signal_start : i + 1]
        try:
            indicators = calculate_all_indicators(signal_history)
            indicators["ticker"] = ticker
            pattern_history = signal_history if req.engine_mode == "universal_v2" else signal_history.tail(180)
            setup = detect_setup(indicators, pattern_history, pattern_mode=req.engine_mode)
            plan = calculate_trade_plan(indicators, setup)
        except Exception:
            continue

        setup_type = str(setup.get("setup_type") or "NO_TRADE").upper()
        direction = str(plan.get("direction") or setup.get("direction") or "NEUTRAL").upper()
        score = _finite(plan.get("quality_score"), _finite(setup.get("confidence")))
        if setup_type == "NO_TRADE" or direction not in {"LONG", "SHORT"}:
            continue
        signal_count += 1
        if score < min_score:
            continue
        if setup_filter and setup_type not in setup_filter:
            continue

        next_open = _finite(history["Open"].iloc[i + 1])
        signal_price = _finite(history["Close"].iloc[i])
        if min(next_open, signal_price) <= 0:
            continue
        entry_price = _adjust_entry(next_open, direction, slippage_pct)
        levels = _plan_levels(
            plan,
            direction=direction,
            signal_price=signal_price,
            entry_price=entry_price,
        )
        if levels is None:
            continue
        stop, target = levels
        open_trade = {
            "ticker": ticker,
            "signal_date": pd.Timestamp(history.index[i]).date().isoformat(),
            "date_in": pd.Timestamp(history.index[i + 1]).date().isoformat(),
            "candle_in": i + 1,
            "direction": direction,
            "setup_type": setup_type,
            "quality": round(score, 4),
            "entry": round(entry_price, 6),
            "raw_open": round(next_open, 6),
            "stop": round(stop, 6),
            "target": round(target, 6),
            "risk_pct": round(abs(entry_price - stop) / entry_price * 100.0, 4),
            "rr_planned": round(abs(target - entry_price) / max(1e-12, abs(entry_price - stop)), 4),
            "source": source,
            "engine_mode": req.engine_mode,
        }

    if open_trade is not None:
        final_exit = _check_exit(open_trade, row=history.iloc[-1], date=history.index[-1],
                                 candle_idx=len(history) - 1, max_hold=max_hold,
                                 slippage_pct=slippage_pct, commission_pct=commission_pct,
                                 policy=req.target_stop_policy)
        if final_exit is not None:
            trades.append(final_exit)
            open_trade = None
    if open_trade is not None:
        last_close = _finite(history["Close"].iloc[-1])
        exit_price = _adjust_exit(last_close, open_trade["direction"], slippage_pct)
        pnl = _pnl_pct(open_trade, exit_price, commission_pct)
        trades.append(
            {
                **open_trade,
                "date_out": pd.Timestamp(history.index[-1]).date().isoformat(),
                "exit_price": round(exit_price, 6),
                "raw_exit_price": round(last_close, 6),
                "exit_reason": "END_OF_DATA",
                "ambiguous_daily_bar": False,
                "pnl_pct": round(pnl, 6),
                "winner": pnl > 0,
                "candles_held": len(history) - 1 - int(open_trade["candle_in"]),
            }
        )

    return {
        "ticker": ticker,
        "source": source,
        "candles": len(history),
        "candles_tested": max(0, len(history) - min_warmup),
        "signals_before_threshold": signal_count,
        "trades": trades,
    }


def _enforce_portfolio_capacity(trades: list[dict[str, Any]], max_concurrent: int) -> tuple[list[dict[str, Any]], int]:
    capacity = max(1, int(max_concurrent))
    accepted: list[dict[str, Any]] = []
    active_exit_dates: list[str] = []
    rejected = 0
    for trade in sorted(trades, key=lambda item: (str(item.get("date_in") or ""), str(item.get("ticker") or ""))):
        entry_date = str(trade.get("date_in") or "")
        active_exit_dates = [date for date in active_exit_dates if date >= entry_date]
        if len(active_exit_dates) >= capacity:
            rejected += 1
            continue
        accepted.append(trade)
        active_exit_dates.append(str(trade.get("date_out") or entry_date))
    return accepted, rejected


def _bootstrap_trade_ci(trades: list[dict[str, Any]], samples: int = 500, seed: int = 73021) -> dict[str, Any]:
    valid = [trade for trade in trades if trade.get("pnl_pct") is not None]
    if len(valid) < 8 or samples <= 0:
        return {"method": "ticker_cluster_bootstrap", "samples": 0, "expectancy_95_ci_pct": None, "win_rate_95_ci_pct": None}
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in valid:
        clusters[str(trade.get("ticker") or "UNKNOWN")].append(trade)
    names = sorted(clusters)
    rng = np.random.default_rng(seed)
    expectations: list[float] = []
    win_rates: list[float] = []
    for _ in range(min(5000, int(samples))):
        draw: list[dict[str, Any]] = []
        for selected in rng.choice(names, size=len(names), replace=True):
            draw.extend(clusters[str(selected)])
        returns = [_finite(item.get("pnl_pct")) for item in draw]
        expectations.append(float(np.mean(returns)) if returns else 0.0)
        win_rates.append(sum(value > 0 for value in returns) / len(returns) * 100.0 if returns else 0.0)
    return {
        "method": "ticker_cluster_bootstrap",
        "samples": len(expectations),
        "expectancy_95_ci_pct": [round(float(np.percentile(expectations, 2.5)), 4), round(float(np.percentile(expectations, 97.5)), 4)],
        "win_rate_95_ci_pct": [round(float(np.percentile(win_rates, 2.5)), 2), round(float(np.percentile(win_rates, 97.5)), 2)],
    }


def _calculate_stats(
    trades: list[dict[str, Any]],
    *,
    initial_equity: float,
    position_size_pct: float,
) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda trade: (str(trade.get("date_out") or ""), str(trade.get("ticker") or "")))
    returns = [_finite(trade.get("pnl_pct")) for trade in ordered]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    equity = max(1.0, float(initial_equity))
    peak = equity
    max_drawdown = 0.0
    exposure = min(100.0, max(0.0, float(position_size_pct))) / 100.0
    equity_curve: list[dict[str, Any]] = []
    for trade, value in zip(ordered, returns):
        equity *= 1.0 + (value / 100.0) * exposure
        peak = max(peak, equity)
        drawdown = (equity / peak - 1.0) * 100.0
        max_drawdown = min(max_drawdown, drawdown)
        equity_curve.append(
            {
                "date": trade.get("date_out"),
                "ticker": trade.get("ticker"),
                "equity": round(equity, 4),
                "drawdown_pct": round(drawdown, 4),
            }
        )
    total = len(ordered)
    win_rate = len(wins) / total * 100.0 if total else 0.0
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    expectancy = float(np.mean(returns)) if returns else 0.0
    std = float(np.std(returns)) if len(returns) > 1 else 0.0
    sharpe_like = expectancy / std * math.sqrt(max(1.0, 252.0 / 20.0)) if std > 1e-12 else 0.0
    setup_breakdown: dict[str, dict[str, Any]] = defaultdict(lambda: {"total": 0, "wins": 0, "return_sum": 0.0})
    ticker_breakdown: dict[str, dict[str, Any]] = defaultdict(lambda: {"total": 0, "wins": 0, "return_sum": 0.0})
    for trade in ordered:
        for key, bucket_name in (("setup_type", "setup"), ("ticker", "ticker")):
            bucket = setup_breakdown if bucket_name == "setup" else ticker_breakdown
            name = str(trade.get(key) or "UNKNOWN")
            bucket[name]["total"] += 1
            bucket[name]["wins"] += int(bool(trade.get("winner")))
            bucket[name]["return_sum"] += _finite(trade.get("pnl_pct"))
    for bucket in (setup_breakdown, ticker_breakdown):
        for values in bucket.values():
            values["win_rate"] = round(values["wins"] / values["total"] * 100.0, 2) if values["total"] else 0.0
            values["avg_return_pct"] = round(values.pop("return_sum") / values["total"], 4) if values["total"] else 0.0
    return {
        "total": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 2),
        "win_rate_pct": round(win_rate, 2),
        "avg_win_pct": round(avg_win, 4),
        "avg_loss_pct": round(avg_loss, 4),
        "expectancy": round(expectancy, 4),
        "expectancy_pct": round(expectancy, 4),
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss > 1e-12 else (999.0 if gross_profit > 0 else 0.0),
        "max_win_pct": round(max(wins, default=0.0), 4),
        "max_loss_pct": round(min(losses, default=0.0), 4),
        "avg_hold_candles": round(float(np.mean([_finite(t.get("candles_held")) for t in ordered])), 2) if ordered else 0.0,
        "cumulative_return_pct": round((equity / max(1.0, initial_equity) - 1.0) * 100.0, 4),
        "ending_equity": round(equity, 4),
        "max_drawdown_pct": round(max_drawdown, 4),
        "sharpe_like": round(sharpe_like, 4),
        "setup_breakdown": dict(setup_breakdown),
        "ticker_breakdown": dict(ticker_breakdown),
        "by_exit_reason": {
            reason: sum(1 for trade in ordered if trade.get("exit_reason") == reason)
            for reason in sorted({str(trade.get("exit_reason")) for trade in ordered})
        },
        "equity_curve": equity_curve,
    }


def _finalize_backtest(
    req: BacktestRequest,
    tickers: list[str],
    per_ticker: list[dict[str, Any]],
    errors: list[dict[str, str]],
    trades: list[dict[str, Any]],
    sources: dict[str, int],
) -> dict[str, Any]:
    trades, capacity_rejections = _enforce_portfolio_capacity(trades, req.max_concurrent_positions)
    stats = _calculate_stats(
        trades,
        initial_equity=max(1.0, req.initial_equity),
        position_size_pct=req.position_size_pct,
    )
    stats["bootstrap_confidence"] = _bootstrap_trade_ci(trades, req.bootstrap_samples)
    stats["capacity_rejections"] = capacity_rejections
    stats["yearly"] = {
        year: {
            "trades": len(group),
            "win_rate_pct": round(sum(bool(item.get("winner")) for item in group) / len(group) * 100.0, 2) if group else 0.0,
            "expectancy_pct": round(float(np.mean([_finite(item.get("pnl_pct")) for item in group])), 4) if group else 0.0,
        }
        for year, group in sorted({
            y: [item for item in trades if str(item.get("date_out") or "")[:4] == y]
            for y in sorted({str(item.get("date_out") or "")[:4] for item in trades if item.get("date_out")})
        }.items())
    }
    config = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    dataset_fp = fingerprint(
        {
            "tickers": tickers,
            "period": req.period,
            "trade_keys": [
                (trade.get("ticker"), trade.get("signal_date"), trade.get("date_in"), trade.get("date_out"))
                for trade in trades
            ],
            "config": config,
        }
    )
    experiment_id = record_experiment(
        experiment_type="backtest",
        status="done" if per_ticker else "failed",
        config={**config, "code_version": "backtest-v4"},
        dataset_fingerprint=dataset_fp,
        dataset_start=min((str(trade.get("signal_date")) for trade in trades), default=None),
        dataset_end=max((str(trade.get("date_out")) for trade in trades), default=None),
        symbols=tickers,
        sample_count=len(trades),
        metrics=stats,
        notes="Next-session execution with daily-bar conservative target/stop handling.",
    )
    status = "done" if per_ticker and not errors else ("partial" if per_ticker else "failed")
    return {
        "status": status,
        "tickers_requested": len(tickers),
        "tickers_completed": len(per_ticker),
        "ticker": tickers[0] if len(tickers) == 1 else "MULTI",
        "tickers": tickers,
        "period": req.period,
        "engine_mode": req.engine_mode,
        "data_source": req.data_source,
        "sources": dict(sources),
        "experiment_id": experiment_id,
        "dataset_fingerprint": dataset_fp,
        "candles_tested": sum(item["candles_tested"] for item in per_ticker),
        "total_signals": len(trades),
        "min_score_used": req.min_score,
        "trades": sorted(trades, key=lambda trade: (str(trade.get("date_in")), str(trade.get("ticker")))),
        "stats": stats,
        "per_ticker": per_ticker,
        "errors": errors,
        "methodology": {
            "causal_indicators": True,
            "entry": "next_session_open",
            "slippage_bps_each_side": req.slippage_bps,
            "commission_bps_round_trip": req.commission_bps,
            "target_stop_policy": req.target_stop_policy,
            "one_open_trade_per_ticker": True,
            "max_concurrent_positions": req.max_concurrent_positions,
            "portfolio_capacity_enforced": True,
            "ticker_cluster_bootstrap": True,
        },
        "warning": "Daily bars cannot determine exact intraday execution order when both target and stop trade during one session.",
    }
    # The backtest is a portfolio-aware evaluation of one scanner mode.  Preserve
    # the scanner's shared provenance contract alongside its trade assumptions.
    result["research_governance"] = scanner_research_governance(
        req.engine_mode, pd.DataFrame(columns=["Close", "Volume"])
    )
    result["research_governance"]["data_lineage"].update({
        "observations": result["candles_tested"],
        "symbols": tickers,
        "fingerprint": dataset_fp,
        "first_session": min((str(trade.get("signal_date")) for trade in trades), default=None),
        "last_session": max((str(trade.get("date_out")) for trade in trades), default=None),
    })
    result["research_governance"]["execution_scope"] = (
        "Next-session daily-bar simulation with configured commission and slippage. "
        "It does not model intraday order priority, order-book liquidity, fire sales, or executable fills."
    )
    result["research_governance"]["portfolio_scope"] = (
        "Cross-ticker backtest with a concurrent-position cap; it is not the multi-sleeve Quant Lab portfolio simulator."
    )
    return result


def _run_backtest_sync(req: BacktestRequest) -> dict[str, Any]:
    tickers = _clean_tickers(req)
    repository = get_market_repository()
    allow_api = str(req.data_source).lower() != "cache_only"
    per_ticker: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    trades: list[dict[str, Any]] = []
    sources: dict[str, int] = defaultdict(int)

    for ticker in tickers:
        try:
            result = repository.get_history(
                ticker,
                period=req.period,
                minimum_bars=max(40, int(req.min_history)) + 2,
                allow_api=allow_api,
            )
            sources[result.metadata.source] += 1
            run = _run_one(ticker, result.history, req, source=result.metadata.source)
            per_ticker.append({key: value for key, value in run.items() if key != "trades"})
            trades.extend(run["trades"])
        except Exception as exc:
            errors.append({"ticker": ticker, "error": str(exc)})

    return _finalize_backtest(req, tickers, per_ticker, errors, trades, sources)


def _run_backtest_from_histories_sync(
    req: BacktestRequest,
    histories: dict[str, pd.DataFrame],
    source: str,
) -> dict[str, Any]:
    """Run a validated, browser-supplied daily-bar backtest without provider credentials."""
    tickers = _clean_tickers(req)
    per_ticker: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    trades: list[dict[str, Any]] = []
    sources: dict[str, int] = defaultdict(int)

    for ticker in tickers:
        history = histories.get(ticker)
        if history is None:
            errors.append({"ticker": ticker, "error": "Browser data is missing for this symbol."})
            continue
        try:
            if len(history) < max(40, int(req.min_history)) + 2:
                raise ValueError(f"Need at least {max(40, int(req.min_history)) + 2} completed daily bars.")
            sources[source] += 1
            run = _run_one(ticker, history, req, source=source)
            per_ticker.append({key: value for key, value in run.items() if key != "trades"})
            trades.extend(run["trades"])
        except Exception as exc:
            errors.append({"ticker": ticker, "error": str(exc)})

    return _finalize_backtest(req, tickers, per_ticker, errors, trades, sources)


@router.post("/run")
async def run_backtest(req: BacktestRequest) -> dict[str, Any]:
    return await asyncio.to_thread(_run_backtest_sync, req)


async def run_backtest_from_histories(
    req: BacktestRequest,
    histories: dict[str, pd.DataFrame],
    source: str,
) -> dict[str, Any]:
    return await asyncio.to_thread(_run_backtest_from_histories_sync, req, histories, source)


if __name__ == "__main__":
    import asyncio
    import json
    import sys

    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    period = sys.argv[2] if len(sys.argv) > 2 else "2y"
    result = asyncio.run(run_backtest(BacktestRequest(ticker=ticker, period=period)))
    print(json.dumps({"stats": result["stats"], "errors": result["errors"]}, indent=2))
