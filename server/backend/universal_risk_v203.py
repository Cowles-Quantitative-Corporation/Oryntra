"""Universal Risk Supervisor V2.0.3 control layer.

V2.0.3 keeps the measurement / predictive machinery from V2.0.2 but changes
how it is allowed to act:

- V1 is still the baseline supervisor and remains the production/research control.
- Risk decisions happen on explicit portfolio rebalance decision events.
- The structural sleeve is active in the V2.0.3 research profile because the
  V2.0.2 ablation showed the only material incremental improvement there.
- Structural intervention is *soft*: a full structural repair is blended toward
  V1 rather than blindly accepted.
- Every structural change must pass an economic gate for modeled risk reduction,
  alpha-priority preservation and incremental turnover.
- The predictive overlay remains causal, validated, percentile-based and bounded,
  but persistence is explicitly counted in rebalance decisions rather than days.
- The optimizer may only remove V1-approved exposure and still has to dominate a
  proportional de-risking baseline before it is accepted.

The default settings below are a predeclared research specification, not claimed
optima.  The paired / sensitivity runners must be used before promotion.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from .universal_risk_v201 import (
    annualized_portfolio_volatility,
    apply_cluster_caps,
    apply_component_risk_cap,
    complete_linkage_clusters,
    component_risk_shares,
    covariance_to_correlation,
)
from .universal_risk_v202 import (
    V202PredictiveConfig,
    alpha_aware_quadratic_trim,
    predictive_gross_modifier,
)

_EPS = 1e-12


@dataclass(frozen=True)
class V203ControlConfig:
    """Decision controls layered on top of the V2.0.2 measurement stack."""

    # Predictive overlay.  "persistence_rebalances" is intentionally explicit:
    # on a weekly model, 2 means two weekly decision events, not two daily bars.
    systemic_trigger_percentile: float = .82
    persistence_rebalances: int = 2
    maximum_additional_gross_reduction: float = .10
    minimum_predictive_gross_change: float = .015

    # Structural sleeve.  The full repair is blended toward V1 by this strength,
    # then accepted only if the economic gate below passes.
    structural_enabled: bool = True
    structural_strength: float = .50
    structural_no_trade_band: float = .015
    minimum_structural_risk_improvement: float = .01
    maximum_structural_priority_loss_fraction: float = .02
    maximum_structural_incremental_turnover: float = .02

    # Optimizer dominance gate after a systemic gross cut.
    optimizer_risk_aversion: float = .25
    optimizer_turnover_penalty: float = 2.0
    optimizer_iterations: int = 250
    optimizer_learning_rate: float = .08
    optimizer_turnover_tolerance: float = .005

    def __post_init__(self) -> None:
        if not (.50 <= self.systemic_trigger_percentile < 1.0):
            raise ValueError("Invalid V2.0.3 systemic trigger percentile")
        if not (1 <= self.persistence_rebalances <= 8):
            raise ValueError("Invalid V2.0.3 persistence_rebalances")
        if not (0 <= self.maximum_additional_gross_reduction <= .35):
            raise ValueError("Invalid V2.0.3 maximum additional gross reduction")
        if not (0 <= self.minimum_predictive_gross_change <= .20):
            raise ValueError("Invalid V2.0.3 predictive no-trade band")
        if not isinstance(self.structural_enabled, bool):
            raise ValueError("structural_enabled must be boolean")
        if not (0 <= self.structural_strength <= 1):
            raise ValueError("Invalid V2.0.3 structural strength")
        if not (0 <= self.structural_no_trade_band <= .20):
            raise ValueError("Invalid V2.0.3 structural no-trade band")
        if not (0 <= self.minimum_structural_risk_improvement <= .50):
            raise ValueError("Invalid V2.0.3 structural risk improvement floor")
        if not (0 <= self.maximum_structural_priority_loss_fraction <= .50):
            raise ValueError("Invalid V2.0.3 structural alpha-priority budget")
        if not (0 <= self.maximum_structural_incremental_turnover <= .50):
            raise ValueError("Invalid V2.0.3 structural turnover budget")
        if not (0 <= self.optimizer_risk_aversion <= 100):
            raise ValueError("Invalid V2.0.3 optimizer risk aversion")
        if not (0 <= self.optimizer_turnover_penalty <= 100):
            raise ValueError("Invalid V2.0.3 optimizer turnover penalty")
        if not (10 <= self.optimizer_iterations <= 5000):
            raise ValueError("Invalid V2.0.3 optimizer iterations")
        if not (0 < self.optimizer_learning_rate <= 1):
            raise ValueError("Invalid V2.0.3 optimizer learning rate")
        if not (0 <= self.optimizer_turnover_tolerance <= .20):
            raise ValueError("Invalid V2.0.3 optimizer turnover tolerance")


def predictive_modifier_v203(
    forecast: dict[str, Any],
    previous_persistence_rebalances: int,
    predictive_config: V202PredictiveConfig,
    control: V203ControlConfig,
) -> dict[str, Any]:
    """V2.0.2 causal predictor with explicitly rebalance-counted persistence."""
    # Reuse the validated percentile logic, but inject V2.0.3's declared controls.
    config = V202PredictiveConfig(
        feature_window=predictive_config.feature_window,
        compare_window=predictive_config.compare_window,
        short_vol_window=predictive_config.short_vol_window,
        long_vol_window=predictive_config.long_vol_window,
        forecast_horizon=predictive_config.forecast_horizon,
        training_window=predictive_config.training_window,
        training_stride=predictive_config.training_stride,
        minimum_training_observations=predictive_config.minimum_training_observations,
        ridge_penalty=predictive_config.ridge_penalty,
        persistence_sessions=control.persistence_rebalances,
        systemic_trigger_percentile=control.systemic_trigger_percentile,
        minimum_validation_r2=predictive_config.minimum_validation_r2,
        validation_fraction=predictive_config.validation_fraction,
        maximum_additional_gross_reduction=control.maximum_additional_gross_reduction,
        minimum_portfolio_change=control.minimum_predictive_gross_change,
        optimizer_risk_aversion=predictive_config.optimizer_risk_aversion,
        optimizer_turnover_penalty=predictive_config.optimizer_turnover_penalty,
        optimizer_iterations=predictive_config.optimizer_iterations,
        optimizer_learning_rate=predictive_config.optimizer_learning_rate,
        optimizer_turnover_tolerance=predictive_config.optimizer_turnover_tolerance,
    )
    result = predictive_gross_modifier(
        forecast,
        int(previous_persistence_rebalances),
        config,
    )
    # Preserve compatibility while making the event semantics explicit in audit.
    return {
        **result,
        "persistence_rebalances": int(result.get("persistence_count", 0)),
        "persistence_unit": "scheduled_rebalance_decisions",
    }


def _priority_loss_fraction(before: float, after: float) -> float:
    if after >= before:
        return 0.0
    scale = max(abs(before), 1e-8)
    return float((before - after) / scale)


def soft_structural_sleeve(
    v1_weights: np.ndarray,
    covariance: np.ndarray,
    priority: np.ndarray,
    *,
    current_weights: np.ndarray | None,
    cluster_enabled: bool,
    component_enabled: bool,
    cluster_threshold: float,
    maximum_cluster_weight: float,
    maximum_component_risk_share: float,
    maximum_trim_iterations: int,
    control: V203ControlConfig,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Create and economically gate a softened structural-risk repair.

    The full structural candidate applies complete-linkage cluster and component
    risk caps.  V2.0.3 then blends only ``structural_strength`` of that repair
    into V1 and accepts it only when modeled risk improves enough without paying
    too much alpha-priority or incremental turnover.
    """
    base = np.maximum(np.asarray(v1_weights, dtype=float).reshape(-1), 0.0)
    cov = np.asarray(covariance, dtype=float)
    p = np.asarray(priority, dtype=float).reshape(-1)
    if cov.shape != (len(base), len(base)) or len(p) != len(base):
        raise ValueError("V2.0.3 structural inputs must align")
    if not control.structural_enabled or base.sum() <= _EPS:
        return base.copy(), {
            "enabled": bool(control.structural_enabled),
            "accepted": False,
            "reason": "structural_disabled" if not control.structural_enabled else "empty_portfolio",
            "strength": float(control.structural_strength),
        }

    corr = covariance_to_correlation(cov)
    active = np.flatnonzero(base > _EPS)
    clusters = complete_linkage_clusters(corr, active, cluster_threshold) if len(active) else []

    full = base.copy()
    if cluster_enabled:
        full = apply_cluster_caps(full, clusters, maximum_cluster_weight, cov, p)
    converged = True
    iterations = 0
    if component_enabled:
        full, converged, iterations = apply_component_risk_cap(
            full,
            cov,
            p,
            maximum_component_risk_share,
            iterations=maximum_trim_iterations,
        )
    full = np.maximum(np.minimum(full, base), 0.0)

    candidate = base + float(control.structural_strength) * (full - base)
    candidate = np.maximum(np.minimum(candidate, base), 0.0)
    portfolio_change = float(np.abs(candidate - base).sum())
    if portfolio_change < control.structural_no_trade_band - 1e-12:
        return base.copy(), {
            "enabled": True,
            "accepted": False,
            "reason": "structural_no_trade_band",
            "strength": float(control.structural_strength),
            "portfolio_change": portfolio_change,
            "clusters": clusters,
            "component_cap_converged": bool(converged),
            "component_cap_iterations": int(iterations),
        }

    risk_before = annualized_portfolio_volatility(base, cov)
    risk_after = annualized_portfolio_volatility(candidate, cov)
    risk_improvement = (
        max(0.0, risk_before - risk_after) / max(risk_before, _EPS)
        if risk_before > _EPS else 0.0
    )
    priority_before = float(p @ base)
    priority_after = float(p @ candidate)
    priority_loss_fraction = _priority_loss_fraction(priority_before, priority_after)

    reference = base if current_weights is None else np.maximum(
        np.asarray(current_weights, dtype=float).reshape(-1), 0.0
    )
    if len(reference) != len(base):
        raise ValueError("current_weights length must match V1 weights")
    baseline_turnover = float(np.abs(base - reference).sum())
    candidate_turnover = float(np.abs(candidate - reference).sum())
    incremental_turnover = max(0.0, candidate_turnover - baseline_turnover)

    risk_gate = risk_improvement + 1e-12 >= control.minimum_structural_risk_improvement
    priority_gate = priority_loss_fraction <= control.maximum_structural_priority_loss_fraction + 1e-12
    turnover_gate = incremental_turnover <= control.maximum_structural_incremental_turnover + 1e-12
    accepted = bool(risk_gate and priority_gate and turnover_gate)

    max_component_before = float(component_risk_shares(base, cov).max()) if len(base) else 0.0
    max_component_after = float(component_risk_shares(candidate, cov).max()) if len(base) else 0.0
    audit = {
        "enabled": True,
        "accepted": accepted,
        "reason": "economic_gate_passed" if accepted else "economic_gate_failed",
        "strength": float(control.structural_strength),
        "portfolio_change": portfolio_change,
        "risk_before": float(risk_before),
        "risk_after": float(risk_after),
        "relative_risk_improvement": float(risk_improvement),
        "minimum_risk_improvement": float(control.minimum_structural_risk_improvement),
        "priority_before": priority_before,
        "priority_after": priority_after,
        "priority_loss_fraction": priority_loss_fraction,
        "maximum_priority_loss_fraction": float(control.maximum_structural_priority_loss_fraction),
        "baseline_turnover": baseline_turnover,
        "candidate_turnover": candidate_turnover,
        "incremental_turnover": incremental_turnover,
        "maximum_incremental_turnover": float(control.maximum_structural_incremental_turnover),
        "max_component_risk_before": max_component_before,
        "max_component_risk_after": max_component_after,
        "clusters": clusters,
        "cluster_intervention_enabled": bool(cluster_enabled),
        "component_intervention_enabled": bool(component_enabled),
        "component_cap_converged": bool(converged),
        "component_cap_iterations": int(iterations),
        "gates": {
            "risk": bool(risk_gate),
            "priority": bool(priority_gate),
            "turnover": bool(turnover_gate),
        },
    }
    return (candidate if accepted else base.copy()), audit


