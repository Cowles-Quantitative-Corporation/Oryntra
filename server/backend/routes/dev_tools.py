from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..database import (
    get_ohlcv_cache_size_bytes,
    get_ohlcv_cache_summary,
    get_recent_vai_training_runs,
    store_ohlcv_bars,
    store_vai_training_run,
)
from ..documented_beta_counts import sync_documented_beta_counts
from ..fetcher import fetch_ticker_data
from ..pattern_lab import (
    cached_tickers as pattern_lab_cached_tickers,
    resolve_tickers as resolve_pattern_lab_tickers,
    run_pattern_lab,
)
from ..pattern_lab_jobs import (
    checkpoint_path as pattern_lab_checkpoint_path,
    launch_worker as launch_pattern_lab_worker,
    list_jobs as list_pattern_lab_jobs,
    read_request as read_pattern_lab_request,
    request_stop as request_pattern_lab_stop,
    status_with_result as pattern_lab_status_with_result,
    write_request as write_pattern_lab_request,
    write_status as write_pattern_lab_status,
)
from ..research_training import train_vai2_research
from ..research_universe import universe_metadata
from ..vai2_model import get_vai2_model_status
from ..vai_model import get_vai_model_status, train_vai_from_lab_rows

router = APIRouter()

TRAINING_TICKERS_150 = ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMZN', 'META', 'GOOGL', 'AMD', 'AVGO', 'JPM', 'V', 'XOM', 'CVX', 'UNH', 'LLY', 'JNJ', 'WMT', 'COST', 'HD', 'MCD', 'NKE', 'CAT', 'BA', 'RTX', 'NEE', 'PLTR', 'CRWD', 'SPY', 'QQQ', 'SMH', 'ORCL', 'NFLX', 'CRM', 'ADBE', 'INTC', 'MU', 'QCOM', 'TXN', 'AMAT', 'LRCX', 'KLAC', 'MRVL', 'NOW', 'SNOW', 'DDOG', 'NET', 'PANW', 'ZS', 'MDB', 'SHOP', 'UBER', 'ABNB', 'DASH', 'PYPL', 'COIN', 'HOOD', 'SOFI', 'SQ', 'MSTR', 'DELL', 'GS', 'MS', 'BAC', 'C', 'WFC', 'AXP', 'BLK', 'SCHW', 'COF', 'MA', 'BRK.B', 'PGR', 'TRV', 'AIG', 'USB', 'PNC', 'TFC', 'BK', 'ICE', 'CME', 'ABBV', 'MRK', 'PFE', 'TMO', 'DHR', 'ABT', 'ISRG', 'SYK', 'MDT', 'GILD', 'AMGN', 'REGN', 'VRTX', 'BMY', 'CVS', 'HUM', 'CI', 'ELV', 'ZBH', 'BSX', 'LOW', 'SBUX', 'TGT', 'TJX', 'ROST', 'LULU', 'CMG', 'YUM', 'KO', 'PEP', 'PG', 'CL', 'KMB', 'MDLZ', 'CAG', 'GIS', 'KR', 'DG', 'DLTR', 'EL', 'DE', 'GE', 'HON', 'UPS', 'FDX', 'LMT', 'NOC', 'GD', 'ETN', 'EMR', 'MMM', 'URI', 'CSX', 'NSC', 'UNP', 'DAL', 'UAL', 'AAL', 'LUV', 'RCL', 'COP', 'SLB', 'EOG', 'MPC', 'PSX', 'OXY', 'KMI', 'WMB', 'HAL', 'BKR', 'DUK', 'SO', 'AEP', 'EXC', 'SRE', 'XEL', 'D', 'PEG', 'ED', 'AWK']
DEFAULT_PATTERN_LAB_TICKERS = ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMZN', 'META', 'GOOGL', 'AMD', 'AVGO', 'JPM', 'V', 'XOM', 'CVX', 'UNH', 'LLY', 'JNJ', 'WMT', 'COST', 'HD', 'MCD', 'NKE', 'CAT', 'BA', 'RTX', 'NEE', 'PLTR', 'CRWD', 'SPY', 'QQQ', 'SMH']

