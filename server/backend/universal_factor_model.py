"""Causal equity factor-risk model for Universal Phase 2 research.

This module deliberately separates *risk explanation* from alpha generation.
Every exposure at an as-of close is constructed from information available no
later than that close. Historical factor returns used for covariance estimation
use exposures known at the prior close.

The base model is intentionally portable: market beta, momentum and low-
volatility can be built from the OHLCV/benchmark data already available in
Universal. Point-in-time sector/industry, size, value and quality panels are
optional; they are never silently inferred from current classifications.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import math
import numpy as np
import pandas as pd

TRADING_DAYS = 252
_EPS = 1e-12


@dataclass(frozen=True)
class FactorModelConfig:
    enabled: bool = False
    lookback_sessions: int = 126
    beta_window: int = 126
    momentum_lookback: int = 126
    momentum_skip: int = 21
    low_volatility_window: int = 63
    ridge_penalty: float = .05
    covariance_shrinkage: float = .30
    idiosyncratic_variance_floor: float = 1e-8
    minimum_factor_return_observations: int = 42
    minimum_external_coverage: float = .80
    include_market: bool = True
    include_momentum: bool = True
    include_low_volatility: bool = True
    include_sector: bool = True
    include_size: bool = True
    include_value: bool = True
    include_quality: bool = True
    feed_supervisor_covariance: bool = True
    maximum_sector_factors: int = 24

    def __post_init__(self) -> None:
        switches = (
            self.enabled, self.include_market, self.include_momentum,
            self.include_low_volatility, self.include_sector, self.include_size,
            self.include_value, self.include_quality, self.feed_supervisor_covariance,
        )
        if not all(isinstance(value, bool) for value in switches):
            raise ValueError("Factor model switches must be boolean")
        if not (63 <= self.lookback_sessions <= 504):
            raise ValueError("lookback_sessions must be within [63, 504]")
        if not (63 <= self.beta_window <= 504):
            raise ValueError("beta_window must be within [63, 504]")
        if not (42 <= self.momentum_lookback <= 504 and 0 <= self.momentum_skip < self.momentum_lookback):
            raise ValueError("Invalid momentum windows")
        if not (21 <= self.low_volatility_window <= 252):
            raise ValueError("Invalid low-volatility window")
        if not (0 <= self.ridge_penalty <= 1000 and 0 <= self.covariance_shrinkage <= 1):
            raise ValueError("Invalid factor estimation controls")
        if not (0 < self.idiosyncratic_variance_floor <= .10):
            raise ValueError("Invalid idiosyncratic variance floor")
        if not (20 <= self.minimum_factor_return_observations <= self.lookback_sessions):
            raise ValueError("Invalid minimum factor-return observations")
        if not (.25 <= self.minimum_external_coverage <= 1):
            raise ValueError("minimum_external_coverage must be within [.25, 1]")
        if not (1 <= self.maximum_sector_factors <= 100):
            raise ValueError("Invalid maximum_sector_factors")


def nearest_psd(matrix: np.ndarray, floor: float = 1e-12) -> np.ndarray:
    x = np.asarray(matrix, dtype=float)
    if x.ndim != 2 or x.shape[0] != x.shape[1]:
        raise ValueError("Matrix must be square")
    x = (x + x.T) / 2.0
    values, vectors = np.linalg.eigh(x)
    values = np.maximum(values, floor)
    repaired = (vectors * values) @ vectors.T
    return (repaired + repaired.T) / 2.0


def _shrink_covariance(covariance: np.ndarray, shrinkage: float) -> np.ndarray:
    cov = nearest_psd(covariance)
    diagonal = np.diag(np.diag(cov))
    return nearest_psd((1.0 - shrinkage) * cov + shrinkage * diagonal)


def _cross_sectional_zscore(values: np.ndarray) -> np.ndarray:
    x = np.asarray(values, dtype=float).reshape(-1)
    finite = np.isfinite(x)
    result = np.zeros_like(x)
    if finite.sum() < 2:
        return result
    mean = float(np.mean(x[finite]))
    scale = float(np.std(x[finite], ddof=0))
    if scale <= _EPS:
        return result
    result[finite] = (x[finite] - mean) / scale
    return np.clip(result, -4.0, 4.0)


def _compounded_return(returns: pd.DataFrame, start: int, end: int) -> np.ndarray:
    if end <= start:
        return np.zeros(returns.shape[1], dtype=float)
    sample = returns.iloc[start:end].to_numpy(dtype=float)
    return np.prod(1.0 + sample, axis=0) - 1.0


def _market_beta(
    returns: pd.DataFrame,
    benchmark_returns: pd.Series,
    as_of: int,
    window: int,
) -> np.ndarray:
    start = max(1, as_of - window + 1)
    asset = returns.iloc[start:as_of + 1].to_numpy(dtype=float)
    market = benchmark_returns.reindex(returns.index).iloc[start:as_of + 1].to_numpy(dtype=float)
    if len(market) < 20 or not np.isfinite(asset).all() or not np.isfinite(market).all():
        return np.zeros(returns.shape[1], dtype=float)
    market_centered = market - market.mean()
    variance = float(np.mean(np.square(market_centered)))
    if variance <= _EPS:
        return np.zeros(returns.shape[1], dtype=float)
    centered = asset - asset.mean(axis=0, keepdims=True)
    covariance = np.mean(centered * market_centered[:, None], axis=0)
    beta = covariance / variance
    return np.clip(beta, -3.0, 3.0)


def _point_in_time_numeric_row(
    panel: pd.DataFrame | None,
    index: pd.DatetimeIndex,
    columns: pd.Index,
    as_of: int,
) -> np.ndarray | None:
    if panel is None:
        return None
    aligned = panel.reindex(index=index, columns=columns).ffill()
    values = aligned.iloc[as_of].to_numpy(dtype=float)
    return values


def _point_in_time_sector_row(
    panel: pd.DataFrame | None,
    index: pd.DatetimeIndex,
    columns: pd.Index,
    as_of: int,
) -> np.ndarray | None:
    if panel is None:
        return None
    aligned = panel.reindex(index=index, columns=columns).ffill()
    values = aligned.iloc[as_of].astype("object").to_numpy()
    return values


def _current_factor_names(
    returns: pd.DataFrame,
    benchmark_returns: pd.Series | None,
    as_of: int,
    config: FactorModelConfig,
    sector_labels: pd.DataFrame | None,
    size_scores: pd.DataFrame | None,
    value_scores: pd.DataFrame | None,
    quality_scores: pd.DataFrame | None,
) -> tuple[list[str], dict[str, Any]]:
    names: list[str] = []
    availability: dict[str, Any] = {}

    if config.include_market:
        if benchmark_returns is None:
            raise ValueError("Enabled market factor requires explicit benchmark returns")
        names.append("market")
        availability["market"] = True

    if config.include_momentum:
        names.append("momentum")
        availability["momentum"] = True
    if config.include_low_volatility:
        names.append("low_volatility")
        availability["low_volatility"] = True

    for label, enabled, panel in (
        ("size", config.include_size, size_scores),
        ("value", config.include_value, value_scores),
        ("quality", config.include_quality, quality_scores),
    ):
        if not enabled:
            availability[label] = False
            continue
        row = _point_in_time_numeric_row(panel, returns.index, returns.columns, as_of)
        coverage = 0.0 if row is None else float(np.isfinite(row).mean())
        availability[label] = {"available": bool(row is not None and coverage >= config.minimum_external_coverage), "coverage": coverage}
        if row is not None and coverage >= config.minimum_external_coverage:
            names.append(label)

    if config.include_sector:
        sectors = _point_in_time_sector_row(sector_labels, returns.index, returns.columns, as_of)
        if sectors is None:
            availability["sector"] = {"available": False, "reason": "no_point_in_time_sector_panel"}
        else:
            clean = [str(value).strip() for value in sectors if value is not None and str(value).strip() and str(value).lower() != "nan"]
            counts: dict[str, int] = {}
            for sector in clean:
                counts[sector] = counts.get(sector, 0) + 1
            ordered = sorted(counts, key=lambda key: (-counts[key], key))[:config.maximum_sector_factors]
            names.extend([f"sector:{sector}" for sector in ordered])
            availability["sector"] = {"available": bool(ordered), "factor_count": len(ordered), "labels": ordered}
    else:
        availability["sector"] = {"available": False, "reason": "disabled"}

    return names, availability


def _exposure_matrix(
    returns: pd.DataFrame,
    benchmark_returns: pd.Series | None,
    as_of: int,
    factor_names: list[str],
    config: FactorModelConfig,
    *,
    sector_labels: pd.DataFrame | None = None,
    size_scores: pd.DataFrame | None = None,
    value_scores: pd.DataFrame | None = None,
    quality_scores: pd.DataFrame | None = None,
) -> np.ndarray:
    n = returns.shape[1]
    matrix = np.zeros((n, len(factor_names)), dtype=float)
    name_to_col = {name: i for i, name in enumerate(factor_names)}

    if "market" in name_to_col:
        if benchmark_returns is None:
            raise ValueError("Market exposure requires benchmark returns")
        matrix[:, name_to_col["market"]] = _market_beta(returns, benchmark_returns, as_of, config.beta_window)

    if "momentum" in name_to_col:
        end = max(1, as_of - config.momentum_skip + 1)
        start = max(1, end - config.momentum_lookback)
        momentum = _compounded_return(returns, start, end)
        matrix[:, name_to_col["momentum"]] = _cross_sectional_zscore(momentum)

    if "low_volatility" in name_to_col:
        start = max(1, as_of - config.low_volatility_window + 1)
        sample = returns.iloc[start:as_of + 1].to_numpy(dtype=float)
        volatility = np.std(sample, axis=0, ddof=0) * math.sqrt(TRADING_DAYS)
        matrix[:, name_to_col["low_volatility"]] = -_cross_sectional_zscore(volatility)

    for label, panel in (("size", size_scores), ("value", value_scores), ("quality", quality_scores)):
        if label not in name_to_col:
            continue
        row = _point_in_time_numeric_row(panel, returns.index, returns.columns, as_of)
        if row is None:
            continue
        matrix[:, name_to_col[label]] = _cross_sectional_zscore(row)

    sector_names = [name for name in factor_names if name.startswith("sector:")]
    if sector_names:
        row = _point_in_time_sector_row(sector_labels, returns.index, returns.columns, as_of)
        if row is not None:
            row = np.asarray(["" if value is None else str(value).strip() for value in row], dtype=object)
            for name in sector_names:
                sector = name.split(":", 1)[1]
                matrix[:, name_to_col[name]] = (row == sector).astype(float)

    if not np.isfinite(matrix).all():
        raise ValueError("Factor exposure matrix contains non-finite values")
    return matrix


def _ridge_cross_section(x: np.ndarray, y: np.ndarray, penalty: float) -> np.ndarray:
    if x.size == 0:
        return np.zeros(0, dtype=float)
    gram = x.T @ x + float(penalty) * np.eye(x.shape[1])
    return np.linalg.solve(gram, x.T @ y)


def build_factor_snapshot(
    returns: pd.DataFrame,
    *,
    as_of: int,
    benchmark_returns: pd.Series | None,
    config: FactorModelConfig,
    sector_labels: pd.DataFrame | None = None,
    size_scores: pd.DataFrame | None = None,
    value_scores: pd.DataFrame | None = None,
    quality_scores: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Build a causal factor-risk snapshot at one completed close.

    Factor-return history is estimated from realized asset returns at session t
    using exposures known at t-1. That explicit one-session lag is the key
    causality rule for the risk model.
    """
    if not config.enabled:
        return {"enabled": False, "available": False, "reason": "factor_model_disabled"}
    if not isinstance(returns, pd.DataFrame) or not isinstance(returns.index, pd.DatetimeIndex):
        raise ValueError("Factor model requires a dated return DataFrame")
    if not returns.index.is_monotonic_increasing:
        raise ValueError("Factor model returns must be increasing by date")
    if as_of < 1 or as_of >= len(returns):
        raise ValueError("as_of is outside the return panel")
    observed = returns.iloc[1:as_of + 1].to_numpy(dtype=float)
    if not np.isfinite(observed).all():
        raise ValueError("Factor model requires complete finite returns through as_of")

    factor_names, availability = _current_factor_names(
        returns, benchmark_returns, as_of, config,
        sector_labels, size_scores, value_scores, quality_scores,
    )
    if not factor_names:
        return {
            "enabled": True,
            "available": False,
            "reason": "no_available_factors",
            "factor_availability": availability,
        }

    current_exposures = _exposure_matrix(
        returns, benchmark_returns, as_of, factor_names, config,
        sector_labels=sector_labels, size_scores=size_scores,
        value_scores=value_scores, quality_scores=quality_scores,
    )

    start = max(1, as_of - config.lookback_sessions + 1)
    factor_rows: list[np.ndarray] = []
    residual_rows: list[np.ndarray] = []
    dates: list[pd.Timestamp] = []
    market_col = factor_names.index("market") if "market" in factor_names else None
    other_cols = [i for i in range(len(factor_names)) if i != market_col]
    aligned_benchmark = None if benchmark_returns is None else benchmark_returns.reindex(returns.index)

    for t in range(start, as_of + 1):
        # Exposure is frozen at the prior completed close; return occurs at t.
        if t - 1 < max(config.momentum_lookback, config.beta_window, config.low_volatility_window):
            continue
        x = _exposure_matrix(
            returns, benchmark_returns, t - 1, factor_names, config,
            sector_labels=sector_labels, size_scores=size_scores,
            value_scores=value_scores, quality_scores=quality_scores,
        )
        y = returns.iloc[t].to_numpy(dtype=float)
        factor_return = np.zeros(len(factor_names), dtype=float)
        residual = y.copy()

        if market_col is not None:
            market_return = float(aligned_benchmark.iloc[t])
            if not math.isfinite(market_return):
                continue
            factor_return[market_col] = market_return
            residual = residual - x[:, market_col] * market_return

        if other_cols:
            estimated = _ridge_cross_section(x[:, other_cols], residual, config.ridge_penalty)
            factor_return[other_cols] = estimated
            residual = residual - x[:, other_cols] @ estimated

        if np.isfinite(factor_return).all() and np.isfinite(residual).all():
            factor_rows.append(factor_return)
            residual_rows.append(residual)
            dates.append(returns.index[t])

    if len(factor_rows) < config.minimum_factor_return_observations:
        return {
            "enabled": True,
            "available": False,
            "reason": "insufficient_factor_return_history",
            "observations": len(factor_rows),
            "minimum_observations": config.minimum_factor_return_observations,
            "factor_names": factor_names,
            "factor_availability": availability,
        }

    factor_returns = np.asarray(factor_rows, dtype=float)
    residuals = np.asarray(residual_rows, dtype=float)
    factor_covariance = np.atleast_2d(np.cov(factor_returns, rowvar=False, ddof=0))
    factor_covariance = _shrink_covariance(factor_covariance, config.covariance_shrinkage)
    idiosyncratic_variance = np.maximum(np.var(residuals, axis=0, ddof=0), config.idiosyncratic_variance_floor)
    asset_covariance = nearest_psd(
        current_exposures @ factor_covariance @ current_exposures.T
        + np.diag(idiosyncratic_variance),
        config.idiosyncratic_variance_floor,
    )

    return {
        "enabled": True,
        "available": True,
        "as_of": str(returns.index[as_of].date()),
        "factor_names": factor_names,
        "exposures": current_exposures,
        "factor_covariance": factor_covariance,
        "idiosyncratic_variance": idiosyncratic_variance,
        "asset_covariance": asset_covariance,
        "factor_returns": factor_returns,
        "factor_return_dates": dates,
        "factor_availability": availability,
        "observations": len(factor_rows),
        "config": asdict(config),
    }


