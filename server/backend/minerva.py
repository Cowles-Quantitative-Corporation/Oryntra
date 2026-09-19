"""Minerva: a research-only Universal V2 candidate profile.

The profile names a frozen price-only baseline.  Fundamental overlays remain
zero until they have availability-dated records and pass a separate,
predeclared evaluation; a name is not a release approval.
"""
from __future__ import annotations

from dataclasses import replace

from .universal_engine import UniversalConfig
from .universal_position_policy import PositionPolicyConfig
from .universal_institutional_decision import InstitutionalDecisionConfig
from .universal_risk_supervisor import RiskSupervisorConfig, v201_risk_config, v202_risk_config, v203_risk_config
from .universal_factor_model import FactorModelConfig
from .universal_optimizer import PortfolioOptimizerConfig
from .universal_phase3 import Phase3Config
from .alpha_v1 import AlphaV1Config


MINERVA_ID = "minerva_v1_research"
MINERVA_STATUS = "candidate_not_release_approved"


def minerva_baseline(**overrides: object) -> UniversalConfig:
    """Create the wider-universe price-only candidate from the frozen study."""
    config = UniversalConfig(
        alpha_model="walk_forward_ridge",
        research_profile="minerva_v1",
        ridge_training_sessions=756,
        ridge_retrain_sessions=21,
        ridge_horizon_sessions=5,
        ridge_penalty=1.0,
        entry_threshold=.20,
        vol_target=.18,
        maximum_positions=24,
        rebalance="weekly",
        cost_bps=12.0,
        impact_bps=18.0,
        participation=.02,
    )
    return replace(config, **overrides)


def minerva_residual_momentum_candidate(**overrides: object) -> UniversalConfig:
    """Return the separate Minerva candidate with two market-residual horizons."""
    return minerva_baseline(**({"ridge_residual_momentum_21": True, "ridge_residual_momentum_63": True} | overrides))


def minerva_score_persistence_candidate(**overrides: object) -> UniversalConfig:
    """Return a separate candidate that smooths successive causal ridge ranks."""
    return minerva_baseline(**({"ridge_score_smoothing": .50} | overrides))


def minerva_economic_interactions_candidate(**overrides: object) -> UniversalConfig:
    """Return the separate Minerva candidate with three named causal interactions."""
    return minerva_baseline(**({"ridge_economic_interactions": True} | overrides))


def tba1_ohlcv_structure_candidate(**overrides: object) -> UniversalConfig:
    """TBA 1: Minerva risk construction with a compact OHLCV feature sleeve."""
    return _tba_candidate({"ridge_include_ohlcv_structure": True}, overrides)


def tba2_qlib_risk_candidate(**overrides: object) -> UniversalConfig:
    """TBA 2: portfolio construction for externally frozen Qlib scores."""
    return _tba_candidate({}, overrides)


def tba3_market_residual_target_candidate(**overrides: object) -> UniversalConfig:
    """TBA 3: learn a forward return residualized by completed market beta."""
    return _tba_candidate({"ridge_target_market_residual": True}, overrides)


def tba4_completed_ic_gate_candidate(**overrides: object) -> UniversalConfig:
    """TBA 4: trade only when completed trailing rank IC is nonnegative."""
    return _tba_candidate({"ridge_ic_gate": True, "ridge_ic_lookback_sessions": 63, "ridge_ic_minimum": 0.0}, overrides)


def tba5_residual_momentum_candidate(**overrides: object) -> UniversalConfig:
    """TBA 5: add completed 21/63-session stock-market residual momentum."""
    return _tba_candidate({"ridge_residual_momentum_21": True, "ridge_residual_momentum_63": True}, overrides)


def tba6_residual_lifecycle_candidate(**overrides: object) -> UniversalConfig:
    """TBA 6: TBA 5 with the existing causal lifecycle policy enabled."""
    return _tba_candidate({"ridge_residual_momentum_21": True, "ridge_residual_momentum_63": True,
                           "position_policy": PositionPolicyConfig(enabled=True)}, overrides)


