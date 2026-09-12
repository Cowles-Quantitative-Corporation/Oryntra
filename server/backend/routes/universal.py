"""Authenticated, bounded upload route for the shared research engine."""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from dataclasses import asdict

import pandas as pd
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..market_repository import normalize_ticker
from ..research_experiments import record_experiment
from ..universal_engine import UniversalConfig, scan_snapshot
from ..universal_research_blueprint import research_blueprint
from ..universal_taxonomy import seed_symbol_lookup, taxonomy_contract
from ..universal_research import run_universal
from .analysis import browser_bars_to_history
from .auth import require_current_user

router = APIRouter()
_run_slots = asyncio.Semaphore(1)


class History(BaseModel):
    ticker: str
    bars: list[dict] = Field(min_length=254, max_length=4000)


class CashReturn(BaseModel):
    date: date
    return_daily: float = Field(ge=-.05, le=.05, allow_inf_nan=False)


class MarketObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    observed_at: datetime
    available_at: datetime
    decision_at: datetime
    source_manifest_id: str = Field(min_length=1, max_length=160)
    lookback_sessions: int = Field(ge=21, le=252)
    market_return: float = Field(gt=-1, allow_inf_nan=False)
    breadth: float = Field(ge=0, le=1, allow_inf_nan=False)
    median_pairwise_correlation: float = Field(ge=-1, le=1, allow_inf_nan=False)


class UniversalScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    history: History
    configuration: UniversalConfig = Field(default_factory=UniversalConfig)


@router.get("/blueprint")
async def blueprint(http_request: Request):
    require_current_user(http_request)
    return {"ok": True, **research_blueprint()}


@router.get("/taxonomy")
async def taxonomy(http_request: Request):
    """Expose the curated taxonomy contract, never an unverified current universe."""
    require_current_user(http_request)
    return {"ok": True, **taxonomy_contract()}


@router.get("/taxonomy/{symbol}")
async def taxonomy_symbol(symbol: str, http_request: Request):
    """Return a source-attributed multi-family seed view for one symbol."""
    require_current_user(http_request)
    try:
        return {"ok": True, **seed_symbol_lookup(symbol)}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def parse_histories(items: list[History]) -> dict[str, pd.DataFrame]:
    result = {}
    for item in items:
        name = normalize_ticker(item.ticker)
        if name in result:
            raise ValueError("Duplicate history symbol")
        frame = browser_bars_to_history(item.bars, 254, maximum_bars=4000)
        if not frame.index.equals(frame.index.normalize()):
            raise ValueError("V2 uploads require daily session dates at midnight UTC")
        result[name] = frame
    return result


class UniversalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    histories: list[History] = Field(min_length=1, max_length=24)
    benchmarks: list[History] = Field(default_factory=list, max_length=2)
    risk_free: list[CashReturn] = Field(default_factory=list, max_length=4000)
    market_observations: list[MarketObservation] = Field(default_factory=list, max_length=4000)
    configuration: UniversalConfig = Field(default_factory=UniversalConfig)
    evaluation_start: date | None = None
    evaluation_end: date | None = None


def evaluate_upload(request: UniversalRequest) -> dict:
    histories = parse_histories(request.histories)
    benchmark_histories = parse_histories(request.benchmarks)
    market = rf = None
    if benchmark_histories:
        if set(benchmark_histories) != {"SPY", "QQQ"}:
            raise ValueError("Benchmark requires both SPY and QQQ")
        prices = pd.DataFrame({k: v.Close for k, v in benchmark_histories.items()})
        market = prices.pct_change(fill_method=None).sum(axis=1, min_count=2) / 2
    if request.risk_free:
        dates = pd.DatetimeIndex([r.date for r in request.risk_free])
        if dates.has_duplicates or not dates.is_monotonic_increasing:
            raise ValueError("Risk-free returns require unique increasing session dates")
        rf = pd.Series([r.return_daily for r in request.risk_free], index=dates)
    observations = None
    if request.market_observations:
        observations = pd.DataFrame([r.model_dump() for r in request.market_observations])
        observations.index = pd.DatetimeIndex(observations.pop("date"))
    report = run_universal(histories, request.configuration, market, rf,
                           str(request.evaluation_start) if request.evaluation_start else None,
                           str(request.evaluation_end) if request.evaluation_end else None, observations)
    report["raw_market_data_persisted"] = False
    return report


@router.post("/scan-upload")
async def scan_upload(request: UniversalScanRequest, http_request: Request):
    require_current_user(http_request)
    def compute():
        symbol, history = next(iter(parse_histories([request.history]).items()))
        return {"ok": True, "symbol": symbol, **scan_snapshot(history, request.configuration), "raw_market_data_persisted": False}
    try:
        return await asyncio.to_thread(compute)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/run-upload")
async def run_upload(request: UniversalRequest, http_request: Request):
    require_current_user(http_request)
    try:
        async with _run_slots:
            report = await asyncio.to_thread(evaluate_upload, request)
        report["experiment_id"] = await asyncio.to_thread(record_experiment,
            experiment_type="quant_strategy", status="done",
            config={"engine": report["engine_version"], "code_fingerprint": report["code_fingerprint"], "configuration": asdict(request.configuration)},
            dataset_fingerprint=report["dataset_fingerprint"],
            dataset_start=report["universe"]["start"], dataset_end=report["universe"]["end"],
            symbols=report["universe"]["symbols"], sample_count=report["universe"]["sessions"],
            metrics={"alpha": report["alpha"], "results": report["results"]},
            notes="Universal V2 browser-upload research; raw bars are not retained")
        return {"ok": True, **report}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
