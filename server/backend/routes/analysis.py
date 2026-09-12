import asyncio
import copy
import json
import math
import os
import time
import traceback
import weakref
from datetime import datetime
import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from ..fetcher import fetch_ticker_data
from ..indicators import calculate_all_indicators
from ..setup_detector import detect_setup
from ..trade_scorer import calculate_trade_plan
from ..database import get_connection, store_ohlcv_bars, increment_app_counter, get_app_counter
from ..patterns.outcome_tracker import persist_pattern_scan
from ..pattern_analyzer import normalize_pattern_engine_mode
from ..lab_grading import lab_based_stock_grade
from ..corporate_repository import get_corporate_repository

router = APIRouter()

_ANALYSIS_RESULT_TTL = max(0.0, float(os.getenv("ORYNTRA_ANALYSIS_RESULT_TTL", "90")))
_ANALYSIS_LOOKBACK_BARS = max(280, int(os.getenv("ORYNTRA_SCAN_LOOKBACK_BARS", "320")))
_PATTERN_LOOKBACK_BARS = max(90, int(os.getenv("ORYNTRA_PATTERN_LOOKBACK_BARS", "180")))
_ANALYSIS_BATCH_CONCURRENCY = max(1, min(int(os.getenv("ORYNTRA_SCAN_CONCURRENCY", "3")), 8))
_ANALYSIS_RESULT_CACHE: dict[tuple[str, str, str], tuple[float, dict]] = {}
_ANALYSIS_KEY_LOCKS: weakref.WeakValueDictionary = weakref.WeakValueDictionary()
_ANALYSIS_BACKGROUND_TASKS: set[asyncio.Task] = set()
_ANALYSIS_PERSIST_SEMAPHORE = asyncio.Semaphore(2)
_ANALYSIS_CACHE_MAX_ITEMS = max(16, int(os.getenv("ORYNTRA_ANALYSIS_CACHE_MAX_ITEMS", "256")))


def _start_analysis_task(coro):
    task = asyncio.create_task(coro)
    _ANALYSIS_BACKGROUND_TASKS.add(task)
    task.add_done_callback(_ANALYSIS_BACKGROUND_TASKS.discard)
    return task


def _cache_key(req: "ScanRequest") -> tuple[str, str, str]:
    return (req.ticker, req.period, req.pattern_mode)


def _cached_result(key: tuple[str, str, str]) -> dict | None:
    cached = _ANALYSIS_RESULT_CACHE.get(key)
    if not cached:
        return None
    if time.monotonic() - cached[0] > _ANALYSIS_RESULT_TTL:
        _ANALYSIS_RESULT_CACHE.pop(key, None)
        return None
    return copy.deepcopy(cached[1])

DEFAULT_SCREENER_TICKERS = [
    "AAPL", "MSFT", "NVDA", "TSLA", "META", "AMZN", "GOOGL", "AMD",
    "SPY", "QQQ", "JPM", "BAC", "XOM", "GLD", "BTC-USD",
]


class ScanRequest(BaseModel):
    ticker: str
    period: str = "6mo"
    pattern_mode: str = "official"

    @field_validator("ticker")
    @classmethod
    def clean_ticker(cls, v):
        return v.upper().strip()

    @field_validator("period")
    @classmethod
    def valid_period(cls, v):
        allowed = ["5m", "1mo", "6mo", "1y", "5y", "all"]
        if v not in allowed:
            raise ValueError(f"period must be one of {allowed}")
        return v

    @field_validator("pattern_mode")
    @classmethod
    def valid_pattern_mode(cls, v):
        return normalize_pattern_engine_mode(v)


class WatchlistScanRequest(BaseModel):
    tickers: list[str]
    period: str = "6mo"


class ScreenerRequest(BaseModel):
    tickers: list[str] = DEFAULT_SCREENER_TICKERS
    period: str = "6mo"
    min_score: float = 0
    signal_filter: str = ""
    setup_filter: str = ""


class CompareRequest(BaseModel):
    tickers: list[str]
    period: str = "6mo"


@router.post("/scan")
async def scan_ticker(req: ScanRequest):
    return await _run_scan_pipeline(req)