def tba8_institutional_risk_candidate(**overrides: object) -> UniversalConfig:
    """TBA 8: persistent residual forecasts with causal confidence and capacity."""
    return _tba_candidate({
        "ridge_residual_momentum_21": True,
        "ridge_residual_momentum_63": True,
        "ridge_score_smoothing": .25,
        "institutional_decision": InstitutionalDecisionConfig(
            enabled=True,
            minimum_signal_to_noise=0.0,
            # Retain the tunable cost estimate and audit, but do not hard-gate
            # on it: every positive multiplier reduced development Sharpe.
            round_trip_cost_multiplier=0.0,
            weak_edge_scale=.50,
            liquidity_horizon_sessions=3,
            capacity_buffer=.80,
            minimum_dollar_volume=1_000_000.0,
        ),
    }, overrides)


TBA9_ID = "tba9_integrity_research"
TBA9_STATUS = "candidate_not_release_approved"


def tba9_fragility_risk_candidate(**overrides: object) -> UniversalConfig:
    """Rejected V2.0.1 risk branch: TBA8 alpha path plus fragility supervisor."""
    values = {"risk_supervisor": v201_risk_config(), **overrides}
    return replace(tba8_institutional_risk_candidate(), **values)


def risk_v202_candidate(**overrides: object) -> UniversalConfig:
    """Research-only V2.0.2 supervisor on the frozen TBA8 alpha path.

    This is deliberately not a new TBA model. It may only remove V1-approved
    exposure and is never product-selectable or promotion-approved by default.
    """
    values = {"risk_supervisor": v202_risk_config(), **overrides}
    return replace(tba8_institutional_risk_candidate(), **values)


def risk_v203_candidate(**overrides: object) -> UniversalConfig:
    """TBA8 alpha path with Risk Supervisor V2.0.3.

    V2.0.3 preserves V1 as the baseline, activates the softened economically-gated
    structural sleeve, and counts predictive persistence in scheduled rebalance
    decisions. It remains research-only until a fresh point-in-time holdout.
    """
    base = tba8_institutional_risk_candidate()
    values = dict(overrides)
    supervisor = values.pop("risk_supervisor", v203_risk_config())
    return replace(base, risk_supervisor=supervisor, **values)


ALPHA_V1_ID = "alpha_v1_research"
ALPHA_V1_STATUS = "candidate_not_release_approved"


def alpha_v1_candidate(**overrides: object) -> UniversalConfig:
    """Separate Alpha V1 family under the same TBA8 construction/risk stack.

    Paired studies isolate the alpha path: this is private research only and
    does not change Minerva, TBA8, or any public product model.
    """
    base = tba8_institutional_risk_candidate()
    values = dict(overrides)
    alpha_cfg = values.pop("alpha_v1", AlphaV1Config(enabled=True))
    return replace(base, alpha_model="alpha_v1", research_profile="alpha_v1",
                   alpha_v1=alpha_cfg, selection_mode="relative_rank",
                   minimum_relative_rank=.60, entry_threshold=0.0,
                   ridge_residual_momentum_21=False, ridge_residual_momentum_63=False,
                   ridge_include_residual_momentum=False, **values)


def phase2_factor_optimizer_candidate(**overrides: object) -> UniversalConfig:
    """Phase 2A/B: TBA8 alpha path + factor risk + constrained optimizer + V1.

    The base factor model uses causal market, momentum and low-volatility factors.
    Point-in-time sector/industry, size, value and quality factors activate only
    when explicit availability-dated panels are supplied to the research engine.
    """
    base = tba8_institutional_risk_candidate()
    values = dict(overrides)
    factor_model = values.pop("factor_model", FactorModelConfig(enabled=True))
    optimizer = values.pop("portfolio_optimizer", PortfolioOptimizerConfig(enabled=True))
    return replace(base, research_profile="phase2_factor_optimizer",
                   factor_model=factor_model, portfolio_optimizer=optimizer, **values)


def phase2_factor_optimizer_v203_candidate(**overrides: object) -> UniversalConfig:
    """Phase 2A/B with Risk Supervisor V2.0.3 as the post-optimizer safety layer."""
    values = dict(overrides)
    supervisor = values.pop("risk_supervisor", v203_risk_config())
    base = phase2_factor_optimizer_candidate(**values)
    return replace(base, risk_supervisor=supervisor)




def phase15_v203_candidate(**overrides: object) -> UniversalConfig:
    """Explicit Phase 1.5 arm: TBA8 alpha path + frozen Risk V2.0.3 only."""
    values = dict(overrides)
    base = risk_v203_candidate(**values)
    return replace(base, research_profile="phase15_v203")


