"""Alpha V1: causal cross-sectional relative-return research model.

Alpha V1 is intentionally a separate model family from the TBA/Minerva
walk-forward ridge path.  It combines economically named cross-sectional
signals, adapts only their *strength* from completed historical rank-IC
observations, and calibrates the composite into a forward relative-return
forecast with an explicit prediction-error estimate.

No future label is usable until the full forecast horizon has completed.
Optional sector/value/quality inputs must be availability-dated point-in-time
panels supplied by the caller; current classifications are never inferred.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd


ALPHA_V1_ID = "alpha_v1_cross_sectional"
ALPHA_V1_VERSION = "1.0.0-research"
ALPHA_V11_ID = "alpha_v11_robust_cross_sectional"
ALPHA_V11_VERSION = "1.1.0-research"
_EPS = 1e-12


@dataclass(frozen=True)
class AlphaV1Config:
    enabled: bool = False
    horizon_sessions: int = 5
    minimum_history_sessions: int = 252
    ic_lookback_sessions: int = 252
    minimum_ic_observations: int = 42
    calibration_window_sessions: int = 504
    retrain_sessions: int = 21
    beta_window_sessions: int = 126
    volatility_window_sessions: int = 21
    weight_adaptivity: float = 0.65
    minimum_weight_multiplier: float = 0.25
    maximum_weight_multiplier: float = 1.75
    calibration_ridge: float = 1e-4
    disagreement_uncertainty_weight: float = 0.35
    # Alpha V1 defaults preserve the original research candidate.  Alpha V1.1
    # explicitly enables the overlap-robust evidence/calibration switches below.
    overlap_robust_ic: bool = False
    robust_daily_calibration: bool = False
    exact_label_completion: bool = False
    ic_hac_lags: int = 4
    robust_calibration_min_days: int = 42
    include_momentum_21: bool = True
    include_momentum_63: bool = True
    include_momentum_126: bool = True
    include_residual_momentum_21: bool = True
    include_residual_momentum_63: bool = True
    include_short_reversion: bool = True
    include_low_volatility: bool = True
    include_sector_relative: bool = True
    include_value: bool = True
    include_quality: bool = True

    def __post_init__(self) -> None:
        switches = [value for key, value in asdict(self).items() if key.startswith("include_")] + [
            self.enabled, self.overlap_robust_ic, self.robust_daily_calibration, self.exact_label_completion,
        ]
        if not all(isinstance(value, bool) for value in switches):
            raise ValueError("Alpha V1 switches must be boolean")
        if not (2 <= self.horizon_sessions <= 21):
            raise ValueError("Alpha V1 horizon_sessions must be within [2, 21]")
        if not (126 <= self.minimum_history_sessions <= 756):
            raise ValueError("Alpha V1 minimum_history_sessions must be within [126, 756]")
        if not (42 <= self.ic_lookback_sessions <= 756):
            raise ValueError("Alpha V1 ic_lookback_sessions must be within [42, 756]")
        if not (20 <= self.minimum_ic_observations <= self.ic_lookback_sessions):
            raise ValueError("Alpha V1 minimum_ic_observations is invalid")
        if not (126 <= self.calibration_window_sessions <= 1260):
            raise ValueError("Alpha V1 calibration_window_sessions must be within [126, 1260]")
        if not (1 <= self.retrain_sessions <= 63):
            raise ValueError("Alpha V1 retrain_sessions must be within [1, 63]")
        if not (63 <= self.beta_window_sessions <= 504):
            raise ValueError("Alpha V1 beta_window_sessions must be within [63, 504]")
        if not (10 <= self.volatility_window_sessions <= 126):
            raise ValueError("Alpha V1 volatility_window_sessions must be within [10, 126]")
        if not (0 <= self.weight_adaptivity <= 1.5):
            raise ValueError("Alpha V1 weight_adaptivity must be within [0, 1.5]")
        if not (0 < self.minimum_weight_multiplier <= 1 <= self.maximum_weight_multiplier <= 4):
            raise ValueError("Alpha V1 weight multiplier bounds are invalid")
        if not (0 <= self.calibration_ridge <= 100):
            raise ValueError("Alpha V1 calibration_ridge is invalid")
        if not (0 <= self.disagreement_uncertainty_weight <= 2):
            raise ValueError("Alpha V1 disagreement uncertainty weight is invalid")
        if not (0 <= self.ic_hac_lags <= 21):
            raise ValueError("Alpha V1 ic_hac_lags must be within [0, 21]")
        if not (20 <= self.robust_calibration_min_days <= self.calibration_window_sessions):
            raise ValueError("Alpha V1 robust_calibration_min_days is invalid")

    @property
    def fingerprint(self) -> str:
        robust = self.overlap_robust_ic and self.robust_daily_calibration and self.exact_label_completion
        model_id = ALPHA_V11_ID if robust else ALPHA_V1_ID
        version = ALPHA_V11_VERSION if robust else ALPHA_V1_VERSION
        return hashlib.sha256(
            json.dumps({"id": model_id, "version": version, **asdict(self)}, sort_keys=True).encode()
        ).hexdigest()


# Fixed economic priors.  Adaptive evidence may attenuate/amplify these
# directions but never silently flips the intended sign.
_PRIORS: dict[str, float] = {
    "momentum_21": 0.13,
    "momentum_63": 0.18,
    "momentum_126": 0.18,
    "residual_momentum_21": 0.12,
    "residual_momentum_63": 0.14,
    "short_reversion": 0.08,
    "low_volatility": 0.06,
    "sector_relative": 0.06,
    "value": 0.025,
    "quality": 0.025,
}


def _cross_sectional_rank(panel: pd.DataFrame) -> pd.DataFrame:
    ranked = panel.rank(axis=1, pct=True, method="average")
    return ranked.mul(2.0).sub(1.0).where(panel.notna())


def _safe_volatility(returns: pd.DataFrame, window: int) -> pd.DataFrame:
    return returns.rolling(window, min_periods=window).std(ddof=0).clip(lower=0.002)


def _point_in_time_numeric(panel: pd.DataFrame | None, index: pd.DatetimeIndex, columns: pd.Index) -> pd.DataFrame | None:
    if panel is None:
        return None
    if not isinstance(panel.index, pd.DatetimeIndex) or panel.index.has_duplicates:
        raise ValueError("Alpha V1 point-in-time numeric panels require unique dated observations")
    aligned = panel.reindex(index=index, columns=columns).ffill()
    return aligned.astype(float)


def _point_in_time_sector(panel: pd.DataFrame | None, index: pd.DatetimeIndex, columns: pd.Index) -> pd.DataFrame | None:
    if panel is None:
        return None
    if not isinstance(panel.index, pd.DatetimeIndex) or panel.index.has_duplicates:
        raise ValueError("Alpha V1 sector panel requires unique dated observations")
    return panel.reindex(index=index, columns=columns).ffill()


def _sector_relative(raw: pd.DataFrame, sectors: pd.DataFrame | None) -> pd.DataFrame | None:
    if sectors is None:
        return None
    out = pd.DataFrame(np.nan, index=raw.index, columns=raw.columns, dtype=float)
    for i, day in enumerate(raw.index):
        values = raw.iloc[i]
        labels = sectors.iloc[i]
        valid = values.notna() & labels.notna()
        if valid.sum() < 3:
            continue
        frame = pd.DataFrame({"value": values[valid], "sector": labels[valid].astype(str)})
        relative = frame["value"] - frame.groupby("sector")["value"].transform("median")
        out.loc[day, relative.index] = relative
    return _cross_sectional_rank(out)


def _daily_rank_ic(feature: pd.DataFrame, label: pd.DataFrame) -> pd.Series:
    valid = feature.notna() & label.notna()
    x = feature.where(valid).rank(axis=1, method="average")
    y = label.where(valid).rank(axis=1, method="average")
    xc = x.sub(x.mean(axis=1), axis=0)
    yc = y.sub(y.mean(axis=1), axis=0)
    numerator = (xc * yc).sum(axis=1, min_count=1)
    denominator = np.sqrt(xc.pow(2).sum(axis=1, min_count=1) * yc.pow(2).sum(axis=1, min_count=1))
    return numerator.div(denominator).where(valid.sum(axis=1) >= 6)


def _newey_west_mean_se(values: np.ndarray, max_lag: int) -> float:
    """HAC standard error of the sample mean for overlapping IC observations."""
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 2:
        return float("nan")
    centered = x - float(x.mean())
    gamma0 = float(centered @ centered) / n
    long_run = gamma0
    lags = min(int(max_lag), n - 1)
    for lag in range(1, lags + 1):
        gamma = float(centered[lag:] @ centered[:-lag]) / n
        weight = 1.0 - lag / (lags + 1.0)
        long_run += 2.0 * weight * gamma
    return float(np.sqrt(max(long_run, 0.0) / n))


def _rolling_hac_evidence(series: pd.Series, config: AlphaV1Config) -> pd.Series:
    """Causal overlap-aware evidence score in [-1, 1]."""
    out = pd.Series(np.nan, index=series.index, dtype=float)
    for i in range(len(series)):
        start = max(0, i - config.ic_lookback_sessions + 1)
        sample = series.iloc[start:i + 1].dropna().to_numpy(dtype=float)
        if len(sample) < config.minimum_ic_observations:
            continue
        mean_ic = float(sample.mean())
        se = _newey_west_mean_se(sample, min(config.ic_hac_lags, max(0, config.horizon_sessions - 1)))
        if not np.isfinite(se):
            continue
        # Two HAC t-stat units are enough to approach full adaptive strength;
        # clipping through tanh prevents a short noisy regime from dominating.
        evidence_t = mean_ic / max(se, 1e-4)
        out.iloc[i] = float(np.tanh(evidence_t / 2.0))
    return out


def _daily_robust_calibration(
    composite: pd.DataFrame,
    label: pd.DataFrame,
    *,
    start: int,
    end: int,
    ridge: float,
    minimum_days: int,
) -> tuple[float, float, float, float, float] | None:
    """Robust daily cross-sectional calibration.

    One slope is estimated per completed day, then the median is used.  This
    avoids treating every stock/day pair as an independent observation.
    """
    slopes: list[float] = []
    intercepts: list[float] = []
    day_rows: list[tuple[np.ndarray, np.ndarray]] = []
    for j in range(start, end):
        x = composite.iloc[j].to_numpy(dtype=float)
        y = label.iloc[j].to_numpy(dtype=float)
        valid = np.isfinite(x) & np.isfinite(y)
        if valid.sum() < 6:
            continue
        xv, yv = x[valid], y[valid]
        xc = xv - float(xv.mean())
        yc = yv - float(yv.mean())
        denom = float(xc @ xc + ridge)
        if denom <= _EPS:
            continue
        slope = float((xc @ yc) / denom)
        intercept = float(yv.mean() - slope * xv.mean())
        if np.isfinite(slope) and np.isfinite(intercept):
            slopes.append(slope)
            intercepts.append(intercept)
            day_rows.append((xv, yv))
    if len(slopes) < minimum_days:
        return None
    slope = max(0.0, float(np.median(np.asarray(slopes, dtype=float))))
    intercept = float(np.median(np.asarray(intercepts, dtype=float)))
    residuals = np.concatenate([yv - (intercept + slope * xv) for xv, yv in day_rows])
    center_resid = float(np.median(residuals))
    sigma = float(1.4826 * np.median(np.abs(residuals - center_resid)))
    if not np.isfinite(sigma) or sigma <= 1e-6:
        sigma = float(np.sqrt(np.mean(np.square(residuals)))) if len(residuals) else 1.0
    x_all = np.concatenate([xv for xv, _ in day_rows])
    x_center = float(np.median(x_all))
    x_scale = float(1.4826 * np.median(np.abs(x_all - x_center)))
    return intercept, slope, max(sigma, 1e-6), x_center, max(x_scale, 1e-3)


def _build_features(
    closes: pd.DataFrame,
    benchmark_returns: pd.Series | None,
    config: AlphaV1Config,
    *,
    sector_labels: pd.DataFrame | None,
    value_scores: pd.DataFrame | None,
    quality_scores: pd.DataFrame | None,
) -> dict[str, pd.DataFrame]:
    returns = closes.pct_change(fill_method=None)
    vol = _safe_volatility(returns, config.volatility_window_sessions)
    features: dict[str, pd.DataFrame] = {}

    def add_momentum(name: str, window: int) -> None:
        raw = closes.pct_change(window, fill_method=None).div(vol * np.sqrt(window))
        features[name] = _cross_sectional_rank(raw)

    if config.include_momentum_21:
        add_momentum("momentum_21", 21)
    if config.include_momentum_63:
        add_momentum("momentum_63", 63)
    if config.include_momentum_126:
        add_momentum("momentum_126", 126)

    if benchmark_returns is not None and (config.include_residual_momentum_21 or config.include_residual_momentum_63):
        market = benchmark_returns.reindex(closes.index).astype(float)
        variance = market.rolling(config.beta_window_sessions, min_periods=config.beta_window_sessions).var(ddof=0)
        beta = returns.rolling(config.beta_window_sessions, min_periods=config.beta_window_sessions).cov(market).div(variance, axis=0)
        residual = returns.sub(beta.mul(market, axis=0), axis=0)
        residual_vol = _safe_volatility(residual, config.volatility_window_sessions)
        for name, window, enabled in (
            ("residual_momentum_21", 21, config.include_residual_momentum_21),
            ("residual_momentum_63", 63, config.include_residual_momentum_63),
        ):
            if enabled:
                raw = residual.rolling(window, min_periods=window).sum().div(residual_vol * np.sqrt(window))
                features[name] = _cross_sectional_rank(raw)

    if config.include_short_reversion:
        raw = -closes.pct_change(5, fill_method=None).div(vol * np.sqrt(5))
        features["short_reversion"] = _cross_sectional_rank(raw)

    if config.include_low_volatility:
        features["low_volatility"] = _cross_sectional_rank(-vol * np.sqrt(252))

    sectors = _point_in_time_sector(sector_labels, closes.index, closes.columns)
    if config.include_sector_relative and sectors is not None:
        raw63 = closes.pct_change(63, fill_method=None).div(vol * np.sqrt(63))
        sector_feature = _sector_relative(raw63, sectors)
        if sector_feature is not None:
            features["sector_relative"] = sector_feature

    value = _point_in_time_numeric(value_scores, closes.index, closes.columns)
    if config.include_value and value is not None:
        features["value"] = _cross_sectional_rank(value)
    quality = _point_in_time_numeric(quality_scores, closes.index, closes.columns)
    if config.include_quality and quality is not None:
        features["quality"] = _cross_sectional_rank(quality)

    return features


def _variant_identity(config: AlphaV1Config) -> tuple[str, str]:
    robust = config.overlap_robust_ic and config.robust_daily_calibration and config.exact_label_completion
    return (ALPHA_V11_ID, ALPHA_V11_VERSION) if robust else (ALPHA_V1_ID, ALPHA_V1_VERSION)


def alpha_v1_scores(
    closes: pd.DataFrame,
    *,
    benchmark_returns: pd.Series | None = None,
    sector_labels: pd.DataFrame | None = None,
    value_scores: pd.DataFrame | None = None,
    quality_scores: pd.DataFrame | None = None,
    config: AlphaV1Config = AlphaV1Config(enabled=True),
) -> dict[str, Any]:
    """Return causal cross-sectional score, expected relative return and uncertainty."""
    if not config.enabled:
        raise ValueError("Alpha V1 config must be enabled")
    if not isinstance(closes.index, pd.DatetimeIndex) or closes.index.has_duplicates:
        raise ValueError("Alpha V1 requires a unique DatetimeIndex")
    if len(closes.columns) < 2:
        raise ValueError("Alpha V1 requires a cross-sectional universe")
    values = closes.astype(float)
    if not np.isfinite(values.to_numpy()).all() or (values <= 0).any().any():
        raise ValueError("Alpha V1 requires complete positive close prices")
    if benchmark_returns is not None:
        market = benchmark_returns.reindex(values.index)
        if not np.isfinite(market.iloc[1:].to_numpy()).all():
            raise ValueError("Alpha V1 benchmark must cover all price sessions after the first")

    features = _build_features(
        values, benchmark_returns, config,
        sector_labels=sector_labels, value_scores=value_scores, quality_scores=quality_scores,
    )
    if not features:
        raise ValueError("Alpha V1 has no active features")

    horizon = config.horizon_sessions
    forward = values.shift(-horizon).div(values).sub(1.0)
    relative_label = forward.sub(forward.mean(axis=1), axis=0)

    ic: dict[str, pd.Series] = {name: _daily_rank_ic(panel, relative_label) for name, panel in features.items()}
    # A label beginning on row r is usable only after r+horizon is completed.
    # Alpha V1 retains its original one-session conservative lag.  Alpha V1.1
    # uses the exact completed-label boundary and HAC-adjusted IC evidence.
    completion_lag = horizon if config.exact_label_completion else horizon + 1
    shifted_ic = {name: series.shift(completion_lag) for name, series in ic.items()}
    completed_ic = {
        name: series.rolling(config.ic_lookback_sessions, min_periods=config.minimum_ic_observations)
        for name, series in shifted_ic.items()
    }
    robust_evidence = (
        {name: _rolling_hac_evidence(series, config) for name, series in shifted_ic.items()}
        if config.overlap_robust_ic else {}
    )

    score = pd.DataFrame(0.0, index=values.index, columns=values.columns)
    predicted = pd.DataFrame(0.0, index=values.index, columns=values.columns)
    error = pd.DataFrame(1.0, index=values.index, columns=values.columns)
    composite = pd.DataFrame(np.nan, index=values.index, columns=values.columns)
    feature_weights: list[dict[str, Any]] = []

    minimum_start = max(config.minimum_history_sessions, 126, horizon + config.minimum_ic_observations + 1)
    for i in range(minimum_start, len(values)):
        active_rows: list[np.ndarray] = []
        weights: list[float] = []
        weight_record: dict[str, float] = {}
        for name, panel in features.items():
            row = panel.iloc[i].to_numpy(dtype=float)
            observations = int(completed_ic[name].count().iloc[i])
            if config.overlap_robust_ic:
                evidence_value = robust_evidence[name].iloc[i]
                evidence = float(evidence_value) if observations >= config.minimum_ic_observations and np.isfinite(evidence_value) else 0.0
            else:
                mean_ic = completed_ic[name].mean().iloc[i]
                std_ic = completed_ic[name].std(ddof=0).iloc[i]
                if observations < config.minimum_ic_observations or not np.isfinite(mean_ic):
                    evidence = 0.0
                else:
                    se = float(std_ic) / np.sqrt(max(observations, 1)) if np.isfinite(std_ic) else 0.0
                    evidence = float(np.tanh(float(mean_ic) / max(se + 0.03, 0.03)))
            multiplier = float(np.clip(
                1.0 + config.weight_adaptivity * evidence,
                config.minimum_weight_multiplier,
                config.maximum_weight_multiplier,
            ))
            weight = float(_PRIORS.get(name, 0.05) * multiplier)
            if np.isfinite(row).sum() < max(2, len(row) // 3):
                continue
            active_rows.append(np.nan_to_num(row, nan=0.0))
            weights.append(weight)
            weight_record[name] = weight
        if not active_rows or sum(weights) <= _EPS:
            continue
        matrix = np.vstack(active_rows)
        w = np.asarray(weights, dtype=float)
        current = np.average(matrix, axis=0, weights=w)
        composite.iloc[i] = current
        rank = pd.Series(current, index=values.columns).rank(pct=True, method="average").mul(2).sub(1)
        score.iloc[i] = rank.fillna(0.0)
        feature_weights.append({"date": str(values.index[i].date()), "weights": weight_record})

    # Calibrate the already-causal composite into a relative-return forecast.
    # Retraining uses only labels whose full forward horizon has completed.
    last_intercept = 0.0
    last_slope = 0.0
    last_sigma = 1.0
    last_center = 0.0
    last_ss = 1.0
    last_scale = 1.0
    first_calibration = minimum_start + horizon + config.minimum_ic_observations
    for i in range(first_calibration, len(values)):
        if i == first_calibration or (i - first_calibration) % config.retrain_sessions == 0:
            end = i - horizon + (1 if config.exact_label_completion else 0)
            start = max(minimum_start, end - config.calibration_window_sessions)
            if config.robust_daily_calibration:
                robust = _daily_robust_calibration(
                    composite, relative_label, start=start, end=end, ridge=config.calibration_ridge,
                    minimum_days=config.robust_calibration_min_days,
                )
                if robust is not None:
                    last_intercept, last_slope, last_sigma, last_center, last_scale = robust
                    last_ss = max(last_scale * last_scale, 1e-6)
            else:
                x = composite.iloc[start:end].to_numpy(dtype=float).reshape(-1)
                y = relative_label.iloc[start:end].to_numpy(dtype=float).reshape(-1)
                valid = np.isfinite(x) & np.isfinite(y)
                if valid.sum() >= max(100, len(values.columns) * 20):
                    xv = x[valid]
                    yv = y[valid]
                    center = float(xv.mean())
                    xc = xv - center
                    denom = float(xc @ xc + config.calibration_ridge)
                    slope = float((xc @ (yv - yv.mean())) / max(denom, _EPS))
                    # Economic direction is predeclared; a negative calibration
                    # suppresses the forecast rather than reversing the model.
                    slope = max(0.0, slope)
                    intercept = float(yv.mean() - slope * center)
                    fitted = intercept + slope * xv
                    sigma = float(np.sqrt(np.mean(np.square(yv - fitted))))
                    last_intercept, last_slope, last_sigma = intercept, slope, max(sigma, 1e-6)
                    last_center, last_ss = center, max(float(xc @ xc), 1e-6)
                    last_scale = max(float(np.sqrt(last_ss / max(len(xv), 1))), 1e-3)
        current = composite.iloc[i].to_numpy(dtype=float)
        valid_current = np.isfinite(current)
        if not valid_current.any():
            continue
        forecast = np.zeros(len(current), dtype=float)
        forecast[valid_current] = last_intercept + last_slope * current[valid_current]
        predicted.iloc[i] = forecast
        feature_matrix = np.vstack([
            np.nan_to_num(panel.iloc[i].to_numpy(dtype=float), nan=0.0) for panel in features.values()
        ])
        disagreement = np.std(feature_matrix, axis=0, ddof=0)
        if config.robust_daily_calibration:
            standardized = (current - last_center) / max(last_scale, 1e-3)
            leverage = 0.10 * np.square(standardized)
        else:
            leverage = np.square(current - last_center) / last_ss
        uncertainty = last_sigma * np.sqrt(1.0 + np.maximum(leverage, 0.0))
        uncertainty *= 1.0 + config.disagreement_uncertainty_weight * disagreement
        error.iloc[i] = np.where(np.isfinite(uncertainty), np.maximum(uncertainty, 1e-6), 1.0)

    ready = composite.notna() & score.ne(0)
    return {
        "score": score.where(ready, 0.0).clip(-1.0, 1.0),
        "predicted_return": predicted.where(ready, 0.0),
        "prediction_error": error.where(ready, 1.0),
        "ready": ready,
        "feature_names": list(features),
        "feature_weights": feature_weights,
        "model_id": _variant_identity(config)[0],
        "model_version": _variant_identity(config)[1],
        "config_fingerprint": config.fingerprint,
        "forecast_horizon_sessions": horizon,
        "target": "cross-sectional forward relative return",
        "evidence_method": "overlap_hac" if config.overlap_robust_ic else "original_naive_se",
        "calibration_method": "daily_cross_sectional_robust" if config.robust_daily_calibration else "original_pooled_ols",
        "completion_lag_sessions": completion_lag,
    }


def alpha_v1_contract(config: AlphaV1Config) -> dict[str, Any]:
    return {
        "id": _variant_identity(config)[0],
        "version": _variant_identity(config)[1],
        "enabled": bool(config.enabled),
        "config": asdict(config),
        "config_fingerprint": config.fingerprint,
        "target": "cross-sectional forward relative return",
        "causality": "feature weights and calibration use only fully completed forward labels",
        "evidence_method": "overlap_hac" if config.overlap_robust_ic else "original_naive_se",
        "calibration_method": "daily_cross_sectional_robust" if config.robust_daily_calibration else "original_pooled_ols",
        "external_data": "sector/value/quality sleeves activate only from supplied availability-dated panels",
        "status": "research_candidate_not_release_approved",
    }
