from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from ..analysis_access import (
    policy_status,
    refund_quota,
    require_analysis_user,
    reserve_quota,
    usage_status,
)
from ..public_payload import assert_no_raw_market_data, public_analysis_payload
from ..model_access import MINERVA_BASELINE_QUANT_MODEL_ID, normalize_scanner_model, require_model_access
from ..minerva import minerva_baseline
from ..universal_engine import portfolio_targets
from .analysis import (
    ScanRequest,
    _run_scan_pipeline,
    _run_uploaded_scan_pipeline,
    browser_bars_to_history,
)

router = APIRouter()


class IntelligenceScanRequest(BaseModel):
    ticker: str
    period: str = "6mo"
    model: str = "v8"

    @field_validator("ticker")
    @classmethod
    def clean_ticker(cls, value: str) -> str:
        cleaned = value.upper().strip()
        if not cleaned or len(cleaned) > 10:
            raise ValueError("Enter a valid ticker symbol.")
        if any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for char in cleaned):
            raise ValueError("Ticker contains unsupported characters.")
        return cleaned

    @field_validator("period")
    @classmethod
    def valid_period(cls, value: str) -> str:
        if value not in {"5m", "1mo", "6mo", "1y", "5y", "all"}:
            raise ValueError("Unsupported analysis period.")
        return value

    @field_validator("model")
    @classmethod
    def valid_model(cls, value: str) -> str:
        return normalize_scanner_model(value)


class IntelligenceMultiScanRequest(BaseModel):
    tickers: list[str]
    period: str = "6mo"
    model: str = "v8"

    @field_validator("tickers")
    @classmethod
    def clean_tickers(cls, values: list[str]) -> list[str]:
        output: list[str] = []
        for raw in values[:20]:
            ticker = IntelligenceScanRequest.clean_ticker(str(raw))
            if ticker not in output:
                output.append(ticker)
        if not output:
            raise ValueError("Provide at least one ticker.")
        return output

    @field_validator("period")
    @classmethod
    def valid_period(cls, value: str) -> str:
        return IntelligenceScanRequest.valid_period(value)

    @field_validator("model")
    @classmethod
    def valid_model(cls, value: str) -> str:
        return IntelligenceScanRequest.valid_model(value)


class BrowserMarketBar(BaseModel):
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float = Field(ge=0)


class BrowserScanRequest(IntelligenceScanRequest):
    provider: str
    bars: list[BrowserMarketBar] = Field(min_length=320, max_length=2_000)

    @field_validator("provider")
    @classmethod
    def valid_provider(cls, value: str) -> str:
        if value not in {"polygon", "twelvedata"}:
            raise ValueError("provider must be polygon or twelvedata")
        return value


class BrowserHistoryBundle(BaseModel):
    ticker: str
    bars: list[BrowserMarketBar] = Field(min_length=758, max_length=2_000)

    @field_validator("ticker")
    @classmethod
    def clean_ticker(cls, value: str) -> str:
        return IntelligenceScanRequest.clean_ticker(value)


class MinervaScannerRequest(BaseModel):
    target_ticker: str
    provider: str
    histories: list[BrowserHistoryBundle] = Field(min_length=3, max_length=8)

    @field_validator("target_ticker")
    @classmethod
    def clean_target(cls, value: str) -> str:
        return IntelligenceScanRequest.clean_ticker(value)

    @field_validator("provider")
    @classmethod
    def valid_provider(cls, value: str) -> str:
        return BrowserScanRequest.valid_provider(value)


async def _one_scan(ticker: str, period: str, model: str, quota: dict, policy: dict) -> dict:
    raw = await _run_scan_pipeline(
        ScanRequest(ticker=ticker, period=period, pattern_mode=model),
    )
    payload = public_analysis_payload(raw, quota=quota, policy=policy)
    assert_no_raw_market_data(payload)
    return payload


