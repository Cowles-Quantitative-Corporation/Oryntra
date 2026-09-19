import numpy as np
import pandas as pd
from dataclasses import replace

from backend.minerva import (
    phase2_factor_optimizer_candidate,
    phase2_factor_optimizer_v203_candidate,
    risk_v203_candidate,
    tba8_institutional_risk_candidate,
)
from backend.universal_engine import portfolio_targets
from backend.universal_factor_model import FactorModelConfig
from backend.universal_optimizer import PortfolioOptimizerConfig
from backend.universal_risk_supervisor import v203_risk_config


def _inputs(days=470, assets=12, seed=205):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2017-01-02", periods=days)
    market = rng.normal(.0003, .008, days)
    common = rng.normal(.0001, .005, days)
    daily = np.empty((days, assets))
    for j in range(assets):
        daily[:, j] = (.8 + .04 * j) * market + (1 if j < 6 else -.3) * common + rng.normal(0, .005, days)
    prices = pd.DataFrame(100 * np.cumprod(1 + daily, axis=0), index=dates, columns=[f"S{j:02d}" for j in range(assets)])
    volumes = pd.DataFrame(20e6, index=dates, columns=prices.columns)
    benchmark = pd.Series(market, index=dates)
    panel = {
        "score": pd.DataFrame(.8, index=dates, columns=prices.columns),
        "volatility": pd.DataFrame(.012, index=dates, columns=prices.columns),
        "predicted_return": pd.DataFrame(np.tile(np.linspace(.01, .04, assets), (days, 1)), index=dates, columns=prices.columns),
        "prediction_error": pd.DataFrame(.02, index=dates, columns=prices.columns),
    }
    return dates, prices, volumes, benchmark, panel


def test_phase2_candidates_preserve_v1_or_v203_as_explicit_post_optimizer_risk_layer():
    v1 = tba8_institutional_risk_candidate()
    phase2_v1 = phase2_factor_optimizer_candidate()
    phase2_v203 = phase2_factor_optimizer_v203_candidate()
    assert phase2_v1.factor_model.enabled and phase2_v1.portfolio_optimizer.enabled
    assert phase2_v1.risk_supervisor == v1.risk_supervisor
    assert phase2_v203.factor_model.enabled and phase2_v203.portfolio_optimizer.enabled
    assert phase2_v203.risk_supervisor.v203_enabled
    assert not phase2_v203.risk_supervisor.v202_enabled
    assert phase2_v203.research_profile == "phase2_factor_optimizer"


def test_phase2_engine_produces_factor_optimizer_and_v203_audits_on_same_rebalance_schedule():
    dates, prices, volumes, benchmark, panel = _inputs()
    config = phase2_factor_optimizer_v203_candidate(
        risk_supervisor=v203_risk_config(
            v201_bootstrap_samples=0,
            v202_predictive_risk_enabled=False,
        )
    )
    targets = portfolio_targets(
        prices, config, benchmark_returns=benchmark, volumes=volumes, precomputed_panel=panel,
    )
    factor_audit = targets.attrs["factor_model_audit"]
    optimizer_audit = targets.attrs["portfolio_optimizer_audit"]
    risk_audit = targets.attrs["risk_supervisor_target_audit"]
    periods = dates.to_period("W-FRI")
    expected = sum(i == 252 or periods[i] != periods[i - 1] for i in range(252, len(dates)))
    assert len(risk_audit) == expected
    assert len(factor_audit) == expected
    assert len(optimizer_audit) == expected
    assert all(row["factor_names"] for row in factor_audit if row["available"])
    assert all(row["decision_unit"] == "scheduled_rebalance" for row in risk_audit)
    for i in range(253, len(dates)):
        if periods[i] == periods[i - 1]:
            assert np.allclose(targets.iloc[i].to_numpy(), targets.iloc[i - 1].to_numpy())


def test_non_phase2_v203_candidate_does_not_activate_factor_or_optimizer():
    config = risk_v203_candidate()
    assert not config.factor_model.enabled
    assert not config.portfolio_optimizer.enabled


def test_factor_diagnostics_only_do_not_change_frozen_v1_portfolio_behavior():
    _, prices, volumes, benchmark, panel = _inputs(seed=209)
    baseline = tba8_institutional_risk_candidate()
    diagnostics = replace(
        baseline,
        research_profile="phase2_factor_optimizer",
        factor_model=FactorModelConfig(enabled=True, feed_supervisor_covariance=False),
        portfolio_optimizer=PortfolioOptimizerConfig(enabled=False),
    )
    expected = portfolio_targets(prices, baseline, benchmark_returns=benchmark, volumes=volumes, precomputed_panel=panel)
    observed = portfolio_targets(prices, diagnostics, benchmark_returns=benchmark, volumes=volumes, precomputed_panel=panel)
    assert np.allclose(observed.to_numpy(), expected.to_numpy())
    assert observed.attrs["factor_model_audit"]
    assert not observed.attrs["portfolio_optimizer_audit"]
