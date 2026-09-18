"""Causal net-edge and capacity controls for the private TBA 8 candidate."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import numpy as np


@dataclass(frozen=True)
class InstitutionalDecisionConfig:
    """Research-only controls joining forecast confidence to implementability."""

    enabled: bool = False
    minimum_signal_to_noise: float = 0.0
    round_trip_cost_multiplier: float = 1.0
    weak_edge_scale: float = 0.50
    liquidity_horizon_sessions: int = 1
    capacity_buffer: float = 0.80
    minimum_dollar_volume: float = 1_000_000.0

    def __post_init__(self) -> None:
        values = asdict(self)
        if not isinstance(self.enabled, bool):
            raise ValueError("Institutional decision enabled switch must be boolean")
        if not all(math.isfinite(float(value)) for name, value in values.items() if name != "enabled"):
            raise ValueError("Institutional decision controls must be finite")
        if not (0 <= self.minimum_signal_to_noise <= 5):
            raise ValueError("minimum_signal_to_noise must be within [0, 5]")
        if not (0 <= self.round_trip_cost_multiplier <= 5):
            raise ValueError("round_trip_cost_multiplier must be within [0, 5]")
        if not (0 <= self.weak_edge_scale <= 1):
            raise ValueError("weak_edge_scale must be within [0, 1]")
        if not (1 <= self.liquidity_horizon_sessions <= 20 and isinstance(self.liquidity_horizon_sessions, int)):
            raise ValueError("liquidity_horizon_sessions must be an integer from 1 through 20")
        if not (0 < self.capacity_buffer <= 1):
            raise ValueError("capacity_buffer must be within (0, 1]")
        if not (0 <= self.minimum_dollar_volume <= 1e12):
            raise ValueError("minimum_dollar_volume must be within [0, 1e12]")


def constrain_institutional_weights(
    weights: np.ndarray,
    predicted_return: np.ndarray,
    prediction_error: np.ndarray,
    prior_median_dollar_volume: np.ndarray,
    *,
    capital: float,
    base_cost_bps: float,
    impact_bps: float,
    participation: float,
    config: InstitutionalDecisionConfig,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Scale weak forecasts and cap targets to causal capacity.

    The predicted return and uncertainty come from the same completed-history
    ridge fit. Liquidity uses the prior 20-session median dollar volume. The
    function may only reduce a proposed long-only portfolio and leaves the
    difference in cash.
    """
    proposed = np.asarray(weights, dtype=float)
    predicted = np.asarray(predicted_return, dtype=float)
    uncertainty = np.asarray(prediction_error, dtype=float)
    adv = np.asarray(prior_median_dollar_volume, dtype=float)
    if not config.enabled:
        return proposed.copy(), {"enabled": False, "scale": 1.0}
    if not (proposed.shape == predicted.shape == uncertainty.shape == adv.shape):
        raise ValueError("Institutional decision arrays must share one shape")
    if (not np.isfinite(proposed).all() or not np.isfinite(predicted).all()
            or not np.isfinite(uncertainty).all() or not np.isfinite(adv).all()):
        raise ValueError("Institutional decision arrays must be finite")
    if (proposed < 0).any() or (uncertainty <= 0).any() or (adv < 0).any():
        raise ValueError("Institutional decision inputs have invalid bounds")
    if not (math.isfinite(capital) and capital > 0):
        raise ValueError("Institutional decision capital must be positive and finite")

    signal_to_noise = np.divide(predicted, uncertainty, out=np.full_like(predicted, -np.inf), where=uncertainty > 0)
    desired_notional = proposed * capital
    implied_participation = np.divide(
        desired_notional,
        adv,
        out=np.full_like(desired_notional, np.inf),
        where=adv > 0,
    )
    estimated_round_trip_cost = (
        2.0 * (base_cost_bps + impact_bps * np.sqrt(np.maximum(implied_participation, 0.0))) / 10_000.0
    )
    liquid = adv >= config.minimum_dollar_volume
    # Avoid the undefined 0 * infinity case for names with no usable ADV.
    # With a zero multiplier the active hurdle is forecast sign only; a
    # positive multiplier activates the estimated-cost comparison.
    cost_hurdle = (
        predicted > 0
        if config.round_trip_cost_multiplier == 0
        else predicted > config.round_trip_cost_multiplier * estimated_round_trip_cost
    )
    strong_edge = (
        (proposed > 0)
        & (signal_to_noise >= config.minimum_signal_to_noise)
        & cost_hurdle
    )
    capacity_weight = (
        adv
        * participation
        * config.liquidity_horizon_sessions
        * config.capacity_buffer
        / capital
    )
    confidence_scale = np.where(strong_edge, 1.0, config.weak_edge_scale)
    confidence_adjusted = proposed * confidence_scale
    adjusted = np.where(liquid, np.minimum(confidence_adjusted, capacity_weight), 0.0)
    return adjusted, {
        "enabled": True,
        "scale": float(adjusted.sum() / max(proposed.sum(), 1e-18)),
        "proposed_names": int((proposed > 0).sum()),
        "strong_edge_names": int(strong_edge.sum()),
        "weak_edge_names": int(((proposed > 0) & ~strong_edge).sum()),
        "illiquid_names": int(((proposed > 0) & ~liquid).sum()),
        "capacity_limited_names": int((liquid & (capacity_weight < confidence_adjusted)).sum()),
        "maximum_implied_participation": float(np.max(implied_participation[proposed > 0])) if (proposed > 0).any() else 0.0,
    }


def institutional_decision_contract(config: InstitutionalDecisionConfig) -> dict[str, Any]:
    return {
        "id": "universal_tba8_institutional_decision_v1",
        "status": "active_research" if config.enabled else "disabled",
        "configuration": asdict(config),
        "controls": [
            "strong-edge status uses a configurable predicted-return versus estimated round-trip-cost hurdle",
            "strong-edge status uses a configurable training-error signal-to-noise floor",
            "weak forecasts receive a bounded scale rather than an all-or-nothing decision",
            "prior median dollar volume must clear the declared liquidity floor",
            "target notional is capped to a buffered multi-session participation budget",
        ],
        "timing": "Forecast and uncertainty use completed training labels; capacity uses prior completed-session dollar volume.",
        "invariant": "The layer may reject or reduce a proposed long position but cannot create, reverse, or lever one.",
        "active_hurdle_note": (
            "The current zero cost multiplier distinguishes positive from nonpositive forecasts; "
            "estimated cost remains audited but is not an active hard gate."
            if config.enabled and config.round_trip_cost_multiplier == 0 else
            "The configured positive multiplier makes estimated round-trip cost an active strong-edge hurdle."
        ),
    }
