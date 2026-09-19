"""Universal Risk Supervisor V2.0.1 research primitives.

This module is intentionally model-agnostic. It accepts a proposed long-only
stock portfolio plus only information available through the completed close and
returns diagnostics / adjustments. It does not create forecasts and it never
increases gross or single-name exposure.

V2.0.1 themes:
- adaptive covariance ensemble (long / short / downside / optional anchor)
- statistical factor and security risk decomposition
- diversification-fragility / dimensionality-collapse diagnostics
- downside tail dependence
- moving-block bootstrap uncertainty around portfolio risk
- liquidity / exit-capacity diagnostics
- alpha-priority-preserving de-risking
- synthetic stress and what-if analysis
- risk-radar exceptions suitable for research audit / UI use

The thresholds are research defaults, not claims of optimality. They must be
validated by the repository's sensitivity / holdout protocols before promotion.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Sequence

import numpy as np

TRADING_DAYS = 252
_EPS = 1e-12


@dataclass(frozen=True)
class V201DiagnosticsConfig:
    long_window: int = 126
    short_window: int = 21
    downside_quantile: float = .35
    tail_quantile: float = .10
    covariance_shrinkage: float = .35
    covariance_anchor_weight: float = .20
    ewma_half_life: float = 42.0
    cluster_threshold: float = .70
    bootstrap_samples: int = 120
    bootstrap_block_size: int = 5
    bootstrap_seed: int = 701
    statistical_factor_count: int = 5


def _as_returns(values: np.ndarray) -> np.ndarray:
    x = np.asarray(values, dtype=float)
    if x.ndim != 2 or x.shape[1] < 1:
        raise ValueError("returns_history must have shape [time, assets]")
    if x.shape[0] < 5:
        raise ValueError("V2.0.1 needs at least 5 completed return sessions")
    if not np.isfinite(x).all():
        raise ValueError("returns_history contains non-finite values")
    return x


def _as_weights(values: np.ndarray, n_assets: int) -> np.ndarray:
    w = np.asarray(values, dtype=float).reshape(-1)
    if len(w) != n_assets:
        raise ValueError("weights length must equal asset count")
    if not np.isfinite(w).all() or np.any(w < -1e-12):
        raise ValueError("V2.0.1 currently requires finite long-only weights")
    return np.maximum(w, 0.0)


def nearest_psd(matrix: np.ndarray, floor: float = 1e-12) -> np.ndarray:
    x = np.asarray(matrix, dtype=float)
    if x.ndim != 2 or x.shape[0] != x.shape[1] or not np.isfinite(x).all():
        raise ValueError("covariance must be a finite square matrix")
    x = (x + x.T) / 2.0
    eigenvalues, eigenvectors = np.linalg.eigh(x)
    eigenvalues = np.maximum(eigenvalues, floor)
    repaired = (eigenvectors * eigenvalues) @ eigenvectors.T
    return (repaired + repaired.T) / 2.0


def covariance_to_correlation(covariance: np.ndarray) -> np.ndarray:
    covariance = nearest_psd(covariance)
    volatility = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    denominator = np.outer(volatility, volatility)
    correlation = np.divide(
        covariance,
        denominator,
        out=np.zeros_like(covariance),
        where=denominator > 0,
    )
    correlation = np.clip((correlation + correlation.T) / 2.0, -1.0, 1.0)
    np.fill_diagonal(correlation, 1.0)
    return correlation


def sample_covariance(returns: np.ndarray) -> np.ndarray:
    returns = _as_returns(returns)
    covariance = np.atleast_2d(np.cov(returns, rowvar=False, ddof=0))
    return nearest_psd(covariance)


def ewma_covariance(returns: np.ndarray, half_life: float) -> np.ndarray:
    returns = _as_returns(returns)
    if not math.isfinite(half_life) or half_life <= 1:
        raise ValueError("ewma_half_life must exceed 1")
    n = len(returns)
    decay = math.exp(math.log(.5) / half_life)
    ages = np.arange(n - 1, -1, -1, dtype=float)
    weights = np.power(decay, ages)
    weights /= weights.sum()
    mean = np.average(returns, axis=0, weights=weights)
    centered = returns - mean
    covariance = (centered * weights[:, None]).T @ centered
    return nearest_psd(covariance)


def shrink_covariance(covariance: np.ndarray, shrinkage: float) -> np.ndarray:
    if not (0 <= shrinkage <= 1):
        raise ValueError("shrinkage must be within [0, 1]")
    covariance = nearest_psd(covariance)
    diagonal = np.diag(np.diag(covariance))
    return nearest_psd((1 - shrinkage) * covariance + shrinkage * diagonal)


def annualized_portfolio_volatility(weights: np.ndarray, covariance: np.ndarray) -> float:
    w = np.asarray(weights, dtype=float).reshape(-1)
    covariance = nearest_psd(covariance)
    if covariance.shape != (len(w), len(w)):
        raise ValueError("covariance shape must match weights")
    variance = float(w @ covariance @ w)
    return math.sqrt(max(variance, 0.0) * TRADING_DAYS)


def component_risk_shares(weights: np.ndarray, covariance: np.ndarray) -> np.ndarray:
    w = np.asarray(weights, dtype=float).reshape(-1)
    covariance = nearest_psd(covariance)
    if covariance.shape != (len(w), len(w)):
        raise ValueError("covariance shape must match weights")
    contribution = np.maximum(w * (covariance @ w), 0.0)
    total = float(contribution.sum())
    return contribution / total if total > _EPS else np.zeros_like(w)


def effective_bets(weights: np.ndarray) -> float:
    w = np.asarray(weights, dtype=float).reshape(-1)
    gross = float(w.sum())
    if gross <= _EPS:
        return 0.0
    normalized = w / gross
    return float(1.0 / max(float(np.square(normalized).sum()), _EPS))


def _average_positive_correlation(correlation: np.ndarray, active: np.ndarray | None = None) -> float:
    correlation = np.asarray(correlation, dtype=float)
    if active is not None:
        active = np.asarray(active, dtype=int)
        correlation = correlation[np.ix_(active, active)]
    if correlation.shape[0] <= 1:
        return 0.0
    values = correlation[~np.eye(correlation.shape[0], dtype=bool)]
    return float(np.maximum(values, 0.0).mean()) if len(values) else 0.0


def complete_linkage_clusters(correlation: np.ndarray, active: Iterable[int], threshold: float) -> list[list[int]]:
    """Greedy complete-linkage clusters.

    A candidate joins a cluster only when it is at least ``threshold`` correlated
    with every existing member. This avoids the V1 chain effect A~B, B~C => A/B/C
    even when A and C are weakly related.
    """
    if not (0 < threshold < 1):
        raise ValueError("cluster threshold must be within (0, 1)")
    correlation = np.asarray(correlation, dtype=float)
    remaining = set(int(i) for i in active)
    clusters: list[list[int]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        cluster = [seed]
        changed = True
        while changed:
            changed = False
            candidates = sorted(remaining)
            for candidate in candidates:
                if min(float(correlation[candidate, member]) for member in cluster) >= threshold:
                    cluster.append(candidate)
                    remaining.remove(candidate)
                    changed = True
        clusters.append(sorted(cluster))
    return sorted(clusters, key=lambda item: (-len(item), item))


def pca_risk_structure(covariance: np.ndarray, factor_count: int = 5) -> dict[str, Any]:
    covariance = nearest_psd(covariance)
    values, vectors = np.linalg.eigh(covariance)
    order = np.argsort(values)[::-1]
    values = np.maximum(values[order], 0.0)
    vectors = vectors[:, order]
    total = float(values.sum())
    if total <= _EPS:
        return {
            "first_pc_share": 0.0,
            "top_factor_share": 0.0,
            "effective_dimension": 0.0,
            "eigenvalue_shares": [],
            "loadings": np.zeros((covariance.shape[0], 0)),
        }
    shares = values / total
    effective_dimension = float(total * total / max(float(np.square(values).sum()), _EPS))
    k = min(max(1, factor_count), len(values))
    return {
        "first_pc_share": float(shares[0]),
        "top_factor_share": float(shares[:k].sum()),
        "effective_dimension": effective_dimension,
        "eigenvalue_shares": shares[:k].tolist(),
        "loadings": vectors[:, :k],
    }


def statistical_factor_risk(
    weights: np.ndarray,
    covariance: np.ndarray,
    symbols: Sequence[str] | None = None,
    factor_count: int = 5,
) -> dict[str, Any]:
    """Decompose variance into PCA statistical factors plus security-level risk.

    These are statistical factors, not claimed economic factors. A later economic
    factor model can plug into the same output contract without changing callers.
    """
    w = np.asarray(weights, dtype=float).reshape(-1)
    covariance = nearest_psd(covariance)
    structure = pca_risk_structure(covariance, factor_count)
    values, vectors = np.linalg.eigh(covariance)
    order = np.argsort(values)[::-1]
    values = np.maximum(values[order], 0.0)
    vectors = vectors[:, order]
    total_variance = float(w @ covariance @ w)
    factor_rows: list[dict[str, float]] = []
    explained = 0.0
    if total_variance > _EPS:
        for i in range(min(max(1, factor_count), len(values))):
            exposure = float(w @ vectors[:, i])
            contribution = max(values[i] * exposure * exposure, 0.0)
            explained += contribution
            factor_rows.append({
                "factor": f"statistical_pc_{i + 1}",
                "exposure": exposure,
                "variance_contribution": contribution,
                "risk_share": contribution / total_variance,
            })
    residual = max(total_variance - explained, 0.0)
    security_share = component_risk_shares(w, covariance)
    names = list(symbols) if symbols is not None else [str(i) for i in range(len(w))]
    security_rows = [
        {"symbol": names[i], "weight": float(w[i]), "component_risk_share": float(security_share[i])}
        for i in range(len(w)) if w[i] > 0
    ]
    security_rows.sort(key=lambda row: row["component_risk_share"], reverse=True)
    return {
        "portfolio_variance": total_variance,
        "statistical_factors": factor_rows,
        "residual_variance": residual,
        "residual_risk_share": residual / total_variance if total_variance > _EPS else 0.0,
        "security_risk": security_rows,
        "first_pc_share": structure["first_pc_share"],
        "effective_dimension": structure["effective_dimension"],
    }


def vectorized_tail_dependence(returns: np.ndarray, quantile: float) -> float:
    """Average joint-tail frequency relative to independence, without O(N^2) Python loops."""
    returns = _as_returns(returns)
    if returns.shape[1] <= 1:
        return 1.0
    if not (0 < quantile < .5):
        raise ValueError("tail quantile must be within (0, .5)")
    thresholds = np.quantile(returns, quantile, axis=0)
    tail = (returns <= thresholds).astype(float)
    probability = tail.mean(axis=0)
    joint = (tail.T @ tail) / len(tail)
    expected = np.outer(probability, probability)
    ratio = np.divide(joint, expected, out=np.ones_like(joint), where=expected > _EPS)
    upper = np.triu_indices_from(ratio, k=1)
    values = ratio[upper]
    return float(values.mean()) if len(values) else 1.0


def moving_block_bootstrap_volatility_uncertainty(
    returns: np.ndarray,
    weights: np.ndarray,
    samples: int,
    block_size: int,
    seed: int,
) -> dict[str, float]:
    returns = _as_returns(returns)
    w = _as_weights(weights, returns.shape[1])
    if samples <= 0:
        vol = annualized_portfolio_volatility(w, sample_covariance(returns))
        return {"p10": vol, "median": vol, "p90": vol, "relative_width": 0.0}
    block_size = max(1, min(int(block_size), len(returns)))
    rng = np.random.default_rng(seed)
    starts = np.arange(0, len(returns) - block_size + 1)
    values = np.empty(samples, dtype=float)
    blocks_needed = math.ceil(len(returns) / block_size)
    for b in range(samples):
        chosen = rng.choice(starts, size=blocks_needed, replace=True)
        sample = np.concatenate([returns[start:start + block_size] for start in chosen], axis=0)[:len(returns)]
        values[b] = annualized_portfolio_volatility(w, sample_covariance(sample))
    p10, median, p90 = np.quantile(values, [.10, .50, .90])
    return {
        "p10": float(p10),
        "median": float(median),
        "p90": float(p90),
        "relative_width": float((p90 - p10) / max(float(median), _EPS)),
    }


def volatility_acceleration(returns: np.ndarray, weights: np.ndarray, short_window: int, long_window: int) -> float:
    returns = _as_returns(returns)
    w = _as_weights(weights, returns.shape[1])
    series = returns @ w
    long_n = min(max(2, int(long_window)), len(series))
    short_n = min(max(2, int(short_window)), long_n)
    long_vol = float(np.std(series[-long_n:], ddof=0))
    short_vol = float(np.std(series[-short_n:], ddof=0))
    return short_vol / long_vol if long_vol > _EPS else 1.0


def topology_metrics(
    returns: np.ndarray,
    weights: np.ndarray,
    cluster_threshold: float,
    recent_window: int,
    compare_window: int,
) -> dict[str, Any]:
    returns = _as_returns(returns)
    w = _as_weights(weights, returns.shape[1])
    recent_n = min(max(5, recent_window), len(returns))
    recent = returns[-recent_n:]
    recent_corr = covariance_to_correlation(sample_covariance(recent))
    prior_end = len(returns) - recent_n
    prior_n = min(max(5, compare_window), prior_end)
    if prior_n >= 5:
        prior = returns[prior_end - prior_n:prior_end]
        prior_corr = covariance_to_correlation(sample_covariance(prior))
        prior_average = _average_positive_correlation(prior_corr, np.flatnonzero(w > _EPS))
    else:
        prior_average = _average_positive_correlation(recent_corr, np.flatnonzero(w > _EPS))
    active = np.flatnonzero(w > _EPS)
    current_average = _average_positive_correlation(recent_corr, active)
    clusters = complete_linkage_clusters(recent_corr, active, cluster_threshold) if len(active) else []
    if len(active) > 1:
        active_corr = recent_corr[np.ix_(active, active)]
        off = active_corr[~np.eye(len(active), dtype=bool)]
        density = float(np.mean(off >= cluster_threshold))
    else:
        density = 0.0
    return {
        "average_positive_correlation": current_average,
        "previous_average_positive_correlation": prior_average,
        "correlation_convergence": current_average - prior_average,
        "network_density": density,
        "clusters": clusters,
        "cluster_count": len(clusters),
        "largest_cluster_size": max((len(cluster) for cluster in clusters), default=0),
    }


def adaptive_covariance_ensemble(
    returns: np.ndarray,
    weights: np.ndarray,
    config: V201DiagnosticsConfig,
    anchor_covariance: np.ndarray | None = None,
) -> tuple[np.ndarray, dict[str, float]]:
    returns = _as_returns(returns)
    w = _as_weights(weights, returns.shape[1])
    long_n = min(config.long_window, len(returns))
    short_n = min(config.short_window, long_n)
    long_sample = returns[-long_n:]
    short_sample = returns[-short_n:]
    long_cov = shrink_covariance(ewma_covariance(long_sample, config.ewma_half_life), config.covariance_shrinkage)
    short_cov = shrink_covariance(sample_covariance(short_sample), config.covariance_shrinkage)
    portfolio_series = long_sample @ (w / max(float(w.sum()), _EPS)) if w.sum() > _EPS else long_sample.mean(axis=1)
    cutoff = float(np.quantile(portfolio_series, config.downside_quantile))
    downside_sample = long_sample[portfolio_series <= cutoff]
    downside_cov = (
        shrink_covariance(sample_covariance(downside_sample), config.covariance_shrinkage)
        if len(downside_sample) >= 5 else long_cov
    )
    acceleration = volatility_acceleration(returns, w, config.short_window, config.long_window)
    stress = float(np.clip(acceleration - 1.0, 0.0, 1.0))
    short_weight = .20 + .25 * stress
    downside_weight = .20 + .20 * stress
    anchor_weight = config.covariance_anchor_weight if anchor_covariance is not None else 0.0
    long_weight = 1.0 - short_weight - downside_weight - anchor_weight
    if long_weight < .10:
        deficit = .10 - long_weight
        reduce = short_weight + downside_weight
        if reduce > _EPS:
            short_weight -= deficit * short_weight / reduce
            downside_weight -= deficit * downside_weight / reduce
        long_weight = .10
    ensemble = long_weight * long_cov + short_weight * short_cov + downside_weight * downside_cov
    if anchor_covariance is not None:
        anchor = nearest_psd(anchor_covariance)
        if anchor.shape != ensemble.shape:
            raise ValueError("anchor covariance shape must match returns universe")
        ensemble += anchor_weight * anchor
    ensemble = nearest_psd(ensemble)
    return ensemble, {
        "long_weight": float(long_weight),
        "short_weight": float(short_weight),
        "downside_weight": float(downside_weight),
        "anchor_weight": float(anchor_weight),
        "volatility_acceleration": float(acceleration),
    }


def liquidity_diagnostics(
    weights: np.ndarray,
    capital: float | None,
    adv_dollars: np.ndarray | None,
    participation: float,
    horizon_sessions: int,
) -> dict[str, Any]:
    w = np.asarray(weights, dtype=float).reshape(-1)
    if capital is None or adv_dollars is None:
        return {"available": False, "reason": "capital_or_adv_missing"}
    if not math.isfinite(capital) or capital <= 0:
        raise ValueError("capital must be positive when liquidity diagnostics are enabled")
    adv = np.asarray(adv_dollars, dtype=float).reshape(-1)
    if len(adv) != len(w):
        raise ValueError("adv_dollars length must match weights")
    valid = np.isfinite(adv) & (adv > 0)
    daily_capacity = np.where(valid, adv * participation, np.nan)
    notional = w * capital
    days = np.divide(notional, daily_capacity, out=np.full_like(w, np.inf), where=valid)
    maximum_weight = np.where(valid, adv * participation * horizon_sessions / capital, 0.0)
    return {
        "available": True,
        "days_to_liquidate": days,
        "maximum_liquid_weight": maximum_weight,
        "max_days_to_liquidate": float(np.max(days[w > _EPS])) if np.any(w > _EPS) else 0.0,
        "illiquid_weight": float(w[(w > maximum_weight + 1e-12)].sum()),
        "missing_adv_names": int((~valid & (w > _EPS)).sum()),
    }


def alpha_priority(
    expected_alpha: np.ndarray | None,
    prediction_error: np.ndarray | None,
    fallback_priority: np.ndarray | None,
    n_assets: int,
) -> np.ndarray:
    if expected_alpha is not None:
        alpha = np.asarray(expected_alpha, dtype=float).reshape(-1)
        if len(alpha) != n_assets:
            raise ValueError("expected_alpha length must match weights")
        alpha = np.nan_to_num(alpha, nan=0.0, posinf=0.0, neginf=0.0)
        if prediction_error is not None:
            error = np.asarray(prediction_error, dtype=float).reshape(-1)
            if len(error) != n_assets:
                raise ValueError("prediction_error length must match weights")
            finite_positive = error[np.isfinite(error) & (error > _EPS)]
            floor = float(np.quantile(finite_positive, .10)) if len(finite_positive) else 1.0
            error = np.where(np.isfinite(error) & (error > _EPS), np.maximum(error, floor), 1.0)
            return alpha / error
        return alpha
    if fallback_priority is not None:
        priority = np.asarray(fallback_priority, dtype=float).reshape(-1)
        if len(priority) != n_assets:
            raise ValueError("fallback_priority length must match weights")
        return np.nan_to_num(priority, nan=0.0, posinf=0.0, neginf=0.0)
    return np.zeros(n_assets, dtype=float)


def _trim_scores(weights: np.ndarray, covariance: np.ndarray, priority: np.ndarray) -> np.ndarray:
    marginal_variance = np.maximum(covariance @ weights, 0.0)
    risk_per_weight = marginal_variance / np.maximum(weights, _EPS)
    utility = priority / np.maximum(risk_per_weight, _EPS)
    return np.where(priority < 0, -1e12 + priority, utility)


def trim_to_gross(
    weights: np.ndarray,
    target_gross: float,
    covariance: np.ndarray,
    priority: np.ndarray,
) -> np.ndarray:
    adjusted = np.asarray(weights, dtype=float).copy()
    target_gross = max(0.0, min(float(target_gross), float(adjusted.sum())))
    excess = float(adjusted.sum() - target_gross)
    if excess <= _EPS:
        return adjusted
    order = np.argsort(_trim_scores(adjusted, covariance, priority))
    for index in order:
        if excess <= _EPS:
            break
        cut = min(float(adjusted[index]), excess)
        adjusted[index] -= cut
        excess -= cut
    return np.maximum(adjusted, 0.0)


def apply_cluster_caps(
    weights: np.ndarray,
    clusters: list[list[int]],
    cap: float,
    covariance: np.ndarray,
    priority: np.ndarray,
) -> np.ndarray:
    adjusted = np.asarray(weights, dtype=float).copy()
    for cluster in clusters:
        if not cluster:
            continue
        current = float(adjusted[cluster].sum())
        if current <= cap + _EPS:
            continue
        local = adjusted[cluster]
        local_covariance = covariance[np.ix_(cluster, cluster)]
        local_priority = priority[cluster]
        adjusted[cluster] = trim_to_gross(local, cap, local_covariance, local_priority)
    return adjusted


def apply_component_risk_cap(
    weights: np.ndarray,
    covariance: np.ndarray,
    priority: np.ndarray,
    maximum_share: float,
    iterations: int = 120,
) -> tuple[np.ndarray, bool, int]:
    adjusted = np.asarray(weights, dtype=float).copy()
    for iteration in range(iterations):
        shares = component_risk_shares(adjusted, covariance)
        offenders = np.flatnonzero((adjusted > _EPS) & (shares > maximum_share + 1e-6))
        if not len(offenders):
            return adjusted, True, iteration
        candidate_scores = _trim_scores(adjusted, covariance, priority)
        chosen = int(offenders[np.argmin(candidate_scores[offenders])])
        # Small steps avoid the oscillatory over-trim of V1's one-shot ratio.
        severity = min(1.0, (float(shares[chosen]) / maximum_share) - 1.0)
        cut_fraction = max(.02, min(.20, .05 + .15 * severity))
        adjusted[chosen] *= 1.0 - cut_fraction
    return adjusted, False, iterations


def fragility_components(
    *,
    first_pc_share: float,
    effective_dimension: float,
    average_correlation: float,
    correlation_convergence: float,
    network_density: float,
    tail_dependence: float,
    volatility_acceleration_ratio: float,
    risk_uncertainty_width: float,
    effective_bet_count: float,
    minimum_effective_bets: float,
    liquidity_days: float | None,
    maximum_liquidity_days: float,
) -> dict[str, float]:
    def ramp(value: float, warning: float, severe: float) -> float:
        if value <= warning:
            return 0.0
        return float(np.clip((value - warning) / max(severe - warning, _EPS), 0.0, 1.0))

    components = {
        "common_component": ramp(first_pc_share, .40, .70),
        "dimension_collapse": ramp(max(0.0, minimum_effective_bets - effective_dimension), 0.0, max(minimum_effective_bets - 1.0, 1.0)),
        "correlation_level": ramp(average_correlation, .35, .75),
        "correlation_convergence": ramp(max(correlation_convergence, 0.0), .05, .30),
        "network_density": ramp(network_density, .20, .70),
        "tail_dependence": ramp(tail_dependence, 1.50, 3.50),
        "volatility_acceleration": ramp(volatility_acceleration_ratio, 1.20, 2.25),
        "risk_estimate_uncertainty": ramp(risk_uncertainty_width, .30, .90),
        "effective_bets": ramp(max(0.0, minimum_effective_bets - effective_bet_count), 0.0, max(minimum_effective_bets - 1.0, 1.0)),
    }
    if liquidity_days is not None:
        components["liquidity"] = ramp(liquidity_days, maximum_liquidity_days, maximum_liquidity_days * 3.0)
    return components


def weighted_fragility_score(components: dict[str, float]) -> float:
    weights = {
        "common_component": .16,
        "dimension_collapse": .12,
        "correlation_level": .08,
        "correlation_convergence": .14,
        "network_density": .07,
        "tail_dependence": .14,
        "volatility_acceleration": .12,
        "risk_estimate_uncertainty": .08,
        "effective_bets": .09,
        "liquidity": .10,
    }
    denominator = sum(weights[key] for key in components)
    return 100.0 * sum(components[key] * weights[key] for key in components) / max(denominator, _EPS)


def synthetic_stress_tests(
    weights: np.ndarray,
    covariance: np.ndarray,
    factor_risk: dict[str, Any],
    symbols: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Current-exposure stress diagnostics.

    These are transparent synthetic scenarios, not forecasts or copied Aladdin
    scenarios. They are designed to answer how the current book behaves when
    volatility, correlation, or common-factor concentration deteriorates.
    """
    w = np.asarray(weights, dtype=float).reshape(-1)
    covariance = nearest_psd(covariance)
    gross = float(w.sum())
    vol = annualized_portfolio_volatility(w, covariance)
    correlation = covariance_to_correlation(covariance)
    sigma = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    converged = nearest_psd(.15 * np.diag(np.diag(covariance)) + .85 * np.outer(sigma, sigma))
    converged_vol = annualized_portfolio_volatility(w, converged)
    security_rows = factor_risk.get("security_risk", [])
    top_symbols = [row["symbol"] for row in security_rows[:3]]
    names = list(symbols) if symbols is not None else [str(i) for i in range(len(w))]
    top_indices = [names.index(name) for name in top_symbols if name in names]
    top_weight = float(w[top_indices].sum()) if top_indices else 0.0
    return [
        {
            "id": "broad_equity_shock",
            "type": "synthetic",
            "assumption": "all active equities -8%",
            "estimated_return": -0.08 * gross,
        },
        {
            "id": "correlation_convergence",
            "type": "synthetic",
            "assumption": "85% common covariance / 15% idiosyncratic covariance",
            "baseline_annual_volatility": vol,
            "stressed_annual_volatility": converged_vol,
            "volatility_multiple": converged_vol / max(vol, _EPS),
            "estimated_one_day_3sigma_loss": -3.0 * converged_vol / math.sqrt(TRADING_DAYS),
        },
        {
            "id": "common_factor_break",
            "type": "synthetic",
            "assumption": "largest statistical common component reverses",
            "estimated_one_day_3sigma_loss": -3.0 * vol / math.sqrt(TRADING_DAYS) * max(.5, float(factor_risk.get("first_pc_share", 0.0))),
        },
        {
            "id": "top_risk_names_gap",
            "type": "synthetic",
            "assumption": f"top component-risk names ({', '.join(top_symbols) or 'n/a'}) gap -15%",
            "estimated_return": -0.15 * top_weight,
        },
        {
            "id": "volatility_doubles",
            "type": "synthetic",
            "assumption": "covariance scaled 4x => volatility 2x",
            "baseline_annual_volatility": vol,
            "stressed_annual_volatility": 2.0 * vol,
            "estimated_one_day_3sigma_loss": -6.0 * vol / math.sqrt(TRADING_DAYS),
        },
        {
            "id": "correlation_snapshot",
            "type": "diagnostic",
            "average_positive_correlation": _average_positive_correlation(correlation, np.flatnonzero(w > _EPS)),
        },
    ]


