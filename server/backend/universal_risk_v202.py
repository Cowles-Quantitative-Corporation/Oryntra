"""Universal Risk Supervisor V2.0.2 predictive / V1-anchored research layer.

V2.0.2 keeps the useful V2.0.1 diagnostics but changes the decision architecture:

- V1 remains the baseline supervisor and is never silently replaced.
- fragility metrics are diagnostics, not a hand-weighted exposure controller.
- a causal walk-forward systemic-risk model forecasts forward realized volatility
  (and forward drawdown for audit) from completed market-structure features.
- V2 may only reduce gross beyond V1 after a persistent predictive-risk signal,
  and the extra cut is capped.
- selective de-risking uses a constrained quadratic optimizer that explicitly
  trades off alpha priority, covariance risk, and turnover.
- portfolio-specific PCA / tail-dependence diagnostics weight large holdings more
  heavily than tiny positions.

This is research code. It does not assert that the V2.0.2 hypothesis is superior;
that must be demonstrated by paired, sensitivity, ablation, and unseen holdout work.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np

from .universal_risk_v201 import (
    _average_positive_correlation,
    annualized_portfolio_volatility,
    covariance_to_correlation,
    effective_bets,
    nearest_psd,
    pca_risk_structure,
    sample_covariance,
)

TRADING_DAYS = 252
_EPS = 1e-12


@dataclass(frozen=True)
class V202PredictiveConfig:
    feature_window: int = 42
    compare_window: int = 42
    short_vol_window: int = 21
    long_vol_window: int = 63
    forecast_horizon: int = 10
    training_window: int = 756
    training_stride: int = 5
    minimum_training_observations: int = 60
    ridge_penalty: float = 8.0
    persistence_sessions: int = 3
    systemic_trigger_percentile: float = .85
    minimum_validation_r2: float = 0.0
    validation_fraction: float = .25
    maximum_additional_gross_reduction: float = .15
    minimum_portfolio_change: float = .02
    optimizer_risk_aversion: float = .25
    optimizer_turnover_penalty: float = 2.0
    optimizer_iterations: int = 250
    optimizer_learning_rate: float = .08
    optimizer_turnover_tolerance: float = .005

    def __post_init__(self) -> None:
        if not (10 <= self.feature_window <= 252 and 10 <= self.compare_window <= 252):
            raise ValueError("Invalid V2.0.2 structural windows")
        if not (5 <= self.short_vol_window < self.long_vol_window <= 252):
            raise ValueError("Invalid V2.0.2 volatility windows")
        if not (2 <= self.forecast_horizon <= 21):
            raise ValueError("Invalid V2.0.2 forecast horizon")
        if not (126 <= self.training_window <= 2520 and 1 <= self.training_stride <= 21):
            raise ValueError("Invalid V2.0.2 training window / stride")
        if not (20 <= self.minimum_training_observations <= 500):
            raise ValueError("Invalid V2.0.2 minimum training observations")
        if not (.001 <= self.ridge_penalty <= 1e5):
            raise ValueError("Invalid V2.0.2 ridge penalty")
        if not (1 <= self.persistence_sessions <= 10):
            raise ValueError("Invalid V2.0.2 persistence")
        if not (.50 <= self.systemic_trigger_percentile < 1.0):
            raise ValueError("Invalid V2.0.2 systemic trigger percentile")
        if not (-1.0 <= self.minimum_validation_r2 <= 1.0):
            raise ValueError("Invalid V2.0.2 validation R2 floor")
        if not (.10 <= self.validation_fraction <= .40):
            raise ValueError("Invalid V2.0.2 validation fraction")
        if not (0 <= self.maximum_additional_gross_reduction <= .50):
            raise ValueError("Invalid V2.0.2 additional gross cap")
        if not (0 <= self.minimum_portfolio_change <= .20):
            raise ValueError("Invalid V2.0.2 no-trade band")
        if not (0 <= self.optimizer_risk_aversion <= 100 and 0 <= self.optimizer_turnover_penalty <= 100):
            raise ValueError("Invalid V2.0.2 optimizer penalties")
        if not (10 <= self.optimizer_iterations <= 5000 and 0 < self.optimizer_learning_rate <= 1):
            raise ValueError("Invalid V2.0.2 optimizer settings")
        if not (0 <= self.optimizer_turnover_tolerance <= .20):
            raise ValueError("Invalid V2.0.2 optimizer turnover tolerance")


SYSTEMIC_FEATURE_NAMES = (
    "average_positive_correlation",
    "correlation_convergence",
    "first_pc_share",
    "effective_dimension_inverse",
    "equal_weight_volatility",
    "volatility_acceleration",
    "downside_semideviation",
    "expected_shortfall_10pct",
    "cross_sectional_dispersion",
    "negative_breadth",
)


def _finite_returns(returns: np.ndarray) -> np.ndarray:
    x = np.asarray(returns, dtype=float)
    if x.ndim != 2 or x.shape[1] < 1:
        raise ValueError("returns must have shape [time, assets]")
    if len(x) < 5 or not np.isfinite(x).all():
        raise ValueError("returns must be finite with at least five sessions")
    return x


def _max_drawdown_from_returns(returns: np.ndarray) -> float:
    r = np.asarray(returns, dtype=float).reshape(-1)
    if not len(r):
        return 0.0
    # Include the starting NAV of 1.0 so a loss on the first forward session
    # counts as drawdown rather than becoming the initial peak.
    nav = np.concatenate(([1.0], np.cumprod(1.0 + r)))
    peaks = np.maximum.accumulate(nav)
    drawdown = 1.0 - nav / np.maximum(peaks, _EPS)
    return float(np.max(drawdown))


def _expected_shortfall(loss_returns: np.ndarray, probability: float = .10) -> float:
    r = np.sort(np.asarray(loss_returns, dtype=float).reshape(-1))
    if not len(r):
        return 0.0
    count = max(1, int(math.ceil(len(r) * probability)))
    return max(0.0, -float(r[:count].mean()))


def _systemic_features_at(returns: np.ndarray, end: int, config: V202PredictiveConfig) -> np.ndarray | None:
    """Features at ``end`` use only observations at or before ``end``."""
    x = _finite_returns(returns)
    needed = max(
        config.feature_window + config.compare_window,
        config.long_vol_window,
    )
    if end + 1 < needed:
        return None

    recent = x[end - config.feature_window + 1:end + 1]
    prior = x[end - config.feature_window - config.compare_window + 1:end - config.feature_window + 1]
    recent_cov = sample_covariance(recent)
    prior_cov = sample_covariance(prior)
    recent_corr = covariance_to_correlation(recent_cov)
    prior_corr = covariance_to_correlation(prior_cov)
    avg_corr = _average_positive_correlation(recent_corr)
    prior_avg = _average_positive_correlation(prior_corr)
    structure = pca_risk_structure(recent_cov, factor_count=min(5, x.shape[1]))

    eq = x[:end + 1].mean(axis=1)
    short = eq[-config.short_vol_window:]
    long = eq[-config.long_vol_window:]
    short_vol = float(np.std(short, ddof=0) * math.sqrt(TRADING_DAYS))
    long_vol = float(np.std(long, ddof=0) * math.sqrt(TRADING_DAYS))
    downside = long[long < 0]
    downside_semideviation = float(np.std(downside, ddof=0) * math.sqrt(TRADING_DAYS)) if len(downside) else 0.0
    dispersion = float(np.mean(np.std(x[end - config.short_vol_window + 1:end + 1], axis=1, ddof=0)))
    compounded = np.prod(1.0 + x[end - config.short_vol_window + 1:end + 1], axis=0) - 1.0
    negative_breadth = float(np.mean(compounded < 0))

    return np.asarray([
        avg_corr,
        avg_corr - prior_avg,
        float(structure["first_pc_share"]),
        1.0 / max(float(structure["effective_dimension"]), 1.0),
        long_vol,
        short_vol / max(long_vol, _EPS),
        downside_semideviation,
        _expected_shortfall(long, .10),
        dispersion,
        negative_breadth,
    ], dtype=float)


def build_systemic_risk_panel(returns: np.ndarray, config: V202PredictiveConfig) -> dict[str, Any]:
    """Precompute causal systemic features and future labels once per study.

    Labels are allowed to be precomputed because ``predict_systemic_risk`` only
    admits a row after ``label_available_at <= as_of``. This explicit availability
    gate prevents future labels from entering a historical prediction.
    """
    x = _finite_returns(returns)
    n = len(x)
    features = np.full((n, len(SYSTEMIC_FEATURE_NAMES)), np.nan, dtype=float)
    forward_vol = np.full(n, np.nan, dtype=float)
    forward_drawdown = np.full(n, np.nan, dtype=float)
    label_available_at = np.full(n, -1, dtype=int)

    for end in range(n):
        row = _systemic_features_at(x, end, config)
        if row is not None:
            features[end] = row
        label_end = end + config.forecast_horizon
        if label_end < n:
            future = x[end + 1:label_end + 1].mean(axis=1)
            forward_vol[end] = float(np.std(future, ddof=0) * math.sqrt(TRADING_DAYS))
            forward_drawdown[end] = _max_drawdown_from_returns(future)
            label_available_at[end] = label_end

    return {
        "feature_names": SYSTEMIC_FEATURE_NAMES,
        "features": features,
        "forward_volatility": forward_vol,
        "forward_drawdown": forward_drawdown,
        "label_available_at": label_available_at,
        "rows": n,
    }


def _ridge_predict(x: np.ndarray, y: np.ndarray, current: np.ndarray, penalty: float) -> tuple[float, dict[str, Any]]:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float).reshape(-1)
    current = np.asarray(current, dtype=float).reshape(-1)
    mean = x.mean(axis=0)
    scale = x.std(axis=0, ddof=0)
    scale = np.where(scale > 1e-10, scale, 1.0)
    z = (x - mean) / scale
    z_current = (current - mean) / scale
    y_mean = float(y.mean())
    centered_y = y - y_mean
    gram = z.T @ z + float(penalty) * np.eye(z.shape[1])
    beta = np.linalg.solve(gram, z.T @ centered_y)
    fitted = y_mean + z @ beta
    prediction = float(y_mean + z_current @ beta)
    denom = float(np.sum(np.square(y - y_mean)))
    r2 = 1.0 - float(np.sum(np.square(y - fitted))) / denom if denom > _EPS else 0.0
    return prediction, {
        "r2_in_sample": r2,
        "coefficient_norm": float(np.linalg.norm(beta)),
    }


def _empirical_percentile(sample: np.ndarray, value: float) -> float:
    x = np.asarray(sample, dtype=float)
    x = x[np.isfinite(x)]
    if not len(x):
        return .5
    return float((np.sum(x < value) + .5 * np.sum(x == value)) / len(x))


def predict_systemic_risk(panel: dict[str, Any], as_of: int, config: V202PredictiveConfig) -> dict[str, Any]:
    features = np.asarray(panel["features"], dtype=float)
    label_available = np.asarray(panel["label_available_at"], dtype=int)
    y_vol = np.asarray(panel["forward_volatility"], dtype=float)
    y_dd = np.asarray(panel["forward_drawdown"], dtype=float)
    if as_of < 0 or as_of >= len(features):
        raise ValueError("as_of is outside the predictive-risk panel")
    current = features[as_of]
    if not np.isfinite(current).all():
        return {"available": False, "reason": "current_features_unavailable", "as_of": int(as_of)}

    lower = max(0, as_of - config.training_window)
    eligible = (
        (np.arange(len(features)) >= lower)
        & (label_available >= 0)
        & (label_available <= as_of)
        & np.isfinite(y_vol)
        & np.isfinite(y_dd)
        & np.isfinite(features).all(axis=1)
    )
    rows = np.flatnonzero(eligible)[::config.training_stride]
    if len(rows) < config.minimum_training_observations:
        return {
            "available": False,
            "reason": "insufficient_completed_training_labels",
            "as_of": int(as_of),
            "training_observations": int(len(rows)),
        }

    x = features[rows]
    vol = y_vol[rows]
    dd = y_dd[rows]

    # Chronological internal validation.  The final forecast still trains on all
    # completed labels, but V2 is not allowed to act unless the same model family
    # demonstrated positive explanatory power on the most recent held-out slice.
    validation_count = max(10, int(math.ceil(len(rows) * config.validation_fraction)))
    training_count = len(rows) - validation_count
    validation = {
        "available": False,
        "volatility_r2": None,
        "drawdown_r2": None,
        "validation_observations": int(validation_count),
    }
    if training_count >= max(20, config.minimum_training_observations // 2):
        x_train, x_validation = x[:training_count], x[training_count:]
        vol_train, vol_validation = vol[:training_count], vol[training_count:]
        dd_train, dd_validation = dd[:training_count], dd[training_count:]

        def validation_r2(y_train: np.ndarray, y_validation: np.ndarray) -> float:
            predictions = np.asarray([
                _ridge_predict(x_train, y_train, row, config.ridge_penalty)[0]
                for row in x_validation
            ], dtype=float)
            baseline = float(np.mean(y_train))
            denominator = float(np.sum(np.square(y_validation - baseline)))
            if denominator <= _EPS:
                return 0.0
            return 1.0 - float(np.sum(np.square(y_validation - predictions))) / denominator

        validation = {
            "available": True,
            "volatility_r2": float(validation_r2(vol_train, vol_validation)),
            "drawdown_r2": float(validation_r2(dd_train, dd_validation)),
            "validation_observations": int(validation_count),
        }

    predicted_vol, vol_fit = _ridge_predict(x, vol, current, config.ridge_penalty)
    predicted_dd, dd_fit = _ridge_predict(x, dd, current, config.ridge_penalty)
    predicted_vol = max(0.0, predicted_vol)
    predicted_dd = max(0.0, predicted_dd)
    volatility_percentile = _empirical_percentile(vol, predicted_vol)
    drawdown_percentile = _empirical_percentile(dd, predicted_dd)
    systemic_percentile = max(volatility_percentile, drawdown_percentile)
    return {
        "available": True,
        "as_of": int(as_of),
        "training_observations": int(len(rows)),
        "latest_training_feature_index": int(rows[-1]),
        "latest_training_label_available_at": int(label_available[rows[-1]]),
        "predicted_forward_volatility": predicted_vol,
        "predicted_forward_drawdown": predicted_dd,
        "volatility_percentile": volatility_percentile,
        "drawdown_percentile": drawdown_percentile,
        "systemic_percentile": systemic_percentile,
        "validation": validation,
        "feature_values": {name: float(value) for name, value in zip(SYSTEMIC_FEATURE_NAMES, current)},
        "volatility_fit": vol_fit,
        "drawdown_fit": dd_fit,
    }


def portfolio_weighted_risk_structure(weights: np.ndarray, covariance: np.ndarray) -> dict[str, float]:
    """PCA on a weight-scaled correlation matrix, so tiny holdings matter less."""
    w = np.asarray(weights, dtype=float).reshape(-1)
    cov = nearest_psd(covariance)
    if cov.shape != (len(w), len(w)):
        raise ValueError("covariance shape must match weights")
    gross = float(w.sum())
    if gross <= _EPS:
        return {"first_pc_share": 0.0, "effective_dimension": 0.0}
    normalized = np.maximum(w, 0.0) / gross
    corr = covariance_to_correlation(cov)
    d = np.diag(np.sqrt(normalized))
    matrix = nearest_psd(d @ corr @ d)
    values = np.maximum(np.linalg.eigvalsh(matrix), 0.0)
    total = float(values.sum())
    if total <= _EPS:
        return {"first_pc_share": 0.0, "effective_dimension": 0.0}
    values = values[::-1]
    return {
        "first_pc_share": float(values[0] / total),
        "effective_dimension": float(total * total / max(float(np.square(values).sum()), _EPS)),
    }


def portfolio_weighted_tail_dependence(returns: np.ndarray, weights: np.ndarray, quantile: float) -> float:
    """Pairwise downside dependence weighted by the capital in each pair."""
    x = _finite_returns(returns)
    w = np.asarray(weights, dtype=float).reshape(-1)
    if len(w) != x.shape[1]:
        raise ValueError("weights length must match returns universe")
    active = np.flatnonzero(w > _EPS)
    if len(active) <= 1:
        return 1.0
    if not (0 < quantile < .5):
        raise ValueError("quantile must be within (0, .5)")
    xa = x[:, active]
    wa = w[active] / max(float(w[active].sum()), _EPS)
    thresholds = np.quantile(xa, quantile, axis=0)
    tail = (xa <= thresholds).astype(float)
    p = tail.mean(axis=0)
    joint = tail.T @ tail / len(tail)
    expected = np.outer(p, p)
    ratio = np.divide(joint, expected, out=np.ones_like(joint), where=expected > _EPS)
    pair_weight = np.outer(wa, wa)
    upper = np.triu_indices(len(active), k=1)
    weights_upper = pair_weight[upper]
    ratios_upper = ratio[upper]
    denominator = float(weights_upper.sum())
    return float(np.sum(weights_upper * ratios_upper) / denominator) if denominator > _EPS else 1.0


def project_capped_simplex(values: np.ndarray, upper: np.ndarray, target_sum: float) -> np.ndarray:
    """Euclidean projection to 0 <= x <= upper and sum(x) == target_sum."""
    y = np.asarray(values, dtype=float).reshape(-1)
    u = np.maximum(np.asarray(upper, dtype=float).reshape(-1), 0.0)
    target = float(np.clip(target_sum, 0.0, u.sum()))
    if target <= _EPS:
        return np.zeros_like(y)
    if abs(target - float(u.sum())) <= 1e-12:
        return u.copy()
    lo = float(np.min(y - u)) - 1.0
    hi = float(np.max(y)) + 1.0
    for _ in range(100):
        mid = (lo + hi) / 2.0
        projected = np.clip(y - mid, 0.0, u)
        if projected.sum() > target:
            lo = mid
        else:
            hi = mid
    return np.clip(y - hi, 0.0, u)


def alpha_aware_quadratic_trim(
    base_weights: np.ndarray,
    target_gross: float,
    covariance: np.ndarray,
    priority: np.ndarray,
    *,
    current_weights: np.ndarray | None,
    risk_aversion: float,
    turnover_penalty: float,
    iterations: int,
    learning_rate: float,
) -> tuple[np.ndarray, dict[str, float]]:
    """Convex projected-gradient trim under a no-position-increase constraint.

    Minimize, subject to 0 <= w <= base and sum(w)=target:

        - normalized_alpha' w
        + risk_aversion * w' normalized_covariance w
        + turnover_penalty * ||w - current||^2

    This is deliberately modest: it only decides *which* V1-approved exposure to
    remove. It cannot add names or raise gross exposure.
    """
    base = np.maximum(np.asarray(base_weights, dtype=float).reshape(-1), 0.0)
    cov = nearest_psd(covariance)
    p = np.asarray(priority, dtype=float).reshape(-1)
    if len(base) != len(p) or cov.shape != (len(base), len(base)):
        raise ValueError("optimizer inputs must align")
    target = float(np.clip(target_gross, 0.0, base.sum()))
    if abs(target - float(base.sum())) <= 1e-12:
        return base.copy(), {"iterations": 0, "objective_change": 0.0}

    p_scale = max(float(np.std(p)), float(np.max(np.abs(p))) * .10, 1e-8)
    p_norm = p / p_scale
    cov_scale = max(float(np.trace(cov)) / max(len(base), 1), _EPS)
    cov_norm = cov / cov_scale
    reference = base if current_weights is None else np.maximum(np.asarray(current_weights, dtype=float).reshape(-1), 0.0)
    if len(reference) != len(base):
        raise ValueError("current_weights length must match base weights")

    w = project_capped_simplex(base, base, target)

    def objective(v: np.ndarray) -> float:
        return float(
            -p_norm @ v
            + risk_aversion * (v @ cov_norm @ v)
            + turnover_penalty * np.square(v - reference).sum()
        )

    starting = objective(w)
    current_objective = starting
    completed_iterations = 0
    for iteration in range(iterations):
        gradient = (
            -p_norm
            + 2.0 * risk_aversion * (cov_norm @ w)
            + 2.0 * turnover_penalty * (w - reference)
        )
        step = learning_rate
        proposal = w
        proposal_objective = current_objective
        # Backtracking prevents the optimizer from accepting a numerically worse
        # portfolio merely because the configured learning rate was too large.
        for _ in range(20):
            candidate = project_capped_simplex(w - step * gradient, base, target)
            candidate_objective = objective(candidate)
            if candidate_objective <= current_objective + 1e-12:
                proposal = candidate
                proposal_objective = candidate_objective
                break
            step *= .5
        completed_iterations = iteration + 1
        if np.linalg.norm(proposal - w) < 1e-10:
            w = proposal
            current_objective = proposal_objective
            break
        w = proposal
        current_objective = proposal_objective
    return w, {
        "iterations": int(completed_iterations),
        "objective_change": float(current_objective - starting),
    }


def predictive_gross_modifier(
    forecast: dict[str, Any],
    previous_persistence_count: int,
    config: V202PredictiveConfig,
) -> dict[str, Any]:
    """Bounded systemic-risk modifier based on validated forecast percentiles.

    The predictor is intentionally universe/systemic, not a forecast of the
    portfolio's absolute volatility.  Therefore it must *not* be compared to the
    portfolio volatility target.  It acts only when a causal forward-risk forecast
    sits in an extreme part of its own completed-label distribution and the
    chronological validation slice shows non-negative predictive value.
    """
    if not forecast.get("available"):
        return {
            "triggered": False,
            "persistence_count": 0,
            "gross_modifier": 1.0,
            "reason": forecast.get("reason", "forecast_unavailable"),
        }

    validation = forecast.get("validation") or {}
    if not validation.get("available"):
        return {
            "triggered": False,
            "persistence_count": 0,
            "gross_modifier": 1.0,
            "reason": "predictor_validation_unavailable",
        }

    vol_validation_r2 = float(validation.get("volatility_r2") or 0.0)
    drawdown_validation_r2 = float(validation.get("drawdown_r2") or 0.0)
    validated = max(vol_validation_r2, drawdown_validation_r2) >= config.minimum_validation_r2
    if not validated:
        return {
            "triggered": False,
            "persistence_count": 0,
            "gross_modifier": 1.0,
            "reason": "predictor_failed_chronological_validation",
            "volatility_validation_r2": vol_validation_r2,
            "drawdown_validation_r2": drawdown_validation_r2,
        }

    systemic_percentile = float(forecast.get("systemic_percentile", max(
        float(forecast.get("volatility_percentile", .5)),
        float(forecast.get("drawdown_percentile", .5)),
    )))
    elevated = systemic_percentile >= config.systemic_trigger_percentile
    persistence = int(previous_persistence_count + 1) if elevated else 0
    if not elevated:
        return {
            "triggered": False,
            "persistence_count": persistence,
            "gross_modifier": 1.0,
            "systemic_percentile": systemic_percentile,
            "reason": "systemic_forecast_below_trigger_percentile",
        }

    intensity = float(np.clip(
        (systemic_percentile - config.systemic_trigger_percentile)
        / max(1.0 - config.systemic_trigger_percentile, _EPS),
        0.0,
        1.0,
    ))
    bounded = 1.0 - config.maximum_additional_gross_reduction * intensity
    if persistence < config.persistence_sessions:
        return {
            "triggered": False,
            "persistence_count": persistence,
            "gross_modifier": 1.0,
            "candidate_gross_modifier": bounded,
            "systemic_percentile": systemic_percentile,
            "reason": "persistence_requirement_not_met",
        }
    return {
        "triggered": bounded < 1.0 - 1e-12,
        "persistence_count": persistence,
        "gross_modifier": bounded,
        "candidate_gross_modifier": bounded,
        "systemic_percentile": systemic_percentile,
        "reason": "persistent_validated_systemic_risk_forecast",
    }
