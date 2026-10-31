from __future__ import annotations

"""Walk-forward-only calibration helpers for QOptima resilience weights."""

from typing import Any
import numpy as np
import pandas as pd

from .resilience import (
    correlation_collapse_covariance,
    downside_covariance,
    regime_stress_covariance,
    positive_increment,
    nearest_psd,
)


def _upper(a: np.ndarray) -> np.ndarray:
    idx = np.triu_indices_from(a)
    return np.asarray(a, dtype=float)[idx]


def calibrate_resilience_weights(
    returns: pd.DataFrame,
    base_covariance: np.ndarray,
    *,
    correlation_blend: float = 0.65,
    min_training_sessions: int = 84,
    max_weight: float = 2.0,
    covariance_method: str = "sample",
    covariance_parameters: dict[str, Any] | None = None,
    forecast_error_covariance: np.ndarray | None = None,
) -> dict[str, Any]:
    """Fit non-negative resilience coefficients *inside* a training window.

    The lookback is split chronologically: the first ~70% constructs stress
    components and the last ~30% supplies a realized covariance target.  NNLS
    fits the residual covariance using the component increments.  This is a
    calibration experiment, not evidence that the terms are predictive out of
    sample; the walk-forward backtest must judge that separately.

    Forecast disagreement is intentionally absent here because it requires
    point-in-time realized forecast errors.  Its coefficient therefore remains
    zero unless a validated error panel is supplied elsewhere.
    """
    clean = returns.replace([np.inf, -np.inf], np.nan).dropna(how="any")
    if len(clean) < int(min_training_sessions):
        return {
            "weights": {"downside": 0.0, "correlation_collapse": 0.0, "forecast_disagreement": 0.0, "regime": 0.0},
            "status": "INSUFFICIENT_TRAINING_DATA",
            "training_sessions": int(len(clean)),
            "validated_components": [],
            "warnings": ["Resilience coefficients left at zero because the training window is too short."],
        }
    split = max(42, min(len(clean) - 21, int(round(len(clean) * 0.70))))
    train = clean.iloc[:split]
    validate = clean.iloc[split:]
    if len(validate) < 20:
        return {
            "weights": {"downside": 0.0, "correlation_collapse": 0.0, "forecast_disagreement": 0.0, "regime": 0.0},
            "status": "INSUFFICIENT_CALIBRATION_HOLDOUT",
            "training_sessions": int(len(clean)),
            "validated_components": [],
            "warnings": ["Resilience coefficients left at zero because the in-window calibration holdout is too short."],
        }

    # Calibrate relative to the *same* base estimator selected for the research
    # arm.  Using sample covariance here while evaluating shrinkage/RMT later
    # would quietly move estimator error into the resilience coefficients.
    try:
        from .covariance import estimate_covariance
        base_train = np.asarray(estimate_covariance(train, covariance_method, parameters=covariance_parameters).covariance, dtype=float)
        realized = np.asarray(estimate_covariance(validate, covariance_method, parameters=covariance_parameters).covariance, dtype=float)
    except Exception:
        base_train = nearest_psd(train.cov().to_numpy(dtype=float) * 252.0, floor=1e-12)
        realized = nearest_psd(validate.cov().to_numpy(dtype=float) * 252.0, floor=1e-12)
    comps = {
        "downside": positive_increment(downside_covariance(train, base_train), base_train),
        "correlation_collapse": positive_increment(
            correlation_collapse_covariance(base_train, blend=correlation_blend), base_train
        ),
        "regime": positive_increment(regime_stress_covariance(train, base_train), base_train),
    }
    if forecast_error_covariance is not None:
        f = np.asarray(forecast_error_covariance, dtype=float)
        if f.shape == base_train.shape and np.isfinite(f).all():
            comps["forecast_disagreement"] = nearest_psd(f, floor=0.0)
    names = tuple(comps.keys())
    target = _upper(realized - base_train)
    X = np.column_stack([_upper(comps[name]) for name in names])
    # Scale columns to keep NNLS numerically stable, then unscale coefficients.
    norms = np.linalg.norm(X, axis=0)
    active = norms > 1e-12
    coeff = np.zeros(len(names), dtype=float)
    warnings: list[str] = []
    if np.any(active):
        try:
            from scipy.optimize import nnls
            scaled = X[:, active] / norms[active]
            fitted, residual = nnls(scaled, target)
            coeff[active] = fitted / norms[active]
        except Exception as exc:
            warnings.append(f"NNLS calibration unavailable ({type(exc).__name__}); resilience coefficients left at zero.")
            residual = float(np.linalg.norm(target))
    else:
        residual = float(np.linalg.norm(target))
        warnings.append("Stress components were numerically empty in this training window.")
    coeff = np.clip(coeff, 0.0, float(max_weight))

    # Avoid double counting: highly collinear components are shrunk as a group.
    if X.shape[1] > 1:
        corr = np.corrcoef(X.T)
        corr = np.nan_to_num(corr, nan=0.0)
        for i in range(len(names)):
            overlap = sum(max(0.0, abs(float(corr[i, j])) - 0.80) for j in range(len(names)) if j != i)
            coeff[i] /= 1.0 + overlap
    weights = {name: float(coeff[i]) for i, name in enumerate(names)}
    weights.setdefault("forecast_disagreement", 0.0)
    predicted = base_train.copy()
    for name in names:
        predicted += weights[name] * comps[name]
    predicted = nearest_psd(predicted, floor=1e-12)
    baseline_err = float(np.linalg.norm(realized - base_train, ord="fro"))
    fitted_err = float(np.linalg.norm(realized - predicted, ord="fro"))
    return {
        "weights": weights,
        "status": "CALIBRATED_IN_TRAINING_WINDOW",
        "training_sessions": int(len(train)),
        "calibration_sessions": int(len(validate)),
        "validated_components": [name for name in names if weights[name] > 0],
        "forecast_disagreement_status": ("VALIDATED_FROM_PIT_REALIZED_ERRORS" if "forecast_disagreement" in comps else "UNVALIDATED_ZERO"),
        "baseline_frobenius_error": baseline_err,
        "fitted_frobenius_error": fitted_err,
        "relative_error_improvement": (baseline_err - fitted_err) / baseline_err if baseline_err > 1e-12 else 0.0,
        "correlation_blend": float(correlation_blend),
        "covariance_method": str(covariance_method),
        "covariance_parameters": dict(covariance_parameters or {}),
        "warnings": warnings,
    }


