"""Constrained, factor-aware portfolio optimizer for Universal Phase 2 research.

The optimizer is deliberately conservative: it starts from Universal's existing
proposal, rewards expected alpha, penalizes factor/idio risk and turnover, and
projects every step back into long-only portfolio constraints. If the numerical
candidate does not improve the declared utility or violates an invariant, the
original proposal is returned unchanged.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import math
import numpy as np

_EPS = 1e-12


@dataclass(frozen=True)
class PortfolioOptimizerConfig:
    enabled: bool = False
    alpha_reward: float = 1.0
    risk_aversion: float = .35
    proposal_anchor_penalty: float = .75
    turnover_penalty: float = 1.00
    maximum_one_way_turnover: float = .30
    minimum_gross_retention: float = .95
    minimum_portfolio_change: float = .01
    maximum_sector_weight: float = .35
    maximum_absolute_style_exposure: float | None = 1.50
    liquidity_horizon_sessions: int = 3
    liquidity_buffer: float = .80
    iterations: int = 300
    learning_rate: float = .04
    convergence_tolerance: float = 1e-8
    minimum_utility_improvement: float = 1e-6

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if isinstance(value, bool) or value is None:
                continue
            if not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be boolean")
        if not (0 <= self.alpha_reward <= 100 and 0 <= self.risk_aversion <= 100):
            raise ValueError("Invalid optimizer alpha/risk weights")
        if not (0 <= self.proposal_anchor_penalty <= 100 and 0 <= self.turnover_penalty <= 100):
            raise ValueError("Invalid optimizer regularization")
        if not (0 <= self.maximum_one_way_turnover <= 2):
            raise ValueError("maximum_one_way_turnover must be within [0, 2]")
        if not (0 < self.minimum_gross_retention <= 1):
            raise ValueError("minimum_gross_retention must be within (0, 1]")
        if not (0 <= self.minimum_portfolio_change <= .50):
            raise ValueError("Invalid optimizer no-trade band")
        if not (0 < self.maximum_sector_weight <= 1):
            raise ValueError("maximum_sector_weight must be within (0, 1]")
        if self.maximum_absolute_style_exposure is not None and self.maximum_absolute_style_exposure <= 0:
            raise ValueError("maximum_absolute_style_exposure must be positive or None")
        if not (1 <= self.liquidity_horizon_sessions <= 20 and 0 < self.liquidity_buffer <= 1):
            raise ValueError("Invalid optimizer liquidity settings")
        if not (20 <= self.iterations <= 5000 and 0 < self.learning_rate <= 1):
            raise ValueError("Invalid optimizer iteration controls")
        if not (0 <= self.convergence_tolerance <= .1 and 0 <= self.minimum_utility_improvement <= 1):
            raise ValueError("Invalid optimizer convergence controls")


def _priority(expected_alpha: np.ndarray | None, prediction_error: np.ndarray | None, fallback: np.ndarray) -> np.ndarray:
    if expected_alpha is None:
        raw = np.maximum(np.asarray(fallback, dtype=float).reshape(-1), 0.0)
    else:
        alpha = np.asarray(expected_alpha, dtype=float).reshape(-1)
        if prediction_error is None:
            raw = np.maximum(alpha, 0.0)
        else:
            error = np.asarray(prediction_error, dtype=float).reshape(-1)
            finite_positive = error[np.isfinite(error) & (error > 1e-8)]
            floor = float(np.median(finite_positive)) if len(finite_positive) else 1.0
            raw = np.maximum(alpha, 0.0) / np.maximum(np.where(np.isfinite(error), error, floor), floor * .25)
    raw = np.where(np.isfinite(raw), raw, 0.0)
    maximum = float(np.max(raw)) if len(raw) else 0.0
    return raw / maximum if maximum > _EPS else np.zeros_like(raw)


def _liquidity_caps(
    n: int,
    *,
    adv_dollars: np.ndarray | None,
    capital: float,
    participation: float,
    name_cap: float,
    config: PortfolioOptimizerConfig,
) -> np.ndarray:
    caps = np.full(n, float(name_cap), dtype=float)
    if adv_dollars is None:
        return caps
    adv = np.asarray(adv_dollars, dtype=float).reshape(-1)
    if len(adv) != n:
        raise ValueError("ADV vector must align with optimizer universe")
    valid = np.isfinite(adv) & (adv > 0)
    capacity = np.zeros(n, dtype=float)
    capacity[valid] = (
        adv[valid] * float(participation) * config.liquidity_horizon_sessions * config.liquidity_buffer
        / max(float(capital), _EPS)
    )
    caps = np.minimum(caps, np.maximum(capacity, 0.0))
    return caps


def _project_constraints(
    weights: np.ndarray,
    *,
    gross_cap: float,
    per_name_caps: np.ndarray,
    factor_names: list[str],
    exposures: np.ndarray,
    maximum_sector_weight: float,
    maximum_market_beta: float | None,
    maximum_absolute_style_exposure: float | None,
) -> np.ndarray:
    w = np.maximum(np.minimum(np.asarray(weights, dtype=float), per_name_caps), 0.0)
    for _ in range(5):
        gross = float(w.sum())
        if gross > gross_cap + 1e-12:
            w *= gross_cap / gross

        # Exact sector constraints: sector exposures are one-hot 0/1.
        for j, name in enumerate(factor_names):
            if not name.startswith("sector:"):
                continue
            mask = exposures[:, j] > .5
            sector_weight = float(w[mask].sum())
            if sector_weight > maximum_sector_weight + 1e-12 and sector_weight > _EPS:
                w[mask] *= maximum_sector_weight / sector_weight

        if maximum_market_beta is not None and "market" in factor_names:
            j = factor_names.index("market")
            beta = float(w @ exposures[:, j])
            if beta > maximum_market_beta + 1e-12 and beta > _EPS:
                w *= maximum_market_beta / beta

        if maximum_absolute_style_exposure is not None:
            for style in ("momentum", "low_volatility", "size", "value", "quality"):
                if style not in factor_names:
                    continue
                j = factor_names.index(style)
                exposure = float(w @ exposures[:, j])
                limit = float(maximum_absolute_style_exposure)
                if exposure > limit + 1e-12:
                    positive = exposures[:, j] > 0
                    contribution = float(w[positive] @ exposures[positive, j])
                    excess = exposure - limit
                    if contribution > _EPS:
                        scale = max(0.0, 1.0 - excess / contribution)
                        w[positive] *= scale
                elif exposure < -limit - 1e-12:
                    negative = exposures[:, j] < 0
                    contribution = float(-(w[negative] @ exposures[negative, j]))
                    excess = -limit - exposure
                    if contribution > _EPS:
                        scale = max(0.0, 1.0 - excess / contribution)
                        w[negative] *= scale

        w = np.maximum(np.minimum(w, per_name_caps), 0.0)
    return w


def _restore_gross(
    weights: np.ndarray,
    *,
    target_gross: float,
    priority: np.ndarray,
    gross_cap: float,
    per_name_caps: np.ndarray,
    factor_names: list[str],
    exposures: np.ndarray,
    maximum_sector_weight: float,
    maximum_market_beta: float | None,
    maximum_absolute_style_exposure: float | None,
) -> np.ndarray:
    """Refill unused gross toward the alpha proposal without violating constraints.

    Portfolio construction should reshape risk; the independent supervisor owns
    gross de-risking. This routine therefore tries to preserve the proposal's
    gross exposure whenever the declared constraints allow it.
    """
    w = np.asarray(weights, dtype=float).copy()
    target = min(float(target_gross), float(gross_cap))
    for _ in range(12):
        gap = target - float(w.sum())
        if gap <= 1e-8:
            break
        room = np.maximum(per_name_caps - w, 0.0)
        desirability = room * (.10 + np.maximum(priority, 0.0))
        if float(desirability.sum()) <= _EPS:
            break
        addition = gap * desirability / desirability.sum()
        w += np.minimum(addition, room)
        w = _project_constraints(
            w, gross_cap=gross_cap, per_name_caps=per_name_caps,
            factor_names=factor_names, exposures=exposures,
            maximum_sector_weight=maximum_sector_weight,
            maximum_market_beta=maximum_market_beta,
            maximum_absolute_style_exposure=maximum_absolute_style_exposure,
        )
    return w


def _apply_turnover_cap(candidate: np.ndarray, current: np.ndarray, maximum_turnover: float) -> np.ndarray:
    delta = candidate - current
    turnover = float(np.abs(delta).sum())
    if turnover <= maximum_turnover + 1e-12 or turnover <= _EPS:
        return candidate
    return current + delta * (maximum_turnover / turnover)


def _utility(
    w: np.ndarray,
    *,
    priority: np.ndarray,
    covariance: np.ndarray,
    proposed: np.ndarray,
    current: np.ndarray,
    baseline_variance: float,
    config: PortfolioOptimizerConfig,
) -> float:
    gross_scale = max(float(proposed.sum()), .05)
    anchor_scale = max(float(np.square(proposed).sum()), 1e-4)
    turnover_scale = max(float(np.square(np.maximum(current, proposed * .25)).sum()), 1e-4)
    alpha_term = config.alpha_reward * float(priority @ w) / gross_scale
    risk_term = config.risk_aversion * float(w @ covariance @ w) / baseline_variance
    anchor_term = config.proposal_anchor_penalty * float(np.square(w - proposed).sum()) / anchor_scale
    turnover_term = config.turnover_penalty * float(np.square(w - current).sum()) / turnover_scale
    return alpha_term - risk_term - anchor_term - turnover_term


def optimize_portfolio(
    proposed_weights: np.ndarray,
    *,
    factor_snapshot: dict[str, Any],
    expected_alpha: np.ndarray | None,
    prediction_error: np.ndarray | None,
    fallback_priority: np.ndarray,
    current_weights: np.ndarray | None,
    gross_cap: float,
    name_cap: float,
    maximum_market_beta: float | None,
    adv_dollars: np.ndarray | None,
    capital: float,
    participation: float,
    config: PortfolioOptimizerConfig,
) -> tuple[np.ndarray, dict[str, Any]]:
    proposed = np.asarray(proposed_weights, dtype=float).reshape(-1)
    if not config.enabled:
        return proposed.copy(), {"enabled": False, "accepted": False, "reason": "optimizer_disabled"}
    if not factor_snapshot.get("available"):
        return proposed.copy(), {
            "enabled": True, "accepted": False, "reason": "factor_model_unavailable",
            "factor_reason": factor_snapshot.get("reason"),
        }
    covariance = np.asarray(factor_snapshot["asset_covariance"], dtype=float)
    exposures = np.asarray(factor_snapshot["exposures"], dtype=float)
    factor_names = list(factor_snapshot["factor_names"])
    if covariance.shape != (len(proposed), len(proposed)) or exposures.shape[0] != len(proposed):
        raise ValueError("Optimizer factor model does not align with portfolio")
    current = np.zeros_like(proposed) if current_weights is None else np.asarray(current_weights, dtype=float).reshape(-1)
    if len(current) != len(proposed):
        raise ValueError("Current weights must align with proposed weights")
    # A flat initialization is not an economic reason to turn the portfolio into
    # cash. Execution costs already price the initial entry; turnover regularization
    # is meant to discourage unnecessary *reshuffling* of an established book.
    initialization_reference = bool(float(current.sum()) <= _EPS and float(proposed.sum()) > _EPS)
    turnover_reference = proposed.copy() if initialization_reference else current.copy()
    priority = _priority(expected_alpha, prediction_error, fallback_priority)
    if not np.any(priority > 0):
        priority = np.maximum(proposed, 0.0)
        priority /= max(float(priority.max()), _EPS)

    per_name_caps = _liquidity_caps(
        len(proposed), adv_dollars=adv_dollars, capital=capital,
        participation=participation, name_cap=name_cap, config=config,
    )
    baseline = _project_constraints(
        proposed,
        gross_cap=gross_cap,
        per_name_caps=per_name_caps,
        factor_names=factor_names,
        exposures=exposures,
        maximum_sector_weight=config.maximum_sector_weight,
        maximum_market_beta=maximum_market_beta,
        maximum_absolute_style_exposure=config.maximum_absolute_style_exposure,
    )
    baseline = _restore_gross(
        baseline, target_gross=float(proposed.sum()), priority=priority, gross_cap=gross_cap,
        per_name_caps=per_name_caps, factor_names=factor_names, exposures=exposures,
        maximum_sector_weight=config.maximum_sector_weight, maximum_market_beta=maximum_market_beta,
        maximum_absolute_style_exposure=config.maximum_absolute_style_exposure,
    )
    baseline = _apply_turnover_cap(baseline, turnover_reference, config.maximum_one_way_turnover)
    baseline = _project_constraints(
        baseline,
        gross_cap=gross_cap,
        per_name_caps=per_name_caps,
        factor_names=factor_names,
        exposures=exposures,
        maximum_sector_weight=config.maximum_sector_weight,
        maximum_market_beta=maximum_market_beta,
        maximum_absolute_style_exposure=config.maximum_absolute_style_exposure,
    )

    baseline_variance = max(float(proposed @ covariance @ proposed), 1e-8)
    w = baseline.copy()
    start_utility = _utility(
        w, priority=priority, covariance=covariance, proposed=proposed,
        current=turnover_reference, baseline_variance=baseline_variance, config=config,
    )
    best = w.copy()
    best_utility = start_utility
    previous = w.copy()
    iterations_used = 0

    gross_scale = max(float(proposed.sum()), .05)
    anchor_scale = max(float(np.square(proposed).sum()), 1e-4)
    turnover_scale = max(float(np.square(np.maximum(turnover_reference, proposed * .25)).sum()), 1e-4)

    for iteration in range(config.iterations):
        iterations_used = iteration + 1
        gradient = (
            config.alpha_reward * priority / gross_scale
            - config.risk_aversion * 2.0 * (covariance @ w) / baseline_variance
            - config.proposal_anchor_penalty * 2.0 * (w - proposed) / anchor_scale
            - config.turnover_penalty * 2.0 * (w - turnover_reference) / turnover_scale
        )
        candidate = w + config.learning_rate * gradient
        candidate = _project_constraints(
            candidate,
            gross_cap=gross_cap,
            per_name_caps=per_name_caps,
            factor_names=factor_names,
            exposures=exposures,
            maximum_sector_weight=config.maximum_sector_weight,
            maximum_market_beta=maximum_market_beta,
            maximum_absolute_style_exposure=config.maximum_absolute_style_exposure,
        )
        candidate = _restore_gross(
            candidate, target_gross=float(proposed.sum()), priority=priority, gross_cap=gross_cap,
            per_name_caps=per_name_caps, factor_names=factor_names, exposures=exposures,
            maximum_sector_weight=config.maximum_sector_weight, maximum_market_beta=maximum_market_beta,
            maximum_absolute_style_exposure=config.maximum_absolute_style_exposure,
        )
        candidate = _apply_turnover_cap(candidate, turnover_reference, config.maximum_one_way_turnover)
        candidate = _project_constraints(
            candidate,
            gross_cap=gross_cap,
            per_name_caps=per_name_caps,
            factor_names=factor_names,
            exposures=exposures,
            maximum_sector_weight=config.maximum_sector_weight,
            maximum_market_beta=maximum_market_beta,
            maximum_absolute_style_exposure=config.maximum_absolute_style_exposure,
        )
        utility = _utility(
            candidate, priority=priority, covariance=covariance, proposed=proposed,
            current=turnover_reference, baseline_variance=baseline_variance, config=config,
        )
        if utility > best_utility:
            best = candidate.copy()
            best_utility = utility
        if float(np.abs(candidate - previous).sum()) <= config.convergence_tolerance:
            w = candidate
            break
        previous = w
        w = candidate

    portfolio_change = float(np.abs(best - proposed).sum())
    required_gross = float(baseline.sum()) * config.minimum_gross_retention
    accepted = (
        best_utility >= start_utility + config.minimum_utility_improvement
        and portfolio_change >= config.minimum_portfolio_change - 1e-12
        and float(best.sum()) >= required_gross - 1e-12
    )
    final = best if accepted else proposed.copy()

    if np.any(final < -1e-12) or float(final.sum()) > gross_cap + 1e-8 or not np.isfinite(final).all():
        final = proposed.copy()
        accepted = False
        reason = "invariant_fallback"
    else:
        reason = "optimized" if accepted else ("no_trade_band" if portfolio_change < config.minimum_portfolio_change else "no_utility_improvement")

    def exposures_for(weights: np.ndarray) -> dict[str, float]:
        vector = weights @ exposures
        return {name: float(value) for name, value in zip(factor_names, vector)}

    return final, {
        "enabled": True,
        "accepted": bool(accepted),
        "reason": reason,
        "iterations": int(iterations_used),
        "utility_before": float(start_utility),
        "utility_candidate": float(best_utility),
        "portfolio_change": portfolio_change,
        "turnover_reference_initialized": initialization_reference,
        "turnover_from_current_before": float(np.abs(proposed - current).sum()),
        "turnover_from_current_after": float(np.abs(final - current).sum()),
        "turnover_used_for_optimization": float(np.abs(final - turnover_reference).sum()),
        "required_gross": required_gross,
        "modeled_variance_before": float(proposed @ covariance @ proposed),
        "modeled_variance_after": float(final @ covariance @ final),
        "gross_before": float(proposed.sum()),
        "gross_after": float(final.sum()),
        "factor_exposure_before": exposures_for(proposed),
        "factor_exposure_after": exposures_for(final),
        "liquidity_caps": [float(value) for value in per_name_caps],
        "configuration": asdict(config),
    }


def optimizer_contract(config: PortfolioOptimizerConfig) -> dict[str, Any]:
    return {
        "id": "universal_phase2_factor_aware_optimizer_v1",
        "status": "active_research" if config.enabled else "disabled",
        "objective": "Preserve expected-alpha priority while penalizing factor/idio risk, proposal drift and turnover under explicit portfolio constraints.",
        "fallback": "Return the pre-optimizer Universal proposal if the factor model is unavailable, utility does not improve, the no-trade band is not crossed, or an invariant fails.",
        "configuration": asdict(config),
    }
