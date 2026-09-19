"""Causal portfolio risk supervision shared by Universal research models.

V1 compatibility remains available for frozen studies. V2.0.1 is retained as
a rejected/reproducible fragility branch. V2.0.2 is the current research branch:
it anchors every decision to V1, keeps the richer diagnostics, and permits only
validated, persistent, bounded additional de-risking. All cross-sectional inputs
must be available through the completed close; execution remains next-open.

Minerva's frozen baseline stays disabled unless a caller explicitly opts in.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math
from typing import Any, Sequence

import numpy as np

from .universal_risk_v201 import (
    V201DiagnosticsConfig,
    adaptive_covariance_ensemble,
    alpha_priority,
    annualized_portfolio_volatility,
    apply_cluster_caps,
    apply_component_risk_cap,
    component_risk_shares,
    complete_linkage_clusters,
    covariance_to_correlation,
    effective_bets,
    evaluate_what_if,
    fragility_components,
    liquidity_diagnostics,
    moving_block_bootstrap_volatility_uncertainty,
    pca_risk_structure,
    risk_radar,
    statistical_factor_risk,
    synthetic_stress_tests,
    topology_metrics,
    trim_to_gross,
    vectorized_tail_dependence,
    weighted_fragility_score,
)
from .universal_risk_v202 import (
    V202PredictiveConfig,
    alpha_aware_quadratic_trim,
    portfolio_weighted_risk_structure,
    portfolio_weighted_tail_dependence,
    predictive_gross_modifier,
)


RISK_MODEL_ID = "universal_stock_risk_supervisor_v2_0_1"
RISK_MODEL_V202_ID = "universal_stock_risk_supervisor_v2_0_2"


@dataclass(frozen=True)
class RiskSupervisorConfig:
    # V1 / compatibility switches -------------------------------------------------
    enabled: bool = False
    component_risk_enabled: bool = True
    correlation_cluster_enabled: bool = True
    correlation_regime_enabled: bool = True
    diversification_enabled: bool = True
    realized_volatility_enabled: bool = True
    volatility_shock_enabled: bool = True
    tail_loss_enabled: bool = True
    drawdown_enabled: bool = True
    gradual_recovery_enabled: bool = True
    maximum_risk_contribution: float = .22
    correlation_cluster_threshold: float = .75
    maximum_cluster_weight: float = .35
    maximum_average_correlation: float = .55
    correlation_regime_floor_scale: float = .50
    minimum_effective_bets: float = 4.0
    concentration_floor_scale: float = .40
    realized_volatility_window: int = 21
    realized_volatility_floor_scale: float = .35
    volatility_shock_short_window: int = 10
    volatility_shock_threshold: float = 1.50
    volatility_shock_floor_scale: float = .50
    tail_loss_window: int = 63
    tail_probability: float = .10
    maximum_expected_shortfall_multiple: float = 2.75
    tail_loss_floor_scale: float = .35
    drawdown_lookback_sessions: int = 63
    soft_drawdown: float = .06
    hard_drawdown: float = .14
    hard_drawdown_scale: float = .30
    scale_rebalance_step: float = .05
    maximum_daily_recovery_step: float = .10

    # V2.0.1 explicit research profile -------------------------------------------
    v201_enabled: bool = False
    factor_risk_enabled: bool = True
    fragility_enabled: bool = True
    tail_dependence_enabled: bool = True
    risk_uncertainty_enabled: bool = True
    liquidity_enabled: bool = True
    alpha_preservation_enabled: bool = True
    stress_testing_enabled: bool = True
    risk_radar_enabled: bool = True

    v201_long_window: int = 126
    v201_short_window: int = 21
    v201_topology_window: int = 42
    v201_topology_compare_window: int = 21
    v201_tail_window: int = 126
    v201_tail_quantile: float = .10
    v201_covariance_shrinkage: float = .35
    v201_covariance_anchor_weight: float = .20
    v201_ewma_half_life: float = 42.0
    v201_bootstrap_samples: int = 120
    v201_bootstrap_block_size: int = 5
    v201_bootstrap_seed: int = 701
    v201_statistical_factor_count: int = 5
    v201_liquidity_participation: float = .10
    v201_liquidity_horizon_sessions: int = 5
    v201_maximum_liquidation_days: float = 5.0
    v201_converging_score: float = 30.0
    v201_fragile_score: float = 50.0
    v201_systemic_score: float = 70.0
    v201_converging_scale: float = .85
    v201_fragile_scale: float = .65
    v201_systemic_scale: float = .35
    v201_recovery_scale: float = .55
    v201_state_recovery_score: float = 20.0
    v201_maximum_trim_iterations: int = 120

    # V2.0.2: V1-anchored predictive risk overlay. V2.0.1 diagnostics remain
    # available, but their hand-weighted fragility score is diagnostic only.
    v202_enabled: bool = False
    v202_predictive_risk_enabled: bool = True
    v202_structural_intervention_enabled: bool = False
    v202_liquidity_intervention_enabled: bool = False
    v202_forecast_horizon: int = 10
    v202_training_window: int = 756
    v202_training_stride: int = 5
    v202_minimum_training_observations: int = 60
    v202_ridge_penalty: float = 8.0
    v202_persistence_sessions: int = 3
    v202_systemic_trigger_percentile: float = .85
    v202_minimum_validation_r2: float = 0.0
    v202_validation_fraction: float = .25
    v202_maximum_additional_gross_reduction: float = .15
    v202_minimum_portfolio_change: float = .02
    v202_optimizer_risk_aversion: float = .25
    v202_optimizer_turnover_penalty: float = 2.0
    v202_optimizer_iterations: int = 250
    v202_optimizer_learning_rate: float = .08
    v202_optimizer_turnover_tolerance: float = .005

    def __post_init__(self) -> None:
        switches = [
            self.enabled, self.component_risk_enabled, self.correlation_cluster_enabled,
            self.correlation_regime_enabled, self.diversification_enabled,
            self.realized_volatility_enabled, self.volatility_shock_enabled,
            self.tail_loss_enabled, self.drawdown_enabled, self.gradual_recovery_enabled,
            self.v201_enabled, self.factor_risk_enabled, self.fragility_enabled,
            self.tail_dependence_enabled, self.risk_uncertainty_enabled,
            self.liquidity_enabled, self.alpha_preservation_enabled,
            self.stress_testing_enabled, self.risk_radar_enabled,
            self.v202_enabled, self.v202_predictive_risk_enabled,
            self.v202_structural_intervention_enabled, self.v202_liquidity_intervention_enabled,
        ]
        if not all(isinstance(value, bool) for value in switches):
            raise ValueError("Risk supervisor switches must be boolean")
        numeric_values = [
            value for value in asdict(self).values()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        if not all(math.isfinite(float(value)) for value in numeric_values):
            raise ValueError("Risk supervisor numeric settings must be finite")
        if not (.05 <= self.maximum_risk_contribution <= .50):
            raise ValueError("maximum_risk_contribution must be within [.05, .50]")
        if not (.25 <= self.correlation_cluster_threshold <= .99):
            raise ValueError("correlation_cluster_threshold must be within [.25, .99]")
        if not (.10 <= self.maximum_cluster_weight <= 1):
            raise ValueError("maximum_cluster_weight must be within [.10, 1]")
        if not (.10 <= self.maximum_average_correlation <= .95 and 0 < self.correlation_regime_floor_scale <= 1):
            raise ValueError("Invalid correlation-regime controls")
        if not (1 <= self.minimum_effective_bets <= 24):
            raise ValueError("minimum_effective_bets must be within [1, 24]")
        if not (0 < self.concentration_floor_scale <= 1 and 0 < self.realized_volatility_floor_scale <= 1):
            raise ValueError("Risk floor scales must be within (0, 1]")
        if not (10 <= self.realized_volatility_window <= 126 and 21 <= self.drawdown_lookback_sessions <= 252):
            raise ValueError("Invalid realized-risk lookback")
        if not (5 <= self.volatility_shock_short_window < self.realized_volatility_window
                and 1 < self.volatility_shock_threshold <= 5
                and 0 < self.volatility_shock_floor_scale <= 1):
            raise ValueError("Invalid volatility-shock controls")
        if not (21 <= self.tail_loss_window <= 252 and .01 <= self.tail_probability <= .25
                and 1 <= self.maximum_expected_shortfall_multiple <= 10
                and 0 < self.tail_loss_floor_scale <= 1):
            raise ValueError("Invalid tail-loss controls")
        if not (0 < self.soft_drawdown < self.hard_drawdown < .75):
            raise ValueError("Drawdown thresholds must be increasing and below 75%")
        if not (0 < self.hard_drawdown_scale <= 1 and 0 < self.scale_rebalance_step <= .25
                and 0 < self.maximum_daily_recovery_step <= .50):
            raise ValueError("Invalid drawdown scale or rebalance step")
        if not (21 <= self.v201_short_window <= self.v201_long_window <= 504):
            raise ValueError("Invalid V2.0.1 covariance windows")
        if not (10 <= self.v201_topology_compare_window <= self.v201_topology_window <= 252):
            raise ValueError("Invalid V2.0.1 topology windows")
        if not (21 <= self.v201_tail_window <= 504 and .01 <= self.v201_tail_quantile <= .25):
            raise ValueError("Invalid V2.0.1 tail settings")
        if not (0 <= self.v201_covariance_shrinkage <= 1 and 0 <= self.v201_covariance_anchor_weight <= .5):
            raise ValueError("Invalid V2.0.1 covariance blend")
        if not (1 < self.v201_ewma_half_life <= 252):
            raise ValueError("Invalid V2.0.1 EWMA half life")
        if not (0 <= self.v201_bootstrap_samples <= 2000 and 1 <= self.v201_bootstrap_block_size <= 63):
            raise ValueError("Invalid V2.0.1 bootstrap settings")
        if not (1 <= self.v201_statistical_factor_count <= 12):
            raise ValueError("Invalid V2.0.1 statistical factor count")
        if not (0 < self.v201_liquidity_participation <= .25 and 1 <= self.v201_liquidity_horizon_sessions <= 20):
            raise ValueError("Invalid V2.0.1 liquidity settings")
        if not (0 < self.v201_converging_score < self.v201_fragile_score < self.v201_systemic_score < 100):
            raise ValueError("V2.0.1 fragility thresholds must be increasing below 100")
        if not all(0 < value <= 1 for value in (
            self.v201_converging_scale, self.v201_fragile_scale,
            self.v201_systemic_scale, self.v201_recovery_scale,
        )):
            raise ValueError("V2.0.1 state exposure scales must be within (0, 1]")
        if not (0 <= self.v201_state_recovery_score < self.v201_converging_score):
            raise ValueError("V2.0.1 recovery score must be below converging score")
        V202PredictiveConfig(
            feature_window=self.v201_topology_window,
            compare_window=self.v201_topology_compare_window,
            short_vol_window=self.v201_short_window,
            long_vol_window=max(self.v201_long_window // 2, self.v201_short_window + 1),
            forecast_horizon=self.v202_forecast_horizon,
            training_window=self.v202_training_window,
            training_stride=self.v202_training_stride,
            minimum_training_observations=self.v202_minimum_training_observations,
            ridge_penalty=self.v202_ridge_penalty,
            persistence_sessions=self.v202_persistence_sessions,
            systemic_trigger_percentile=self.v202_systemic_trigger_percentile,
            minimum_validation_r2=self.v202_minimum_validation_r2,
            validation_fraction=self.v202_validation_fraction,
            maximum_additional_gross_reduction=self.v202_maximum_additional_gross_reduction,
            minimum_portfolio_change=self.v202_minimum_portfolio_change,
            optimizer_risk_aversion=self.v202_optimizer_risk_aversion,
            optimizer_turnover_penalty=self.v202_optimizer_turnover_penalty,
            optimizer_iterations=self.v202_optimizer_iterations,
            optimizer_learning_rate=self.v202_optimizer_learning_rate,
            optimizer_turnover_tolerance=self.v202_optimizer_turnover_tolerance,
        )


def v201_risk_config(**overrides: object) -> RiskSupervisorConfig:
    """Recommended V2.0.1 research profile without altering frozen V1 studies.

    Cross-sectional V2.0.1 owns volatility/correlation/tail fragility. The
    execution layer retains the drawdown circuit breaker as an independent
    realized-loss failsafe, avoiding duplicate realized-vol / tail scaling.
    """
    base = RiskSupervisorConfig(
        enabled=True,
        v201_enabled=True,
        component_risk_enabled=True,
        correlation_cluster_enabled=True,
        correlation_regime_enabled=False,
        diversification_enabled=True,
        realized_volatility_enabled=False,
        volatility_shock_enabled=False,
        tail_loss_enabled=False,
        drawdown_enabled=True,
        gradual_recovery_enabled=True,
        maximum_risk_contribution=.18,
        correlation_cluster_threshold=.70,
        maximum_cluster_weight=.35,
        minimum_effective_bets=4.0,
    )
    return replace(base, **overrides)


def v202_risk_config(**overrides: object) -> RiskSupervisorConfig:
    """V2.0.2 research profile anchored to the proven TBA8 V1 controls.

    The V1 component-risk supervisor remains the baseline. New structural metrics
    are diagnostic by default; only the causal predictive-volatility overlay may
    make an additional, persistence-gated and capped gross reduction.
    """
    base = RiskSupervisorConfig(
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
        v202_enabled=True,
        v202_predictive_risk_enabled=True,
        v202_structural_intervention_enabled=False,
        v202_liquidity_intervention_enabled=False,
    )
    return replace(base, **overrides)


def _correlation_clusters(covariance: np.ndarray, active: np.ndarray, threshold: float) -> list[list[int]]:
    correlation = covariance_to_correlation(covariance)
    # Preserve V1 semantics for compatibility studies.
    remaining = set(int(index) for index in active)
    clusters: list[list[int]] = []
    while remaining:
        seed = remaining.pop()
        component = {seed}
        frontier = [seed]
        while frontier:
            node = frontier.pop()
            linked = {index for index in remaining if correlation[node, index] >= threshold}
            remaining.difference_update(linked)
            component.update(linked)
            frontier.extend(linked)
        clusters.append(sorted(component))
    return clusters


def _legacy_supervise(weights: np.ndarray, covariance: np.ndarray,
                      config: RiskSupervisorConfig) -> tuple[np.ndarray, dict[str, Any]]:
    adjusted = np.asarray(weights, dtype=float).copy()
    covariance = np.asarray(covariance, dtype=float)
    if not config.enabled or adjusted.sum() <= 0:
        return adjusted, {"enabled": config.enabled, "scale": 1.0, "cluster_count": 0, "model": "v1_compat"}
    if covariance.shape != (len(adjusted), len(adjusted)) or not np.isfinite(covariance).all():
        raise ValueError("Risk supervisor requires a finite square covariance matrix")
    active = np.flatnonzero(adjusted > 0)
    correlation = covariance_to_correlation(covariance)
    clusters = _correlation_clusters(covariance, active, config.correlation_cluster_threshold)
    if config.correlation_cluster_enabled:
        for cluster in clusters:
            cluster_weight = float(adjusted[cluster].sum())
            if cluster_weight > config.maximum_cluster_weight:
                adjusted[cluster] *= config.maximum_cluster_weight / cluster_weight
    if config.component_risk_enabled:
        for _ in range(12):
            share = component_risk_shares(adjusted, covariance)
            offenders = np.flatnonzero((adjusted > 0) & (share > config.maximum_risk_contribution + 1e-10))
            if not len(offenders):
                break
            for index in offenders:
                adjusted[index] *= config.maximum_risk_contribution / share[index]
    gross = float(adjusted.sum())
    bet_count = 0.0
    average_positive_correlation = 0.0
    correlation_scale = 1.0
    concentration_scale = 1.0
    if gross > 0:
        bet_count = effective_bets(adjusted)
        if config.diversification_enabled and bet_count < config.minimum_effective_bets:
            concentration_scale = max(config.concentration_floor_scale, bet_count / config.minimum_effective_bets)
            adjusted *= concentration_scale
        if config.correlation_regime_enabled and len(active) > 1:
            active_correlation = correlation[np.ix_(active, active)]
            off_diagonal = active_correlation[~np.eye(len(active), dtype=bool)]
            average_positive_correlation = float(np.maximum(off_diagonal, 0.0).mean())
            if average_positive_correlation > config.maximum_average_correlation:
                correlation_scale = max(
                    config.correlation_regime_floor_scale,
                    config.maximum_average_correlation / average_positive_correlation,
                )
                adjusted *= correlation_scale
    return adjusted, {
        "enabled": True,
        "model": "v1_compat",
        "scale": float(adjusted.sum() / max(weights.sum(), 1e-18)),
        "cluster_count": len(clusters),
        "effective_bets": bet_count,
        "average_positive_correlation": average_positive_correlation,
        "correlation_regime_scale": correlation_scale,
        "concentration_scale": concentration_scale,
    }


def _classify_state(score: float, previous_state: str | None, config: RiskSupervisorConfig) -> str:
    if score >= config.v201_systemic_score:
        raw = "systemic"
    elif score >= config.v201_fragile_score:
        raw = "fragile"
    elif score >= config.v201_converging_score:
        raw = "converging"
    else:
        raw = "normal"
    if previous_state in {"fragile", "systemic", "recovery"} and raw in {"normal", "converging"}:
        if score > config.v201_state_recovery_score:
            return "recovery"
    return raw


def _state_scale(state: str, config: RiskSupervisorConfig) -> float:
    return {
        "normal": 1.0,
        "converging": config.v201_converging_scale,
        "fragile": config.v201_fragile_scale,
        "systemic": config.v201_systemic_scale,
        "recovery": config.v201_recovery_scale,
    }[state]


def _serializable_liquidity(liquidity: dict[str, Any]) -> dict[str, Any]:
    output = dict(liquidity)
    for key in ("days_to_liquidate", "maximum_liquid_weight"):
        if key in output and isinstance(output[key], np.ndarray):
            output[key] = output[key].tolist()
    return output


def _supervise_v201(
    weights: np.ndarray,
    covariance: np.ndarray,
    config: RiskSupervisorConfig,
    *,
    returns_history: np.ndarray,
    expected_alpha: np.ndarray | None,
    prediction_error: np.ndarray | None,
    fallback_priority: np.ndarray | None,
    adv_dollars: np.ndarray | None,
    capital: float | None,
    symbols: Sequence[str] | None,
    previous_state: str | None,
) -> tuple[np.ndarray, dict[str, Any]]:
    proposed = np.asarray(weights, dtype=float).reshape(-1)
    if proposed.sum() <= 0:
        return proposed.copy(), {
            "enabled": True, "model": RISK_MODEL_ID, "state": "normal", "fragility_score": 0.0,
            "scale": 1.0, "cluster_count": 0, "risk_radar": [], "stress_tests": [],
        }
    returns_history = np.asarray(returns_history, dtype=float)
    if returns_history.ndim != 2 or returns_history.shape[1] != len(proposed):
        raise ValueError("V2.0.1 returns_history must align with the portfolio universe")
    if not np.isfinite(returns_history).all():
        raise ValueError("V2.0.1 returns_history must be finite")
    diagnostics = V201DiagnosticsConfig(
        long_window=config.v201_long_window,
        short_window=config.v201_short_window,
        downside_quantile=.35,
        tail_quantile=config.v201_tail_quantile,
        covariance_shrinkage=config.v201_covariance_shrinkage,
        covariance_anchor_weight=config.v201_covariance_anchor_weight,
        ewma_half_life=config.v201_ewma_half_life,
        cluster_threshold=config.correlation_cluster_threshold,
        bootstrap_samples=config.v201_bootstrap_samples if config.risk_uncertainty_enabled else 0,
        bootstrap_block_size=config.v201_bootstrap_block_size,
        bootstrap_seed=config.v201_bootstrap_seed,
        statistical_factor_count=config.v201_statistical_factor_count,
    )
    ensemble_covariance, ensemble_audit = adaptive_covariance_ensemble(
        returns_history, proposed, diagnostics, anchor_covariance=covariance,
    )
    priority = alpha_priority(expected_alpha, prediction_error, fallback_priority, len(proposed))
    topology = topology_metrics(
        returns_history, proposed, config.correlation_cluster_threshold,
        config.v201_topology_window, config.v201_topology_compare_window,
    )
    structure = pca_risk_structure(ensemble_covariance, config.v201_statistical_factor_count)
    factor_risk = (
        statistical_factor_risk(proposed, ensemble_covariance, symbols, config.v201_statistical_factor_count)
        if config.factor_risk_enabled else {}
    )
    tail_sample = returns_history[-min(config.v201_tail_window, len(returns_history)):]
    tail_dependence = (
        vectorized_tail_dependence(tail_sample, config.v201_tail_quantile)
        if config.tail_dependence_enabled else 1.0
    )
    uncertainty = (
        moving_block_bootstrap_volatility_uncertainty(
            returns_history[-min(config.v201_long_window, len(returns_history)):], proposed,
            config.v201_bootstrap_samples, config.v201_bootstrap_block_size, config.v201_bootstrap_seed,
        ) if config.risk_uncertainty_enabled else {"p10": 0.0, "median": 0.0, "p90": 0.0, "relative_width": 0.0}
    )
    liquidity = (
        liquidity_diagnostics(
            proposed, capital, adv_dollars, config.v201_liquidity_participation,
            config.v201_liquidity_horizon_sessions,
        ) if config.liquidity_enabled else {"available": False, "reason": "disabled"}
    )
    original_gross = float(proposed.sum())
    original_volatility = annualized_portfolio_volatility(proposed, ensemble_covariance)
    original_bets = effective_bets(proposed)
    original_component = component_risk_shares(proposed, ensemble_covariance)
    max_liquidity_days = (
        float(liquidity["max_days_to_liquidate"])
        if liquidity.get("available") and math.isfinite(float(liquidity["max_days_to_liquidate"])) else None
    )
    components = fragility_components(
        first_pc_share=float(structure["first_pc_share"]),
        effective_dimension=float(structure["effective_dimension"]),
        average_correlation=float(topology["average_positive_correlation"]),
        correlation_convergence=float(topology["correlation_convergence"]),
        network_density=float(topology["network_density"]),
        tail_dependence=float(tail_dependence),
        volatility_acceleration_ratio=float(ensemble_audit["volatility_acceleration"]),
        risk_uncertainty_width=float(uncertainty["relative_width"]),
        effective_bet_count=original_bets,
        minimum_effective_bets=config.minimum_effective_bets,
        liquidity_days=max_liquidity_days,
        maximum_liquidity_days=config.v201_maximum_liquidation_days,
    )
    fragility_score = weighted_fragility_score(components) if config.fragility_enabled else 0.0
    state = _classify_state(fragility_score, previous_state, config)
    state_scale = _state_scale(state, config) if config.fragility_enabled else 1.0
    # Universal's existing ex-ante volatility target remains the final absolute
    # volatility ceiling in ``portfolio_targets``. V2.0.1 chooses *which* risk
    # to remove and applies its fragility state scale here, avoiding a second
    # duplicate covariance-volatility target inside the supervisor itself.
    diversification_scale = 1.0
    if config.diversification_enabled and original_bets > 0 and original_bets < config.minimum_effective_bets:
        diversification_scale = max(config.concentration_floor_scale, original_bets / config.minimum_effective_bets)

    adjusted = proposed.copy()
    if liquidity.get("available"):
        liquid_cap = np.asarray(liquidity["maximum_liquid_weight"], dtype=float)
        adjusted = np.minimum(adjusted, liquid_cap)
    active = np.flatnonzero(adjusted > 1e-12)
    correlation = covariance_to_correlation(ensemble_covariance)
    clusters = complete_linkage_clusters(correlation, active, config.correlation_cluster_threshold) if len(active) else []
    if config.correlation_cluster_enabled:
        adjusted = apply_cluster_caps(
            adjusted, clusters, config.maximum_cluster_weight, ensemble_covariance, priority,
        )
    component_converged = True
    component_iterations = 0
    if config.component_risk_enabled:
        adjusted, component_converged, component_iterations = apply_component_risk_cap(
            adjusted, ensemble_covariance, priority, config.maximum_risk_contribution,
            iterations=config.v201_maximum_trim_iterations,
        )
    target_gross = min(float(adjusted.sum()), original_gross * state_scale * diversification_scale)
    if config.alpha_preservation_enabled:
        adjusted = trim_to_gross(adjusted, target_gross, ensemble_covariance, priority)
    elif adjusted.sum() > target_gross + 1e-12:
        adjusted *= target_gross / float(adjusted.sum())
    adjusted = np.maximum(np.minimum(adjusted, proposed), 0.0)

    final_component = component_risk_shares(adjusted, ensemble_covariance)
    final_volatility = annualized_portfolio_volatility(adjusted, ensemble_covariance)
    final_bets = effective_bets(adjusted)
    before_what_if = evaluate_what_if(proposed, ensemble_covariance, priority)
    after_what_if = evaluate_what_if(adjusted, ensemble_covariance, priority)
    stress_tests = synthetic_stress_tests(adjusted, ensemble_covariance, factor_risk, symbols) if config.stress_testing_enabled else []
    radar = risk_radar(
        fragility_score=fragility_score,
        first_pc_share=float(structure["first_pc_share"]),
        effective_dimension=float(structure["effective_dimension"]),
        average_correlation=float(topology["average_positive_correlation"]),
        correlation_convergence=float(topology["correlation_convergence"]),
        tail_dependence=float(tail_dependence),
        volatility_acceleration_ratio=float(ensemble_audit["volatility_acceleration"]),
        uncertainty_width=float(uncertainty["relative_width"]),
        maximum_component_risk=float(final_component.max()) if len(final_component) else 0.0,
        max_days_to_liquidate=max_liquidity_days,
        component_limit=config.maximum_risk_contribution,
    ) if config.risk_radar_enabled else []

    if np.any(adjusted < -1e-12) or np.any(adjusted - proposed > 1e-10):
        raise AssertionError("V2.0.1 supervisor violated long-only / no-risk-increase invariant")
    if float(adjusted.sum()) > original_gross + 1e-10:
        raise AssertionError("V2.0.1 supervisor increased gross exposure")
    if not np.isfinite(adjusted).all():
        raise AssertionError("V2.0.1 supervisor produced non-finite weights")

    alpha_before = float(priority @ proposed)
    alpha_after = float(priority @ adjusted)
    alpha_retention = alpha_after / alpha_before if abs(alpha_before) > 1e-12 else None
    return adjusted, {
        "enabled": True,
        "model": RISK_MODEL_ID,
        "state": state,
        "previous_state": previous_state,
        "fragility_score": float(fragility_score),
        "fragility_components": components,
        "scale": float(adjusted.sum() / max(original_gross, 1e-18)),
        "gross_before": original_gross,
        "gross_after": float(adjusted.sum()),
        "state_scale": float(state_scale),
        "diversification_scale": float(diversification_scale),
        "portfolio_volatility_before": float(original_volatility),
        "portfolio_volatility_after": float(final_volatility),
        "effective_bets_before": float(original_bets),
        "effective_bets_after": float(final_bets),
        "maximum_component_risk_before": float(original_component.max()) if len(original_component) else 0.0,
        "maximum_component_risk_after": float(final_component.max()) if len(final_component) else 0.0,
        "component_cap_converged": bool(component_converged),
        "component_cap_iterations": int(component_iterations),
        "cluster_count": len(clusters),
        "clusters": clusters,
        "average_positive_correlation": float(topology["average_positive_correlation"]),
        "correlation_convergence": float(topology["correlation_convergence"]),
        "network_density": float(topology["network_density"]),
        "first_pc_share": float(structure["first_pc_share"]),
        "effective_risk_dimension": float(structure["effective_dimension"]),
        "tail_dependence_ratio": float(tail_dependence),
        "volatility_acceleration": float(ensemble_audit["volatility_acceleration"]),
        "risk_estimate_uncertainty": uncertainty,
        "covariance_ensemble": ensemble_audit,
        "factor_risk": factor_risk,
        "liquidity": _serializable_liquidity(liquidity),
        "alpha_priority_before": alpha_before,
        "alpha_priority_after": alpha_after,
        "alpha_priority_retention": alpha_retention,
        "what_if": {"before": before_what_if, "after": after_what_if},
        "stress_tests": stress_tests,
        "risk_radar": radar,
    }




def _v202_predictive_config(config: RiskSupervisorConfig) -> V202PredictiveConfig:
    return V202PredictiveConfig(
        feature_window=config.v201_topology_window,
        compare_window=config.v201_topology_compare_window,
        short_vol_window=config.v201_short_window,
        long_vol_window=max(config.v201_long_window // 2, config.v201_short_window + 1),
        forecast_horizon=config.v202_forecast_horizon,
        training_window=config.v202_training_window,
        training_stride=config.v202_training_stride,
        minimum_training_observations=config.v202_minimum_training_observations,
        ridge_penalty=config.v202_ridge_penalty,
        persistence_sessions=config.v202_persistence_sessions,
        systemic_trigger_percentile=config.v202_systemic_trigger_percentile,
        minimum_validation_r2=config.v202_minimum_validation_r2,
        validation_fraction=config.v202_validation_fraction,
        maximum_additional_gross_reduction=config.v202_maximum_additional_gross_reduction,
        minimum_portfolio_change=config.v202_minimum_portfolio_change,
        optimizer_risk_aversion=config.v202_optimizer_risk_aversion,
        optimizer_turnover_penalty=config.v202_optimizer_turnover_penalty,
        optimizer_iterations=config.v202_optimizer_iterations,
        optimizer_learning_rate=config.v202_optimizer_learning_rate,
        optimizer_turnover_tolerance=config.v202_optimizer_turnover_tolerance,
    )


def _supervise_v202(
    weights: np.ndarray,
    covariance: np.ndarray,
    config: RiskSupervisorConfig,
    *,
    returns_history: np.ndarray,
    expected_alpha: np.ndarray | None,
    prediction_error: np.ndarray | None,
    fallback_priority: np.ndarray | None,
    adv_dollars: np.ndarray | None,
    capital: float | None,
    symbols: Sequence[str] | None,
    predictive_forecast: dict[str, Any] | None,
    previous_persistence_count: int,
    current_weights: np.ndarray | None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """V1-anchored V2.0.2 intervention.

    V1 owns baseline structural risk. V2.0.2 keeps V2.0.1 diagnostics but only
    makes an additional portfolio change when a completed-label predictive-risk
    forecast persists. The hand-weighted fragility score never controls exposure.
    """
    proposed = np.asarray(weights, dtype=float).reshape(-1)
    if proposed.sum() <= 0:
        return proposed.copy(), {
            "enabled": True,
            "model": RISK_MODEL_V202_ID,
            "scale": 1.0,
            "persistence_count": 0,
            "predictive_forecast": predictive_forecast or {"available": False, "reason": "empty_portfolio"},
        }
    history = np.asarray(returns_history, dtype=float)
    if history.ndim != 2 or history.shape[1] != len(proposed) or not np.isfinite(history).all():
        raise ValueError("V2.0.2 returns_history must be finite and align with the portfolio")

    # 1) Preserve the exact V1-compatible risk decision as the baseline.
    v1_weights, v1_audit = _legacy_supervise(proposed, covariance, config)
    v1_gross = float(v1_weights.sum())

    # 2) Retain the richer V2.0.1 measurement stack for diagnostics / research.
    diagnostics = V201DiagnosticsConfig(
        long_window=config.v201_long_window,
        short_window=config.v201_short_window,
        downside_quantile=.35,
        tail_quantile=config.v201_tail_quantile,
        covariance_shrinkage=config.v201_covariance_shrinkage,
        covariance_anchor_weight=config.v201_covariance_anchor_weight,
        ewma_half_life=config.v201_ewma_half_life,
        cluster_threshold=config.correlation_cluster_threshold,
        bootstrap_samples=config.v201_bootstrap_samples if config.risk_uncertainty_enabled else 0,
        bootstrap_block_size=config.v201_bootstrap_block_size,
        bootstrap_seed=config.v201_bootstrap_seed,
        statistical_factor_count=config.v201_statistical_factor_count,
    )
    ensemble_covariance, ensemble_audit = adaptive_covariance_ensemble(
        history, v1_weights, diagnostics, anchor_covariance=covariance,
    )
    priority = alpha_priority(expected_alpha, prediction_error, fallback_priority, len(proposed))
    topology = topology_metrics(
        history, v1_weights, config.correlation_cluster_threshold,
        config.v201_topology_window, config.v201_topology_compare_window,
    )
    raw_structure = pca_risk_structure(ensemble_covariance, config.v201_statistical_factor_count)
    weighted_structure = portfolio_weighted_risk_structure(v1_weights, ensemble_covariance)
    factor_risk = (
        statistical_factor_risk(v1_weights, ensemble_covariance, symbols, config.v201_statistical_factor_count)
        if config.factor_risk_enabled else {}
    )
    tail_sample = history[-min(config.v201_tail_window, len(history)):]
    weighted_tail = (
        portfolio_weighted_tail_dependence(tail_sample, v1_weights, config.v201_tail_quantile)
        if config.tail_dependence_enabled else 1.0
    )
    uncertainty = (
        moving_block_bootstrap_volatility_uncertainty(
            history[-min(config.v201_long_window, len(history)):], v1_weights,
            config.v201_bootstrap_samples, config.v201_bootstrap_block_size, config.v201_bootstrap_seed,
        ) if config.risk_uncertainty_enabled else {"p10": 0.0, "median": 0.0, "p90": 0.0, "relative_width": 0.0}
    )
    liquidity = (
        liquidity_diagnostics(
            v1_weights, capital, adv_dollars, config.v201_liquidity_participation,
            config.v201_liquidity_horizon_sessions,
        ) if config.liquidity_enabled else {"available": False, "reason": "disabled"}
    )
    max_liquidity_days = (
        float(liquidity["max_days_to_liquidate"])
        if liquidity.get("available") and math.isfinite(float(liquidity["max_days_to_liquidate"])) else None
    )

    # Diagnostic fragility score is retained for research continuity only.
    components = fragility_components(
        first_pc_share=float(weighted_structure["first_pc_share"]),
        effective_dimension=float(weighted_structure["effective_dimension"]),
        average_correlation=float(topology["average_positive_correlation"]),
        correlation_convergence=float(topology["correlation_convergence"]),
        network_density=float(topology["network_density"]),
        tail_dependence=float(weighted_tail),
        volatility_acceleration_ratio=float(ensemble_audit["volatility_acceleration"]),
        risk_uncertainty_width=float(uncertainty["relative_width"]),
        effective_bet_count=effective_bets(v1_weights),
        minimum_effective_bets=config.minimum_effective_bets,
        liquidity_days=max_liquidity_days,
        maximum_liquidity_days=config.v201_maximum_liquidation_days,
    )
    diagnostic_fragility = weighted_fragility_score(components) if config.fragility_enabled else 0.0

    forecast = predictive_forecast or {"available": False, "reason": "predictive_panel_not_supplied"}
    # The predictive layer forecasts systemic/universe risk, not the portfolio's
    # absolute volatility.  It therefore acts on its own validated percentile
    # distribution rather than comparing unlike quantities to the portfolio vol target.
    modifier = predictive_gross_modifier(
        forecast if config.v202_predictive_risk_enabled else {"available": False, "reason": "predictive_risk_disabled"},
        int(previous_persistence_count),
        _v202_predictive_config(config),
    )

    target_gross = v1_gross * float(modifier["gross_modifier"])
    extra_cut = max(0.0, v1_gross - target_gross)
    if modifier.get("triggered") and extra_cut < config.v202_minimum_portfolio_change - 1e-12:
        target_gross = v1_gross
        modifier = {**modifier, "triggered": False, "gross_modifier": 1.0, "reason": "no_trade_band"}

    # Optional structural / liquidity intervention remains available for ablation,
    # but is off in the recommended V2.0.2 profile until it proves incremental value.
    upper = v1_weights.copy()
    correlation = covariance_to_correlation(ensemble_covariance)
    active = np.flatnonzero(upper > 1e-12)
    clusters = complete_linkage_clusters(correlation, active, config.correlation_cluster_threshold) if len(active) else []
    component_converged = True
    component_iterations = 0
    if config.v202_structural_intervention_enabled:
        if config.correlation_cluster_enabled:
            upper = apply_cluster_caps(upper, clusters, config.maximum_cluster_weight, ensemble_covariance, priority)
        if config.component_risk_enabled:
            upper, component_converged, component_iterations = apply_component_risk_cap(
                upper, ensemble_covariance, priority, config.maximum_risk_contribution,
                iterations=config.v201_maximum_trim_iterations,
            )
    if config.v202_liquidity_intervention_enabled and liquidity.get("available"):
        upper = np.minimum(upper, np.asarray(liquidity["maximum_liquid_weight"], dtype=float))
    target_gross = min(target_gross, float(upper.sum()))

    if target_gross < float(upper.sum()) - 1e-12:
        # Systemic de-risking has a simple, robust baseline: proportional scaling.
        # The alpha-aware optimizer is allowed to reshape the book only when it
        # *dominates* that baseline on modeled risk and alpha without meaningfully
        # increasing turnover.  Otherwise we fall back to proportional scaling.
        proportional = upper * (target_gross / max(float(upper.sum()), 1e-18))
        optimized, optimizer_core_audit = alpha_aware_quadratic_trim(
            upper,
            target_gross,
            ensemble_covariance,
            priority,
            current_weights=current_weights,
            risk_aversion=config.v202_optimizer_risk_aversion,
            turnover_penalty=config.v202_optimizer_turnover_penalty,
            iterations=config.v202_optimizer_iterations,
            learning_rate=config.v202_optimizer_learning_rate,
        )
        reference = upper if current_weights is None else np.maximum(np.asarray(current_weights, dtype=float).reshape(-1), 0.0)
        proportional_risk = annualized_portfolio_volatility(proportional, ensemble_covariance)
        optimized_risk = annualized_portfolio_volatility(optimized, ensemble_covariance)
        proportional_priority = float(priority @ proportional)
        optimized_priority = float(priority @ optimized)
        proportional_turnover = float(np.abs(proportional - reference).sum())
        optimized_turnover = float(np.abs(optimized - reference).sum())
        turnover_limit = proportional_turnover + config.v202_optimizer_turnover_tolerance
        optimizer_dominates = (
            optimized_risk <= proportional_risk + 1e-10
            and optimized_priority >= proportional_priority - 1e-10
            and optimized_turnover <= turnover_limit + 1e-10
        )
        adjusted = optimized if optimizer_dominates else proportional
        optimizer_audit = {
            **optimizer_core_audit,
            "selected": "optimized" if optimizer_dominates else "proportional_fallback",
            "dominance_gate_passed": bool(optimizer_dominates),
            "proportional_risk": float(proportional_risk),
            "optimized_risk": float(optimized_risk),
            "proportional_priority": proportional_priority,
            "optimized_priority": optimized_priority,
            "proportional_turnover": proportional_turnover,
            "optimized_turnover": optimized_turnover,
            "turnover_limit": turnover_limit,
        }
    else:
        adjusted = upper.copy()
        optimizer_audit = {"iterations": 0, "objective_change": 0.0, "selected": "no_change", "dominance_gate_passed": False}

    # Strong invariants: V2.0.2 can only remove exposure already approved by V1.
    adjusted = np.maximum(np.minimum(adjusted, v1_weights), 0.0)
    if np.any(adjusted - v1_weights > 1e-10) or float(adjusted.sum()) > v1_gross + 1e-10:
        raise AssertionError("V2.0.2 increased exposure beyond the V1 baseline")
    if not np.isfinite(adjusted).all():
        raise AssertionError("V2.0.2 produced non-finite weights")

    final_component = component_risk_shares(adjusted, ensemble_covariance)
    before_what_if = evaluate_what_if(v1_weights, ensemble_covariance, priority)
    after_what_if = evaluate_what_if(adjusted, ensemble_covariance, priority)
    stress_tests = synthetic_stress_tests(adjusted, ensemble_covariance, factor_risk, symbols) if config.stress_testing_enabled else []
    radar = risk_radar(
        fragility_score=diagnostic_fragility,
        first_pc_share=float(weighted_structure["first_pc_share"]),
        effective_dimension=float(weighted_structure["effective_dimension"]),
        average_correlation=float(topology["average_positive_correlation"]),
        correlation_convergence=float(topology["correlation_convergence"]),
        tail_dependence=float(weighted_tail),
        volatility_acceleration_ratio=float(ensemble_audit["volatility_acceleration"]),
        uncertainty_width=float(uncertainty["relative_width"]),
        maximum_component_risk=float(final_component.max()) if len(final_component) else 0.0,
        max_days_to_liquidate=max_liquidity_days,
        component_limit=config.maximum_risk_contribution,
    ) if config.risk_radar_enabled else []

    priority_v1 = float(priority @ v1_weights)
    priority_after = float(priority @ adjusted)
    return adjusted, {
        "enabled": True,
        "model": RISK_MODEL_V202_ID,
        "scale": float(adjusted.sum() / max(float(proposed.sum()), 1e-18)),
        "v1_scale": float(v1_gross / max(float(proposed.sum()), 1e-18)),
        "v1_baseline": v1_audit,
        "gross_before": float(proposed.sum()),
        "gross_after_v1": v1_gross,
        "gross_after": float(adjusted.sum()),
        "predictive_forecast": forecast,
        "predictive_modifier": modifier,
        "persistence_count": int(modifier.get("persistence_count", 0)),
        "fragility_score": float(diagnostic_fragility),
        "fragility_score_role": "diagnostic_only_not_an_exposure_trigger",
        "fragility_components": components,
        "portfolio_weighted_first_pc_share": float(weighted_structure["first_pc_share"]),
        "portfolio_weighted_effective_dimension": float(weighted_structure["effective_dimension"]),
        "raw_first_pc_share": float(raw_structure["first_pc_share"]),
        "weighted_tail_dependence_ratio": float(weighted_tail),
        "average_positive_correlation": float(topology["average_positive_correlation"]),
        "correlation_convergence": float(topology["correlation_convergence"]),
        "network_density": float(topology["network_density"]),
        "risk_estimate_uncertainty": uncertainty,
        "covariance_ensemble": ensemble_audit,
        "factor_risk": factor_risk,
        "liquidity": _serializable_liquidity(liquidity),
        "structural_intervention_enabled": bool(config.v202_structural_intervention_enabled),
        "liquidity_intervention_enabled": bool(config.v202_liquidity_intervention_enabled),
        "component_cap_converged": bool(component_converged),
        "component_cap_iterations": int(component_iterations),
        "clusters": clusters,
        "optimizer": optimizer_audit,
        "alpha_priority_v1": priority_v1,
        "alpha_priority_after": priority_after,
        "alpha_priority_retention_vs_v1": priority_after / priority_v1 if abs(priority_v1) > 1e-12 else None,
        "what_if": {"v1": before_what_if, "v202": after_what_if},
        "stress_tests": stress_tests,
        "risk_radar": radar,
    }


def supervise_cross_sectional_weights(
    weights: np.ndarray,
    covariance: np.ndarray,
    config: RiskSupervisorConfig,
    *,
    returns_history: np.ndarray | None = None,
    expected_alpha: np.ndarray | None = None,
    prediction_error: np.ndarray | None = None,
    fallback_priority: np.ndarray | None = None,
    adv_dollars: np.ndarray | None = None,
    capital: float | None = None,
    symbols: Sequence[str] | None = None,
    previous_state: str | None = None,
    predictive_forecast: dict[str, Any] | None = None,
    previous_persistence_count: int = 0,
    current_weights: np.ndarray | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Supervise proposed stock weights while preserving frozen V1 behavior.

    - V1 callers keep the original supervisor.
    - rejected V2.0.1 remains reproducible when ``v201_enabled`` is explicit.
    - V2.0.2 is a separate V1-anchored predictive research branch.
    """
    if config.v202_enabled:
        if returns_history is None:
            return _legacy_supervise(weights, covariance, config)
        return _supervise_v202(
            weights, covariance, config,
            returns_history=returns_history,
            expected_alpha=expected_alpha,
            prediction_error=prediction_error,
            fallback_priority=fallback_priority,
            adv_dollars=adv_dollars,
            capital=capital,
            symbols=symbols,
            predictive_forecast=predictive_forecast,
            previous_persistence_count=previous_persistence_count,
            current_weights=current_weights,
        )
    if config.v201_enabled and returns_history is not None:
        return _supervise_v201(
            weights, covariance, config,
            returns_history=returns_history,
            expected_alpha=expected_alpha,
            prediction_error=prediction_error,
            fallback_priority=fallback_priority,
            adv_dollars=adv_dollars,
            capital=capital,
            symbols=symbols,
            previous_state=previous_state,
        )
    return _legacy_supervise(weights, covariance, config)


