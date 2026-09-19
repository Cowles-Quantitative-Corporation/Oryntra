from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone

import pandas as pd
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..corporate_repository import get_corporate_repository
from ..market_repository import get_market_repository, normalize_ticker
from ..quant_research import MODEL_PROFILES, STRATEGIES, QuantConfig, evaluate_strategies
from ..research_experiments import record_experiment
from ..model_access import MINERVA_BASELINE_QUANT_MODEL_ID, MINERVA_MODEL_ID, require_minerva_baseline_quant_access, require_model_access
from ..minerva import minerva_baseline
from ..universal_research import run_universal
from ..internal_access import require_cqc_internal_operator
from .analysis import browser_bars_to_history
from .auth import require_current_user

MINERVA_CATALOG_ENTRY = {
    "id": MINERVA_MODEL_ID,
    "label": "Minerva V1 research candidate",
    "description": "A frozen Universal-generation research candidate. Access is subscription-gated, but it cannot be run in Quant Lab until its documented validation and release gates are met.",
    "access": "subscription",
    "availability": "research_candidate_not_released",
    "long_short_default": False,
}

router = APIRouter()
# The browser-upload endpoint accepts authenticated, user-supplied daily bars
# and never accepts a provider key. Keep this small surface available to the
# signed-in website and mobile app even when private Quant Lab administration
# routes remain disabled.
public_router = APIRouter()


async def _evaluate_histories(histories: dict, config: QuantConfig) -> dict:
    if config.model == "universal_v2":
        return await asyncio.to_thread(evaluate_strategies, histories, config)
    index = pd.DatetimeIndex(sorted({timestamp for history in histories.values() for timestamp in history.index}))
    corporate_repository = get_corporate_repository()
    corporate_scores, corporate_metadata = await asyncio.to_thread(corporate_repository.factor_panel, list(histories), index)
    macro_features, macro_metadata = await asyncio.to_thread(corporate_repository.macro_panel, index)
    report = await asyncio.to_thread(evaluate_strategies, histories, config, corporate_scores, macro_features)
    report["corporate_data"].update(corporate_metadata)
    report["macro_data"].update(macro_metadata)
    report["macro_context"] = await asyncio.to_thread(corporate_repository.macro_snapshot, index.max() if len(index) else None)
    return report


def _serialized_configuration(config: object) -> dict:
    serializer = getattr(config, "as_dict", None)
    return serializer() if callable(serializer) else asdict(config)


def _attach_experiment_record(
    report: dict,
    config: object,
    dataset_fingerprint: str,
    *,
    source: str,
) -> None:
    """Persist the immutable inputs and headline diagnostics for a completed research run."""
    primary = next((row for row in report.get("results", []) if row.get("id") == "strategy_ensemble"), (report.get("results") or [{}])[0])
    try:
        identifier = record_experiment(
            experiment_type="quant_strategy",
            status="done",
            config={"code_version": report.get("engine_version", "quant-api-v1"), "code_fingerprint": report.get("code_fingerprint"), "source": source, "quant_config": _serialized_configuration(config), "engine_configuration": report.get("engine_configuration"), "config_fingerprint": report.get("config_fingerprint")},
            dataset_fingerprint=dataset_fingerprint,
            dataset_start=report.get("universe", {}).get("start"),
            dataset_end=report.get("universe", {}).get("end"),
            symbols=report.get("universe", {}).get("symbols", []),
            sample_count=report.get("universe", {}).get("sessions"),
            metrics={"primary_result": primary, "benchmark": report.get("benchmark", {}), "validation": report.get("validation", {}), "alpha": report.get("alpha", {})},
        )
        report["experiment_id"] = identifier
        report["experiment_recording"] = "recorded"
    except Exception as exc:
        # The report remains useful when a local experiment database is unavailable.
        report["experiment_recording"] = f"not_recorded: {exc}"


