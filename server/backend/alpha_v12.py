"""Alpha V1.2: stabilized robust cross-sectional research family.

Two frozen-comparable variants are implemented here:

* ``alpha_v12`` (main): TBA8's causal residual/ridge rank is a fixed anchor,
  blended with a softened robust Alpha-V1 cross-sectional rank.
* ``alpha_v12_alt``: the same V1.2 cross-sectional and calibration machinery,
  but with no TBA/TBA9 forecast input at all.

The design intentionally separates *forecast magnitude* from *forecast
uncertainty*.  HAC evidence changes Alpha sleeve weights only modestly around
predeclared priors; the final expected-return calibration is causal, robust and
smoothed between retrains; disagreement increases uncertainty rather than
silently crushing the expected return.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd

from .alpha_v1 import AlphaV1Config, alpha_v1_scores
from .universal_learning import walk_forward_ridge_scores


ALPHA_V12_ID = "alpha_v12_hybrid"
ALPHA_V12_ALT_ID = "alpha_v12_alt_cross_sectional"
ALPHA_V12_VERSION = "1.2.0-research"
_EPS = 1e-12


@dataclass(frozen=True)
class AlphaV12Config:
    enabled: bool = False
    include_tba_anchor: bool = True
    horizon_sessions: int = 5

    # Predeclared blend priors.  Evidence inside the Alpha sleeve may adjust
    # individual feature weights, but it does not data-mine the top-level blend.
    anchor_prior_weight: float = 0.55

    # Softer V1.1 evidence handling: weak evidence shrinks toward the economic
    # prior rather than switching a sleeve off.
    cross_weight_adaptivity: float = 0.25
    cross_minimum_weight_multiplier: float = 0.70
    cross_maximum_weight_multiplier: float = 1.30
    cross_disagreement_uncertainty_weight: float = 0.20
    cross_ic_hac_lags: int = 4

    # One final calibration for the combined forecast.  The robust candidate
    # slope is smoothed rather than allowed to jump at each retrain.
    calibration_window_sessions: int = 504
    calibration_retrain_sessions: int = 21
    calibration_minimum_days: int = 63
    calibration_ridge: float = 1e-4
    calibration_smoothing: float = 0.80

    # Uncertainty is an output, not a second alpha shrinkage mechanism.
    model_disagreement_uncertainty_weight: float = 0.50
    cross_uncertainty_weight: float = 0.25

    # TBA8 anchor specification.  These mirror the frozen TBA8 alpha engine,
    # not a newly optimized parameter set.
    anchor_training_sessions: int = 756
    anchor_retrain_sessions: int = 21
    anchor_penalty: float = 1.0
    anchor_score_smoothing: float = 0.25

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool) or not isinstance(self.include_tba_anchor, bool):
            raise ValueError("Alpha V1.2 switches must be boolean")
        if not (2 <= self.horizon_sessions <= 21):
            raise ValueError("Alpha V1.2 horizon_sessions must be within [2, 21]")
        if not (0.0 <= self.anchor_prior_weight <= 0.90):
            raise ValueError("Alpha V1.2 anchor_prior_weight must be within [0, 0.90]")
        if not self.include_tba_anchor and self.anchor_prior_weight != 0.0:
            raise ValueError("Alpha V1.2 Alt must use anchor_prior_weight=0")
        if self.include_tba_anchor and self.anchor_prior_weight <= 0.0:
            raise ValueError("Alpha V1.2 Hybrid requires a positive anchor prior")
        if not (0 <= self.cross_weight_adaptivity <= 1.0):
            raise ValueError("Alpha V1.2 cross_weight_adaptivity must be within [0, 1]")
        if not (0 < self.cross_minimum_weight_multiplier <= 1 <= self.cross_maximum_weight_multiplier <= 2):
            raise ValueError("Alpha V1.2 cross weight multiplier bounds are invalid")
        if not (0 <= self.cross_disagreement_uncertainty_weight <= 1):
            raise ValueError("Alpha V1.2 cross disagreement uncertainty weight is invalid")
        if not (0 <= self.cross_ic_hac_lags <= 21):
            raise ValueError("Alpha V1.2 HAC lag count is invalid")
        if not (126 <= self.calibration_window_sessions <= 1260):
            raise ValueError("Alpha V1.2 calibration window is invalid")
        if not (1 <= self.calibration_retrain_sessions <= 63):
            raise ValueError("Alpha V1.2 calibration retrain cadence is invalid")
        if not (20 <= self.calibration_minimum_days <= self.calibration_window_sessions):
            raise ValueError("Alpha V1.2 calibration minimum is invalid")
        if not (0 <= self.calibration_ridge <= 100):
            raise ValueError("Alpha V1.2 calibration ridge is invalid")
        if not (0 <= self.calibration_smoothing < 1):
            raise ValueError("Alpha V1.2 calibration smoothing must be within [0, 1)")
        if not (0 <= self.model_disagreement_uncertainty_weight <= 2):
            raise ValueError("Alpha V1.2 disagreement uncertainty weight is invalid")
        if not (0 <= self.cross_uncertainty_weight <= 2):
            raise ValueError("Alpha V1.2 cross uncertainty weight is invalid")
        if not (252 <= self.anchor_training_sessions <= 2520):
            raise ValueError("Alpha V1.2 anchor training window is invalid")
        if not (1 <= self.anchor_retrain_sessions <= 63):
            raise ValueError("Alpha V1.2 anchor retrain cadence is invalid")
        if not (.001 <= self.anchor_penalty <= 1e5):
            raise ValueError("Alpha V1.2 anchor penalty is invalid")
        if not (0 <= self.anchor_score_smoothing < 1):
            raise ValueError("Alpha V1.2 anchor score smoothing is invalid")

    @property
    def model_id(self) -> str:
        return ALPHA_V12_ID if self.include_tba_anchor else ALPHA_V12_ALT_ID

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps({"id": self.model_id, "version": ALPHA_V12_VERSION, **asdict(self)}, sort_keys=True).encode()
        ).hexdigest()


def _validate_price_panel(panel: pd.DataFrame, name: str, *, allow_zero: bool = False) -> None:
    if not isinstance(panel, pd.DataFrame) or panel.empty:
        raise ValueError(f"Alpha V1.2 requires a non-empty {name} panel")
    if not isinstance(panel.index, pd.DatetimeIndex) or panel.index.has_duplicates or not panel.index.is_monotonic_increasing:
        raise ValueError(f"Alpha V1.2 {name} requires unique increasing dates")
    values = panel.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError(f"Alpha V1.2 {name} must be complete and finite")
    if allow_zero:
        if (values < 0).any():
            raise ValueError(f"Alpha V1.2 {name} cannot be negative")
    elif (values <= 0).any():
        raise ValueError(f"Alpha V1.2 {name} must be positive")


def _rank_panel(panel: pd.DataFrame) -> pd.DataFrame:
    ranked = panel.rank(axis=1, pct=True, method="average")
    return ranked.mul(2.0).sub(1.0).where(panel.notna(), 0.0)


def _cross_core_config(config: AlphaV12Config) -> AlphaV1Config:
    """V1.1 concepts with deliberately softer evidence/configuration."""
    return AlphaV1Config(
        enabled=True,
        horizon_sessions=config.horizon_sessions,
        overlap_robust_ic=True,
        robust_daily_calibration=True,
        exact_label_completion=True,
        weight_adaptivity=config.cross_weight_adaptivity,
        minimum_weight_multiplier=config.cross_minimum_weight_multiplier,
        maximum_weight_multiplier=config.cross_maximum_weight_multiplier,
        disagreement_uncertainty_weight=config.cross_disagreement_uncertainty_weight,
        ic_hac_lags=min(config.cross_ic_hac_lags, max(0, config.horizon_sessions - 1)),
    )


def _candidate_calibration(
    score: pd.DataFrame,
    label: pd.DataFrame,
    *,
    start: int,
    end: int,
    ridge: float,
    minimum_days: int,
) -> tuple[float, float] | None:
    """Median daily cross-sectional slope plus robust residual scale.

    Relative-return labels are demeaned each day, so the final V1.2 calibration
    deliberately has no fitted intercept.  This removes one unstable degree of
    freedom and makes the expected-return output easier to audit.
    """
    slopes: list[float] = []
    residuals: list[np.ndarray] = []
    rows: list[tuple[np.ndarray, np.ndarray]] = []
    for j in range(start, end):
        x = score.iloc[j].to_numpy(dtype=float)
        y = label.iloc[j].to_numpy(dtype=float)
        valid = np.isfinite(x) & np.isfinite(y)
        if valid.sum() < 6:
            continue
        xv = x[valid]
        yv = y[valid]
        xc = xv - float(np.mean(xv))
        yc = yv - float(np.mean(yv))
        denom = float(xc @ xc + ridge)
        if denom <= _EPS:
            continue
        slope = max(0.0, float((xc @ yc) / denom))
        if np.isfinite(slope):
            slopes.append(slope)
            rows.append((xc, yc))
    if len(slopes) < minimum_days:
        return None
    slope = float(np.median(np.asarray(slopes, dtype=float)))
    for xc, yc in rows:
        residuals.append(yc - slope * xc)
    resid = np.concatenate(residuals) if residuals else np.asarray([1.0])
    center = float(np.median(resid))
    sigma = float(1.4826 * np.median(np.abs(resid - center)))
    if not np.isfinite(sigma) or sigma <= 1e-6:
        sigma = float(np.sqrt(np.mean(np.square(resid)))) if len(resid) else 1.0
    return max(slope, 0.0), max(sigma, 1e-6)


def alpha_v12_scores(
    closes: pd.DataFrame,
    opens: pd.DataFrame,
    volumes: pd.DataFrame,
    *,
    benchmark_returns: pd.Series | None = None,
    sector_labels: pd.DataFrame | None = None,
    value_scores: pd.DataFrame | None = None,
    quality_scores: pd.DataFrame | None = None,
    config: AlphaV12Config = AlphaV12Config(enabled=True),
) -> dict[str, Any]:
    """Return V1.2 score, expected relative return and uncertainty.

    The top-level blend weights are fixed priors.  HAC evidence only acts inside
    the cross-sectional sleeve.  The final calibration uses only fully completed
    next-open-to-horizon-close labels and is smoothed between retrains.
    """
    if not config.enabled:
        raise ValueError("Alpha V1.2 config must be enabled")
    _validate_price_panel(closes, "close")
    _validate_price_panel(opens, "open")
    _validate_price_panel(volumes, "volume", allow_zero=True)
    for panel, name in ((opens, "open"), (volumes, "volume")):
        if not panel.index.equals(closes.index) or not panel.columns.equals(closes.columns):
            raise ValueError(f"Alpha V1.2 {name} panel must exactly align with closes")
    if len(closes.columns) < 2:
        raise ValueError("Alpha V1.2 requires a cross-sectional universe")
    if benchmark_returns is None:
        raise ValueError("Alpha V1.2 requires an explicit completed model benchmark")
    market = benchmark_returns.reindex(closes.index).astype(float)
    if not np.isfinite(market.iloc[1:].to_numpy()).all():
        raise ValueError("Alpha V1.2 benchmark must cover every actionable session")

    cross_config = _cross_core_config(config)
    cross = alpha_v1_scores(
        closes,
        benchmark_returns=market,
        sector_labels=sector_labels,
        value_scores=value_scores,
        quality_scores=quality_scores,
        config=cross_config,
    )
    cross_score = cross["score"].astype(float)
    cross_ready = cross["ready"].astype(bool)

    anchor: dict[str, pd.DataFrame] | None = None
    anchor_score = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
    anchor_ready = pd.DataFrame(True, index=closes.index, columns=closes.columns)
    if config.include_tba_anchor:
        anchor = walk_forward_ridge_scores(
            closes,
            opens,
            volumes,
            training_sessions=config.anchor_training_sessions,
            retrain_sessions=config.anchor_retrain_sessions,
            horizon_sessions=config.horizon_sessions,
            penalty=config.anchor_penalty,
            benchmark_returns=market,
            residual_momentum_horizons=(21, 63),
            score_smoothing=config.anchor_score_smoothing,
            return_diagnostics=True,
        )
        anchor_score = anchor["score"].astype(float)
        row_ready = anchor_score.abs().sum(axis=1) > 1e-12
        anchor_ready = pd.DataFrame(
            np.repeat(row_ready.to_numpy()[:, None], len(closes.columns), axis=1),
            index=closes.index,
            columns=closes.columns,
        )

    anchor_weight = config.anchor_prior_weight if config.include_tba_anchor else 0.0
    cross_weight = 1.0 - anchor_weight
    raw = cross_weight * cross_score + anchor_weight * anchor_score
    ready = cross_ready & anchor_ready
    score = _rank_panel(raw).where(ready, 0.0)

    # Final expected-return calibration matches the actual next-open execution
    # boundary.  A row beginning at t is complete at close t+horizon.
    horizon = config.horizon_sessions
    forward = closes.shift(-horizon).div(opens.shift(-1)).sub(1.0)
    relative_label = forward.sub(forward.mean(axis=1), axis=0)

    predicted = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
    error = pd.DataFrame(1.0, index=closes.index, columns=closes.columns)
    calibration_slope = pd.Series(np.nan, index=closes.index, dtype=float)
    calibration_sigma = pd.Series(np.nan, index=closes.index, dtype=float)

    # Conservative warmup covers the anchor if present and the robust Alpha
    # evidence/calibration history otherwise.
    cross_minimum = max(252, cross_config.minimum_history_sessions)
    anchor_minimum = config.anchor_training_sessions + horizon + 1 if config.include_tba_anchor else 0
    first = max(cross_minimum, anchor_minimum, horizon + config.calibration_minimum_days + 1)
    last_slope: float | None = None
    last_sigma: float | None = None

    for i in range(first, len(closes)):
        if i == first or (i - first) % config.calibration_retrain_sessions == 0:
            # Exact completed-label boundary: row i-horizon is known at close i.
            end = i - horizon + 1
            start = max(0, end - config.calibration_window_sessions)
            candidate = _candidate_calibration(
                score,
                relative_label,
                start=start,
                end=end,
                ridge=config.calibration_ridge,
                minimum_days=config.calibration_minimum_days,
            )
            if candidate is not None:
                candidate_slope, candidate_sigma = candidate
                if last_slope is None:
                    last_slope = candidate_slope
                    last_sigma = candidate_sigma
                else:
                    keep = config.calibration_smoothing
                    last_slope = keep * last_slope + (1.0 - keep) * candidate_slope
                    last_sigma = keep * float(last_sigma) + (1.0 - keep) * candidate_sigma
        if last_slope is None or last_sigma is None:
            continue
        calibration_slope.iloc[i] = last_slope
        calibration_sigma.iloc[i] = last_sigma
        row_score = score.iloc[i].to_numpy(dtype=float)
        predicted.iloc[i] = last_slope * row_score

        # Uncertainty does not reduce the expected return a second time.  It is
        # passed downstream for Phase 2 to price explicitly.
        cross_err = cross["prediction_error"].iloc[i].to_numpy(dtype=float)
        finite_cross = cross_err[np.isfinite(cross_err) & (cross_err > 0)]
        cross_med = float(np.median(finite_cross)) if len(finite_cross) else 1.0
        cross_ratio = np.clip(np.nan_to_num(cross_err / max(cross_med, 1e-8), nan=1.0, posinf=2.0, neginf=1.0), 1.0, 2.0)
        uncertainty_multiplier = 1.0 + config.cross_uncertainty_weight * (cross_ratio - 1.0)
        if config.include_tba_anchor:
            disagreement = np.abs(anchor_score.iloc[i].to_numpy(dtype=float) - cross_score.iloc[i].to_numpy(dtype=float)) / 2.0
            uncertainty_multiplier *= 1.0 + config.model_disagreement_uncertainty_weight * np.clip(disagreement, 0.0, 1.0)
        error.iloc[i] = np.maximum(last_sigma * uncertainty_multiplier, 1e-6)

    calibrated_ready = ready & calibration_slope.notna().to_numpy()[:, None]
    return {
        "score": score.where(calibrated_ready, 0.0).clip(-1.0, 1.0),
        "predicted_return": predicted.where(calibrated_ready, 0.0),
        "prediction_error": error.where(calibrated_ready, 1.0),
        "ready": calibrated_ready,
        "model_id": config.model_id,
        "model_version": ALPHA_V12_VERSION,
        "config_fingerprint": config.fingerprint,
        "forecast_horizon_sessions": horizon,
        "target": "next-open-to-horizon-close cross-sectional relative return",
        "anchor_enabled": bool(config.include_tba_anchor),
        "anchor_prior_weight": float(anchor_weight),
        "cross_prior_weight": float(cross_weight),
        "cross_model_id": cross["model_id"],
        "cross_config_fingerprint": cross["config_fingerprint"],
        "cross_feature_names": cross["feature_names"],
        "cross_feature_weights": cross["feature_weights"],
        "anchor_score": anchor_score,
        "cross_score": cross_score,
        "calibration_slope": calibration_slope,
        "calibration_sigma": calibration_sigma,
        "evidence_method": "softened_overlap_hac_inside_cross_sleeve",
        "calibration_method": "smoothed_robust_daily_cross_sectional_slope",
        "blend_method": "fixed_prior_rank_blend" if config.include_tba_anchor else "cross_sectional_only",
    }


def alpha_v12_contract(config: AlphaV12Config) -> dict[str, Any]:
    return {
        "id": config.model_id,
        "version": ALPHA_V12_VERSION,
        "enabled": bool(config.enabled),
        "variant": "hybrid" if config.include_tba_anchor else "alt_no_tba_anchor",
        "config": asdict(config),
        "config_fingerprint": config.fingerprint,
        "target": "next-open-to-horizon-close cross-sectional relative return",
        "causality": "all adaptive evidence/calibration uses only fully completed historical labels",
        "top_level_blend": (
            "fixed TBA8 anchor prior plus stabilized Alpha-family cross-sectional prior"
            if config.include_tba_anchor else
            "stabilized Alpha-family cross-sectional forecast only; no TBA/TBA9 forecast input"
        ),
        "uncertainty": "model disagreement and cross-sleeve uncertainty are exported downstream rather than used as duplicate alpha shrinkage",
        "external_data": "sector/value/quality sleeves activate only from supplied availability-dated panels",
        "status": "research_candidate_not_release_approved",
    }
