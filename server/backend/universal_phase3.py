"""Phase 3 institutional research diagnostics for Universal.

Phase 3 is deliberately *observational*: it measures current-portfolio stress,
liquidity/capacity, risk exceptions, what-if repairs, and attribution without
changing target weights.  That invariant lets research compare Phase 1.5,
Phase 1.5+3, and Phase 1.5+2+3 without accidentally attributing P&L changes to
what is meant to be a diagnostic layer.

All calculations are causal to the supplied completed close. Historical stress
scenarios are selected only from the supplied trailing return history; no
future dates are consulted.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import numpy as np
import pandas as pd


TRADING_DAYS = 252
_EPS = 1e-12


@dataclass(frozen=True)
class Phase3Config:
    """Research-only institutional diagnostic layer.

    The defaults are intentionally interpretable rather than optimized.  Phase
    3 cannot alter a position; thresholds only determine Risk Radar labels.
    """

    enabled: bool = False

    # Historical/current-exposure stress
    historical_lookback_sessions: int = 756
    historical_multi_day_window: int = 5
    synthetic_market_shock: float = -0.10
    synthetic_top_name_gap: float = -0.20
    synthetic_top_name_count: int = 5
    correlation_crisis_level: float = 0.90
    volatility_shock_multiple: float = 2.0

    # Liquidity/capacity stress
    liquidity_adv_haircut: float = 0.50
    liquidity_spread_multiple: float = 3.0
    liquidation_participation: float = 0.10
    maximum_exit_days: float = 5.0
    capacity_horizon_sessions: int = 3
    capacity_buffer: float = 0.80

    # Risk radar thresholds
    component_risk_warning: float = 0.20
    component_risk_critical: float = 0.30
    effective_bets_warning: float = 4.0
    effective_bets_critical: float = 2.5
    stress_loss_warning: float = 0.08
    stress_loss_critical: float = 0.15
    systematic_share_warning: float = 0.65
    systematic_share_critical: float = 0.80

    # What-if diagnostics
    what_if_trim_fraction: float = 0.25
    what_if_top_risk_names: int = 3

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("Phase3 enabled must be boolean")
        integer_fields = {
            "historical_lookback_sessions": self.historical_lookback_sessions,
            "historical_multi_day_window": self.historical_multi_day_window,
            "synthetic_top_name_count": self.synthetic_top_name_count,
            "capacity_horizon_sessions": self.capacity_horizon_sessions,
            "what_if_top_risk_names": self.what_if_top_risk_names,
        }
        if not all(isinstance(value, int) for value in integer_fields.values()):
            raise ValueError("Phase3 session/count settings must be integers")
        if not (63 <= self.historical_lookback_sessions <= 5040):
            raise ValueError("historical_lookback_sessions must be within [63, 5040]")
        if not (2 <= self.historical_multi_day_window <= 21):
            raise ValueError("historical_multi_day_window must be within [2, 21]")
        if not (1 <= self.synthetic_top_name_count <= 24):
            raise ValueError("synthetic_top_name_count must be within [1, 24]")
        if not (-0.75 <= self.synthetic_market_shock < 0):
            raise ValueError("synthetic_market_shock must be a negative return")
        if not (-0.95 <= self.synthetic_top_name_gap < 0):
            raise ValueError("synthetic_top_name_gap must be a negative return")
        if not (0.50 <= self.correlation_crisis_level <= 0.999):
            raise ValueError("correlation_crisis_level must be within [.50, .999]")
        if not (1.0 <= self.volatility_shock_multiple <= 10.0):
            raise ValueError("volatility_shock_multiple must be within [1, 10]")
        if not (0 < self.liquidity_adv_haircut <= 1):
            raise ValueError("liquidity_adv_haircut must be within (0, 1]")
        if not (1 <= self.liquidity_spread_multiple <= 20):
            raise ValueError("liquidity_spread_multiple must be within [1, 20]")
        if not (0 < self.liquidation_participation <= 0.25):
            raise ValueError("liquidation_participation must be within (0, .25]")
        if not (0 < self.maximum_exit_days <= 60):
            raise ValueError("maximum_exit_days must be within (0, 60]")
        if not (1 <= self.capacity_horizon_sessions <= 20):
            raise ValueError("capacity_horizon_sessions must be within [1, 20]")
        if not (0 < self.capacity_buffer <= 1):
            raise ValueError("capacity_buffer must be within (0, 1]")
        if not (0 < self.component_risk_warning < self.component_risk_critical <= 1):
            raise ValueError("component-risk thresholds must be increasing")
        if not (1 <= self.effective_bets_critical < self.effective_bets_warning <= 24):
            raise ValueError("effective-bet thresholds must satisfy critical < warning")
        if not (0 < self.stress_loss_warning < self.stress_loss_critical < 1):
            raise ValueError("stress-loss thresholds must be increasing")
        if not (0 < self.systematic_share_warning < self.systematic_share_critical <= 1):
            raise ValueError("systematic-share thresholds must be increasing")
        if not (0 < self.what_if_trim_fraction <= 1):
            raise ValueError("what_if_trim_fraction must be within (0, 1]")
        if not (1 <= self.what_if_top_risk_names <= 24):
            raise ValueError("what_if_top_risk_names must be within [1, 24]")


def _validate_weights(weights: np.ndarray, n_assets: int) -> np.ndarray:
    w = np.asarray(weights, dtype=float).reshape(-1)
    if len(w) != n_assets or not np.isfinite(w).all() or (w < -1e-12).any():
        raise ValueError("Phase3 requires finite long-only weights aligned to returns")
    return np.maximum(w, 0.0)


def _nearest_psd(matrix: np.ndarray, floor: float = 1e-12) -> np.ndarray:
    x = np.asarray(matrix, dtype=float)
    x = (x + x.T) / 2.0
    values, vectors = np.linalg.eigh(x)
    values = np.maximum(values, floor)
    result = (vectors * values) @ vectors.T
    return (result + result.T) / 2.0


def _correlation(covariance: np.ndarray) -> np.ndarray:
    cov = np.asarray(covariance, dtype=float)
    vol = np.sqrt(np.maximum(np.diag(cov), 0.0))
    denom = np.outer(vol, vol)
    corr = np.divide(cov, denom, out=np.zeros_like(cov), where=denom > 0)
    corr = np.clip((corr + corr.T) / 2.0, -1.0, 1.0)
    np.fill_diagonal(corr, 1.0)
    return corr


def _annualized_volatility(weights: np.ndarray, covariance: np.ndarray) -> float:
    variance = float(weights @ covariance @ weights)
    return float(math.sqrt(max(variance, 0.0) * TRADING_DAYS))


def _component_risk(weights: np.ndarray, covariance: np.ndarray) -> np.ndarray:
    contribution = np.maximum(weights * (covariance @ weights), 0.0)
    total = float(contribution.sum())
    return np.divide(contribution, total, out=np.zeros_like(contribution), where=total > _EPS)


def _effective_bets(weights: np.ndarray) -> float:
    gross = float(weights.sum())
    if gross <= _EPS:
        return 0.0
    normalized = weights / gross
    return float(1.0 / max(float(np.square(normalized).sum()), _EPS))


def _market_beta_vector(
    returns_history: np.ndarray,
    benchmark_history: np.ndarray | None,
) -> np.ndarray:
    n_assets = returns_history.shape[1]
    if benchmark_history is None:
        benchmark = returns_history.mean(axis=1)
    else:
        benchmark = np.asarray(benchmark_history, dtype=float).reshape(-1)
        if len(benchmark) != len(returns_history) or not np.isfinite(benchmark).all():
            raise ValueError("Phase3 benchmark history must align with return history")
    market_variance = float(np.var(benchmark, ddof=0))
    if market_variance <= _EPS:
        return np.zeros(n_assets, dtype=float)
    centered_market = benchmark - benchmark.mean()
    centered_assets = returns_history - returns_history.mean(axis=0)
    covariance = centered_assets.T @ centered_market / len(benchmark)
    return np.asarray(covariance / market_variance, dtype=float)


def _scenario_result(
    scenario_id: str,
    asset_shocks: np.ndarray,
    weights: np.ndarray,
    symbols: list[str],
    *,
    description: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    shocks = np.asarray(asset_shocks, dtype=float).reshape(-1)
    contribution = weights * shocks
    order = np.argsort(contribution)
    return {
        "id": scenario_id,
        "description": description,
        "portfolio_return": float(contribution.sum()),
        "portfolio_loss": float(max(0.0, -contribution.sum())),
        "worst_contributors": [
            {
                "symbol": str(symbols[i]),
                "shock": float(shocks[i]),
                "contribution": float(contribution[i]),
            }
            for i in order[: min(5, len(order))]
            if contribution[i] < 0
        ],
        "metadata": {} if metadata is None else metadata,
    }


def historical_stress_scenarios(
    returns_history: np.ndarray,
    weights: np.ndarray,
    symbols: list[str],
    config: Phase3Config,
    *,
    dates: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Reapply the worst *prior observed* current-exposure shocks.

    Scenario selection uses the current portfolio to identify the worst completed
    historical 1-day and rolling-N-day windows within the trailing sample.  This
    avoids using future crises inside historical backtests.
    """
    x = np.asarray(returns_history, dtype=float)
    if len(x) == 0:
        return []
    lookback = min(config.historical_lookback_sessions, len(x))
    x = x[-lookback:]
    date_slice = None if dates is None else dates[-lookback:]
    portfolio = x @ weights
    worst_idx = int(np.argmin(portfolio))
    one_meta: dict[str, Any] = {"source": "trailing_completed_history"}
    if date_slice is not None and len(date_slice) == len(x):
        one_meta["date"] = str(date_slice[worst_idx])
    scenarios = [
        _scenario_result(
            "historical_worst_1d",
            x[worst_idx],
            weights,
            symbols,
            description="Worst completed one-session shock for the current portfolio inside the trailing causal window.",
            metadata=one_meta,
        )
    ]

    horizon = config.historical_multi_day_window
    if len(x) >= horizon:
        gross_paths = np.cumprod(1.0 + x, axis=0)
        rolling = np.empty((len(x) - horizon + 1, x.shape[1]), dtype=float)
        for start in range(len(rolling)):
            if start == 0:
                rolling[start] = gross_paths[horizon - 1] - 1.0
            else:
                rolling[start] = gross_paths[start + horizon - 1] / gross_paths[start - 1] - 1.0
        portfolio_windows = rolling @ weights
        worst_start = int(np.argmin(portfolio_windows))
        multi_meta: dict[str, Any] = {
            "source": "trailing_completed_history",
            "window_sessions": horizon,
        }
        if date_slice is not None and len(date_slice) == len(x):
            multi_meta["start"] = str(date_slice[worst_start])
            multi_meta["end"] = str(date_slice[worst_start + horizon - 1])
        scenarios.append(
            _scenario_result(
                f"historical_worst_{horizon}d",
                rolling[worst_start],
                weights,
                symbols,
                description=f"Worst completed {horizon}-session current-portfolio shock inside the trailing causal window.",
                metadata=multi_meta,
            )
        )
    return scenarios