def risk_radar(
    *,
    fragility_score: float,
    first_pc_share: float,
    effective_dimension: float,
    average_correlation: float,
    correlation_convergence: float,
    tail_dependence: float,
    volatility_acceleration_ratio: float,
    uncertainty_width: float,
    maximum_component_risk: float,
    max_days_to_liquidate: float | None,
    component_limit: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(level: str, code: str, message: str, value: float | None = None) -> None:
        rows.append({"level": level, "code": code, "message": message, "value": value})

    if fragility_score >= 70:
        add("critical", "fragility", "Portfolio fragility is in the systemic range", fragility_score)
    elif fragility_score >= 50:
        add("high", "fragility", "Portfolio fragility is elevated", fragility_score)
    elif fragility_score >= 30:
        add("medium", "fragility", "Diversification structure is converging", fragility_score)
    if first_pc_share >= .60:
        add("high", "common_component", "One statistical component explains most covariance", first_pc_share)
    elif first_pc_share >= .45:
        add("medium", "common_component", "Common-component concentration is rising", first_pc_share)
    if effective_dimension < 3:
        add("high", "dimension", "Effective risk dimensionality is very low", effective_dimension)
    if average_correlation >= .60:
        add("high", "correlation", "Average positive correlation is high", average_correlation)
    if correlation_convergence >= .15:
        add("high", "correlation_acceleration", "Correlations are converging rapidly", correlation_convergence)
    elif correlation_convergence >= .07:
        add("medium", "correlation_acceleration", "Correlations are converging", correlation_convergence)
    if tail_dependence >= 3.0:
        add("high", "tail_dependence", "Joint downside events occur far more often than independence implies", tail_dependence)
    elif tail_dependence >= 1.75:
        add("medium", "tail_dependence", "Downside co-movement is elevated", tail_dependence)
    if volatility_acceleration_ratio >= 1.75:
        add("high", "volatility_acceleration", "Short-horizon volatility is accelerating sharply", volatility_acceleration_ratio)
    elif volatility_acceleration_ratio >= 1.30:
        add("medium", "volatility_acceleration", "Short-horizon volatility is accelerating", volatility_acceleration_ratio)
    if uncertainty_width >= .70:
        add("high", "risk_uncertainty", "Risk estimate confidence interval is unusually wide", uncertainty_width)
    if maximum_component_risk > component_limit + 1e-6:
        add("high", "component_risk", "A security exceeds the component-risk budget", maximum_component_risk)
    if max_days_to_liquidate is not None and max_days_to_liquidate > 5:
        add("high", "liquidity", "At least one position exceeds the normal liquidation horizon", max_days_to_liquidate)
    order = {"critical": 0, "high": 1, "medium": 2, "normal": 3}
    return sorted(rows, key=lambda row: (order.get(row["level"], 9), row["code"]))


def evaluate_what_if(
    candidate_weights: np.ndarray,
    covariance: np.ndarray,
    priority: np.ndarray | None = None,
) -> dict[str, float]:
    w = np.asarray(candidate_weights, dtype=float).reshape(-1)
    covariance = nearest_psd(covariance)
    shares = component_risk_shares(w, covariance)
    output = {
        "gross": float(w.sum()),
        "annualized_volatility": annualized_portfolio_volatility(w, covariance),
        "effective_bets": effective_bets(w),
        "maximum_component_risk": float(shares.max()) if len(shares) else 0.0,
    }
    if priority is not None:
        p = np.asarray(priority, dtype=float).reshape(-1)
        if len(p) == len(w):
            output["priority_retained"] = float(p @ w)
    return output
