"""Leakage-safe, walk-forward cross-sectional learning for Universal V2 research."""
from __future__ import annotations

import numpy as np
import pandas as pd


FEATURE_NAMES = (
    "return_5", "return_21", "return_63", "return_126", "return_252",
    "breakout_63", "volatility_21", "relative_volume_21",
)
OHLCV_STRUCTURE_FEATURE_NAMES = (
    "overnight_gap", "intraday_return", "intraday_range", "close_location",
    "volume_trend_5_21", "amihud_21",
)


def walk_forward_ridge_scores(closes: pd.DataFrame, opens: pd.DataFrame, volumes: pd.DataFrame,
                              *, training_sessions: int = 756, retrain_sessions: int = 21,
                              horizon_sessions: int = 5, penalty: float = 10.0,
                              benchmark_returns: pd.Series | None = None,
                              include_residual_momentum: bool = False,
                              residual_momentum_horizons: tuple[int, ...] | None = None,
                              target_market_residual: bool = False,
                              include_economic_interactions: bool = False,
                              corporate_quality: pd.DataFrame | None = None,
                              corporate_observed: pd.DataFrame | None = None,
                              highs: pd.DataFrame | None = None,
                              lows: pd.DataFrame | None = None,
                              include_ohlcv_structure: bool = False,
                              score_smoothing: float = 0.0,
                              ic_gate: bool = False,
                              ic_lookback_sessions: int = 63,
                              ic_minimum: float = 0.0) -> pd.DataFrame:
    """Return cross-sectional scores trained only on outcomes completed before each date.

    At close ``t`` the model sees features through ``t``. Training labels are
    open(t+1)-to-close(t+horizon) returns, demeaned across the investable
    universe for that date. A label is eligible only when it completed strictly
    before the prediction close. Scores are percentile ranks in [-1, 1].
    """
    _validate_panel(closes, "closes")
    _validate_panel(opens, "opens")
    _validate_panel(volumes, "volumes", allow_zero=True)
    for panel, name in ((opens, "opens"), (volumes, "volumes")):
        if not panel.index.equals(closes.index) or not panel.columns.equals(closes.columns):
            raise ValueError(f"{name} must exactly align with closes")
    if include_ohlcv_structure:
        if highs is None or lows is None:
            raise ValueError("OHLCV structure features require aligned high and low panels")
        _validate_panel(highs, "highs")
        _validate_panel(lows, "lows")
        for panel, name in ((highs, "highs"), (lows, "lows")):
            if not panel.index.equals(closes.index) or not panel.columns.equals(closes.columns):
                raise ValueError(f"{name} must exactly align with closes")
        if (highs < lows).any().any() or (highs < closes).any().any() or (highs < opens).any().any() or (lows > closes).any().any() or (lows > opens).any().any():
            raise ValueError("OHLC panels must enclose each session's open and close")
    if not (252 <= training_sessions <= 2520 and 1 <= retrain_sessions <= 63 and 2 <= horizon_sessions <= 21 and .001 <= penalty <= 1e5):
        raise ValueError("Invalid walk-forward ridge configuration")
    if not (0 <= score_smoothing < 1):
        raise ValueError("score_smoothing must be in [0, 1)")
    if not (isinstance(ic_gate, bool) and 21 <= ic_lookback_sessions <= 252 and -1 <= ic_minimum <= 1):
        raise ValueError("Invalid information-coefficient gate configuration")

    residual_horizons = ((21, 63) if include_residual_momentum else ()) if residual_momentum_horizons is None else tuple(residual_momentum_horizons)
    if any(horizon not in {21, 63} for horizon in residual_horizons) or len(set(residual_horizons)) != len(residual_horizons):
        raise ValueError("Residual momentum horizons must be a unique subset of 21 and 63")
    if residual_horizons or target_market_residual:
        if benchmark_returns is None:
            raise ValueError("Residual features or target require a completed benchmark return series")
        market = benchmark_returns.reindex(closes.index).astype(float).copy()
        # The initial percentage-change observation has no predecessor and is
        # outside every 126-session residual window. It may be zeroed locally;
        # every actionable observation still requires an observed benchmark.
        if not np.isfinite(market.iloc[1:].to_numpy()).all() or (market.iloc[1:] <= -1).any():
            raise ValueError("Residual momentum benchmark must cover every actionable price session")
        market.iloc[0] = 0.0
    else:
        market = None
    if (corporate_quality is None) != (corporate_observed is None):
        raise ValueError("Corporate learner inputs require both quality and observed panels")
    if corporate_quality is not None:
        if not (corporate_quality.index.equals(closes.index) and corporate_quality.columns.equals(closes.columns)
                and corporate_observed.index.equals(closes.index) and corporate_observed.columns.equals(closes.columns)):
            raise ValueError("Corporate learner panels must exactly align with prices")
        if not np.isfinite(corporate_quality.to_numpy(dtype=float)).all() or (corporate_quality.abs() > 1).any().any():
            raise ValueError("Corporate quality learner values must be finite and bounded")
        if corporate_observed.to_numpy().dtype != bool:
            raise ValueError("Corporate observed learner panel must be boolean")
    features = _feature_cube(closes, volumes, market, residual_horizons,
                             include_economic_interactions=include_economic_interactions,
                             corporate_quality=corporate_quality,
                             corporate_observed=corporate_observed,
                             opens=opens if include_ohlcv_structure else None,
                             highs=highs, lows=lows)
    # Decision is made after close t; execution begins at the following open.
    forward = closes.shift(-horizon_sessions).div(opens.shift(-1)).sub(1)
    if target_market_residual:
        # The forward stock outcome is adjusted by a beta estimated strictly
        # from returns completed at the decision close.  Market performance
        # after that close is label-only information and is never a feature.
        returns = closes.pct_change(fill_method=None)
        market_variance = market.rolling(126, min_periods=126).var(ddof=0)
        beta = returns.rolling(126, min_periods=126).cov(market).div(market_variance, axis=0)
        market_level = (1.0 + market).cumprod()
        market_forward = market_level.shift(-horizon_sessions).div(market_level).sub(1)
        label = forward.sub(beta.mul(market_forward, axis=0), axis=0)
    else:
        label = forward
    relative_label = label.sub(label.mean(axis=1), axis=0)
    scores = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
    last_score: np.ndarray | None = None
    first = max(252, training_sessions + horizon_sessions + 1)
    for current in range(first, len(closes)):
        if last_score is None or (current - first) % retrain_sessions == 0:
            # Labels through current - horizon - 1 are fully known at close current.
            end = current - horizon_sessions
            start = max(252, end - training_sessions)
            feature_block = features[start:end]
            label_block = relative_label.iloc[start:end].to_numpy(dtype=float)
            x = feature_block.reshape(-1, feature_block.shape[-1])
            y = label_block.reshape(-1)
            valid = np.isfinite(x).all(axis=1) & np.isfinite(y)
            if valid.sum() < max(100, 10 * features.shape[-1]):
                continue
            x_train, y_train = x[valid], y[valid]
            mean = x_train.mean(axis=0)
            scale = x_train.std(axis=0, ddof=0)
            scale = np.where(scale > 1e-9, scale, 1.0)
            standardized = np.clip((x_train - mean) / scale, -8, 8)
            design = np.column_stack((np.ones(len(standardized)), standardized))
            regularizer = np.eye(design.shape[1]) * penalty
            regularizer[0, 0] = 0.0
            try:
                coefficients = np.linalg.solve(design.T @ design + regularizer, design.T @ y_train)
            except np.linalg.LinAlgError:
                coefficients = np.linalg.lstsq(design.T @ design + regularizer, design.T @ y_train, rcond=None)[0]
            current_features = features[current]
            raw = np.full(len(closes.columns), np.nan)
            valid_current = np.isfinite(current_features).all(axis=1)
            current_design = np.column_stack((np.ones(valid_current.sum()), np.clip((current_features[valid_current] - mean) / scale, -8, 8)))
            raw[valid_current] = current_design @ coefficients
            ranks = pd.Series(raw, index=closes.columns).rank(pct=True, method="first")
            candidate_score = ranks.mul(2).sub(1).fillna(0).to_numpy()
            last_score = candidate_score if last_score is None else (
                score_smoothing * last_score + (1 - score_smoothing) * candidate_score
            )
        if last_score is not None:
            scores.iloc[current] = last_score
    if ic_gate:
        scores = _apply_completed_ic_gate(scores, relative_label, horizon_sessions, ic_lookback_sessions, ic_minimum)
    return scores