def portfolio_factor_diagnostics(weights: np.ndarray, snapshot: dict[str, Any]) -> dict[str, Any]:
    if not snapshot.get("available"):
        return {"available": False, "reason": snapshot.get("reason", "factor_snapshot_unavailable")}
    w = np.asarray(weights, dtype=float).reshape(-1)
    x = np.asarray(snapshot["exposures"], dtype=float)
    factor_covariance = np.asarray(snapshot["factor_covariance"], dtype=float)
    idio = np.asarray(snapshot["idiosyncratic_variance"], dtype=float)
    if len(w) != x.shape[0]:
        raise ValueError("Weights do not align with factor snapshot")
    factor_exposure = w @ x
    factor_marginal = factor_covariance @ factor_exposure
    factor_contribution = factor_exposure * factor_marginal
    factor_variance = float(factor_exposure @ factor_covariance @ factor_exposure)
    idiosyncratic_variance = float(np.sum(np.square(w) * idio))
    total_variance = max(factor_variance + idiosyncratic_variance, _EPS)
    positive_factor = np.maximum(factor_contribution, 0.0)
    positive_total = float(positive_factor.sum()) + idiosyncratic_variance
    risk_shares = (
        positive_factor / positive_total if positive_total > _EPS else np.zeros_like(positive_factor)
    )
    return {
        "available": True,
        "factor_exposure": {name: float(value) for name, value in zip(snapshot["factor_names"], factor_exposure)},
        "factor_variance_contribution": {name: float(value) for name, value in zip(snapshot["factor_names"], factor_contribution)},
        "factor_risk_share": {name: float(value) for name, value in zip(snapshot["factor_names"], risk_shares)},
        "systematic_variance_share": float(factor_variance / total_variance),
        "idiosyncratic_variance_share": float(idiosyncratic_variance / total_variance),
        "annualized_volatility": float(math.sqrt(total_variance * TRADING_DAYS)),
    }


def factor_model_contract(config: FactorModelConfig) -> dict[str, Any]:
    return {
        "id": "universal_phase2_factor_risk_v1",
        "status": "active_research" if config.enabled else "disabled",
        "causality": "Factor return at t uses exposures known at t-1; optional external panels are point-in-time and forward-filled only from prior observations.",
        "base_factors": [
            name for name, enabled in (
                ("market_beta", config.include_market),
                ("momentum", config.include_momentum),
                ("low_volatility", config.include_low_volatility),
            ) if enabled
        ],
        "optional_point_in_time_factors": [
            name for name, enabled in (
                ("sector_industry", config.include_sector),
                ("size", config.include_size),
                ("value", config.include_value),
                ("quality", config.include_quality),
            ) if enabled
        ],
        "configuration": asdict(config),
    }