def dynamic_exposure_scale(net_history: list[float], nav_history: list[float],
                           target_annual_volatility: float,
                           config: RiskSupervisorConfig) -> tuple[float, dict[str, float | None]]:
    """Completed-portfolio realized-risk failsafe used by next-open execution.

    V2 research profiles may disable duplicate realized-vol / tail controls and keep
    drawdown as an independent realized-loss circuit breaker. Legacy profiles
    retain the exact V1 calculation.
    """
    if not config.enabled:
        return 1.0, {"realized_volatility": None, "drawdown": None, "scale": 1.0}
    volatility_scale = 1.0
    volatility_shock_scale = 1.0
    realized_volatility = None
    if config.realized_volatility_enabled and len(net_history) >= config.realized_volatility_window:
        sample = np.asarray(net_history[-config.realized_volatility_window:], dtype=float)
        realized_volatility = float(np.std(sample, ddof=0) * np.sqrt(252))
        if realized_volatility > target_annual_volatility:
            volatility_scale = max(config.realized_volatility_floor_scale,
                                   target_annual_volatility / realized_volatility)
    volatility_shock_ratio = None
    if config.volatility_shock_enabled and len(net_history) >= config.realized_volatility_window:
        long_sample = np.asarray(net_history[-config.realized_volatility_window:], dtype=float)
        short_sample = long_sample[-config.volatility_shock_short_window:]
        long_volatility = float(np.std(long_sample, ddof=0))
        short_volatility = float(np.std(short_sample, ddof=0))
        if long_volatility > 1e-12:
            volatility_shock_ratio = short_volatility / long_volatility
            if volatility_shock_ratio > config.volatility_shock_threshold:
                volatility_shock_scale = max(
                    config.volatility_shock_floor_scale,
                    config.volatility_shock_threshold / volatility_shock_ratio,
                )
    tail_loss_scale = 1.0
    expected_shortfall = None
    if config.tail_loss_enabled and len(net_history) >= config.tail_loss_window:
        tail_sample = np.sort(np.asarray(net_history[-config.tail_loss_window:], dtype=float))
        tail_count = max(1, int(math.ceil(len(tail_sample) * config.tail_probability)))
        expected_shortfall = max(0.0, -float(tail_sample[:tail_count].mean()))
        tail_budget = config.maximum_expected_shortfall_multiple * target_annual_volatility / math.sqrt(252)
        if expected_shortfall > tail_budget:
            tail_loss_scale = max(config.tail_loss_floor_scale, tail_budget / expected_shortfall)
    drawdown_scale = 1.0
    drawdown = None
    if config.drawdown_enabled and nav_history:
        sample_nav = nav_history[-config.drawdown_lookback_sessions:]
        peak = max(sample_nav)
        drawdown = max(0.0, 1.0 - sample_nav[-1] / peak) if peak > 0 else 0.0
        if drawdown >= config.hard_drawdown:
            drawdown_scale = config.hard_drawdown_scale
        elif drawdown > config.soft_drawdown:
            progress = (drawdown - config.soft_drawdown) / (config.hard_drawdown - config.soft_drawdown)
            drawdown_scale = 1.0 - progress * (1.0 - config.hard_drawdown_scale)
    scale = float(min(1.0, volatility_scale, volatility_shock_scale, tail_loss_scale, drawdown_scale))
    return scale, {
        "realized_volatility": realized_volatility,
        "volatility_scale": volatility_scale,
        "volatility_shock_ratio": volatility_shock_ratio,
        "volatility_shock_scale": volatility_shock_scale,
        "expected_shortfall": expected_shortfall,
        "tail_loss_scale": tail_loss_scale,
        "drawdown": drawdown,
        "drawdown_scale": drawdown_scale,
        "scale": scale,
    }