@router.get("/status")
async def intelligence_status(request: Request):
    user = require_analysis_user(request)
    return {
        "service": "oryntra_market_intelligence",
        "status": "ready",
        "policy": policy_status(user),
        "quota": usage_status(user["id"]),
        "chart_provider": "TradingView",
    }


@router.get("/quota")
async def quota_status(request: Request):
    user = require_analysis_user(request)
    return usage_status(user["id"])


@router.post("/scan")
async def scan(req: IntelligenceScanRequest, request: Request):
    user = require_analysis_user(request)
    require_model_access(user, req.model)
    if not policy_status(user)["owner_access"]:
        raise HTTPException(status_code=410, detail="Use the browser-direct scan endpoint. Oryntra does not accept provider keys.")
    quota = reserve_quota(user["id"], 1)
    policy = policy_status(user)
    try:
        return await _one_scan(req.ticker, req.period, req.model, quota, policy)
    except HTTPException:
        refund_quota(user["id"], 1)
        raise
    except Exception:
        refund_quota(user["id"], 1)
        raise


@router.post("/scan-upload")
async def scan_uploaded(req: BrowserScanRequest, request: Request):
    """Authenticated browser-direct path: Oryntra receives bars, never a provider key."""
    user = require_analysis_user(request)
    require_model_access(user, req.model)
    if req.model == MINERVA_BASELINE_QUANT_MODEL_ID:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "MINERVA_UNIVERSE_REQUIRED",
                "message": "Minerva Scanner needs the cross-sectional upload endpoint.",
            },
        )
    quota = reserve_quota(user["id"], 1)
    policy = policy_status(user)
    try:
        raw = await _run_uploaded_scan_pipeline(
            ScanRequest(ticker=req.ticker, period=req.period, pattern_mode=req.model),
            [bar.model_dump() for bar in req.bars],
            req.provider,
        )
        payload = public_analysis_payload(raw, quota=quota, policy=policy)
        assert_no_raw_market_data(payload)
        return payload
    except HTTPException:
        refund_quota(user["id"], 1)
        raise
    except Exception:
        refund_quota(user["id"], 1)
        raise


