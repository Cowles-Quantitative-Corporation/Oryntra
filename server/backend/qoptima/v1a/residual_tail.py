from __future__ import annotations

from math import comb
from functools import lru_cache
import pandas as pd
from statistics import NormalDist
from typing import Any
import numpy as np
from ..scoring_memo import prepared_error_covariances, conservative_order_rank


def _sym(a: np.ndarray) -> np.ndarray:
    return (np.asarray(a, dtype=float) + np.asarray(a, dtype=float).T) / 2.0


@lru_cache(maxsize=256)
def _parse_error_timing(information_cutoffs: tuple, realized_at: tuple) -> tuple[tuple[int, ...], tuple[int, ...]]:
    def parse(value):
        if value is None or isinstance(value, (int, float, bool, np.number)):
            raise ValueError("forecast-error timing requires explicit timestamps")
        try:
            stamp = pd.Timestamp(value)
            if pd.isna(stamp):
                raise ValueError("NaT")
            stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
            return stamp.value
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError("invalid forecast-error timestamp") from exc
    cutoffs = tuple(parse(v) for v in information_cutoffs)
    maturities = tuple(parse(v) for v in realized_at)
    if any(maturity <= cutoff for cutoff, maturity in zip(cutoffs, maturities)):
        raise ValueError("forecast-error outcomes must mature after their forecast information cutoffs")
    return cutoffs, maturities


def _validated_error_timing(n: int, information_cutoffs, realized_at):
    if information_cutoffs is None and realized_at is None:
        return None, None
    if information_cutoffs is None or realized_at is None:
        raise ValueError("forecast-error information cutoffs and realization times must be supplied together")
    if len(information_cutoffs) != n or len(realized_at) != n:
        raise ValueError("forecast-error timing must align with every sample row")
    try:
        return _parse_error_timing(tuple(information_cutoffs), tuple(realized_at))
    except TypeError as exc:
        raise ValueError("invalid forecast-error timestamp values") from exc


def _pit_covariances(samples: np.ndarray, *, window: int, min_history: int, floor: float,
                     information_cutoffs=None, realized_at=None) -> tuple[list[np.ndarray | None], np.ndarray | None]:
    x = np.asarray(samples, dtype=float)
    n, p = x.shape
    out: list[np.ndarray | None] = []
    for i in range(n):
        start = max(0, i - int(window))
        if information_cutoffs is None:
            prior = x[start:i]
        else:
            # These are validated UTC nanosecond timestamps. Retrospective
            # scale for row i may use only outcomes known at that forecast's
            # information cutoff. Select the last window *admitted* errors.
            admitted = [j for j in range(i) if realized_at[j] <= information_cutoffs[i]]
            prior = x[admitted[-int(window):]]
        if len(prior) < int(min_history):
            out.append(None)
            continue
        cov = np.atleast_2d(np.cov(prior, rowvar=False, ddof=1))
        cov = _sym(cov) + float(floor) * np.eye(p)
        out.append(cov)
    prior = x[max(0, n - int(window)) :]
    current = None
    if len(prior) >= max(2, int(min_history)):
        current = _sym(np.atleast_2d(np.cov(prior, rowvar=False, ddof=1))) + float(floor) * np.eye(p)
    return out, current


def _binomial_survival(n: int, p: float, k: int) -> float:
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return float(sum(comb(n, j) * (p ** j) * ((1.0 - p) ** (n - j)) for j in range(k, n + 1)))


def _conservative_order_stat(values: np.ndarray, beta: float, confidence: float, stride: int) -> tuple[float | None, dict[str, Any]]:
    vals = np.asarray(values, dtype=float)
    vals = vals[np.isfinite(vals)]
    if len(vals) < 3:
        return None, {"available": False, "reason": "insufficient_standardized_errors"}
    b = float(np.clip(beta, 1e-4, 0.4999))
    conf = float(np.clip(confidence, 0.5, 0.999))
    step = max(1, int(stride))
    candidates: list[float] = []
    details = []
    for offset in range(step):
        seq = np.sort(vals[offset::step])
        n = len(seq)
        if n < 3:
            continue
        k_star = conservative_order_rank(n, b, conf, _binomial_survival)
        if k_star is None:
            continue
        q = float(seq[k_star - 1])
        candidates.append(q)
        details.append({"offset": offset, "n": n, "k": k_star, "value": q})
    if not candidates:
        return None, {"available": False, "reason": "order_statistic_confidence_bound_unavailable"}
    return float(min(candidates)), {
        "available": True,
        "policy": "deterministic_blocked_binomial_order_statistic_lower_bound_v1",
        "beta": b,
        "confidence": conf,
        "stride": step,
        "offset_bounds": details,
        "dependence_note": "blocking mitigates overlap; binomial coverage is exact only under independent observations",
    }


def _unavailable_tail(reason: str, **details: Any) -> dict[str, Any]:
    """Report missing final support separately from any sub-estimator status."""
    return {
        "tail_value_available": False,
        "order_statistic_bound_available": False,
        "fallback": None,
        "fallback_used": False,
        "tail_method": None,
        **details,
        "available": False,
        "value": 0.0,
        "reason": reason,
        "v1a_refinement_supported": False,
    }


