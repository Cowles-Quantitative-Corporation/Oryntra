import json
import numpy as np
import pandas as pd

from backend.minerva import (
    phase15_phase2_phase3_candidate,
    phase15_phase3_candidate,
    phase15_v203_candidate,
)
from backend.universal_engine import portfolio_targets
from backend.universal_phase3 import Phase3Config, build_phase3_snapshot, realized_security_attribution
from backend.universal_risk_v201 import sample_covariance
from backend.universal_risk_supervisor import v203_risk_config


def _returns(days=500, assets=10, seed=301):
    rng = np.random.default_rng(seed)
    market = rng.normal(.0003, .009, days)
    sector = rng.normal(.0001, .006, days)
    x = np.empty((days, assets))
    for j in range(assets):
        x[:, j] = (.7 + .04 * j) * market + (1.0 if j < assets // 2 else -.2) * sector + rng.normal(0, .006, days)
    return x, market


def _engine_inputs(days=470, assets=12, seed=302):
    x, market = _returns(days, assets, seed)
    dates = pd.bdate_range("2016-01-04", periods=days)
    symbols = [f"S{i:02d}" for i in range(assets)]
    prices = pd.DataFrame(100 * np.cumprod(1 + x, axis=0), index=dates, columns=symbols)
    volumes = pd.DataFrame(15e6, index=dates, columns=symbols)
    benchmark = pd.Series(market, index=dates)
    panel = {
        "score": pd.DataFrame(.8, index=dates, columns=symbols),
        "volatility": pd.DataFrame(.012, index=dates, columns=symbols),
        "predicted_return": pd.DataFrame(np.tile(np.linspace(.01, .04, assets), (days, 1)), index=dates, columns=symbols),
        "prediction_error": pd.DataFrame(.02, index=dates, columns=symbols),
    }
    return dates, prices, volumes, benchmark, panel


def test_phase3_snapshot_is_observational_and_contains_stress_liquidity_radar_and_whatif():
    x, market = _returns(days=500, assets=8, seed=303)
    weights = np.array([.18, .16, .14, .12, .10, .08, .07, .05])
    cov = sample_covariance(x[-126:])
    original = weights.copy()
    snapshot = build_phase3_snapshot(
        weights, x, cov,
        symbols=[f"S{i}" for i in range(8)],
        config=Phase3Config(enabled=True),
        dates=[str(i) for i in range(len(x))],
        benchmark_history=market,
        expected_alpha=np.linspace(.01, .05, 8),
        adv_dollars=np.full(8, 50_000_000.0),
        capital=1_000_000.0,
    )
    assert np.allclose(weights, original)
    assert snapshot["enabled"]
    assert snapshot["status"] == "research_diagnostics_only"
    assert len(snapshot["historical_stress"]) >= 2
    assert len(snapshot["synthetic_stress"]) >= 4
    assert snapshot["liquidity_capacity"]["available"]
    assert snapshot["what_if"]["available"]
    assert "alerts" in snapshot["risk_radar"]
    json.dumps(snapshot, allow_nan=False)


def test_phase3_candidates_are_exact_requested_branch_combinations():
    phase15 = phase15_v203_candidate()
    phase15_p3 = phase15_phase3_candidate()
    all_phases = phase15_phase2_phase3_candidate()

    assert phase15.risk_supervisor.v203_enabled
    assert not phase15.factor_model.enabled
    assert not phase15.portfolio_optimizer.enabled
    assert not phase15.phase3.enabled

    assert phase15_p3.risk_supervisor.v203_enabled
    assert not phase15_p3.factor_model.enabled
    assert not phase15_p3.portfolio_optimizer.enabled
    assert phase15_p3.phase3.enabled

    assert all_phases.risk_supervisor.v203_enabled
    assert all_phases.factor_model.enabled
    assert all_phases.portfolio_optimizer.enabled
    assert all_phases.phase3.enabled


def test_phase3_diagnostics_do_not_change_phase15_targets():
    _, prices, volumes, benchmark, panel = _engine_inputs(seed=304)
    risk = v203_risk_config(v201_bootstrap_samples=0, v202_predictive_risk_enabled=False)
    phase15 = phase15_v203_candidate(risk_supervisor=risk)
    phase15_p3 = phase15_phase3_candidate(risk_supervisor=risk)

    baseline = portfolio_targets(prices, phase15, benchmark_returns=benchmark, volumes=volumes, precomputed_panel=panel)
    observed = portfolio_targets(prices, phase15_p3, benchmark_returns=benchmark, volumes=volumes, precomputed_panel=panel)
    assert np.allclose(baseline.to_numpy(), observed.to_numpy())
    assert observed.attrs["phase3_audit"]
    assert all(row["status"] == "research_diagnostics_only" for row in observed.attrs["phase3_audit"])


def test_phase3_runs_on_same_scheduled_rebalance_cadence_and_phase2_adds_factor_stress():
    dates, prices, volumes, benchmark, panel = _engine_inputs(seed=305)
    risk = v203_risk_config(v201_bootstrap_samples=0, v202_predictive_risk_enabled=False)
    plain = phase15_phase3_candidate(risk_supervisor=risk)
    combined = phase15_phase2_phase3_candidate(risk_supervisor=risk)

    plain_targets = portfolio_targets(prices, plain, benchmark_returns=benchmark, volumes=volumes, precomputed_panel=panel)
    combined_targets = portfolio_targets(prices, combined, benchmark_returns=benchmark, volumes=volumes, precomputed_panel=panel)
    periods = dates.to_period("W-FRI")
    expected = sum(i == 252 or periods[i] != periods[i - 1] for i in range(252, len(dates)))
    assert len(plain_targets.attrs["phase3_audit"]) == expected
    assert len(combined_targets.attrs["phase3_audit"]) == expected
    assert all(not row["factor_risk"]["available"] for row in plain_targets.attrs["phase3_audit"])
    factor_rows = [row for row in combined_targets.attrs["phase3_audit"] if row["factor_risk"]["available"]]
    assert factor_rows
    assert any(any(s["id"] == "synthetic_dominant_factor_shock" for s in row["synthetic_stress"]) for row in factor_rows)
    assert any(row["current_portfolio_factor_adjusted_alpha"]["available"] for row in factor_rows)


def test_realized_security_attribution_is_json_safe_and_reconciles_with_named_residual():
    dates = pd.bdate_range("2024-01-02", periods=5)
    held = pd.DataFrame({"A": [.5] * 5, "B": [.3] * 5}, index=dates)
    returns = pd.DataFrame({"A": [0, .01, -.02, .03, .01], "B": [0, -.01, .01, .02, -.02]}, index=dates)
    net = pd.Series([0, .002, -.006, .02, -.001], index=dates)
    result = realized_security_attribution(held, returns, net)
    assert "unexplained_execution_cash_cost_residual" in result
    json.dumps(result, allow_nan=False)