CACHE_WARM_JOBS: dict[str, dict[str, Any]] = {}
PATTERN_LAB_JOBS: dict[str, dict[str, Any]] = {}
VAI_TRAIN_JOBS: dict[str, dict[str, Any]] = {}
BACKGROUND_TASKS: set[asyncio.Task] = set()


def _start_background_task(coroutine: Any) -> asyncio.Task:
    task = asyncio.create_task(coroutine)
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(BACKGROUND_TASKS.discard)
    return task


class PatternLabRequest(BaseModel):
    tickers: list[str] = Field(default_factory=lambda: DEFAULT_PATTERN_LAB_TICKERS.copy())
    period: str = "all"
    horizon_days: int = 10
    step: int = 5
    min_history: int = 220
    max_tests_per_ticker: int = 50
    data_source: str = "cache_only"
    engine_modes: list[str] = Field(default_factory=lambda: ["official", "v8"])
    universe_mode: str = "manual"
    universe_size: int = 150
    random_seed: int = 73021
    sampling_mode: str = "even"
    random_window_bars: int = 180
    start_date: str = ""
    end_date: str = ""
    transaction_cost_bps: float = 6.0
    slippage_bps: float = 4.0
    api_delay_seconds: float = 13.0
    minimum_confidence: float = 20.0
    target_pct: float = 4.0
    stop_pct: float = 2.5
    ambiguity_policy: str = "stop_first"
    walk_forward_folds: int = 5
    bootstrap_samples: int = 500
    include_rows: bool = True
    max_returned_rows: int = 250000
    lookback_bars: int = 280
    pattern_lookback_bars: int = 180
    resume_from_job_id: str = ""


class VAITrainRequest(BaseModel):
    tickers: list[str] = Field(default_factory=lambda: DEFAULT_PATTERN_LAB_TICKERS.copy())
    period: str = "5y"
    horizon_days: int = 10
    step: int = 8
    min_history: int = 90
    max_tests_per_ticker: int = 30
    data_source: str = "cache_only"
    min_samples: int = 80
    model_version: str = "vai2"
    force_promote: bool = False


class CacheWarmStartRequest(BaseModel):
    tickers: list[str] = Field(default_factory=lambda: DEFAULT_PATTERN_LAB_TICKERS.copy())
    period: str = "5y"
    delay_seconds: float = 13.0
    max_cache_gb: float = 10.0
    stop_on_rate_limit: bool = False


def _clean_tickers(tickers: list[str], max_count: int = 150) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in tickers or []:
        ticker = "".join(ch for ch in str(raw).upper().strip() if ch.isalnum() or ch in {".", "-"})
        if ticker and ticker not in seen:
            cleaned.append(ticker)
            seen.add(ticker)
        if len(cleaned) >= max_count:
            break
    return cleaned


def _clean_period(period: str) -> str:
    value = str(period or "5y").strip().lower()
    return value if value in {"1mo", "6mo", "1y", "2y", "5y", "all"} else "5y"


def _clean_engine_modes(values: list[str] | None) -> list[str]:
    aliases = {"v7": "official", "vai2.1": "vai2", "vai2.2": "vai2"}
    selected: list[str] = []
    for raw in values or []:
        mode = aliases.get(str(raw or "").strip().lower(), str(raw or "").strip().lower())
        if mode in {"official", "v8", "vai2", "universal_v2"} and mode not in selected:
            selected.append(mode)
    return selected or ["official", "v8"]


@router.get("/pattern-modes")
async def pattern_modes():
    return {
        "default": "official",
        "default_lab_tickers": DEFAULT_PATTERN_LAB_TICKERS,
        "training_tickers_150": TRAINING_TICKERS_150,
        "modes": [
            {"id": "universal_v2", "label": "Universal V2 research", "description": "Shared causal signal engine used by the scanner and portfolio ledger."},
            {"id": "official", "label": "V1.0 Official Momentum", "description": "Current production candidate engine."},
            {"id": "v8", "label": "V1.0 Analytics Research", "description": "Directional candidates scored symmetrically with Oryntra analytics."},
            {"id": "vai2", "label": "V1.0 Quant Experimental", "description": "Optional promoted local model layer retained for controlled comparison."},
        ],
    }