class QuantResearchRequest(BaseModel):
    tickers: list[str] = Field(default_factory=lambda: ["SPY", "QQQ", "IWM", "GLD", "TLT", "XLE"])
    period: str = "2y"
    data_source: str = "cache_first"
    data_provider: str | None = "auto"
    model: str = "v1_corporate_quant_system"
    strategy_weights: dict[str, float] = Field(default_factory=dict)
    strategies: list[str] = Field(default_factory=lambda: list(STRATEGIES))
    trend_lookback: int = Field(default=126, ge=21, le=252)
    momentum_lookback: int = Field(default=126, ge=21, le=252)
    reversal_lookback: int = Field(default=5, ge=2, le=30)
    cost_bps: float = Field(default=12, ge=0, le=250)
    borrow_bps_annual: float = Field(default=50, ge=0, le=2000)
    long_short: bool | None = None
    target_annual_volatility: float = Field(default=12, ge=1, le=50)
    max_gross_exposure: float = Field(default=1, ge=.1, le=2)
    max_single_name_weight: float = Field(default=.35, ge=.02, le=1)
    rebalance_frequency: str = "weekly"
    walk_forward_folds: int = Field(default=3, ge=2, le=6)
    regime_conditioned_weights: bool = True
    liquidity_aware_costs: bool = True
    portfolio_value_assumption: float = Field(default=1_000_000, ge=10_000, le=10_000_000_000)
    impact_coefficient_bps: float = Field(default=18, ge=0, le=500)
    max_adv_participation_pct: float = Field(default=2, ge=.01, le=25)

    @field_validator("period")
    @classmethod
    def validate_period(cls, value: str) -> str:
        if value not in {"1y", "2y", "5y", "all"}:
            raise ValueError("period must be one of 1y, 2y, 5y, or all")
        return value

    @field_validator("data_provider")
    @classmethod
    def validate_provider(cls, value: str | None) -> str | None:
        if value is not None and value not in {"cache_only", "auto", "polygon", "twelvedata"}:
            raise ValueError("data_provider must be cache_only, auto, polygon, or twelvedata")
        return value

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        if value not in MODEL_PROFILES and value != MINERVA_MODEL_ID:
            raise ValueError("Choose a supported Quant Lab model profile.")
        return value

    @field_validator("rebalance_frequency")
    @classmethod
    def validate_rebalance(cls, value: str) -> str:
        if value not in {"daily", "weekly", "monthly"}:
            raise ValueError("rebalance_frequency must be daily, weekly, or monthly")
        return value

    @field_validator("strategy_weights")
    @classmethod
    def validate_weights(cls, value: dict[str, float]) -> dict[str, float]:
        clean = {}
        for key, weight in value.items():
            if key in STRATEGIES:
                numeric = float(weight)
                if not 0 <= numeric <= 100:
                    raise ValueError("Strategy allocations must be between 0 and 100 percent.")
                clean[key] = numeric
        return clean


class CorporateImportRequest(BaseModel):
    documents: list[dict] = Field(default_factory=list, max_length=5000)
    facts: list[dict] = Field(default_factory=list, max_length=10000)
    macro_observations: list[dict] = Field(default_factory=list, max_length=10000)


class BrowserQuantHistory(BaseModel):
    ticker: str
    bars: list[dict] = Field(min_length=120, max_length=2_000)


class BrowserQuantResearchRequest(QuantResearchRequest):
    provider: str
    histories: list[BrowserQuantHistory] = Field(min_length=2, max_length=40)

    @field_validator("provider")
    @classmethod
    def validate_browser_provider(cls, value: str) -> str:
        if value not in {"polygon", "twelvedata"}:
            raise ValueError("provider must be polygon or twelvedata")
        return value


class PublicBrowserQuantResearchRequest(BaseModel):
    """Public historical demonstration input: universe and bars only.

    Strategy, sizing, leverage, costs, weights, and profile selection remain
    frozen in server code so a subscriber cannot tune a personal portfolio.
    """
    model_config = ConfigDict(extra="forbid")
    provider: str
    model: str = "v8_official"
    tickers: list[str] = Field(default_factory=lambda: ["SPY", "QQQ", "IWM", "GLD", "TLT", "XLE"])
    histories: list[BrowserQuantHistory] = Field(min_length=2, max_length=40)

    @field_validator("provider")
    @classmethod
    def validate_public_provider(cls, value: str) -> str:
        if value not in {"polygon", "twelvedata"}:
            raise ValueError("provider must be polygon or twelvedata")
        return value

    @field_validator("model")
    @classmethod
    def validate_public_model(cls, value: str) -> str:
        if value not in {"v8_official", MINERVA_BASELINE_QUANT_MODEL_ID}:
            raise ValueError("Choose V8 Official or the Minerva baseline.")
        return value