def _compute_scan_artifacts(hist, ticker: str, pattern_mode: str):
    analysis_hist = hist.tail(_ANALYSIS_LOOKBACK_BARS) if len(hist) > _ANALYSIS_LOOKBACK_BARS else hist
    ind = calculate_all_indicators(analysis_hist)
    ind["ticker"] = ticker
    pattern_hist = analysis_hist.tail(_PATTERN_LOOKBACK_BARS)
    setup = detect_setup(ind, analysis_hist if pattern_mode == "universal_v2" else pattern_hist, pattern_mode=pattern_mode)
    pattern_report = (setup.get("patterns") or {}).get("advanced_patterns", {})
    plan = calculate_trade_plan(ind, setup)
    return analysis_hist, ind, setup, pattern_report, plan


def browser_bars_to_history(bars: list[dict], minimum_bars: int = _ANALYSIS_LOOKBACK_BARS, maximum_bars: int = 2_000) -> pd.DataFrame:
    """Validate browser-supplied daily bars without persisting their raw values."""
    minimum_bars = max(2, int(minimum_bars))
    if not isinstance(bars, list) or len(bars) < minimum_bars:
        raise ValueError(f"Provide at least {minimum_bars} daily bars for this analysis.")
    if len(bars) > maximum_bars:
        raise ValueError(f"This upload may contain at most {maximum_bars:,} bars.")
    records: list[dict] = []
    seen: set[pd.Timestamp] = set()
    for item in bars:
        try:
            timestamp = pd.Timestamp(item.get("timestamp"), tz="UTC").tz_localize(None)
            values = {name: float(item.get(name)) for name in ("open", "high", "low", "close", "volume")}
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Each bar needs a valid timestamp, OHLC price, and volume.") from exc
        if pd.isna(timestamp) or timestamp in seen or not all(math.isfinite(value) for value in values.values()):
            raise ValueError("Bars must have unique timestamps and finite numeric values.")
        if values["open"] <= 0 or values["high"] <= 0 or values["low"] <= 0 or values["close"] <= 0 or values["volume"] < 0:
            raise ValueError("Prices must be positive and volume cannot be negative.")
        if values["low"] > min(values["open"], values["close"], values["high"]) or values["high"] < max(values["open"], values["close"], values["low"]):
            raise ValueError("Each bar must have low <= OHLC <= high.")
        seen.add(timestamp)
        records.append({"timestamp": timestamp, "Open": values["open"], "High": values["high"], "Low": values["low"], "Close": values["close"], "Volume": values["volume"]})
    frame = pd.DataFrame(records).set_index("timestamp").sort_index()
    if len(frame) < minimum_bars:
        raise ValueError(f"Provide at least {minimum_bars} unique daily bars for this analysis.")
    return frame


async def _persist_scan_side_effects(key, ticker, timeframe, hist, provider, pattern_report, result, should_store_bars):
    async with _ANALYSIS_PERSIST_SEMAPHORE:
        try:
            if should_store_bars:
                await asyncio.to_thread(store_ohlcv_bars, ticker, timeframe, hist, provider)
        except Exception as exc:
            print(f"[Oryntra] nonfatal ohlcv store failed for {ticker}: {exc}")
        try:
            persistence = await asyncio.to_thread(persist_pattern_scan, ticker, timeframe, hist, pattern_report)
            result.setdefault("patterns", {})["persistence"] = persistence
            cached = _ANALYSIS_RESULT_CACHE.get(key)
            if cached:
                cached_result = copy.deepcopy(cached[1])
                cached_result.setdefault("patterns", {})["persistence"] = persistence
                _ANALYSIS_RESULT_CACHE[key] = (cached[0], cached_result)
        except Exception as exc:
            print(f"[Oryntra] nonfatal pattern persistence failed for {ticker}: {exc}")
        await asyncio.to_thread(_cache_result, ticker, result)


async def _attach_counter(result: dict) -> dict:
    try:
        result["search_counter"] = await asyncio.to_thread(increment_app_counter, "stock_searches", 1)
    except Exception as exc:
        print(f"[Oryntra] nonfatal counter failed: {exc}")
        result["search_counter"] = None
    return result