@router.get("/pattern-lab/universe")
async def pattern_lab_universe(count: int = 150, seed: int = 73021):
    return universe_metadata(pattern_lab_cached_tickers(), count=max(1, min(int(count), 150)), seed=int(seed))


@router.post("/counters/sync-beta-counts")
async def sync_beta_counts():
    return sync_documented_beta_counts()


@router.get("/cache/status")
async def cache_status(tickers: str = ""):
    ticker_list = [ticker.strip().upper() for ticker in tickers.split(",") if ticker.strip()] or DEFAULT_PATTERN_LAB_TICKERS
    rows = get_ohlcv_cache_summary(ticker_list, "1d")
    by_ticker = {row["ticker"].upper(): row for row in rows}
    return {
        "tickers_requested": len(ticker_list),
        "tickers_cached": len(by_ticker),
        "missing": [ticker for ticker in ticker_list if ticker not in by_ticker],
        "rows": rows,
        "db_size_bytes": get_ohlcv_cache_size_bytes(),
        "db_size_mb": round(get_ohlcv_cache_size_bytes() / 1024 / 1024, 3),
        "note": "Pattern Lab Next defaults to cache_only and makes no provider calls.",
    }


@router.post("/cache/warm-start")
async def cache_warm_start(req: CacheWarmStartRequest):
    tickers = _clean_tickers(req.tickers) or DEFAULT_PATTERN_LAB_TICKERS
    job_id = uuid.uuid4().hex[:12]
    job = {
        "job_id": job_id, "status": "queued", "tickers": tickers,
        "period": _clean_period(req.period), "delay_seconds": max(0.0, min(float(req.delay_seconds), 120.0)),
        "max_cache_gb": req.max_cache_gb, "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None, "total": len(tickers), "completed": 0, "stored_bars": 0,
        "current_ticker": None, "results": [], "errors": [], "db_size_bytes": get_ohlcv_cache_size_bytes(),
    }
    CACHE_WARM_JOBS[job_id] = job
    _start_background_task(_run_cache_warm_job(job, bool(req.stop_on_rate_limit)))
    return job


@router.get("/cache/warm-status/{job_id}")
async def cache_warm_status(job_id: str):
    return CACHE_WARM_JOBS.get(job_id, {"job_id": job_id, "status": "not_found"})


async def _run_cache_warm_job(job: dict[str, Any], stop_on_rate_limit: bool = False):
    job.update({"status": "running", "phase": "warming", "progress_pct": 0.0, "message": "Cache warming started."})
    max_bytes = max(10_000_000, int(float(job.get("max_cache_gb") or 10) * 1024**3))
    try:
        for index, ticker in enumerate(job.get("tickers") or [], start=1):
            job["current_ticker"] = ticker
            if get_ohlcv_cache_size_bytes() >= max_bytes:
                job.update({"status": "stopped", "message": "Configured cache-size cap reached."})
                break
            try:
                data = await asyncio.to_thread(fetch_ticker_data, ticker, job.get("period", "5y"))
                history = data.get("history")
                if history is None or history.empty:
                    raise ValueError("No candles returned")
                provider = data.get("provider", "unknown")
                stored = await asyncio.to_thread(store_ohlcv_bars, ticker, "1d", history, provider)
                job["stored_bars"] += int(stored or 0)
                job["results"].append({"ticker": ticker, "provider": provider, "bars": len(history), "stored": int(stored or 0)})
            except Exception as exc:
                message = str(exc)
                job["errors"].append({"ticker": ticker, "error": message})
                if stop_on_rate_limit and "rate limit" in message.lower():
                    job.update({"status": "stopped", "message": f"Rate limit reached while loading {ticker}."})
                    break
            job.update({
                "completed": index,
                "db_size_bytes": get_ohlcv_cache_size_bytes(),
                "progress_pct": round(index / max(1, len(job.get("tickers") or [])) * 100.0, 2),
                "message": f"Processed {index} of {len(job.get('tickers') or [])} tickers.",
            })
            if index < len(job.get("tickers") or []):
                await asyncio.sleep(float(job.get("delay_seconds") or 0))
        if job.get("status") == "running":
            job["status"] = "failed" if not job["results"] else ("partial" if job["errors"] else "done")
            job["phase"] = "complete" if job["status"] in {"done", "partial"} else "failed"
    except Exception as exc:
        job.update({"status": "failed", "phase": "failed", "message": str(exc)})
    finally:
        job.update({
            "finished_at": datetime.now(timezone.utc).isoformat(), "current_ticker": None,
            "db_size_bytes": get_ohlcv_cache_size_bytes(),
            "db_size_mb": round(get_ohlcv_cache_size_bytes() / 1024 / 1024, 3),
        })