def _apply_completed_ic_gate(scores: pd.DataFrame, labels: pd.DataFrame, horizon_sessions: int,
                             lookback_sessions: int, minimum_ic: float) -> pd.DataFrame:
    """Hold cash when trailing, fully-finished cross-sectional IC is too weak.

    At decision close ``current``, a label starting at ``observed`` is usable
    only when its full horizon completed strictly earlier than ``current``.
    The gate therefore ends its evidence at ``current - horizon - 1``.
    """
    valid = scores.ne(0) & np.isfinite(scores) & np.isfinite(labels)
    # Scores are already ranks but rank again to handle ties from a future
    # model. Pearson correlation of cross-sectional ranks is Spearman IC.
    ranked_scores = scores.where(valid).rank(axis=1, method="average")
    ranked_labels = labels.where(valid).rank(axis=1, method="average")
    centered_scores = ranked_scores.sub(ranked_scores.mean(axis=1), axis=0)
    centered_labels = ranked_labels.sub(ranked_labels.mean(axis=1), axis=0)
    numerator = (centered_scores * centered_labels).sum(axis=1, min_count=1)
    denominator = np.sqrt((centered_scores.pow(2).sum(axis=1, min_count=1)) *
                          (centered_labels.pow(2).sum(axis=1, min_count=1)))
    daily_ic = numerator.div(denominator).where(valid.sum(axis=1) >= 8)
    # shift(horizon + 1) makes the last usable label strictly older than the
    # decision close, exactly matching the loop implementation above.
    completed_ic = daily_ic.shift(horizon_sessions + 1).rolling(lookback_sessions, min_periods=1).mean()
    eligible = np.arange(len(scores)) >= max(252, horizon_sessions + lookback_sessions + 1)
    gated = scores.copy()
    gated.loc[eligible & (completed_ic < minimum_ic)] = 0.0
    return gated


