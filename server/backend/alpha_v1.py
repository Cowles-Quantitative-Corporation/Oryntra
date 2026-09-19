"""Alpha V1: causal, cross-sectional relative-return research candidate.

This is deliberately separate from the TBA8 ridge path.  Its predeclared
price sleeves are adapted only from completed forward rank-IC observations;
optional sector, value, and quality panels must be supplied point-in-time.
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
    weight_adaptivity: float = .65
    minimum_weight_multiplier: float = .25
    maximum_weight_multiplier: float = 1.75
    calibration_ridge: float = 1e-4
    disagreement_uncertainty_weight: float = .35
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
        if not (2 <= self.horizon_sessions <= 21 and 126 <= self.minimum_history_sessions <= 756):
            raise ValueError("Alpha V1 horizon or history setting is invalid")
        if not (42 <= self.minimum_ic_observations <= self.ic_lookback_sessions <= 756):
            raise ValueError("Alpha V1 rank-IC settings are invalid")
        if not (126 <= self.calibration_window_sessions <= 1260 and 1 <= self.retrain_sessions <= 63):
            raise ValueError("Alpha V1 calibration settings are invalid")
        if not (0 <= self.weight_adaptivity <= 1.5 and 0 < self.minimum_weight_multiplier <= 1 <= self.maximum_weight_multiplier <= 4):
            raise ValueError("Alpha V1 sleeve-weight settings are invalid")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps({"id": ALPHA_V1_ID, "version": ALPHA_V1_VERSION, **asdict(self)}, sort_keys=True).encode()).hexdigest()


_PRIORS = {"momentum_21": .13, "momentum_63": .18, "momentum_126": .18,
           "residual_momentum_21": .12, "residual_momentum_63": .14,
           "short_reversion": .08, "low_volatility": .06, "sector_relative": .06,
           "value": .025, "quality": .025}


def _rank(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.rank(axis=1, pct=True, method="average").mul(2).sub(1).where(frame.notna())


def _pit_numeric(panel: pd.DataFrame | None, closes: pd.DataFrame) -> pd.DataFrame | None:
    if panel is None:
        return None
    if not isinstance(panel.index, pd.DatetimeIndex) or panel.index.has_duplicates:
        raise ValueError("Alpha V1 point-in-time panels require a unique DatetimeIndex")
    return panel.reindex(index=closes.index, columns=closes.columns).ffill().astype(float)


def _rank_ic(feature: pd.DataFrame, label: pd.DataFrame) -> pd.Series:
    x, y = feature.rank(axis=1), label.rank(axis=1)
    valid = x.notna() & y.notna()
    x, y = x.where(valid), y.where(valid)
    xc, yc = x.sub(x.mean(axis=1), axis=0), y.sub(y.mean(axis=1), axis=0)
    denominator = np.sqrt(xc.pow(2).sum(axis=1, min_count=1) * yc.pow(2).sum(axis=1, min_count=1))
    return (xc * yc).sum(axis=1, min_count=1).div(denominator).where(valid.sum(axis=1) >= 6)


def alpha_v1_scores(closes: pd.DataFrame, *, benchmark_returns: pd.Series | None = None,
                    sector_labels: pd.DataFrame | None = None, value_scores: pd.DataFrame | None = None,
                    quality_scores: pd.DataFrame | None = None,
                    config: AlphaV1Config = AlphaV1Config(enabled=True)) -> dict[str, Any]:
    """Produce causal cross-sectional scores, forecasts, and uncertainty."""
    if not config.enabled or len(closes.columns) < 2 or not isinstance(closes.index, pd.DatetimeIndex):
        raise ValueError("Alpha V1 requires an enabled cross-sectional dated universe")
    values = closes.astype(float)
    if values.isna().any().any() or (values <= 0).any().any():
        raise ValueError("Alpha V1 requires complete positive close prices")
    returns = values.pct_change(fill_method=None)
    vol = returns.rolling(config.volatility_window_sessions, min_periods=config.volatility_window_sessions).std(ddof=0).clip(lower=.002)
    features: dict[str, pd.DataFrame] = {}
    for name, window, enabled in (("momentum_21", 21, config.include_momentum_21), ("momentum_63", 63, config.include_momentum_63), ("momentum_126", 126, config.include_momentum_126)):
        if enabled: features[name] = _rank(values.pct_change(window, fill_method=None).div(vol * np.sqrt(window)))
    if benchmark_returns is not None:
        market = benchmark_returns.reindex(values.index).astype(float)
        beta = returns.rolling(config.beta_window_sessions, min_periods=config.beta_window_sessions).cov(market).div(market.rolling(config.beta_window_sessions, min_periods=config.beta_window_sessions).var(ddof=0), axis=0)
        residual, residual_vol = returns.sub(beta.mul(market, axis=0), axis=0), None
        residual_vol = residual.rolling(config.volatility_window_sessions, min_periods=config.volatility_window_sessions).std(ddof=0).clip(lower=.002)
        for name, window, enabled in (("residual_momentum_21", 21, config.include_residual_momentum_21), ("residual_momentum_63", 63, config.include_residual_momentum_63)):
            if enabled: features[name] = _rank(residual.rolling(window, min_periods=window).sum().div(residual_vol * np.sqrt(window)))
    if config.include_short_reversion: features["short_reversion"] = _rank(-values.pct_change(5, fill_method=None).div(vol * np.sqrt(5)))
    if config.include_low_volatility: features["low_volatility"] = _rank(-vol * np.sqrt(252))
    for name, enabled, panel in (("value", config.include_value, value_scores), ("quality", config.include_quality, quality_scores)):
        pit = _pit_numeric(panel, values)
        if enabled and pit is not None: features[name] = _rank(pit)
    # Sector labels only activate when explicitly supplied as a dated panel.
    if config.include_sector_relative and sector_labels is not None:
        labels = sector_labels.reindex(index=values.index, columns=values.columns).ffill()
        raw = values.pct_change(63, fill_method=None).div(vol * np.sqrt(63))
        sector = pd.DataFrame(np.nan, index=values.index, columns=values.columns)
        for day in values.index:
            row, groups = raw.loc[day], labels.loc[day]
            valid = row.notna() & groups.notna()
            if valid.sum() >= 3: sector.loc[day, valid] = row[valid] - row[valid].groupby(groups[valid].astype(str)).transform("median")
        features["sector_relative"] = _rank(sector)
    if not features: raise ValueError("Alpha V1 has no active sleeves")
    horizon = config.horizon_sessions
    forward = values.shift(-horizon).div(values).sub(1)
    label = forward.sub(forward.mean(axis=1), axis=0)
    completed_ic = {name: _rank_ic(panel, label).shift(horizon + 1).rolling(config.ic_lookback_sessions, min_periods=config.minimum_ic_observations) for name, panel in features.items()}
    score, forecast, error = (pd.DataFrame(0., index=values.index, columns=values.columns) for _ in range(3))
    error.iloc[:, :] = 1.
    composite = pd.DataFrame(np.nan, index=values.index, columns=values.columns)
    history: list[dict[str, Any]] = []
    start = max(config.minimum_history_sessions, 126, horizon + config.minimum_ic_observations + 1)
    for i in range(start, len(values)):
        rows, weights, audit = [], [], {}
        for name, panel in features.items():
            evidence = completed_ic[name]
            mean = evidence.mean().iloc[i]
            std = evidence.std(ddof=0).iloc[i]
            count = int(evidence.count().iloc[i])
            z = 0. if count < config.minimum_ic_observations or not np.isfinite(mean) else float(np.tanh(float(mean) / max(float(std) / np.sqrt(count) + .03, .03)))
            weight = _PRIORS[name] * float(np.clip(1 + config.weight_adaptivity * z, config.minimum_weight_multiplier, config.maximum_weight_multiplier))
            row = panel.iloc[i].fillna(0.).to_numpy(float)
            if np.isfinite(row).sum() >= 2: rows.append(row); weights.append(weight); audit[name] = weight
        if rows and sum(weights) > 0:
            value = np.average(np.vstack(rows), axis=0, weights=np.asarray(weights))
            composite.iloc[i] = value
            score.iloc[i] = pd.Series(value, index=values.columns).rank(pct=True).mul(2).sub(1).fillna(0.)
            history.append({"date": str(values.index[i].date()), "weights": audit})
    slope, sigma = 0., 1.
    for i in range(start + horizon + config.minimum_ic_observations, len(values)):
        if (i - start) % config.retrain_sessions == 0:
            end, begin = i - horizon, max(start, i - horizon - config.calibration_window_sessions)
            x, y = composite.iloc[begin:end].to_numpy().ravel(), label.iloc[begin:end].to_numpy().ravel()
            valid = np.isfinite(x) & np.isfinite(y)
            if valid.sum() >= max(100, len(values.columns) * 20):
                xv, yv = x[valid], y[valid]; xc = xv - xv.mean()
                slope = max(0., float(xc @ (yv - yv.mean()) / max(float(xc @ xc) + config.calibration_ridge, 1e-12)))
                sigma = max(float(np.sqrt(np.mean((yv - (yv.mean() + slope * xc)) ** 2))), 1e-6)
        row = composite.iloc[i]
        forecast.iloc[i] = row.fillna(0.) * slope
        disagreement = np.std(np.vstack([panel.iloc[i].fillna(0.).to_numpy(float) for panel in features.values()]), axis=0)
        error.iloc[i] = sigma * (1 + config.disagreement_uncertainty_weight * disagreement)
    ready = composite.notna() & score.ne(0)
    return {"score": score.where(ready, 0.).clip(-1., 1.), "predicted_return": forecast.where(ready, 0.), "prediction_error": error.where(ready, 1.), "ready": ready, "feature_names": list(features), "feature_weights": history, "model_id": ALPHA_V1_ID, "model_version": ALPHA_V1_VERSION, "config_fingerprint": config.fingerprint, "forecast_horizon_sessions": horizon, "target": "cross-sectional forward relative return"}


def alpha_v1_contract(config: AlphaV1Config) -> dict[str, Any]:
    return {"id": ALPHA_V1_ID, "version": ALPHA_V1_VERSION, "enabled": config.enabled, "config": asdict(config), "config_fingerprint": config.fingerprint, "target": "cross-sectional forward relative return", "causality": "adaptive weights and calibration use only completed forward labels", "external_data": "sector/value/quality require availability-dated supplied panels", "status": "research_candidate_not_release_approved"}
