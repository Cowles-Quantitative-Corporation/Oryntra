from __future__ import annotations

from typing import Mapping
import numpy as np
import pandas as pd

DEFAULT_PROBE_GROUPS = {
    "stress": ["market_drawdown_63", "downside_vol_21"],
    "volatility": ["realized_vol_21", "realized_vol_63"],
    "liquidity_stress": ["dispersion_21", "breadth_abs_21"],
    "credit_stress": ["downside_share_21", "market_drawdown_21"],
    "correlation_concentration": ["avg_correlation_63", "pc1_share_63"],
    "rates_macro": ["market_momentum_21", "market_momentum_63"],
}


def _rolling_avg_corr(returns: pd.DataFrame, window: int) -> pd.Series:
    values = np.full(len(returns), np.nan, dtype=float)
    arr = returns.to_numpy(dtype=float)
    for i in range(window - 1, len(arr)):
        block = arr[i - window + 1 : i + 1]
        c = np.corrcoef(block, rowvar=False)
        if c.ndim == 2 and c.shape[0] > 1:
            tri = c[np.triu_indices_from(c, 1)]
            tri = tri[np.isfinite(tri)]
            values[i] = float(np.mean(tri)) if tri.size else 0.0
    return pd.Series(values, index=returns.index, dtype=float)


def _rolling_pc1_share(returns: pd.DataFrame, window: int) -> pd.Series:
    values = np.full(len(returns), np.nan, dtype=float)
    arr = returns.to_numpy(dtype=float)
    for i in range(window - 1, len(arr)):
        block = arr[i - window + 1 : i + 1]
        cov = np.cov(block, rowvar=False, ddof=1)
        cov = np.atleast_2d(cov)
        vals = np.linalg.eigvalsh((cov + cov.T) / 2.0)
        vals = np.maximum(vals, 0.0)
        total = float(vals.sum())
        values[i] = float(vals[-1] / total) if total > 0 else 0.0
    return pd.Series(values, index=returns.index, dtype=float)


def build_default_market_features(
    returns: pd.DataFrame,
    *,
    exogenous: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Build a deterministic PIT-only feature panel from asset returns.

    No centered/future rolling operations are used.  Exogenous columns, when
    supplied, are joined by timestamp and are assumed by the caller to already
    satisfy the point-in-time information contract.
    """
    r = returns.replace([np.inf, -np.inf], np.nan).dropna(how="all").copy()
    if r.empty:
        raise ValueError("V1A feature construction requires non-empty returns")
    market = r.mean(axis=1, skipna=True).fillna(0.0)
    dispersion = r.std(axis=1, ddof=0).fillna(0.0)
    breadth = (r > 0).mean(axis=1).fillna(0.0)
    downside_share = (r < 0).mean(axis=1).fillna(0.0)
    wealth = (1.0 + market.clip(lower=-0.999999)).cumprod()
    roll_max_21 = wealth.rolling(21, min_periods=5).max()
    roll_max_63 = wealth.rolling(63, min_periods=10).max()

    out = pd.DataFrame(index=r.index)
    out["market_return_1"] = market
    out["market_momentum_5"] = (1.0 + market).rolling(5, min_periods=2).apply(np.prod, raw=True) - 1.0
    out["market_momentum_21"] = (1.0 + market).rolling(21, min_periods=5).apply(np.prod, raw=True) - 1.0
    out["market_momentum_63"] = (1.0 + market).rolling(63, min_periods=10).apply(np.prod, raw=True) - 1.0
    out["realized_vol_5"] = market.rolling(5, min_periods=2).std(ddof=1) * np.sqrt(252.0)
    out["realized_vol_21"] = market.rolling(21, min_periods=5).std(ddof=1) * np.sqrt(252.0)
    out["realized_vol_63"] = market.rolling(63, min_periods=10).std(ddof=1) * np.sqrt(252.0)
    out["downside_vol_21"] = market.where(market < 0, 0.0).rolling(21, min_periods=5).std(ddof=1) * np.sqrt(252.0)
    out["dispersion_21"] = dispersion.rolling(21, min_periods=5).mean()
    out["breadth_1"] = breadth
    out["breadth_abs_21"] = (breadth - 0.5).abs().rolling(21, min_periods=5).mean()
    out["downside_share_21"] = downside_share.rolling(21, min_periods=5).mean()
    out["market_drawdown_21"] = (wealth / roll_max_21 - 1.0).fillna(0.0)
    out["market_drawdown_63"] = (wealth / roll_max_63 - 1.0).fillna(0.0)
    out["market_skew_63"] = market.rolling(63, min_periods=20).skew()
    out["market_kurtosis_63"] = market.rolling(63, min_periods=20).kurt()
    out["avg_correlation_63"] = _rolling_avg_corr(r.fillna(0.0), 63)
    out["pc1_share_63"] = _rolling_pc1_share(r.fillna(0.0), 63)
    if exogenous is not None and not exogenous.empty:
        ex = exogenous.reindex(out.index).ffill()
        for col in ex.columns:
            out[str(col)] = pd.to_numeric(ex[col], errors="coerce")
    return out.replace([np.inf, -np.inf], np.nan).ffill().dropna(how="any")


def feature_groups_for_columns(columns: list[str]) -> dict[str, list[int]]:
    index = {name: i for i, name in enumerate(columns)}
    groups: dict[str, list[int]] = {}
    for group, names in DEFAULT_PROBE_GROUPS.items():
        idx = [index[n] for n in names if n in index]
        if idx:
            groups[group] = idx
    return groups


def current_feature_vector(
    returns: pd.DataFrame,
    feature_names: list[str],
    *,
    overrides: Mapping[str, float] | None = None,
) -> np.ndarray:
    panel = build_default_market_features(returns)
    if panel.empty:
        raise ValueError("not enough history to construct the V1A feature snapshot")
    row = {str(k): float(v) for k, v in panel.iloc[-1].to_dict().items()}
    row.update({str(k): float(v) for k, v in dict(overrides or {}).items()})
    missing = [name for name in feature_names if name not in row or not np.isfinite(row[name])]
    if missing:
        raise ValueError(f"V1A artifact requires unavailable current features: {missing[:12]}")
    return np.asarray([row[name] for name in feature_names], dtype=np.float64)
