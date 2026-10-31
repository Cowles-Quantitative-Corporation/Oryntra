"""Public CQC Model C / Beta V1 entry point."""
from __future__ import annotations
from dataclasses import asdict, dataclass
import hashlib, json
from pathlib import Path
from typing import Any, Optional
import pandas as pd

from ...alpha_v12 import AlphaV12Config, ALPHA_V12_ALT_ID
from .alpha_context import ALPHA_ALT_CONTEXT_FEATURE_FAMILIES, AlphaAltContextConfig, build_alpha_alt_market_context
from .config import BETA_V1_CONFIG
from .service import BetaService
from .types import BetaState

BETA_V1_ID="beta_v1_macro_regime"
BETA_V1_VERSION="1.2.2-research"

@dataclass(frozen=True)
class BetaV1Config:
    enabled: bool=False
    include_tba_anchor: bool=False
    alpha_feature_context_enabled: bool=True
    horizon_sessions: int=5
    beta_window_sessions: int=126
    volatility_window_sessions: int=21
    include_momentum_21: bool=True
    include_momentum_63: bool=True
    include_momentum_126: bool=True
    include_residual_momentum_21: bool=True
    include_residual_momentum_63: bool=True
    include_short_reversion: bool=True
    include_low_volatility: bool=True
    include_sector_relative: bool=True
    include_value: bool=True
    include_quality: bool=True
    macro_z_window_sessions: int=252
    macro_z_minimum_sessions: int=60
    random_state: int=17
    require_production_frozen_for_risk_v204: bool=True

    def __post_init__(self) -> None:
        if not isinstance(self.enabled,bool) or not isinstance(self.include_tba_anchor,bool):
            raise ValueError("Beta V1 switches must be boolean")
        if self.include_tba_anchor:
            raise ValueError("Beta V1 is pure Model C and cannot consume a TBA anchor")
        if not (2 <= self.horizon_sessions <= 21): raise ValueError("horizon_sessions must be within [2, 21]")
        if not (63 <= self.beta_window_sessions <= 504): raise ValueError("beta_window_sessions must be within [63, 504]")
        if not (10 <= self.volatility_window_sessions <= 126): raise ValueError("volatility_window_sessions must be within [10, 126]")
        if not (126 <= self.macro_z_window_sessions <= 756): raise ValueError("macro_z_window_sessions must be within [126, 756]")
        if not (20 <= self.macro_z_minimum_sessions <= self.macro_z_window_sessions): raise ValueError("macro_z_minimum_sessions is invalid")

    @property
    def model_id(self) -> str: return BETA_V1_ID
    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps({"id":BETA_V1_ID,"version":BETA_V1_VERSION,**asdict(self)},sort_keys=True).encode()).hexdigest()
    @property
    def pure_alpha_alt_template(self) -> AlphaV12Config:
        return AlphaV12Config(enabled=True,include_tba_anchor=False,anchor_prior_weight=0.0,horizon_sessions=self.horizon_sessions)
    @property
    def alpha_context_config(self) -> AlphaAltContextConfig:
        return AlphaAltContextConfig(
            beta_window_sessions=self.beta_window_sessions,volatility_window_sessions=self.volatility_window_sessions,
            include_momentum_21=self.include_momentum_21,include_momentum_63=self.include_momentum_63,
            include_momentum_126=self.include_momentum_126,include_residual_momentum_21=self.include_residual_momentum_21,
            include_residual_momentum_63=self.include_residual_momentum_63,include_short_reversion=self.include_short_reversion,
            include_low_volatility=self.include_low_volatility,include_sector_relative=self.include_sector_relative,
            include_value=self.include_value,include_quality=self.include_quality)

def beta_v1_service(root: str|Path, *, fred_api_key: Optional[str]=None, strict_pit: bool=True,
                    config: BetaV1Config=BetaV1Config(enabled=True), experiment_recorder=None) -> BetaService:
    return BetaService(root,fred_api_key=fred_api_key,strict_pit=strict_pit,random_state=config.random_state,
                       experiment_recorder=experiment_recorder,z_window=config.macro_z_window_sessions,
                       z_min_periods=config.macro_z_minimum_sessions)

def beta_v1_alpha_context(closes: pd.DataFrame, *, benchmark_returns: Optional[pd.Series]=None,
                          sector_labels: Optional[pd.DataFrame]=None,value_scores: Optional[pd.DataFrame]=None,
                          quality_scores: Optional[pd.DataFrame]=None,
                          config: BetaV1Config=BetaV1Config(enabled=True)) -> pd.DataFrame:
    if not config.alpha_feature_context_enabled: return pd.DataFrame(index=closes.index)
    return build_alpha_alt_market_context(closes,benchmark_returns=benchmark_returns,sector_labels=sector_labels,
        value_scores=value_scores,quality_scores=quality_scores,config=config.alpha_context_config)

def beta_v1_state(service: BetaService, market_prices: pd.DataFrame, *, stock_context: Optional[pd.DataFrame]=None) -> BetaState:
    return service.latest_state(market_prices,stock_context=stock_context)

def beta_v1_risk_payload(state: BetaState, *, service: Optional[BetaService]=None) -> dict[str,Any]:
    payload=state.to_dict(); payload.update({"model_id":BETA_V1_ID,"model_version":BETA_V1_VERSION,
        "production_frozen":False,"artifact_sha256":None})
    if service is not None:
        status=service.artifact_status(); payload["production_frozen"]=bool(status.production_frozen)
        if status.production_frozen and service.frozen_path.exists():
            payload["artifact_sha256"]=hashlib.sha256(service.frozen_path.read_bytes()).hexdigest()
    return payload

def beta_v1_contract(config: BetaV1Config=BetaV1Config()) -> dict[str,Any]:
    alt=config.pure_alpha_alt_template
    return {
        "id":BETA_V1_ID,"version":BETA_V1_VERSION,"status":"active_research" if config.enabled else "disabled",
        "configuration":asdict(config),"config_fingerprint":config.fingerprint,
        "model_role":"Model C — independent probabilistic macro/regime engine",
        "alpha_alt_template":{"id":ALPHA_V12_ALT_ID,"include_tba_anchor":alt.include_tba_anchor,"anchor_prior_weight":alt.anchor_prior_weight},
        "shared_raw_feature_families":list(ALPHA_ALT_CONTEXT_FEATURE_FAMILIES),
        "beta_specific_feature_families":["PIT rates and yield-curve state","real yields and inflation breakevens",
            "PIT credit spreads","PIT core inflation and independent growth releases",
            "timestamped non-anchor economic surprises","cross-asset breadth/relative-strength context",
            "probabilistic regime persistence/transition information"],
        "regime_states":list(BETA_V1_CONFIG["states"]),
        "independence":{"tba_anchor_consumed":False,"alpha_forecast_consumed":False,"alpha_score_consumed":False,
            "forbidden_forecast_inputs":list(BETA_V1_CONFIG["independence"]["forbidden_forecast_inputs"])},
        "causality":"strict PIT macro vintages; completed market close enters following open; no future economic revisions",
        "probability_calibration":"time-ordered rank-preserving temperature scaling plus untouched final holdout",
        "risk_v204_compatibility":{"direction":"Beta -> Universal Risk V2.0.4 only","risk_feedback_into_beta":False,
            "production_frozen_required_by_default":config.require_production_frozen_for_risk_v204},
        "live_trading_authorization":False,
    }