def bounded_predictive_trim(
    upper_weights: np.ndarray,
    target_gross: float,
    covariance: np.ndarray,
    priority: np.ndarray,
    *,
    current_weights: np.ndarray | None,
    control: V203ControlConfig,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Apply systemic gross reduction, accepting optimization only if it dominates.

    ``upper_weights`` may already include an accepted structural sleeve.  This
    function cannot add any exposure beyond that upper bound.
    """
    upper = np.maximum(np.asarray(upper_weights, dtype=float).reshape(-1), 0.0)
    target = float(np.clip(target_gross, 0.0, upper.sum()))
    if target >= float(upper.sum()) - 1e-12:
        return upper.copy(), {
            "selected": "no_change",
            "dominance_gate_passed": False,
            "iterations": 0,
            "objective_change": 0.0,
        }

    proportional = upper * (target / max(float(upper.sum()), _EPS))
    optimized, core = alpha_aware_quadratic_trim(
        upper,
        target,
        covariance,
        priority,
        current_weights=current_weights,
        risk_aversion=control.optimizer_risk_aversion,
        turnover_penalty=control.optimizer_turnover_penalty,
        iterations=control.optimizer_iterations,
        learning_rate=control.optimizer_learning_rate,
    )
    reference = upper if current_weights is None else np.maximum(
        np.asarray(current_weights, dtype=float).reshape(-1), 0.0
    )
    proportional_risk = annualized_portfolio_volatility(proportional, covariance)
    optimized_risk = annualized_portfolio_volatility(optimized, covariance)
    proportional_priority = float(priority @ proportional)
    optimized_priority = float(priority @ optimized)
    proportional_turnover = float(np.abs(proportional - reference).sum())
    optimized_turnover = float(np.abs(optimized - reference).sum())
    turnover_limit = proportional_turnover + control.optimizer_turnover_tolerance
    dominates = bool(
        optimized_risk <= proportional_risk + 1e-10
        and optimized_priority >= proportional_priority - 1e-10
        and optimized_turnover <= turnover_limit + 1e-10
    )
    return (optimized if dominates else proportional), {
        **core,
        "selected": "optimized" if dominates else "proportional_fallback",
        "dominance_gate_passed": dominates,
        "proportional_risk": float(proportional_risk),
        "optimized_risk": float(optimized_risk),
        "proportional_priority": proportional_priority,
        "optimized_priority": optimized_priority,
        "proportional_turnover": proportional_turnover,
        "optimized_turnover": optimized_turnover,
        "turnover_limit": float(turnover_limit),
    }
