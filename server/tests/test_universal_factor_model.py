import numpy as np
import pandas as pd

from backend.universal_factor_model import (
    FactorModelConfig,
    build_factor_snapshot,
    nearest_psd,
    portfolio_factor_diagnostics,
)


def _panel(days=430, assets=12, seed=41):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2018-01-01", periods=days)
    market = rng.normal(.0003, .009, days)
    momentum_driver = rng.normal(.0001, .004, days)
    returns = np.empty((days, assets))
    for j in range(assets):
        beta = .7 + .05 * j
        style = 1.0 if j < assets // 2 else -.5
        returns[:, j] = beta * market + style * momentum_driver + rng.normal(0, .006, days)
    frame = pd.DataFrame(returns, index=dates, columns=[f"S{j:02d}" for j in range(assets)])
    benchmark = pd.Series(market, index=dates)
    return frame, benchmark


def test_factor_snapshot_is_causal_to_as_of_and_psd():
    returns, benchmark = _panel()
    config = FactorModelConfig(enabled=True, include_sector=False, include_size=False, include_value=False, include_quality=False)
    as_of = 360
    first = build_factor_snapshot(returns, as_of=as_of, benchmark_returns=benchmark, config=config)
    changed = returns.copy()
    changed.iloc[as_of + 1:] = changed.iloc[as_of + 1:] * 25 + .20
    second = build_factor_snapshot(changed, as_of=as_of, benchmark_returns=benchmark, config=config)
    assert first["available"] and second["available"]
    assert first["factor_names"] == ["market", "momentum", "low_volatility"]
    assert np.allclose(first["exposures"], second["exposures"])
    assert np.allclose(first["factor_covariance"], second["factor_covariance"])
    assert np.allclose(first["asset_covariance"], second["asset_covariance"])
    assert np.linalg.eigvalsh(first["asset_covariance"]).min() >= -1e-10


def test_point_in_time_sector_and_external_style_factors_activate_only_with_coverage():
    returns, benchmark = _panel(days=430, assets=8, seed=42)
    sectors = pd.DataFrame(index=returns.index, columns=returns.columns, dtype=object)
    sectors.loc[:, returns.columns[:4]] = "Technology"
    sectors.loc[:, returns.columns[4:]] = "Financials"
    size = pd.DataFrame(np.tile(np.arange(8), (len(returns), 1)), index=returns.index, columns=returns.columns)
    quality = pd.DataFrame(np.tile(np.linspace(-1, 1, 8), (len(returns), 1)), index=returns.index, columns=returns.columns)
    config = FactorModelConfig(enabled=True, include_value=False)
    snapshot = build_factor_snapshot(
        returns, as_of=380, benchmark_returns=benchmark, config=config,
        sector_labels=sectors, size_scores=size, quality_scores=quality,
    )
    assert snapshot["available"]
    assert "size" in snapshot["factor_names"]
    assert "quality" in snapshot["factor_names"]
    assert "sector:Technology" in snapshot["factor_names"]
    assert "sector:Financials" in snapshot["factor_names"]
    assert "value" not in snapshot["factor_names"]


def test_portfolio_factor_diagnostics_reconciles_systematic_and_idiosyncratic_risk():
    returns, benchmark = _panel(seed=43)
    snapshot = build_factor_snapshot(
        returns, as_of=380, benchmark_returns=benchmark,
        config=FactorModelConfig(enabled=True, include_sector=False, include_size=False, include_value=False, include_quality=False),
    )
    weights = np.full(returns.shape[1], .8 / returns.shape[1])
    diagnostics = portfolio_factor_diagnostics(weights, snapshot)
    assert diagnostics["available"]
    assert 0 <= diagnostics["systematic_variance_share"] <= 1
    assert 0 <= diagnostics["idiosyncratic_variance_share"] <= 1
    assert np.isclose(diagnostics["systematic_variance_share"] + diagnostics["idiosyncratic_variance_share"], 1.0)
    assert diagnostics["annualized_volatility"] > 0