def phase15_phase3_candidate(**overrides: object) -> UniversalConfig:
    """Phase 1.5 + Phase 3 diagnostics, with Phase 2 disabled.

    Phase 3 is observational and cannot change the Phase 1.5 target portfolio.
    """
    values = dict(overrides)
    phase3 = values.pop("phase3", Phase3Config(enabled=True))
    base = risk_v203_candidate(**values)
    return replace(base, research_profile="phase15_phase3", phase3=phase3)


def phase15_phase2_phase3_candidate(**overrides: object) -> UniversalConfig:
    """Phase 1.5 + Phase 2 factor/optimizer + Phase 3 diagnostics."""
    values = dict(overrides)
    phase3 = values.pop("phase3", Phase3Config(enabled=True))
    base = phase2_factor_optimizer_v203_candidate(**values)
    return replace(base, research_profile="phase15_phase2_phase3", phase3=phase3)


def tba9_integrity_candidate(**overrides: object) -> UniversalConfig:
    """TBA 9: TBA 8 economics under a point-in-time research contract.

    The signal and risk controls are intentionally inherited unchanged from
    TBA 8.  TBA 9's upgrade is the required universe, locked-holdout, and
    diagnostic protocol implemented by :mod:`tba9_integrity`; it is not a
    retrospective weight optimization and is never exposed to the product.
    """
    return tba8_institutional_risk_candidate(**({"research_profile": "tba9_integrity"} | overrides))


def _tba_candidate(defaults: dict[str, object], overrides: dict[str, object]) -> UniversalConfig:
    """Apply the shared supervisor to TBA research without changing Minerva."""
    supervisor = RiskSupervisorConfig(
        enabled=True,
        component_risk_enabled=True,
        maximum_risk_contribution=.10,
        correlation_cluster_enabled=False,
        correlation_regime_enabled=False,
        diversification_enabled=False,
        realized_volatility_enabled=False,
        volatility_shock_enabled=False,
        tail_loss_enabled=False,
        drawdown_enabled=False,
        gradual_recovery_enabled=False,
    )
    values = {"risk_supervisor": supervisor, **defaults, **overrides}
    return minerva_baseline(**values)


def minerva_beta_ceiling_candidate(**overrides: object) -> UniversalConfig:
    """Return a separate candidate that can scale down excess market beta to cash."""
    return minerva_baseline(**({"maximum_portfolio_market_beta": .75} | overrides))


def minerva_contract() -> dict[str, object]:
    return {
        "id": MINERVA_ID,
        "status": MINERVA_STATUS,
        "baseline": "756-session ridge, weekly rebalance, maximum 24 holdings; price and volume only",
        "feature_sleeves": [
            {"id": "residual_momentum_21_63", "input": "completed SPY/QQQ benchmark returns and stock OHLCV", "default_weight": "ridge-selected"},
            {"id": "score_persistence", "input": "successive causal walk-forward ridge ranks", "default_weight": 0.0},
            {"id": "economic_interactions", "input": "completed trend, breakout and relative-volume interactions", "default_weight": 0.0},
            {"id": "ohlcv_structure", "input": "completed session high, low, open, close and volume", "default_weight": 0.0},
            {"id": "market_residual_target", "input": "completed 126-session stock beta and later label-only benchmark return", "default_enabled": False},
            {"id": "completed_ic_gate", "input": "finished cross-sectional rank information coefficients", "default_enabled": False},
            {"id": "portfolio_beta_ceiling", "input": "trailing 126-session stock beta to completed SPY/QQQ", "default_weight": None},
            {"id": "fundamental_quality", "input": "availability-dated cross-sectional corporate quality panel", "default_weight": 0.0},
            {"id": "filing_acceleration", "input": "availability-dated change in filing-derived growth score", "default_weight": 0.0},
            {"id": "phase2_factor_risk", "input": "completed benchmark/stock returns plus optional availability-dated sector/style panels", "default_enabled": False},
            {"id": "phase2_portfolio_optimizer", "input": "expected alpha, prediction uncertainty, factor covariance, turnover and liquidity constraints", "default_enabled": False},
            {"id": "phase3_institutional_diagnostics", "input": "approved portfolio targets, completed returns, liquidity and optional Phase 2 factor state", "default_enabled": False},
            {"id": "risk_supervisor_v2_0_3", "input": "V1-approved proposal plus causal fragility/predictive diagnostics", "default_enabled": False},
        ],
        "promotion_gate": "Freeze each nonzero feature weight on development data, then pass an unseen, point-in-time universe evaluation and the documented alpha consistency gates.",
    }