async def _run_scan_pipeline(
    req: ScanRequest,
    *,
    provider_api_keys: dict[str, str] | None = None,
    allow_platform_provider_keys: bool = True,
) -> dict:
    key = _cache_key(req)
    cached = _cached_result(key)
    if cached is not None:
        cached["scanned_at"] = datetime.utcnow().isoformat()
        cached["response_cache"] = True
        return await _attach_counter(cached)

    lock = _ANALYSIS_KEY_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _cached_result(key)
        if cached is not None:
            cached["scanned_at"] = datetime.utcnow().isoformat()
            cached["response_cache"] = True
            return await _attach_counter(cached)
        try:
            data = await asyncio.to_thread(
                fetch_ticker_data,
                req.ticker,
                req.period,
                provider_api_keys=provider_api_keys,
                allow_platform_provider_keys=allow_platform_provider_keys,
            )
            hist = data["history"]
            info = data["info"]
            provider = data.get("provider", "unknown")
            timeframe = _period_to_timeframe(req.period)

            analysis_hist, ind, setup, pattern_report, plan = await asyncio.to_thread(
                _compute_scan_artifacts, hist, req.ticker, req.pattern_mode
            )
            persistence = {"pending": True}
            result = _build_result(
                req.ticker, info, provider, timeframe, ind, setup, plan,
                pattern_report, persistence, hist
            )
            result["pattern_engine_mode"] = req.pattern_mode
            result["response_cache"] = False
            await _attach_counter(result)


            if len(_ANALYSIS_RESULT_CACHE) >= _ANALYSIS_CACHE_MAX_ITEMS and key not in _ANALYSIS_RESULT_CACHE:
                oldest_key = min(_ANALYSIS_RESULT_CACHE, key=lambda item: _ANALYSIS_RESULT_CACHE[item][0])
                _ANALYSIS_RESULT_CACHE.pop(oldest_key, None)
            _ANALYSIS_RESULT_CACHE[key] = (time.monotonic(), copy.deepcopy(result))
            should_store_bars = not bool(data.get("from_cache"))
            _start_analysis_task(_persist_scan_side_effects(
                key, req.ticker, timeframe, analysis_hist, provider, pattern_report,
                copy.deepcopy(result), should_store_bars
            ))
            return result

        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except HTTPException:
            raise
        except Exception as exc:
            print("[Oryntra] analysis traceback:")
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Analysis error: {type(exc).__name__}: {exc}")
        finally:

            if not lock.locked():
                _ANALYSIS_KEY_LOCKS.pop(key, None)