def _public_frozen_request(payload: PublicBrowserQuantResearchRequest) -> BrowserQuantResearchRequest:
    return BrowserQuantResearchRequest(
        tickers=payload.tickers,
        period="2y",
        data_source="browser_direct",
        data_provider=None,
        model="v1_corporate_quant_system",
        strategy_weights={},
        strategies=list(STRATEGIES),
        trend_lookback=126,
        momentum_lookback=126,
        reversal_lookback=5,
        cost_bps=12,
        borrow_bps_annual=50,
        long_short=False,
        target_annual_volatility=12,
        max_gross_exposure=1,
        max_single_name_weight=.35,
        rebalance_frequency="weekly",
        walk_forward_folds=3,
        regime_conditioned_weights=True,
        liquidity_aware_costs=True,
        portfolio_value_assumption=100_000,
        impact_coefficient_bps=18,
        max_adv_participation_pct=2,
        provider=payload.provider,
        histories=payload.histories,
    )


@router.get("/internal/catalog")
async def catalog(http_request: Request):
    require_cqc_internal_operator(http_request)
    models = [{"id": key, "access": "included", "availability": "available", **value} for key, value in MODEL_PROFILES.items()]
    models.append(MINERVA_CATALOG_ENTRY)
    return {"strategies": [{"id": key, **value} for key, value in STRATEGIES.items()], "models": models, "providers": [{"id": "cache_only", "label": "Local database only"}, {"id": "auto", "label": "Automatic (cache, then saved eligible provider)"}, {"id": "polygon", "label": "Polygon / Massive (EOD daily bars; Basic: 5 calls/min)"}, {"id": "twelvedata", "label": "Twelve Data (1-minute capability; Basic: 8 credits/min and 800/day; daily lab only)"}], "guardrails": ["No broker execution or order creation.", "Performance is net of configured turnover, borrow, and liquidity assumptions.", "Volatility targeting can reduce exposure only; it does not apply leverage.", "Corporate inputs must have an auditable public availability timestamp.", "Provider-plan capability does not grant redistribution or commercial rights.", "Chronological holdout and regime reports are diagnostics, not proof of future profitability."]}


@public_router.get("/catalog")
async def public_catalog():
    profile = MODEL_PROFILES["v1_corporate_quant_system"]
    return {
        "models": [{"id": "v1_corporate_quant_system", "label": profile["label"], "availability": "frozen_public_research_profile"}],
        "guardrails": [
            "Historical model demonstration only; not a personal portfolio recommendation.",
            "Configuration is frozen for public use; Oryntra does not collect account value, objectives, holdings, tax status, or risk tolerance.",
            "No broker connection, order, actual position, or performance promise.",
        ],
    }


def _require_model_access(user: dict, model: str) -> None:
    """Compatibility wrapper retained for focused route tests."""
    require_model_access(user, model)


