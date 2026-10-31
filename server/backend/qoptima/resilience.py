from __future__ import annotations

"""QOptima resilience / fragility research utilities.

The construction is deliberately explicit:

    Sigma_eff_raw = Sigma_base
                  + a * Delta_downside
                  + b * Delta_correlation
                  + c * Omega_forecast_error
                  + d * Delta_regime
    Sigma_eff = PSD(Sigma_eff_raw)
    Sigma_alpha = (1-alpha) * Sigma_base + alpha * Sigma_eff

Forecast disagreement is not treated as forecast-error covariance unless a
point-in-time contract provides subsequent realized errors.  Unvalidated raw
model disagreement can be inspected as an experimental diagnostic but is given
zero predictive weight by default.
"""

from typing import Any
import numpy as np
import pandas as pd


def _sym(matrix: np.ndarray) -> np.ndarray:
    a = np.asarray(matrix, dtype=float)
    return (a + a.T) / 2.0


def nearest_psd(matrix: np.ndarray, *, floor: float = 0.0) -> np.ndarray:
    a = _sym(matrix)
    vals, vecs = np.linalg.eigh(a)
    vals = np.maximum(vals, float(floor))
    return _sym((vecs * vals) @ vecs.T)


def psd_projection_with_diagnostics(matrix: np.ndarray, *, floor: float = 0.0) -> tuple[np.ndarray, dict[str, float]]:
    a = _sym(matrix)
    vals, vecs = np.linalg.eigh(a)
    clipped = np.maximum(vals, float(floor))
    out = _sym((vecs * clipped) @ vecs.T)
    delta = float(np.linalg.norm(out - a, ord="fro"))
    denom = float(np.linalg.norm(a, ord="fro"))
    return out, {
        "min_eigenvalue_before": float(vals.min()) if vals.size else 0.0,
        "min_eigenvalue_after": float(clipped.min()) if clipped.size else 0.0,
        "frobenius_change": delta,
        "relative_frobenius_change": delta / denom if denom > 0 else 0.0,
    }



def alpha_covariance(
    base_covariance: np.ndarray,
    effective_covariance: np.ndarray,
    alpha: float,
    *,
    floor: float = 1e-12,
) -> np.ndarray:
    """Return Sigma_alpha = (1-alpha) Sigma_base + alpha Sigma_eff, PSD projected.

    The helper is intentionally public because local optimization, hardware
    planning, backtesting and result reporting must use the identical matrix.
    """
    a = float(np.clip(alpha, 0.0, 1.0))
    base = _sym(np.asarray(base_covariance, dtype=float))
    effective = _sym(np.asarray(effective_covariance, dtype=float))
    if base.shape != effective.shape:
        raise ValueError("base/effective covariance shape mismatch")
    return nearest_psd((1.0 - a) * base + a * effective, floor=floor)

def positive_increment(stress_cov: np.ndarray, base_cov: np.ndarray) -> np.ndarray:
    return nearest_psd(_sym(stress_cov) - _sym(base_cov), floor=0.0)


def correlation_collapse_covariance(base_cov: np.ndarray, *, blend: float = 0.65) -> np.ndarray:
    base = _sym(base_cov)
    vol = np.sqrt(np.maximum(np.diag(base), 1e-12))
    corr = base / np.maximum(np.outer(vol, vol), 1e-12)
    stressed = corr.copy()
    b = float(np.clip(blend, 0.0, 1.0))
    for i in range(len(vol)):
        stressed[i, i] = 1.0
        for j in range(i + 1, len(vol)):
            value = corr[i, j] + b * (1.0 - corr[i, j])
            stressed[i, j] = stressed[j, i] = float(np.clip(value, -1.0, 1.0))
    return nearest_psd(stressed * np.outer(vol, vol), floor=1e-12)


def downside_covariance(returns: pd.DataFrame, base_cov: np.ndarray) -> np.ndarray:
    clean = returns.replace([np.inf, -np.inf], np.nan).dropna(how="any")
    if len(clean) < 20:
        return np.asarray(base_cov, dtype=float)
    market = clean.mean(axis=1)
    subset = clean.loc[market < 0]
    if len(subset) < max(12, int(0.20 * len(clean))):
        cutoff = float(market.quantile(0.35))
        subset = clean.loc[market <= cutoff]
    if len(subset) < 8:
        return np.asarray(base_cov, dtype=float)
    cov = subset.cov().to_numpy(dtype=float) * 252.0
    return nearest_psd(cov, floor=1e-12)