async def _run_uploaded_scan_pipeline(req: ScanRequest, bars: list[dict], provider: str) -> dict:
    """Analyze browser-fetched bars in memory; raw bars are never cached or persisted."""
    try:
        hist = await asyncio.to_thread(browser_bars_to_history, bars)
        analysis_hist, ind, setup, pattern_report, plan = await asyncio.to_thread(
            _compute_scan_artifacts, hist, req.ticker, req.pattern_mode
        )
        result = _build_result(
            req.ticker,
            {"company_name": req.ticker, "exchange": ""},
            f"browser_{provider}",
            _period_to_timeframe(req.period),
            ind,
            setup,
            plan,
            pattern_report,
            {"status": "not_persisted", "reason": "Browser-supplied bars are analyzed in memory only."},
            analysis_hist,
        )
        result["pattern_engine_mode"] = req.pattern_mode
        result["response_cache"] = False
        return await _attach_counter(result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        print("[Oryntra] browser-upload analysis traceback:")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Analysis error: {type(exc).__name__}: {exc}") from exc


async def _scan_many(tickers: list[str], period: str, pattern_mode: str = "official"):
    semaphore = asyncio.Semaphore(_ANALYSIS_BATCH_CONCURRENCY)

    async def one(ticker: str):
        async with semaphore:
            try:
                return ticker, await _run_scan_pipeline(ScanRequest(ticker=ticker, period=period, pattern_mode=pattern_mode)), None
            except HTTPException as exc:
                return ticker, None, exc.detail

    return await asyncio.gather(*(one(ticker) for ticker in tickers))


@router.post("/scan-multiple")
async def scan_multiple(req: WatchlistScanRequest):
    rows = await _scan_many([str(t).upper().strip() for t in req.tickers[:20]], req.period)
    results = [result for _, result, error in rows if result is not None]
    errors = [{"ticker": ticker, "error": error} for ticker, result, error in rows if error]
    results.sort(key=lambda item: item.get("trade_plan", {}).get("quality_score", 0), reverse=True)
    return {"results": results, "errors": errors, "count": len(results), "scanned_at": datetime.utcnow().isoformat()}


@router.post("/screener")
async def screener(req: ScreenerRequest):
    tickers = [str(t).upper().strip() for t in req.tickers[:30]]
    rows = await _scan_many(tickers, req.period)
    results = []
    errors = []
    for ticker, result, error in rows:
        if error:
            errors.append({"ticker": ticker, "error": error})
            continue
        score = result.get("trade_plan", {}).get("quality_score", 0) or 0
        signal = result.get("trade_plan", {}).get("signal", "")
        setup = result.get("setup", {}).get("setup_type", "")
        if score < req.min_score or (req.signal_filter and signal != req.signal_filter) or (req.setup_filter and setup != req.setup_filter):
            continue
        results.append(result)
    results.sort(key=lambda item: item.get("trade_plan", {}).get("quality_score", 0), reverse=True)
    return {
        "results": results, "errors": errors, "count": len(results), "total_scanned": len(tickers),
        "filters_applied": {"min_score": req.min_score, "signal": req.signal_filter, "setup": req.setup_filter},
        "scanned_at": datetime.utcnow().isoformat(),
    }


@router.post("/compare")
async def compare_tickers(req: CompareRequest):
    if len(req.tickers) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 tickers to compare.")
    if len(req.tickers) > 4:
        raise HTTPException(status_code=400, detail="Maximum 4 tickers for comparison.")
    rows = await _scan_many([str(t).upper().strip() for t in req.tickers], req.period)
    results = [result for _, result, error in rows if result is not None]
    errors = [{"ticker": ticker, "error": error} for ticker, result, error in rows if error]
    winner = None
    if results:
        actionable = [r for r in results if r.get("trade_plan", {}).get("signal", "HOLD") != "HOLD"]
        winner = max(actionable or results, key=lambda r: r.get("trade_plan", {}).get("quality_score", 0))
    return {"results": results, "errors": errors, "winner": winner.get("ticker") if winner else None, "compared_at": datetime.utcnow().isoformat()}


@router.get("/stats")
async def get_analysis_stats():
    try:
        count = get_app_counter("stock_searches")
        lab_stock_analyses = get_app_counter("pattern_lab_stock_analyses")
        lab_engine_checks = get_app_counter("pattern_lab_engine_checks")
        return {
            "total_stock_searches": count,
            "pattern_lab_stock_analyses": lab_stock_analyses,
            "pattern_lab_engine_checks": lab_engine_checks,
            "counter_label": "Oryntra analyses completed",
            "ok": True,
        }
    except Exception as first_error:
        try:
            from ..database import init_db
            init_db()
            count = get_app_counter("stock_searches")
            lab_stock_analyses = get_app_counter("pattern_lab_stock_analyses")
            lab_engine_checks = get_app_counter("pattern_lab_engine_checks")
            return {
                "total_stock_searches": count,
                "pattern_lab_stock_analyses": lab_stock_analyses,
                "pattern_lab_engine_checks": lab_engine_checks,
                "counter_label": "Oryntra analyses completed",
                "ok": True,
                "recovered": True,
            }
        except Exception:
            return {"total_stock_searches": 0, "pattern_lab_stock_analyses": 0, "pattern_lab_engine_checks": 0, "ok": False, "error": str(first_error)}


@router.get("/history/{ticker}")
async def get_analysis_history(ticker: str, limit: int = 10):
    ticker = ticker.upper().strip()
    conn = get_connection()
    rows = conn.execute(
        """SELECT analyzed_at, data_json FROM analysis_cache
           WHERE ticker = ? ORDER BY analyzed_at DESC LIMIT ?""",
        (ticker, limit)
    ).fetchall()
    conn.close()
    return [{"analyzed_at": r["analyzed_at"], "data": json.loads(r["data_json"])} for r in rows]


def _price_history(hist, max_points: int = 140) -> list[dict]:
    if hist is None or len(hist) == 0:
        return []
    close_column = None
    for candidate in ("Close", "close", "Adj Close", "adj_close"):
        if candidate in hist.columns:
            close_column = candidate
            break
    if close_column is None:
        return []
    length = len(hist)
    step = max(1, (length + max_points - 1) // max_points)
    indexes = list(range(0, length, step))
    if indexes[-1] != length - 1:
        indexes.append(length - 1)
    points = []
    for index in indexes:
        row = hist.iloc[index]
        value = row.get(close_column)
        try:
            close = round(float(value), 4)
        except (TypeError, ValueError):
            continue
        timestamp = hist.index[index]
        if hasattr(timestamp, "isoformat"):
            timestamp = timestamp.isoformat()
        else:
            timestamp = str(timestamp)
        points.append({"time": timestamp, "close": close})
    return points

def _build_result(ticker, info, provider, timeframe, ind, setup, plan, pattern_report, persistence, hist) -> dict:
    corporate_snapshot = get_corporate_repository().latest_snapshot(ticker)
    corporate_facts = corporate_snapshot.get("facts", {})
    corporate_context = {
        "status": "available" if corporate_facts else "not_yet_loaded",
        "coverage": corporate_snapshot.get("coverage", 0.0),
        "as_of": corporate_snapshot.get("as_of"),
        "facts": [{"metric": metric, "value": item.get("value"), "units": item.get("units"), "available_at": item.get("available_at"), "source_class": item.get("source_class")} for metric, item in sorted(corporate_facts.items())],
        "note": "Public corporate facts provide structured context only. They do not alter the scanner's deterministic numeric score.",
    }
    return {
        "ticker": ticker,
        "company_name": info.get("company_name", ticker),
        "exchange": info.get("exchange", ""),
        "scanned_at": datetime.utcnow().isoformat(),
        "data_provider": provider,
        "timeframe": timeframe,
        "price": ind["price"],
        "price_history": _price_history(hist),
        "prev_close": ind["prev_close"],
        "day_change": ind["day_change"],
        "daily_range": {"high": ind["high_5d"], "low": ind["low_5d"]},
        "ma20": ind["ma20"], "ma50": ind["ma50"], "ma200": ind["ma200"],
        "ema9": ind["ema9"], "ema21": ind["ema21"], "ema50": ind.get("ema50"),
        "high_52w": ind["high_52w"], "low_52w": ind["low_52w"], "high_20d": ind["high_20d"], "low_20d": ind["low_20d"],
        "volume": {"current": ind["vol_current"], "avg_20d": ind["vol_ma20"], "ratio": ind["vol_ratio"], "trend": ind["vol_trend"]},
        "rsi14": ind["rsi14"], "rsi7": ind.get("rsi7"),
        "macd": {"line": ind["macd_line"], "signal": ind["macd_signal"], "hist": ind["macd_hist"], "cross": ind["macd_cross"]},
        "stochastic": {"k": ind["stoch_k"], "d": ind["stoch_d"], "signal": ind["stoch_signal"]},
        "bollinger": {"upper": ind["bb_upper"], "mid": ind["bb_mid"], "lower": ind["bb_lower"], "pct": ind["bb_pct"], "width": ind["bb_width"]},
        "atr14": ind["atr14"], "atr_pct": ind["atr_pct"],
        "adx": {"value": ind.get("adx14"), "di_plus": ind.get("di_plus"), "di_minus": ind.get("di_minus"), "signal": ind.get("adx_signal"), "trend": ind.get("adx_trend")},
        "williams_r": ind.get("williams_r"),
        "obv": {"value": ind.get("obv"), "signal": ind.get("obv_signal"), "trend": ind.get("obv_trend")},
        "vwap_20d": ind.get("vwap_20d"), "above_vwap": ind.get("above_vwap"),
        "ichimoku": {"tenkan": ind.get("ichi_tenkan"), "kijun": ind.get("ichi_kijun"), "senkou_a": ind.get("ichi_senkou_a"), "senkou_b": ind.get("ichi_senkou_b"), "signal": ind.get("ichi_signal")},
        "ema_cross": ind.get("ema_cross"),
        "volume_price_divergence": ind.get("volume_price_divergence"),
        "trend": ind["trend"], "trend_strength": ind["trend_strength"],
        "momentum": {"5d": ind["momentum_5d"], "20d": ind["momentum_20d"], "60d": ind["momentum_60d"]},
        "levels": {"pivot": ind["pivot"], "resist_1": ind["resist_1"], "resist_2": ind["resist_2"], "support_1": ind["support_1"], "support_2": ind["support_2"]},
        "setup": setup,
        "confluence": (setup.get("patterns") or {}).get("momentum_confirmation", {}),
        "patterns": {"summary": pattern_report.get("summary", {}), "recent": pattern_report.get("recent", []), "top_pattern": pattern_report.get("top_pattern"), "warnings": pattern_report.get("warnings", []), "persistence": persistence},
        "lab_based_grade": lab_based_stock_grade(ind, setup, pattern_report),
        "trade_plan": plan,
        "predictions": plan.get("predictions", {}),
        "corporate_context": corporate_context,
    }


def _period_to_timeframe(period: str) -> str:
    return "1d"


def _cache_result(ticker: str, result: dict):
    try:
        conn = get_connection()
        conn.execute("INSERT INTO analysis_cache (ticker, data_json) VALUES (?, ?)", (ticker, json.dumps(result, default=str)))
        conn.execute(
            "INSERT INTO scan_history (ticker, setup_type, score, price) VALUES (?, ?, ?, ?)",
            (ticker, result.get("setup", {}).get("setup_type"), result.get("trade_plan", {}).get("quality_score"), result.get("price"))
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[Oryntra] nonfatal cache failed: {exc}")