@router.post("/minerva-scan-upload")
async def minerva_scan_uploaded(req: MinervaScannerRequest, request: Request):
    """Run the frozen Minerva baseline from the normal single-symbol Scanner UI.

    The browser supplies the target plus a compact comparison universe using its
    local provider credential. Raw OHLCV is analyzed in memory and is not stored.
    """
    user = require_analysis_user(request)
    require_model_access(user, MINERVA_BASELINE_QUANT_MODEL_ID)
    quota = reserve_quota(user["id"], 1)
    policy = policy_status(user)
    try:
        unique: dict[str, BrowserHistoryBundle] = {}
        for item in req.histories:
            unique.setdefault(item.ticker, item)
        if req.target_ticker not in unique:
            raise HTTPException(status_code=400, detail="Minerva upload must include the requested target ticker.")
        if len(unique) < 3:
            raise HTTPException(status_code=400, detail="Minerva Scanner needs at least three unique symbols.")

        config = minerva_baseline()
        minimum_bars = config.ridge_training_sessions + 2
        histories = {}
        for symbol, item in unique.items():
            histories[symbol] = await asyncio.to_thread(
                browser_bars_to_history,
                [bar.model_dump() for bar in item.bars],
                minimum_bars,
            )

        common = None
        for frame in histories.values():
            common = frame.index if common is None else common.intersection(frame.index)
        if common is None or len(common) < minimum_bars:
            raise HTTPException(
                status_code=400,
                detail=f"Minerva Scanner needs at least {minimum_bars} overlapping completed daily sessions across its comparison universe.",
            )
        common = common.sort_values()
        aligned = {symbol: frame.loc[common].copy() for symbol, frame in histories.items()}

        prices = __import__("pandas").DataFrame({symbol: frame["Close"] for symbol, frame in aligned.items()})
        opens = __import__("pandas").DataFrame({symbol: frame["Open"] for symbol, frame in aligned.items()})
        volumes = __import__("pandas").DataFrame({symbol: frame["Volume"] for symbol, frame in aligned.items()})
        targets = await asyncio.to_thread(
            portfolio_targets,
            prices,
            config,
            None,
            None,
            opens,
            volumes,
        )
        target_weight_pct = float(targets.iloc[-1].get(req.target_ticker, 0.0) * 100.0)
        target_frame = aligned[req.target_ticker]
        technical = await _run_uploaded_scan_pipeline(
            ScanRequest(ticker=req.target_ticker, period="5y", pattern_mode="v8"),
            [
                {
                    "timestamp": str(index),
                    "open": float(row.Open),
                    "high": float(row.High),
                    "low": float(row.Low),
                    "close": float(row.Close),
                    "volume": float(row.Volume),
                }
                for index, row in target_frame.iterrows()
            ],
            req.provider,
        )

        import hashlib
        source_hash = hashlib.sha256()
        for symbol in sorted(aligned):
            source_hash.update(symbol.encode("utf-8"))
            source_hash.update(__import__("pandas").util.hash_pandas_object(aligned[symbol][["Open", "Close", "Volume"]], index=True).values.tobytes())

        technical["pattern_engine_mode"] = MINERVA_BASELINE_QUANT_MODEL_ID
        technical["model_profile"] = MINERVA_BASELINE_QUANT_MODEL_ID
        technical["model_label"] = "Minerva baseline"
        technical["minerva"] = {
            "status": "completed",
            "profile": "frozen_cross_sectional_baseline",
            "universe_size": len(aligned),
            "comparison_symbols": list(aligned.keys()),
            "evaluation_start": str(common[minimum_bars - 1].date()),
            "evaluation_end": str(common[-1].date()),
            "target_study_weight_pct": target_weight_pct,
            "dataset_fingerprint": source_hash.hexdigest(),
            "configuration_fingerprint": config.fingerprint,
            "note": "Minerva ran on a compact cross-sectional comparison universe. Technical cards below remain descriptive evidence for the requested symbol; the Minerva study output is shown separately and is not a trade recommendation.",
        }
        payload = public_analysis_payload(technical, quota=quota, policy=policy)
        assert_no_raw_market_data(payload)
        return payload
    except HTTPException:
        refund_quota(user["id"], 1)
        raise
    except ValueError as exc:
        refund_quota(user["id"], 1)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        refund_quota(user["id"], 1)
        raise


@router.post("/scan-multiple")
async def scan_multiple(req: IntelligenceMultiScanRequest, request: Request):
    user = require_analysis_user(request)
    require_model_access(user, req.model)
    cost = len(req.tickers)
    quota = reserve_quota(user["id"], cost)
    policy = policy_status(user)
    semaphore = asyncio.Semaphore(max(1, min(3, len(req.tickers))))

    async def run_one(ticker: str):
        async with semaphore:
            try:
                return ticker, await _one_scan(ticker, req.period, req.model, quota, policy), None
            except HTTPException as exc:
                return ticker, None, exc.detail
            except Exception as exc:
                return ticker, None, str(exc)

    rows = await asyncio.gather(*(run_one(ticker) for ticker in req.tickers))
    successful = [payload for _, payload, error in rows if payload is not None]
    failures = [{"ticker": ticker, "error": error} for ticker, payload, error in rows if error is not None]
    failed_count = len(failures)
    if failed_count:
        quota = refund_quota(user["id"], failed_count)
        for payload in successful:
            payload["quota"] = quota
    successful.sort(key=lambda item: item.get("trade_plan", {}).get("quality_score", 0) or 0, reverse=True)
    return {
        "results": successful,
        "errors": failures,
        "count": len(successful),
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "quota": quota,
        "data_policy": {
            "market_history_included": False,
            "ohlcv_arrays_included": False,
            "chart_provider": "TradingView",
        },
    }