def _feature_cube(closes: pd.DataFrame, volumes: pd.DataFrame,
                  benchmark_returns: pd.Series | None = None,
                  residual_momentum_horizons: tuple[int, ...] = (),
                  *, include_economic_interactions: bool = False,
                  corporate_quality: pd.DataFrame | None = None,
                  corporate_observed: pd.DataFrame | None = None,
                  opens: pd.DataFrame | None = None,
                  highs: pd.DataFrame | None = None,
                  lows: pd.DataFrame | None = None) -> np.ndarray:
    returns = closes.pct_change(fill_method=None)
    volatility = returns.rolling(21, min_periods=21).std(ddof=0).clip(lower=.003)
    high = closes.rolling(63, min_periods=63).max()
    low = closes.rolling(63, min_periods=63).min()
    breakout = closes.sub(low).div((high - low).replace(0, np.nan)).mul(2).sub(1)
    relative_volume = np.log(volumes.where(volumes > 0).div(volumes.rolling(21, min_periods=21).mean()))
    panels = [closes.pct_change(window, fill_method=None).div(volatility * np.sqrt(window)) for window in (5, 21, 63, 126, 252)]
    panels.extend((breakout, volatility * np.sqrt(252), relative_volume))
    if include_economic_interactions:
        # These are deliberately few, named interactions rather than an
        # unconstrained polynomial expansion: medium/long trend agreement,
        # momentum confirmed by unusual participation, and breakout confirmed
        # by unusual participation.  All operands are completed-close fields.
        medium, long = panels[2], panels[4]
        panels.extend((
            medium * long,
            medium * np.tanh(relative_volume),
            breakout * np.tanh(relative_volume),
        ))
    if corporate_quality is not None and corporate_observed is not None:
        # A zero quality value is meaningful only when the companion observed
        # flag is true; the flag lets the learner distinguish it from no filing
        # history without inventing a value for a missing issuer.
        panels.extend((corporate_quality, corporate_observed.astype(float)))
    if opens is not None and highs is not None and lows is not None:
        # A compact, fully named OHLCV set inspired by the public Qlib baseline.
        # Each term is observable by the decision close. It is intentionally a
        # separate candidate, not a 158-feature copy or a default expansion.
        prior_close = closes.shift(1)
        overnight_gap = opens.div(prior_close).sub(1)
        intraday_return = closes.div(opens).sub(1)
        intraday_range = highs.sub(lows).div(closes)
        close_location = closes.sub(lows).div(highs.sub(lows).replace(0, np.nan)).mul(2).sub(1).fillna(0)
        volume_trend = np.log(volumes.rolling(5, min_periods=5).mean().div(volumes.rolling(21, min_periods=21).mean()))
        dollar_volume = closes.mul(volumes).replace(0, np.nan)
        amihud = returns.abs().div(dollar_volume).rolling(21, min_periods=21).mean()
        panels.extend((overnight_gap, intraday_return, intraday_range, close_location, volume_trend, amihud))
    if residual_momentum_horizons:
        if benchmark_returns is None:
            raise ValueError("Residual momentum requires a completed benchmark return series")
        market_variance = benchmark_returns.rolling(126, min_periods=126).var(ddof=0)
        beta = returns.rolling(126, min_periods=126).cov(benchmark_returns).div(market_variance, axis=0)
        residual = returns.sub(beta.mul(benchmark_returns, axis=0), axis=0)
        residual_volatility = residual.rolling(21, min_periods=21).std(ddof=0).clip(lower=.003)
        panels.extend(residual.rolling(window, min_periods=window).sum().div(residual_volatility * np.sqrt(window))
                      for window in residual_momentum_horizons)
    return np.stack([panel.to_numpy(dtype=float) for panel in panels], axis=-1)


def _validate_panel(panel: pd.DataFrame, name: str, *, allow_zero: bool = False) -> None:
    if not isinstance(panel, pd.DataFrame) or panel.empty or not isinstance(panel.index, pd.DatetimeIndex):
        raise ValueError(f"{name} must be a nonempty dated DataFrame")
    if panel.index.has_duplicates or not panel.index.is_monotonic_increasing or panel.columns.has_duplicates:
        raise ValueError(f"{name} requires unique increasing dates and symbols")
    values = panel.to_numpy(dtype=float)
    invalid = values < 0 if allow_zero else values <= 0
    if not np.isfinite(values).all() or invalid.any():
        qualifier = "nonnegative" if allow_zero else "positive"
        raise ValueError(f"{name} requires {qualifier} finite values")