@router.get("/vai/model/status")
async def vai_model_status():
    status = get_vai_model_status()
    status["recent_training_runs"] = get_recent_vai_training_runs(5)
    return status


@router.get("/vai2/model/status")
async def vai2_model_status():
    return get_vai2_model_status()


@router.post("/vai/train/start")
async def vai_train_start(req: VAITrainRequest):
    job_id = uuid.uuid4().hex[:12]
    tickers = _clean_tickers(req.tickers) or DEFAULT_PATTERN_LAB_TICKERS
    job = {
        "job_id": job_id, "status": "queued", "phase": "queued", "progress_pct": 0.0,
        "tickers": tickers, "total_tickers": len(tickers), "started_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(), "finished_at": None,
        "message": "VAI training queued.", "result": None,
    }
    VAI_TRAIN_JOBS[job_id] = job
    _start_background_task(_run_vai_training_job(job_id, req))
    return job


@router.get("/vai/train/status/{job_id}")
async def vai_train_status(job_id: str):
    return VAI_TRAIN_JOBS.get(job_id, {"job_id": job_id, "status": "not_found"})


async def _run_vai_training_job(job_id: str, req: VAITrainRequest):
    job = VAI_TRAIN_JOBS.get(job_id)
    if not job:
        return
    try:
        lab_request = PatternLabRequest(
            tickers=job["tickers"], period=req.period, horizon_days=req.horizon_days,
            step=req.step, min_history=req.min_history, max_tests_per_ticker=req.max_tests_per_ticker,
            data_source=req.data_source, engine_modes=["official"], include_rows=True,
        )
        job.update({"status": "running", "phase": "building_dataset", "message": "Building causal V1.0 rows in an isolated worker.", "updated_at": datetime.now(timezone.utc).isoformat()})
        lab_job = await pattern_lab_start(lab_request)
        lab_job_id = lab_job["job_id"]
        while True:
            lab_status = pattern_lab_status_with_result(lab_job_id)
            job.update({
                "phase": "building_dataset",
                "progress_pct": min(80.0, float(lab_status.get("progress_pct") or 0.0) * 0.8),
                "message": f"Dataset worker: {lab_status.get('message') or lab_status.get('status')}",
                "pattern_lab_job_id": lab_job_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            if lab_status.get("status") not in {"queued", "running", "stopping"}:
                break
            await asyncio.sleep(0.75)
        lab_result = lab_status.get("result") or {}
        if lab_status.get("status") != "done":
            raise RuntimeError(lab_status.get("message") or "Pattern Lab dataset worker failed.")
        rows = (lab_result.get("rows") or {}).get("official") or []
        job.update({"phase": "training_model", "progress_pct": 85.0, "message": f"Training on {len(rows)} causal rows."})
        use_vai2 = str(req.model_version or "vai2").lower() in {"vai2", "vai_2_0", "vai2.1", "vai_2_1", "vai2.2", "vai_2_2", "2"}
        if use_vai2:
            train_result = await asyncio.to_thread(
                train_vai2_research, rows, horizon_days=req.horizon_days,
                min_samples=req.min_samples, force_promote=req.force_promote,
                run_label="VAI2-PatternLabNext",
            )
        else:
            train_result = await asyncio.to_thread(train_vai_from_lab_rows, rows, req.horizon_days, req.min_samples)
        model = train_result.get("model") or {}
        terminal_output = train_result.get("terminal_output") or json.dumps(train_result, indent=2, default=str)
        try:
            store_vai_training_run(
                train_result.get("status") or ("trained" if train_result.get("ok") else "failed"),
                int(model.get("samples") or train_result.get("samples") or len(rows)), req.horizon_days,
                model.get("threshold"), model.get("validation") or train_result, terminal_output,
            )
        except Exception:
            pass
        succeeded = bool(train_result.get("ok"))
        job.update({
            "status": "done" if succeeded else "failed", "phase": "complete" if succeeded else "failed",
            "progress_pct": 100.0, "result": {
                "ok": succeeded, "training": train_result,
                "model_status": get_vai2_model_status() if use_vai2 else get_vai_model_status(),
                "lab_summary": lab_result.get("summary"), "cache": lab_result.get("cache"),
                "terminal_output": terminal_output,
            },
            "message": "VAI training complete." if succeeded else "VAI training failed.",
            "finished_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        job.update({"status": "failed", "phase": "failed", "error": str(exc), "message": str(exc), "finished_at": datetime.now(timezone.utc).isoformat()})


@router.post("/pattern-lab/run")
async def run_pattern_lab_route(req: PatternLabRequest):
    started = await pattern_lab_start(req)
    job_id = started["job_id"]
    while True:
        status = pattern_lab_status_with_result(job_id)
        if status.get("status") not in {"queued", "running", "stopping"}:
            return status.get("result") or status
        await asyncio.sleep(0.5)


@router.post("/pattern-lab/start")
async def pattern_lab_start(req: PatternLabRequest):
    job_id = uuid.uuid4().hex[:12]
    universe = resolve_pattern_lab_tickers(req)
    modes = _clean_engine_modes(req.engine_modes)
    now = datetime.now(timezone.utc).isoformat()
    request_payload = req.model_dump()
    request_payload.update({
        "tickers": universe["tickers"],
        "universe_mode": "manual",
        "universe_size": len(universe["tickers"]),
        "engine_modes": modes,
    })
    job = {
        "job_id": job_id, "status": "queued", "phase": "queued", "progress_pct": 0.0,
        "tickers": universe["tickers"], "total_tickers": len(universe["tickers"]),
        "completed_tickers": 0, "completed_checks": 0,
        "total_checks_estimated": len(universe["tickers"]) * max(1, req.max_tests_per_ticker) * len(modes),
        "modes": modes, "universe": universe, "summary": [], "ticker_errors": [],
        "result": None, "result_available": False, "checkpoint_available": False,
        "started_at": now, "updated_at": now,
        "message": "Pattern Lab queued in an isolated low-priority worker.",
    }
    write_pattern_lab_request(job_id, request_payload)
    write_pattern_lab_status(job_id, job)
    try:
        pid = launch_pattern_lab_worker(job_id)
    except Exception as exc:
        job.update({
            "status": "failed", "phase": "worker_launch_failed",
            "message": str(exc), "error": str(exc),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        write_pattern_lab_status(job_id, job)
        return job
    job.update({"worker_pid": pid, "status": "running", "phase": "worker_starting", "updated_at": datetime.now(timezone.utc).isoformat()})
    write_pattern_lab_status(job_id, job)
    PATTERN_LAB_JOBS[job_id] = job
    return job


@router.get("/pattern-lab/jobs")
async def pattern_lab_jobs(limit: int = 20):
    return {"jobs": list_pattern_lab_jobs(max(1, min(limit, 100)))}


@router.get("/pattern-lab/status/{job_id}")
async def pattern_lab_status(job_id: str):
    return pattern_lab_status_with_result(job_id)


@router.post("/pattern-lab/stop/{job_id}")
async def pattern_lab_stop(job_id: str):
    return request_pattern_lab_stop(job_id)


@router.post("/pattern-lab/resume/{job_id}")
async def pattern_lab_resume(job_id: str):
    original = read_pattern_lab_request(job_id)
    if not original or not pattern_lab_checkpoint_path(job_id).exists():
        return {"job_id": job_id, "status": "not_found", "message": "No resumable checkpoint is available."}
    resumed = dict(original)
    resumed["resume_from_job_id"] = job_id
    return await pattern_lab_start(PatternLabRequest(**resumed))