def risk_supervisor_contract(config: RiskSupervisorConfig) -> dict[str, Any]:
    controls = [
        "ex-ante component-risk contribution cap",
        "correlation-cluster exposure cap",
        "high-correlation regime exposure ceiling",
        "minimum effective-bet exposure scaler",
        "completed-return realized-volatility scaler",
        "completed-return volatility-acceleration scaler",
        "completed-return expected-shortfall scaler",
        "rolling drawdown exposure circuit breaker",
        "gradual exposure recovery after de-risking",
    ]
    if config.v201_enabled or config.v202_enabled:
        controls.extend([
            "adaptive long/short/downside covariance ensemble",
            "complete-linkage correlation topology",
            "statistical-factor / security risk decomposition",
            "effective risk-dimensionality / PCA concentration",
            "portfolio-weighted downside tail-dependence diagnostics",
            "moving-block bootstrap risk-estimate uncertainty",
            "liquidity / liquidation-capacity diagnostics",
            "current-exposure synthetic stress testing",
            "what-if before/after risk comparison",
            "risk-radar exception hierarchy",
        ])
    if config.v202_enabled:
        controls.extend([
            "V1-anchored baseline risk decision",
            "causal walk-forward forward-volatility / drawdown forecast",
            "completed-label availability gate",
            "chronological internal validation gate",
            "systemic percentile trigger (not portfolio-volatility target mismatch)",
            "persistent predictive-risk trigger",
            "bounded incremental gross reduction",
            "portfolio-level no-trade band",
            "alpha/risk/turnover-aware constrained optimizer",
            "proportional de-risking fallback unless optimizer dominates",
            "hand-weighted fragility score retained as diagnostic only",
        ])
    return {
        "id": RISK_MODEL_V202_ID if config.v202_enabled else (RISK_MODEL_ID if config.v201_enabled else "universal_tba_risk_supervisor_v1"),
        "status": "active_research" if config.enabled else "disabled",
        "configuration": asdict(config),
        "controls": controls,
        "timing": "All cross-sectional controls use data available no later than the completed close; execution remains next-open.",
        "scope": "Shared Universal stock-risk layer; frozen Minerva remains disabled unless explicitly overridden.",
        "research_warning": (
            "V2.0.2 forward-risk forecasts and optimizer settings remain research hypotheses until paired, "
            "sensitivity, ablation and unseen point-in-time holdout validation are completed."
            if config.v202_enabled else
            "V2.0.1 fragility weights and thresholds are research hypotheses until sensitivity, ablation and unseen holdout validation are completed."
        ),
    }
