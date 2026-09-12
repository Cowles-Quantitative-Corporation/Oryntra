"""Minerva: a research-only Universal V2 candidate profile.

The profile names a frozen price-only baseline.  Fundamental overlays remain
zero until they have availability-dated records and pass a separate,
predeclared evaluation; a name is not a release approval.
"""
from __future__ import annotations

from dataclasses import replace

from .universal_engine import UniversalConfig
from .universal_position_policy import PositionPolicyConfig


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
    return minerva_baseline(**({"ridge_include_ohlcv_structure": True} | overrides))


def tba3_market_residual_target_candidate(**overrides: object) -> UniversalConfig:
    """TBA 3: learn a forward return residualized by completed market beta."""
    return minerva_baseline(**({"ridge_target_market_residual": True} | overrides))


def tba4_completed_ic_gate_candidate(**overrides: object) -> UniversalConfig:
    """TBA 4: trade only when completed trailing rank IC is nonnegative."""
    return minerva_baseline(**({"ridge_ic_gate": True, "ridge_ic_lookback_sessions": 63, "ridge_ic_minimum": 0.0} | overrides))


def tba5_residual_momentum_candidate(**overrides: object) -> UniversalConfig:
    """TBA 5: add completed 21/63-session stock-market residual momentum."""
    return minerva_baseline(**({"ridge_residual_momentum_21": True, "ridge_residual_momentum_63": True} | overrides))


def tba6_residual_lifecycle_candidate(**overrides: object) -> UniversalConfig:
    """TBA 6: TBA 5 with the existing causal lifecycle policy enabled."""
    return tba5_residual_momentum_candidate(**({"position_policy": PositionPolicyConfig(enabled=True)} | overrides))


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
        ],
        "promotion_gate": "Freeze each nonzero feature weight on development data, then pass an unseen, point-in-time universe evaluation and the documented alpha consistency gates.",
    }
