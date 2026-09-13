"""Causal, TBA-only portfolio risk supervision for Universal research.

This layer does not create forecasts. It constrains forecast portfolios using
only information available through the prior completed close. Minerva remains
frozen with this supervisor disabled.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import numpy as np


@dataclass(frozen=True)
class RiskSupervisorConfig:
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

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not name.endswith("enabled") and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        switches = [value for name, value in asdict(self).items() if name.endswith("enabled")]
        if not all(isinstance(value, bool) for value in switches):
            raise ValueError("Risk supervisor switches must be boolean")
        if not (.05 <= self.maximum_risk_contribution <= .50):
            raise ValueError("maximum_risk_contribution must be within [.05, .50]")
        if not (.25 <= self.correlation_cluster_threshold <= .99):
            raise ValueError("correlation_cluster_threshold must be within [.25, .99]")
        if not (.10 <= self.maximum_cluster_weight <= 1):
            raise ValueError("maximum_cluster_weight must be within [.10, 1]")
        if not (.10 <= self.maximum_average_correlation <= .95
                and 0 < self.correlation_regime_floor_scale <= 1):
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


def _correlation_clusters(covariance: np.ndarray, active: np.ndarray, threshold: float) -> list[list[int]]:
    volatility = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    denominator = np.outer(volatility, volatility)
    correlation = np.divide(covariance, denominator, out=np.zeros_like(covariance), where=denominator > 0)
    correlation = np.clip((correlation + correlation.T) / 2.0, -1.0, 1.0)
    np.fill_diagonal(correlation, 1.0)
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


def supervise_cross_sectional_weights(weights: np.ndarray, covariance: np.ndarray,
                                      config: RiskSupervisorConfig) -> tuple[np.ndarray, dict[str, Any]]:
    """Cap correlated clusters and ex-ante component-risk concentration."""
    adjusted = np.asarray(weights, dtype=float).copy()
    covariance = np.asarray(covariance, dtype=float)
    if not config.enabled or adjusted.sum() <= 0:
        return adjusted, {"enabled": config.enabled, "scale": 1.0, "cluster_count": 0}
    if covariance.shape != (len(adjusted), len(adjusted)) or not np.isfinite(covariance).all():
        raise ValueError("Risk supervisor requires a finite square covariance matrix")

    active = np.flatnonzero(adjusted > 0)
    volatility = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    denominator = np.outer(volatility, volatility)
    correlation = np.divide(covariance, denominator, out=np.zeros_like(covariance), where=denominator > 0)
    correlation = np.clip((correlation + correlation.T) / 2.0, -1.0, 1.0)
    clusters = _correlation_clusters(covariance, active, config.correlation_cluster_threshold)
    if config.correlation_cluster_enabled:
        for cluster in clusters:
            cluster_weight = float(adjusted[cluster].sum())
            if cluster_weight > config.maximum_cluster_weight:
                adjusted[cluster] *= config.maximum_cluster_weight / cluster_weight

    if config.component_risk_enabled:
        for _ in range(12):
            variance = float(adjusted @ covariance @ adjusted)
            if variance <= 1e-18:
                break
            contribution = np.maximum(adjusted * (covariance @ adjusted), 0.0)
            total = float(contribution.sum())
            if total <= 1e-18:
                break
            share = contribution / total
            offenders = np.flatnonzero((adjusted > 0) & (share > config.maximum_risk_contribution + 1e-10))
            if not len(offenders):
                break
            for index in offenders:
                adjusted[index] *= config.maximum_risk_contribution / share[index]

    gross = float(adjusted.sum())
    effective_bets = 0.0
    average_positive_correlation = 0.0
    correlation_scale = 1.0
    if gross > 0:
        normalized = adjusted / gross
        effective_bets = float(1.0 / np.square(normalized).sum())
        if config.diversification_enabled and effective_bets < config.minimum_effective_bets:
            concentration_scale = max(config.concentration_floor_scale, effective_bets / config.minimum_effective_bets)
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
        "scale": float(adjusted.sum() / max(weights.sum(), 1e-18)),
        "cluster_count": len(clusters),
        "effective_bets": effective_bets,
        "average_positive_correlation": average_positive_correlation,
        "correlation_regime_scale": correlation_scale,
    }


def dynamic_exposure_scale(net_history: list[float], nav_history: list[float],
                           target_annual_volatility: float,
                           config: RiskSupervisorConfig) -> tuple[float, dict[str, float | None]]:
    """Use completed portfolio returns and NAV to set today's exposure ceiling."""
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
    return {
        "id": "universal_tba_risk_supervisor_v1",
        "status": "active_research" if config.enabled else "disabled",
        "configuration": asdict(config),
        "controls": [
            "ex-ante component-risk contribution cap",
            "correlation-cluster exposure cap",
            "high-correlation regime exposure ceiling",
            "minimum effective-bet exposure scaler",
            "completed-return realized-volatility scaler",
            "completed-return volatility-acceleration scaler",
            "completed-return expected-shortfall scaler",
            "rolling drawdown exposure circuit breaker",
            "gradual exposure recovery after de-risking",
        ],
        "timing": "All controls use data available no later than the prior completed close.",
        "scope": "Enabled by default for TBA research candidates; frozen Minerva remains unchanged.",
    }
