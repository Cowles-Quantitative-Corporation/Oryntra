from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, field_validator

from ..backtest import BacktestRequest, run_backtest, run_backtest_from_histories
from .analysis import browser_bars_to_history
from .auth import require_current_user
from ..model_access import SCANNER_MODEL_IDS, require_model_access

router = APIRouter()
public_router = APIRouter()


class BrowserBacktestRequest(BacktestRequest):
    """Browser-direct backtest input; provider keys never enter this API."""

    provider: str
    bars: list[dict] = Field(min_length=222, max_length=2_000)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        if value not in {"polygon", "twelvedata"}:
            raise ValueError("provider must be polygon or twelvedata")
        return value


@router.post("/run")
async def backtest_endpoint(req: BacktestRequest):
    try:
        return await run_backtest(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backtest error: {str(e)}")


@router.get("/quick/{ticker}")
async def quick_backtest(ticker: str, period: str = "1y", min_score: float = 55):
    req = BacktestRequest(ticker=ticker.upper(), period=period, min_score=min_score)
    try:
        return await run_backtest(req)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@public_router.post("/run-upload")
@router.post("/run-upload")
async def browser_backtest_endpoint(req: BrowserBacktestRequest, request: Request):
    """Run authenticated research on browser-fetched daily bars only."""
    user = require_current_user(request)
    if req.engine_mode not in SCANNER_MODEL_IDS:
        raise HTTPException(status_code=400, detail="Choose a supported research model.")
    require_model_access(user, req.engine_mode)
    try:
        history = browser_bars_to_history(req.bars, max(40, int(req.min_history)) + 2)
        report = await run_backtest_from_histories(
            BacktestRequest(**req.model_dump(exclude={"provider", "bars"})),
            {req.ticker.upper(): history},
            source=f"browser_{req.provider}",
        )
        report["raw_market_data_persisted"] = False
        report["data_source"] = "browser_direct"
        return report
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Backtest error: {str(exc)}") from exc