def calibrate_fragility_bands(
    returns: pd.DataFrame,
    *,
    covariance_method: str,
    covariance_parameters: dict[str, Any] | None,
    resilience_weights: dict[str, float],
    correlation_blend: float = 0.65,
    min_window: int = 63,
    step: int = 21,
) -> dict[str, Any]:
    """Calibrate descriptive Fragility Premium bands using training data only.

    These are distributional thresholds, not claims that a percentile predicts a
    future loss.  Equal-weight portfolios are used only to create a stable scale
    reference within the training window; out-of-sample backtests must determine
    economic usefulness.
    """
    clean = returns.replace([np.inf, -np.inf], np.nan).dropna(how="any")
    if len(clean) < max(84, min_window + step):
        return {"status": "UNVALIDATED_INSUFFICIENT_HISTORY", "thresholds": None, "observations": 0}
    from .covariance import estimate_covariance
    from .resilience import build_resilience_model
    fps: list[float] = []
    window = min(max(min_window, 63), min(126, len(clean)))
    for end in range(window, len(clean) + 1, max(5, int(step))):
        block = clean.iloc[end-window:end]
        try:
            est = estimate_covariance(block, covariance_method, parameters=covariance_parameters)
            resilience = build_resilience_model(
                block, est.covariance, tuple(block.columns),
                downside_weight=float(resilience_weights.get("downside", 0.0)),
                correlation_weight=float(resilience_weights.get("correlation_collapse", 0.0)),
                forecast_weight=0.0,
                regime_weight=float(resilience_weights.get("regime", 0.0)),
                correlation_blend=correlation_blend,
                alpha=1.0,
            )
            w = np.full(block.shape[1], 1.0 / block.shape[1], dtype=float)
            bvar = max(0.0, float(w @ np.asarray(est.covariance) @ w))
            evar = max(0.0, float(w @ np.asarray(resilience["effective_covariance"]) @ w))
            if bvar > 1e-16:
                fps.append(float(np.sqrt(evar / bvar) - 1.0))
        except Exception:
            continue
    if len(fps) < 4:
        return {"status": "UNVALIDATED_INSUFFICIENT_SAMPLES", "thresholds": None, "observations": len(fps)}
    arr = np.asarray(fps, dtype=float)
    q50, q75, q90 = np.quantile(arr, [0.50, 0.75, 0.90])
    return {
        "status": "TRAINING_DISTRIBUTION_CALIBRATED",
        "observations": int(len(arr)),
        "thresholds": {"elevated": float(q50), "high": float(q75), "extreme": float(q90)},
        "sample_min": float(arr.min()), "sample_max": float(arr.max()),
        "meaning": "training-window distributional Fragility Premium bands; not validated predictive loss thresholds",
    }