def synthetic_stress_scenarios(
    returns_history: np.ndarray,
    weights: np.ndarray,
    covariance: np.ndarray,
    symbols: list[str],
    config: Phase3Config,
    *,
    benchmark_history: np.ndarray | None = None,
    factor_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    x = np.asarray(returns_history, dtype=float)
    beta = _market_beta_vector(x, benchmark_history)
    market_shocks = beta * config.synthetic_market_shock
    scenarios = [
        _scenario_result(
            "synthetic_market_shock",
            market_shocks,
            weights,
            symbols,
            description="Current completed-return market betas shocked by the configured equity-market decline.",
            metadata={"market_shock": config.synthetic_market_shock},
        )
    ]

    top = np.argsort(weights)[::-1][: min(config.synthetic_top_name_count, len(weights))]
    gap = np.zeros_like(weights)
    gap[top] = config.synthetic_top_name_gap
    scenarios.append(
        _scenario_result(
            "synthetic_top_names_gap",
            gap,
            weights,
            symbols,
            description="Largest current holdings receive a simultaneous adverse gap.",
            metadata={
                "gap": config.synthetic_top_name_gap,
                "count": int(len(top)),
                "symbols": [str(symbols[i]) for i in top],
            },
        )
    )

    vol = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    crisis_corr = np.full_like(covariance, config.correlation_crisis_level)
    np.fill_diagonal(crisis_corr, 1.0)
    crisis_cov = _nearest_psd(crisis_corr * np.outer(vol, vol))
    scenarios.append({
        "id": "synthetic_correlation_crisis",
        "description": "Current single-name volatilities with all pairwise correlations forced toward the configured crisis level.",
        "base_annualized_volatility": _annualized_volatility(weights, covariance),
        "stressed_annualized_volatility": _annualized_volatility(weights, crisis_cov),
        "correlation_level": float(config.correlation_crisis_level),
    })
    scenarios.append({
        "id": "synthetic_volatility_shock",
        "description": "Current covariance scaled by the configured volatility multiple.",
        "base_annualized_volatility": _annualized_volatility(weights, covariance),
        "stressed_annualized_volatility": _annualized_volatility(
            weights, covariance * config.volatility_shock_multiple ** 2
        ),
        "volatility_multiple": float(config.volatility_shock_multiple),
    })

    if factor_snapshot and factor_snapshot.get("available"):
        factor_names = list(factor_snapshot.get("factor_names", []))
        exposures = np.asarray(factor_snapshot["exposures"], dtype=float)
        factor_cov = np.asarray(factor_snapshot["factor_covariance"], dtype=float)
        factor_sigma = np.sqrt(np.maximum(np.diag(factor_cov), 0.0))
        portfolio_exposure = weights @ exposures
        factor_risk_proxy = np.abs(portfolio_exposure * factor_sigma)
        if len(factor_risk_proxy):
            idx = int(np.argmax(factor_risk_proxy))
            direction = -1.0 if portfolio_exposure[idx] >= 0 else 1.0
            factor_move = np.zeros(len(factor_names), dtype=float)
            factor_move[idx] = direction * 2.5 * factor_sigma[idx]
            asset_shock = exposures @ factor_move
            scenarios.append(
                _scenario_result(
                    "synthetic_dominant_factor_shock",
                    asset_shock,
                    weights,
                    symbols,
                    description="Dominant current factor exposure receives a 2.5-sigma adverse factor move.",
                    metadata={
                        "factor": str(factor_names[idx]),
                        "factor_shock": float(factor_move[idx]),
                        "portfolio_factor_exposure": float(portfolio_exposure[idx]),
                    },
                )
            )
    return scenarios


def liquidity_and_capacity(
    weights: np.ndarray,
    adv_dollars: np.ndarray | None,
    capital: float,
    config: Phase3Config,
) -> dict[str, Any]:
    if adv_dollars is None:
        return {"available": False, "reason": "adv_unavailable"}
    adv = np.asarray(adv_dollars, dtype=float).reshape(-1)
    if len(adv) != len(weights) or not np.isfinite(adv).all() or (adv <= 0).any():
        return {"available": False, "reason": "adv_invalid_or_incomplete"}
    notional = weights * float(capital)
    normal_capacity = adv * config.liquidation_participation
    stressed_capacity = adv * config.liquidity_adv_haircut * config.liquidation_participation
    normal_days = np.divide(notional, normal_capacity, out=np.zeros_like(notional), where=normal_capacity > 0)
    stress_days = np.divide(notional, stressed_capacity, out=np.zeros_like(notional), where=stressed_capacity > 0)
    total_adv = float(adv.sum())
    capacity_dollars = (
        total_adv
        * config.liquidity_adv_haircut
        * config.liquidation_participation
        * config.capacity_horizon_sessions
        * config.capacity_buffer
    )
    gross = float(weights.sum())
    return {
        "available": True,
        "maximum_exit_days": float(normal_days.max()) if len(normal_days) else 0.0,
        "maximum_stressed_exit_days": float(stress_days.max()) if len(stress_days) else 0.0,
        "weighted_exit_days": float(np.sum(normal_days * weights) / max(gross, _EPS)),
        "weighted_stressed_exit_days": float(np.sum(stress_days * weights) / max(gross, _EPS)),
        "stressed_capacity_dollars": capacity_dollars,
        "capital_to_stressed_capacity": float(capital / max(capacity_dollars, _EPS)),
        "adv_haircut": float(config.liquidity_adv_haircut),
        "spread_multiple": float(config.liquidity_spread_multiple),
        "participation": float(config.liquidation_participation),
    }


def current_portfolio_multifactor_alpha(
    weights: np.ndarray,
    returns_history: np.ndarray,
    dates: list[str] | None,
    factor_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """Descriptive static-current-portfolio factor-adjusted alpha diagnostic.

    This is *not* the strategy's realized alpha.  It asks how the portfolio held
    at today's weights would have behaved over the factor model's completed
    return-history dates after controlling for those factors.  The label is
    deliberately explicit to prevent this diagnostic from being promoted into a
    performance claim.
    """
    if not factor_snapshot or not factor_snapshot.get("available"):
        return {"available": False, "reason": "factor_model_unavailable"}
    if dates is None:
        return {"available": False, "reason": "dated_return_history_required"}
    factor_returns = np.asarray(factor_snapshot.get("factor_returns"), dtype=float)
    factor_dates = [str(pd.Timestamp(day).date()) for day in factor_snapshot.get("factor_return_dates", [])]
    if factor_returns.ndim != 2 or len(factor_returns) != len(factor_dates) or len(factor_returns) < 20:
        return {"available": False, "reason": "insufficient_factor_return_history"}
    date_to_row = {str(pd.Timestamp(day).date()): i for i, day in enumerate(dates)}
    matched_factor_rows: list[int] = []
    matched_asset_rows: list[int] = []
    for factor_row, day in enumerate(factor_dates):
        if day in date_to_row:
            matched_factor_rows.append(factor_row)
            matched_asset_rows.append(date_to_row[day])
    if len(matched_factor_rows) < 20:
        return {"available": False, "reason": "insufficient_date_overlap"}
    f = factor_returns[matched_factor_rows]
    asset = np.asarray(returns_history, dtype=float)[matched_asset_rows]
    y = asset @ np.asarray(weights, dtype=float)
    design = np.column_stack([np.ones(len(f)), f])
    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    fitted = design @ coefficients
    residual = y - fitted
    ss_total = float(np.square(y - y.mean()).sum())
    ss_residual = float(np.square(residual).sum())
    r2 = None if ss_total <= _EPS else float(1.0 - ss_residual / ss_total)
    names = list(factor_snapshot.get("factor_names", []))
    betas = {str(names[i]): float(coefficients[i + 1]) for i in range(min(len(names), len(coefficients) - 1))}
    return {
        "available": True,
        "definition": "Static current weights replayed over completed factor-history dates; descriptive diagnostic, not strategy alpha.",
        "observations": int(len(y)),
        "annualized_intercept": float(coefficients[0] * TRADING_DAYS),
        "daily_intercept": float(coefficients[0]),
        "r_squared": r2,
        "factor_betas": betas,
        "residual_annualized_volatility": float(np.std(residual, ddof=0) * math.sqrt(TRADING_DAYS)),
    }


def _factor_diagnostics(weights: np.ndarray, factor_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not factor_snapshot or not factor_snapshot.get("available"):
        return {"available": False, "reason": "factor_model_unavailable"}
    # Local implementation avoids making Phase 3 depend on Phase 2 modules at import time.
    exposure_matrix = np.asarray(factor_snapshot["exposures"], dtype=float)
    factor_cov = np.asarray(factor_snapshot["factor_covariance"], dtype=float)
    idio = np.asarray(factor_snapshot["idiosyncratic_variance"], dtype=float)
    names = list(factor_snapshot["factor_names"])
    exposure = weights @ exposure_matrix
    marginal = factor_cov @ exposure
    contribution = exposure * marginal
    factor_variance = float(exposure @ factor_cov @ exposure)
    idio_variance = float(np.sum(np.square(weights) * idio))
    total = max(factor_variance + idio_variance, _EPS)
    absolute = np.abs(contribution)
    dominant = int(np.argmax(absolute)) if len(absolute) else None
    return {
        "available": True,
        "systematic_variance_share": float(max(factor_variance, 0.0) / total),
        "idiosyncratic_variance_share": float(max(idio_variance, 0.0) / total),
        "factor_exposures": {str(names[i]): float(exposure[i]) for i in range(len(names))},
        "factor_variance_contributions": {str(names[i]): float(contribution[i]) for i in range(len(names))},
        "dominant_factor": None if dominant is None else str(names[dominant]),
        "dominant_factor_absolute_contribution": 0.0 if dominant is None else float(absolute[dominant]),
    }


def what_if_risk_repair(
    weights: np.ndarray,
    covariance: np.ndarray,
    symbols: list[str],
    config: Phase3Config,
    *,
    expected_alpha: np.ndarray | None = None,
) -> dict[str, Any]:
    if weights.sum() <= _EPS:
        return {"available": False, "reason": "empty_portfolio"}
    shares = _component_risk(weights, covariance)
    candidates = np.argsort(shares)[::-1][: min(config.what_if_top_risk_names, len(weights))]
    repaired = weights.copy()
    repaired[candidates] *= 1.0 - config.what_if_trim_fraction
    risk_before = _annualized_volatility(weights, covariance)
    risk_after = _annualized_volatility(repaired, covariance)
    alpha_before = alpha_after = retention = None
    if expected_alpha is not None:
        alpha = np.asarray(expected_alpha, dtype=float).reshape(-1)
        if len(alpha) == len(weights) and np.isfinite(alpha).all():
            alpha_before = float(alpha @ weights)
            alpha_after = float(alpha @ repaired)
            retention = None if abs(alpha_before) <= _EPS else float(alpha_after / alpha_before)
    return {
        "available": True,
        "action": f"trim top {len(candidates)} component-risk names by {config.what_if_trim_fraction:.0%} to cash",
        "symbols": [str(symbols[i]) for i in candidates],
        "gross_before": float(weights.sum()),
        "gross_after": float(repaired.sum()),
        "annualized_volatility_before": risk_before,
        "annualized_volatility_after": risk_after,
        "volatility_reduction": float(risk_before - risk_after),
        "alpha_before": alpha_before,
        "alpha_after": alpha_after,
        "alpha_retention": retention,
    }


def risk_radar(
    weights: np.ndarray,
    covariance: np.ndarray,
    historical: list[dict[str, Any]],
    synthetic: list[dict[str, Any]],
    liquidity: dict[str, Any],
    factor: dict[str, Any],
    config: Phase3Config,
    symbols: list[str],
) -> dict[str, Any]:
    alerts: list[dict[str, Any]] = []
    shares = _component_risk(weights, covariance)
    max_component = float(shares.max()) if len(shares) else 0.0
    max_idx = int(np.argmax(shares)) if len(shares) else 0
    if max_component >= config.component_risk_critical:
        alerts.append({"severity": "critical", "type": "component_risk", "value": max_component,
                       "message": f"{symbols[max_idx]} contributes {max_component:.1%} of modeled portfolio risk."})
    elif max_component >= config.component_risk_warning:
        alerts.append({"severity": "high", "type": "component_risk", "value": max_component,
                       "message": f"{symbols[max_idx]} contributes {max_component:.1%} of modeled portfolio risk."})

    bets = _effective_bets(weights)
    if bets <= config.effective_bets_critical:
        alerts.append({"severity": "critical", "type": "effective_bets", "value": bets,
                       "message": f"Portfolio behaves like only {bets:.2f} equal-weight capital bets."})
    elif bets <= config.effective_bets_warning:
        alerts.append({"severity": "high", "type": "effective_bets", "value": bets,
                       "message": f"Portfolio behaves like only {bets:.2f} equal-weight capital bets."})

    loss_scenarios = [row for row in historical + synthetic if "portfolio_loss" in row]
    if loss_scenarios:
        worst = max(loss_scenarios, key=lambda row: float(row.get("portfolio_loss", 0.0)))
        loss = float(worst.get("portfolio_loss", 0.0))
        if loss >= config.stress_loss_critical:
            alerts.append({"severity": "critical", "type": "stress_loss", "value": loss,
                           "message": f"{worst['id']} implies a {loss:.1%} current-exposure loss."})
        elif loss >= config.stress_loss_warning:
            alerts.append({"severity": "high", "type": "stress_loss", "value": loss,
                           "message": f"{worst['id']} implies a {loss:.1%} current-exposure loss."})

    if liquidity.get("available"):
        stressed_days = float(liquidity["maximum_stressed_exit_days"])
        if stressed_days > config.maximum_exit_days * 2:
            alerts.append({"severity": "critical", "type": "liquidity", "value": stressed_days,
                           "message": f"Largest position needs {stressed_days:.2f} stressed-market exit days."})
        elif stressed_days > config.maximum_exit_days:
            alerts.append({"severity": "high", "type": "liquidity", "value": stressed_days,
                           "message": f"Largest position needs {stressed_days:.2f} stressed-market exit days."})

    if factor.get("available"):
        share = float(factor["systematic_variance_share"])
        if share >= config.systematic_share_critical:
            alerts.append({"severity": "critical", "type": "systematic_factor_risk", "value": share,
                           "message": f"Systematic factors explain {share:.1%} of modeled variance."})
        elif share >= config.systematic_share_warning:
            alerts.append({"severity": "high", "type": "systematic_factor_risk", "value": share,
                           "message": f"Systematic factors explain {share:.1%} of modeled variance."})

    severity_rank = {"critical": 0, "high": 1, "medium": 2, "normal": 3}
    alerts.sort(key=lambda row: (severity_rank.get(str(row["severity"]), 9), str(row["type"])))
    overall = "normal"
    if any(row["severity"] == "critical" for row in alerts):
        overall = "critical"
    elif any(row["severity"] == "high" for row in alerts):
        overall = "high"
    elif alerts:
        overall = "medium"
    return {"status": overall, "alerts": alerts, "alert_count": len(alerts)}


def build_phase3_snapshot(
    weights: np.ndarray,
    returns_history: np.ndarray,
    covariance: np.ndarray,
    *,
    symbols: list[str],
    config: Phase3Config,
    dates: list[str] | None = None,
    benchmark_history: np.ndarray | None = None,
    expected_alpha: np.ndarray | None = None,
    adv_dollars: np.ndarray | None = None,
    capital: float = 1_000_000.0,
    factor_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a completed-close Phase 3 diagnostic snapshot.

    Invariant: returned diagnostics never modify ``weights``.
    """
    x = np.asarray(returns_history, dtype=float)
    if x.ndim != 2 or len(x) < 21 or not np.isfinite(x).all():
        raise ValueError("Phase3 requires at least 21 complete finite return sessions")
    w = _validate_weights(weights, x.shape[1])
    cov = np.asarray(covariance, dtype=float)
    if cov.shape != (x.shape[1], x.shape[1]) or not np.isfinite(cov).all():
        raise ValueError("Phase3 covariance must be finite and align with the asset universe")
    if len(symbols) != len(w):
        raise ValueError("Phase3 symbols must align with weights")
    if not config.enabled:
        return {"enabled": False, "status": "disabled"}
    cov = _nearest_psd(cov)

    historical = historical_stress_scenarios(x, w, symbols, config, dates=dates)
    synthetic = synthetic_stress_scenarios(
        x, w, cov, symbols, config,
        benchmark_history=benchmark_history,
        factor_snapshot=factor_snapshot,
    )
    liquidity = liquidity_and_capacity(w, adv_dollars, capital, config)
    factor = _factor_diagnostics(w, factor_snapshot)
    factor_adjusted_alpha = current_portfolio_multifactor_alpha(w, x, dates, factor_snapshot)
    what_if = what_if_risk_repair(w, cov, symbols, config, expected_alpha=expected_alpha)
    radar = risk_radar(w, cov, historical, synthetic, liquidity, factor, config, symbols)
    shares = _component_risk(w, cov)
    order = np.argsort(shares)[::-1]

    return {
        "enabled": True,
        "status": "research_diagnostics_only",
        "invariant": "Phase 3 observes and stress-tests approved targets; it cannot alter target weights.",
        "gross_exposure": float(w.sum()),
        "annualized_volatility": _annualized_volatility(w, cov),
        "effective_bets": _effective_bets(w),
        "component_risk": [
            {"symbol": str(symbols[i]), "share": float(shares[i])}
            for i in order[: min(10, len(order))]
            if w[i] > 0
        ],
        "historical_stress": historical,
        "synthetic_stress": synthetic,
        "liquidity_capacity": liquidity,
        "factor_risk": factor,
        "current_portfolio_factor_adjusted_alpha": factor_adjusted_alpha,
        "what_if": what_if,
        "risk_radar": radar,
    }


def realized_security_attribution(
    held_weights: pd.DataFrame,
    asset_returns: pd.DataFrame,
    net_returns: pd.Series,
) -> dict[str, Any]:
    """Descriptive realized attribution for a completed evaluation period.

    Security contribution uses prior-close held weights times close-to-close asset
    returns.  The residual includes execution timing, trading costs, cash carry,
    and any effects not represented by the simple close-to-close contribution.
    It is therefore explicitly labeled rather than forced to reconcile as alpha.
    """
    held = held_weights.reindex(index=asset_returns.index, columns=asset_returns.columns).fillna(0.0)
    returns = asset_returns.reindex(index=held.index, columns=held.columns)
    if returns.isna().any().any():
        returns = returns.fillna(0.0)
    contribution = held.shift(1).fillna(0.0) * returns
    by_symbol = contribution.sum(axis=0).sort_values()
    gross_security = float(contribution.sum(axis=1).sum())
    net_total = float(net_returns.reindex(held.index).fillna(0.0).sum())
    return {
        "definition": "Prior-close held weight times close-to-close security return; residual includes execution/cost/cash/timing effects.",
        "security_contribution_sum": gross_security,
        "net_return_sum": net_total,
        "unexplained_execution_cash_cost_residual": float(net_total - gross_security),
        "largest_detractors": [
            {"symbol": str(symbol), "contribution": float(value)}
            for symbol, value in by_symbol.head(min(10, len(by_symbol))).items()
        ],
        "largest_contributors": [
            {"symbol": str(symbol), "contribution": float(value)}
            for symbol, value in by_symbol.tail(min(10, len(by_symbol))).sort_values(ascending=False).items()
        ],
    }


def phase3_contract(config: Phase3Config) -> dict[str, Any]:
    return {
        "id": "universal_phase3_institutional_diagnostics_v1",
        "status": "active_research" if config.enabled else "disabled",
        "configuration": asdict(config),
        "capabilities": [
            "causal trailing historical current-exposure stress scenarios",
            "synthetic market, concentrated-gap, correlation-crisis and volatility shocks",
            "optional dominant-factor stress when Phase 2 factor inputs exist",
            "descriptive current-portfolio multifactor alpha diagnostic when Phase 2 factor history exists",
            "scenario attribution by current security",
            "liquidity haircut, stressed exit horizon and capacity diagnostics",
            "Risk Radar exception hierarchy",
            "non-binding what-if risk repair with alpha-retention diagnostics",
            "realized security contribution attribution in research reports",
        ],
        "timing": "Decision snapshots use only returns, factor state and liquidity known through the completed close.",
        "invariant": "Phase 3 is diagnostic-only and cannot change portfolio targets or execution.",
    }
