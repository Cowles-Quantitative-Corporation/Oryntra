import numpy as np
import pandas as pd

from backend.minerva import (
    minerva_baseline,
    tba1_ohlcv_structure_candidate,
    tba2_qlib_risk_candidate,
    tba3_market_residual_target_candidate,
    tba4_completed_ic_gate_candidate,
    tba5_residual_momentum_candidate,
    tba6_residual_lifecycle_candidate,
    tba8_institutional_risk_candidate,
)
from backend.portfolio_execution import simulate_book
from backend.universal_engine import UniversalConfig
from backend.universal_risk_supervisor import (
    RiskSupervisorConfig,
    dynamic_exposure_scale,
    supervise_cross_sectional_weights,
)


def test_every_named_tba_candidate_enables_supervisor_but_minerva_does_not():
    assert minerva_baseline().risk_supervisor.enabled is False
    candidates = (
        tba1_ohlcv_structure_candidate(),
        tba2_qlib_risk_candidate(),
        tba3_market_residual_target_candidate(),
        tba4_completed_ic_gate_candidate(),
        tba5_residual_momentum_candidate(),
        tba6_residual_lifecycle_candidate(),
        tba8_institutional_risk_candidate(),
    )
    assert all(candidate.risk_supervisor.enabled for candidate in candidates)
    assert all(candidate.risk_supervisor.component_risk_enabled for candidate in candidates)
    assert all(candidate.risk_supervisor.maximum_risk_contribution == .10 for candidate in candidates)
    assert all(not candidate.risk_supervisor.drawdown_enabled for candidate in candidates)


def test_cluster_and_component_caps_only_reduce_risky_concentration():
    config = RiskSupervisorConfig(enabled=True, maximum_cluster_weight=.35,
                                  maximum_risk_contribution=.30,
                                  minimum_effective_bets=1)
    weights = np.array([.40, .30, .20, .10])
    covariance = np.array([
        [.040, .035, .000, .000],
        [.035, .040, .000, .000],
        [.000, .000, .010, .000],
        [.000, .000, .000, .010],
    ])
    adjusted, audit = supervise_cross_sectional_weights(weights, covariance, config)
    assert adjusted[:2].sum() <= .35 + 1e-12
    assert np.all(adjusted >= 0)
    assert adjusted.sum() <= weights.sum()
    assert audit["cluster_count"] == 3


def test_high_average_correlation_scales_the_whole_book_to_cash():
    config = RiskSupervisorConfig(
        enabled=True,
        maximum_cluster_weight=1,
        maximum_risk_contribution=.50,
        maximum_average_correlation=.40,
        correlation_regime_floor_scale=.20,
        minimum_effective_bets=1,
    )
    weights = np.full(4, .25)
    covariance = np.full((4, 4), .032)
    np.fill_diagonal(covariance, .040)
    adjusted, audit = supervise_cross_sectional_weights(weights, covariance, config)
    assert np.isclose(audit["average_positive_correlation"], .8)
    assert np.isclose(audit["correlation_regime_scale"], .5)
    assert np.isclose(adjusted.sum(), .5)


def test_dynamic_scale_uses_completed_volatility_and_drawdown_only():
    config = RiskSupervisorConfig(enabled=True, realized_volatility_window=10,
                                  volatility_shock_short_window=5,
                                  drawdown_lookback_sessions=21)
    scale, audit = dynamic_exposure_scale([.04, -.04] * 5, [100, 101, 99, 96, 94], .12, config)
    assert config.realized_volatility_floor_scale <= scale < 1
    assert audit["realized_volatility"] > .12
    assert audit["drawdown"] > 0
    unchanged, _ = dynamic_exposure_scale([.04, -.04] * 5 + [.90], [100, 101, 99, 96, 94] + [10], .12, config)
    assert scale != unchanged


def test_dynamic_scale_detects_completed_tail_loss_and_volatility_acceleration():
    config = RiskSupervisorConfig(
        enabled=True,
        realized_volatility_window=21,
        volatility_shock_short_window=5,
        tail_loss_window=21,
        tail_probability=.10,
    )
    calm_then_stress = [0.0] * 16 + [-.06, .04, -.05, .03, -.04]
    scale, audit = dynamic_exposure_scale(calm_then_stress, [100.0] * 22, .12, config)
    assert scale < 1
    assert audit["volatility_shock_ratio"] > config.volatility_shock_threshold
    assert audit["expected_shortfall"] > 0
    assert audit["tail_loss_scale"] < 1


def test_execution_audits_and_rebalances_a_drawdown_reduction():
    dates = pd.bdate_range("2022-01-03", periods=45)
    close = np.r_[np.full(25, 100.0), np.linspace(100, 80, 20)]
    history = {"AAA": pd.DataFrame({"Open": close, "Close": close, "Volume": 1e9}, index=dates)}
    targets = pd.DataFrame(0.0, index=dates, columns=["AAA"])
    targets.iloc[20:] = .8
    config = UniversalConfig(
        initial_equity=10_000,
        cost_bps=0,
        impact_bps=0,
        trade_buffer=0,
        vol_target=.12,
        risk_supervisor=RiskSupervisorConfig(
            enabled=True,
            realized_volatility_window=10,
            volatility_shock_short_window=5,
            drawdown_lookback_sessions=21,
            scale_rebalance_step=.01,
        ),
    )
    book = simulate_book(history, targets, config)
    scales = [row["scale"] for row in book["risk_supervisor_audit"]]
    assert min(scales) < 1
    assert book["held"].iloc[-1, 0] < .8
    assert all(trade["signal_date"] < trade["date"] for trade in book["fills"])