@router.post("/corporate/import")
async def import_corporate_data(request: CorporateImportRequest, http_request: Request):
    require_cqc_internal_operator(http_request)
    repository = get_corporate_repository()
    try:
        return {"ok": True, "documents_imported": await asyncio.to_thread(repository.import_documents, request.documents), "facts_imported": await asyncio.to_thread(repository.import_facts, request.facts), "macro_observations_imported": await asyncio.to_thread(repository.import_macro, request.macro_observations)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/corporate/{ticker}")
async def corporate_snapshot(ticker: str, http_request: Request):
    require_cqc_internal_operator(http_request)
    repository = get_corporate_repository()
    snapshot = await asyncio.to_thread(repository.latest_snapshot, ticker)
    macro = await asyncio.to_thread(repository.macro_snapshot)
    return {"ok": True, "corporate": snapshot, "macro": macro, "research_only": True}


@router.post("/run")
async def run_research(request: QuantResearchRequest, http_request: Request):
    user = require_cqc_internal_operator(http_request)
    _require_model_access(user, request.model)
    tickers: list[str] = []
    for raw in request.tickers[:40]:
        try:
            ticker = normalize_ticker(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if ticker not in tickers:
            tickers.append(ticker)
    if len(tickers) < 2:
        raise HTTPException(status_code=400, detail="Choose at least two unique symbols.")
    strategy_ids = tuple(item for item in request.strategies if item in STRATEGIES)
    if not strategy_ids:
        raise HTTPException(status_code=400, detail="Choose at least one supported research strategy.")
    provider = request.data_provider or ("cache_only" if request.data_source == "cache_only" else "auto")
    config = QuantConfig(strategies=strategy_ids, trend_lookback=request.trend_lookback, momentum_lookback=request.momentum_lookback, reversal_lookback=request.reversal_lookback, cost_bps=request.cost_bps, borrow_bps_annual=request.borrow_bps_annual, long_short=request.long_short, model=request.model, strategy_weights=request.strategy_weights, target_annual_volatility=request.target_annual_volatility, max_gross_exposure=request.max_gross_exposure, max_single_name_weight=request.max_single_name_weight, rebalance_frequency=request.rebalance_frequency, walk_forward_folds=request.walk_forward_folds, regime_conditioned_weights=request.regime_conditioned_weights, liquidity_aware_costs=request.liquidity_aware_costs, portfolio_value_assumption=request.portfolio_value_assumption, impact_coefficient_bps=request.impact_coefficient_bps, max_adv_participation_pct=request.max_adv_participation_pct)
    repository, histories, metadata, errors = get_market_repository(), {}, {}, []
    minimum_bars = 254 if config.model == "universal_v2" else max(config.trend_lookback, config.momentum_lookback, 63) + 5
    for ticker in tickers:
        try:
            item = await asyncio.to_thread(repository.get_history, ticker, period=request.period, minimum_bars=minimum_bars, allow_api=provider != "cache_only", provider_preference=provider)
            histories[ticker], metadata[ticker] = item.history, item.metadata.__dict__
        except Exception as exc:
            errors.append({"ticker": ticker, "error": str(exc)})
    if len(histories) < 2 or (config.model == "universal_v2" and errors):
        raise HTTPException(status_code=400, detail={"message": "Not enough usable histories for a comparison.", "errors": errors})
    try:
        report = await _evaluate_histories(histories, config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    dataset_fingerprint = repository.dataset_fingerprint(histories, configuration=config.as_dict())
    report.update({"ok": True, "run_at": datetime.now(timezone.utc).isoformat(), "configuration": config.as_dict(), "data_source": request.data_source, "data_provider": provider, "source_metadata": metadata, "errors": errors, "dataset_fingerprint": dataset_fingerprint})
    _attach_experiment_record(report, config, dataset_fingerprint, source="server_repository")
    return report


@router.post("/internal/run-upload")
async def run_internal_browser_research(request: BrowserQuantResearchRequest, http_request: Request):
    """Evaluate browser-fetched daily histories without accepting or retaining a provider key."""
    user = require_cqc_internal_operator(http_request)
    _require_model_access(user, request.model)
    tickers: list[str] = []
    for raw in request.tickers[:40]:
        try:
            ticker = normalize_ticker(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if ticker not in tickers:
            tickers.append(ticker)
    if len(tickers) < 2:
        raise HTTPException(status_code=400, detail="Choose at least two unique symbols.")
    strategy_ids = tuple(item for item in request.strategies if item in STRATEGIES)
    if not strategy_ids:
        raise HTTPException(status_code=400, detail="Choose at least one supported research strategy.")
    supplied = {normalize_ticker(item.ticker): item.bars for item in request.histories}
    missing = [ticker for ticker in tickers if ticker not in supplied]
    if missing:
        raise HTTPException(status_code=400, detail=f"Browser data is missing for: {', '.join(missing)}.")
    config = QuantConfig(strategies=strategy_ids, trend_lookback=request.trend_lookback, momentum_lookback=request.momentum_lookback, reversal_lookback=request.reversal_lookback, cost_bps=request.cost_bps, borrow_bps_annual=request.borrow_bps_annual, long_short=request.long_short, model=request.model, strategy_weights=request.strategy_weights, target_annual_volatility=request.target_annual_volatility, max_gross_exposure=request.max_gross_exposure, max_single_name_weight=request.max_single_name_weight, rebalance_frequency=request.rebalance_frequency, walk_forward_folds=request.walk_forward_folds, regime_conditioned_weights=request.regime_conditioned_weights, liquidity_aware_costs=request.liquidity_aware_costs, portfolio_value_assumption=request.portfolio_value_assumption, impact_coefficient_bps=request.impact_coefficient_bps, max_adv_participation_pct=request.max_adv_participation_pct)
    histories, metadata, errors = {}, {}, []
    minimum_bars = 254 if config.model == "universal_v2" else max(config.trend_lookback, config.momentum_lookback, 63) + 5
    for ticker in tickers:
        try:
            history = await asyncio.to_thread(browser_bars_to_history, supplied[ticker], minimum_bars)
            if len(history) < minimum_bars:
                raise ValueError(f"Need at least {minimum_bars} daily bars for this research configuration.")
            histories[ticker] = history
            metadata[ticker] = {"provider": f"browser_{request.provider}", "bars": len(history), "raw_history_persisted": False}
        except (ValueError, TypeError) as exc:
            errors.append({"ticker": ticker, "error": str(exc)})
    if len(histories) < 2 or (config.model == "universal_v2" and errors):
        raise HTTPException(status_code=400, detail={"message": "Not enough usable browser histories for a comparison.", "errors": errors})
    try:
        report = await _evaluate_histories(histories, config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    repository = get_market_repository()
    dataset_fingerprint = repository.dataset_fingerprint(histories, configuration=config.as_dict())
    report.update({"ok": True, "run_at": datetime.now(timezone.utc).isoformat(), "configuration": config.as_dict(), "data_source": "browser_direct", "data_provider": f"browser_{request.provider}", "source_metadata": metadata, "errors": errors, "dataset_fingerprint": dataset_fingerprint, "raw_market_data_persisted": False})
    _attach_experiment_record(report, config, dataset_fingerprint, source="browser_direct")
    return report


@public_router.post("/run-upload")
async def run_public_browser_research(payload: PublicBrowserQuantResearchRequest, http_request: Request):
    """Run a fixed Base or paid Minerva multi-symbol research profile."""
    user = require_current_user(http_request)
    if payload.model == MINERVA_BASELINE_QUANT_MODEL_ID:
        require_minerva_baseline_quant_access(user)
    request = _public_frozen_request(payload)
    tickers: list[str] = []
    for raw in request.tickers[:40]:
        try:
            ticker = normalize_ticker(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if ticker not in tickers:
            tickers.append(ticker)
    if len(tickers) < 2:
        raise HTTPException(status_code=400, detail="Choose at least two unique symbols.")
    supplied = {normalize_ticker(item.ticker): item.bars for item in request.histories}
    missing = [ticker for ticker in tickers if ticker not in supplied]
    if missing:
        raise HTTPException(status_code=400, detail=f"Browser data is missing for: {', '.join(missing)}.")
    config = QuantConfig(
        strategies=tuple(STRATEGIES), trend_lookback=126, momentum_lookback=126,
        reversal_lookback=5, cost_bps=12, borrow_bps_annual=50, long_short=False,
        model="v1_corporate_quant_system", strategy_weights={}, target_annual_volatility=12,
        max_gross_exposure=1, max_single_name_weight=.35, rebalance_frequency="weekly",
        walk_forward_folds=3, regime_conditioned_weights=True, liquidity_aware_costs=True,
        portfolio_value_assumption=100_000, impact_coefficient_bps=18,
        max_adv_participation_pct=2,
    )
    histories, metadata, errors = {}, {}, []
    if payload.model == MINERVA_BASELINE_QUANT_MODEL_ID:
        minerva_config = minerva_baseline()
        minimum_bars = minerva_config.ridge_training_sessions + 2
    else:
        minerva_config = None
        minimum_bars = max(config.trend_lookback, config.momentum_lookback, 63) + 5
    for ticker in tickers:
        try:
            history = await asyncio.to_thread(browser_bars_to_history, supplied[ticker], minimum_bars)
            histories[ticker] = history
            metadata[ticker] = {"provider": f"browser_{request.provider}", "bars": len(history), "raw_history_persisted": False}
        except (ValueError, TypeError) as exc:
            errors.append({"ticker": ticker, "error": str(exc)})
    if len(histories) < 2:
        raise HTTPException(status_code=400, detail={"message": "Not enough usable browser histories for a comparison.", "errors": errors})
    try:
        report = await asyncio.to_thread(run_universal, histories, minerva_config) if minerva_config else await _evaluate_histories(histories, config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    repository = get_market_repository()
    active_config = minerva_config if minerva_config else config
    active_config_payload = _serialized_configuration(active_config)
    dataset_fingerprint = repository.dataset_fingerprint(histories, configuration=active_config_payload)
    public_profile = "minerva_baseline" if minerva_config else "v8_official"
    report.update({"ok": True, "run_at": datetime.now(timezone.utc).isoformat(), "configuration": active_config_payload, "public_profile": public_profile, "research_boundary": "Frozen historical demonstration only; not a personal portfolio recommendation.", "data_source": "browser_direct", "data_provider": f"browser_{request.provider}", "source_metadata": metadata, "errors": errors, "dataset_fingerprint": dataset_fingerprint, "raw_market_data_persisted": False})
    _attach_experiment_record(report, active_config, dataset_fingerprint, source=f"{public_profile}_browser_direct")
    return report