def regime_stress_covariance(returns: pd.DataFrame, base_cov: np.ndarray) -> np.ndarray:
    clean = returns.replace([np.inf, -np.inf], np.nan).dropna(how="any")
    if len(clean) < 30:
        return np.asarray(base_cov, dtype=float)
    market = clean.mean(axis=1)
    rolling = market.rolling(20, min_periods=10).std()
    threshold = float(rolling.dropna().quantile(0.75)) if rolling.notna().any() else float("nan")
    if not np.isfinite(threshold):
        return np.asarray(base_cov, dtype=float)
    subset = clean.loc[rolling >= threshold]
    if len(subset) < 10:
        return np.asarray(base_cov, dtype=float)
    cov = subset.cov().to_numpy(dtype=float) * 252.0
    return nearest_psd(cov, floor=1e-12)


def forecast_disagreement_covariance(
    symbols: tuple[str, ...] | list[str],
    model_forecasts: dict[str, dict[str, float]] | None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Raw cross-model forecast dispersion, diagnostic only unless validated."""
    n = len(symbols)
    zero = np.zeros((n, n), dtype=float)
    if not model_forecasts or len(model_forecasts) < 2:
        return zero, {"available": False, "models": 0, "reason": "need_at_least_two_model_forecasts", "validated": False}
    rows = []
    used = []
    for model, mapping in model_forecasts.items():
        if not isinstance(mapping, dict):
            continue
        try:
            vector = [float(mapping[str(symbol)]) for symbol in symbols]
        except (KeyError, TypeError, ValueError):
            continue
        if not np.isfinite(vector).all():
            continue
        rows.append(vector)
        used.append(str(model))
    if len(rows) < 2:
        return zero, {"available": False, "models": len(rows), "reason": "insufficient_aligned_model_forecasts", "validated": False}
    cov = np.atleast_2d(np.cov(np.asarray(rows, dtype=float), rowvar=False, ddof=1))
    if cov.shape != (n, n):
        return zero, {"available": False, "models": len(rows), "reason": "forecast_covariance_shape_mismatch", "validated": False}
    return nearest_psd(cov, floor=0.0), {
        "available": True,
        "models": len(rows),
        "model_ids": used,
        "validated": False,
        "warning": "Raw model disagreement is not forecast-error covariance and receives zero predictive weight unless explicitly experimental.",
    }


def build_resilience_model(
    returns: pd.DataFrame,
    base_covariance: np.ndarray,
    symbols: tuple[str, ...] | list[str],
    *,
    downside_weight: float = 0.0,
    correlation_weight: float = 0.0,
    forecast_weight: float = 0.0,
    regime_weight: float = 0.0,
    correlation_blend: float = 0.65,
    model_forecasts: dict[str, dict[str, float]] | None = None,
    forecast_error_covariance: np.ndarray | None = None,
    forecast_error_metadata: dict[str, Any] | None = None,
    allow_experimental_forecast_disagreement: bool = False,
    alpha: float = 1.0,
) -> dict[str, Any]:
    base = nearest_psd(base_covariance, floor=1e-12)
    down_stress = downside_covariance(returns, base)
    corr_stress = correlation_collapse_covariance(base, blend=correlation_blend)
    regime_stress = regime_stress_covariance(returns, base)

    raw_disagreement, raw_disagreement_meta = forecast_disagreement_covariance(symbols, model_forecasts)
    if forecast_error_covariance is not None:
        forecast_component = nearest_psd(np.asarray(forecast_error_covariance, dtype=float), floor=0.0)
        forecast_meta = {
            "available": True,
            "validated": True,
            "source": "point_in_time_realized_forecast_errors",
            **dict(forecast_error_metadata or {}),
        }
        effective_forecast_weight = max(0.0, float(forecast_weight))
    elif allow_experimental_forecast_disagreement and raw_disagreement_meta.get("available"):
        forecast_component = raw_disagreement
        forecast_meta = {**raw_disagreement_meta, "source": "raw_cross_model_disagreement_experimental"}
        effective_forecast_weight = max(0.0, float(forecast_weight))
    else:
        forecast_component = raw_disagreement
        forecast_meta = {**raw_disagreement_meta, "source": "raw_cross_model_disagreement_diagnostic_only"}
        effective_forecast_weight = 0.0

    components = {
        "downside": positive_increment(down_stress, base),
        "correlation_collapse": positive_increment(corr_stress, base),
        "forecast_disagreement": nearest_psd(forecast_component, floor=0.0),
        "regime": positive_increment(regime_stress, base),
    }
    weights = {
        "downside": max(0.0, float(downside_weight)),
        "correlation_collapse": max(0.0, float(correlation_weight)),
        "forecast_disagreement": effective_forecast_weight,
        "regime": max(0.0, float(regime_weight)),
    }
    raw_effective = base.copy()
    for name, matrix in components.items():
        raw_effective += weights[name] * matrix
    effective, projection = psd_projection_with_diagnostics(raw_effective, floor=1e-12)
    a = float(np.clip(alpha, 0.0, 1.0))
    blended_raw = (1.0 - a) * base + a * effective
    blended = alpha_covariance(base, effective, a, floor=1e-12)
    _, blend_projection = psd_projection_with_diagnostics(blended_raw, floor=1e-12)
    return {
        "base_covariance": base,
        "effective_covariance": effective,
        "alpha_covariance": blended,
        "alpha": a,
        "components": components,
        "weights": weights,
        "metadata": {
            "correlation_blend": float(np.clip(correlation_blend, 0.0, 1.0)),
            "forecast_disagreement": forecast_meta,
            "construction": "PSD(base + a*Delta_downside + b*Delta_correlation + c*Omega_validated_forecast_error + d*Delta_regime)",
            "psd_projection": projection,
            "alpha_psd_projection": blend_projection,
            "weights_status": "caller_or_calibration_supplied; zero is preferred for unvalidated components",
        },
    }


def fragility_metrics(
    weights: np.ndarray,
    expected_returns: np.ndarray,
    risk_free_rate: float,
    base_covariance: np.ndarray,
    effective_covariance: np.ndarray,
    components: dict[str, np.ndarray] | None = None,
    component_weights: dict[str, float] | None = None,
    *,
    alpha_covariance: np.ndarray | None = None,
    alpha: float = 1.0,
    current_weights: np.ndarray | None = None,
    transaction_cost_bps: float = 0.0,
    calibrated_band: str | None = None,
    historical_percentile: float | None = None,
) -> dict[str, Any]:
    w = np.asarray(weights, dtype=float)
    mu = np.asarray(expected_returns, dtype=float)
    base_cov = np.asarray(base_covariance, dtype=float)
    eff_cov = np.asarray(effective_covariance, dtype=float)
    blend_cov = np.asarray(alpha_covariance if alpha_covariance is not None else eff_cov, dtype=float)
    normal_var = max(0.0, float(w @ base_cov @ w))
    effective_var = max(0.0, float(w @ eff_cov @ w))
    alpha_var = max(0.0, float(w @ blend_cov @ w))
    normal_vol = float(np.sqrt(normal_var))
    effective_vol = float(np.sqrt(effective_var))
    alpha_vol = float(np.sqrt(alpha_var))
    fp = (effective_vol / normal_vol - 1.0) if normal_vol > 1e-12 else 0.0

    gross = float(w.sum())
    cash_weight = 1.0 - gross
    gross_expected_return = float(mu @ w + cash_weight * float(risk_free_rate))
    gross_excess_return = gross_expected_return - float(risk_free_rate)
    qrs_gross = gross_excess_return / alpha_vol if alpha_vol > 1e-12 else None
    turnover_l1 = float(np.abs(w - np.asarray(current_weights, dtype=float)).sum()) if current_weights is not None else 0.0
    transaction_cost = turnover_l1 * float(transaction_cost_bps) / 10000.0
    cost_adjusted_return = gross_expected_return - transaction_cost
    cost_adjusted_excess = cost_adjusted_return - float(risk_free_rate)
    qrs_cost_adjusted = cost_adjusted_excess / alpha_vol if alpha_vol > 1e-12 else None

    breakdown: dict[str, Any] = {}
    for name, matrix in (components or {}).items():
        weight = float((component_weights or {}).get(name, 0.0))
        increment = weight * np.asarray(matrix, dtype=float)
        increment_var = float(w @ increment @ w)
        component_cov = base_cov + increment
        component_vol = float(np.sqrt(max(0.0, float(w @ component_cov @ w))))
        component_premium = (component_vol / normal_vol - 1.0) if normal_vol > 1e-12 else 0.0
        breakdown[name] = {
            "weight": weight,
            "volatility_with_component": component_vol,
            "fragility_premium": component_premium,
            "incremental_variance": increment_var,
            "variance_share_of_effective_increment": (
                increment_var / max(effective_var - normal_var, 1e-18) if effective_var > normal_var + 1e-18 else 0.0
            ),
        }

    return {
        "normal_volatility": normal_vol,
        "effective_volatility": effective_vol,
        "alpha_volatility": alpha_vol,
        "alpha": float(np.clip(alpha, 0.0, 1.0)),
        "fragility_premium": float(fp),
        "qrs": qrs_gross,
        "gross_qrs": qrs_gross,
        "cost_adjusted_qrs": qrs_cost_adjusted,
        "expected_return": gross_expected_return,
        "expected_return_including_cash": gross_expected_return,
        "cash_weight": cash_weight,
        "turnover_l1": turnover_l1,
        "transaction_cost_return": transaction_cost,
        "cost_adjusted_expected_return": cost_adjusted_return,
        "component_breakdown": breakdown,
        "band": calibrated_band,
        "band_source": "walk_forward_calibrated" if calibrated_band else "UNVALIDATED_NO_BAND",
        "historical_percentile": historical_percentile,
    }