def standardized_conservative_portfolio_tail(
    error_samples: np.ndarray | None,
    weights: np.ndarray,
    *,
    beta: float,
    confidence: float,
    covariance_window: int,
    min_history: int,
    covariance_floor: float,
    block_stride: int = 1,
    regime_labels: list[str | None] | tuple[str | None, ...] | None = None,
    current_regime_label: str | None = None,
    regime_min_samples: int = 6,
    information_cutoffs=None,
    realized_at=None,
) -> dict[str, Any]:
    if error_samples is None:
        if information_cutoffs is not None or realized_at is not None:
            raise ValueError("forecast-error timing requires aligned samples")
        return _unavailable_tail("no_point_in_time_forecast_errors")
    x = np.asarray(error_samples, dtype=float)
    w = np.asarray(weights, dtype=float).reshape(-1)
    if x.ndim != 2 or x.shape[1] != len(w) or len(x) < 3 or not np.isfinite(x).all():
        return _unavailable_tail("invalid_or_misaligned_forecast_errors")
    cutoffs, maturities = _validated_error_timing(len(x), information_cutoffs, realized_at)
    covs, current_cov = prepared_error_covariances(
        x, window=int(covariance_window), min_history=int(min_history), floor=float(covariance_floor),
        builder=_pit_covariances, information_cutoffs=cutoffs, realized_at=maturities,
    )
    z = []
    z_labels: list[str | None] = []
    labels = list(regime_labels) if regime_labels is not None else [None] * len(x)
    if len(labels) != len(x):
        return _unavailable_tail("misaligned_regime_labels")
    for err, cov, label in zip(x, covs, labels):
        if cov is None:
            continue
        scale = float(np.sqrt(max(float(w @ cov @ w), 0.0)))
        if scale <= 0:
            continue
        z.append(float(w @ err) / scale)
        z_labels.append(label)
    zarr = np.asarray(z, dtype=float)
    q_global, meta = _conservative_order_stat(zarr, beta, confidence, block_stride)
    q = q_global
    q_regime = None
    regime_meta: dict[str, Any] = {"available": False, "reason": "no_current_regime_label"}
    if current_regime_label is not None and len(zarr):
        mask = np.asarray([label == current_regime_label for label in z_labels], dtype=bool)
        regime_values = zarr[mask]
        if len(regime_values) >= int(regime_min_samples):
            q_regime, regime_meta = _conservative_order_stat(regime_values, beta, confidence, block_stride)
            if q_regime is not None:
                q = q_regime if q is None else min(float(q), float(q_regime))
                regime_meta = {**regime_meta, "regime_label": current_regime_label, "samples": int(len(regime_values))}
        else:
            regime_meta = {"available": False, "reason": "insufficient_current_regime_support", "regime_label": current_regime_label, "samples": int(len(regime_values))}
    fallback = None
    if q is None and len(zarr) >= 4:
        # Conservative deterministic heavy-tail fallback.  It is deliberately
        # more negative than the Gaussian beta quantile when empirical support
        # is inadequate; less information cannot weaken the haircut.
        nd = NormalDist()
        gaussian = float(nd.inv_cdf(float(beta)))
        q = float(min(np.quantile(zarr, min(float(beta), 0.10)), 1.25 * gaussian))
        fallback = "heavy_tail_parametric_or_empirical_min_fallback_v1"
    if q is None or current_cov is None:
        return _unavailable_tail(
            "insufficient_PIT_tail_support",
            standardized_samples=int(len(zarr)),
            order_statistic_bound_available=q_global is not None or q_regime is not None,
            global_tail=dict(meta),
            regime_tail=regime_meta,
        )
    current_scale = float(np.sqrt(max(float(w @ current_cov @ w), 0.0)))
    value = float(current_scale * q)
    if fallback is not None:
        tail_method = fallback
    elif q_global is not None and q_regime is not None:
        tail_method = "conservative_min_global_and_current_regime_order_statistics"
    elif q_regime is not None:
        tail_method = "current_regime_order_statistic_lower_bound"
    else:
        tail_method = "global_order_statistic_lower_bound"
    return {
        # A failed global bound must not overwrite a successful fallback or
        # regime value. Keep its status/reason under global_tail instead.
        **{key: val for key, val in meta.items() if key not in {"available", "reason"}},
        **({"historical_scale_clock": "forecast_information_cutoff_matured_prior_errors_v1"} if cutoffs is not None else {}),
        "available": True,
        "tail_value_available": True,
        "order_statistic_bound_available": q_global is not None or q_regime is not None,
        "tail_method": tail_method,
        "value": value,
        "standardized_lower_quantile": float(q),
        "current_error_scale": current_scale,
        "standardized_samples": int(len(zarr)),
        "fallback": fallback,
        "fallback_used": fallback is not None,
        "v1a_refinement_supported": True,
        "global_standardized_lower_quantile": None if q_global is None else float(q_global),
        "regime_standardized_lower_quantile": None if q_regime is None else float(q_regime),
        "regime_tail": regime_meta,
        "global_tail": dict(meta),
        "regime_policy": "REGIME_TAIL_V1_conservative_min_global_and_current_regime_when_PIT_labels_available",
    }
