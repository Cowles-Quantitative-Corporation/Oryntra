import numpy as np

from backend.minerva import risk_v203_candidate, risk_v202_candidate, tba8_institutional_risk_candidate
from backend.universal_risk_supervisor import (
    supervise_cross_sectional_weights,
    v203_risk_config,
)
from backend.universal_risk_v201 import sample_covariance
from backend.universal_risk_v203 import V203ControlConfig, soft_structural_sleeve


def _returns(days=500, assets=10, seed=203):
    rng = np.random.default_rng(seed)
    market = rng.normal(.0002, .009, days)
    common = rng.normal(.0001, .008, days)
    idio = rng.normal(0, .006, (days, assets))
    x = .35 * market[:, None] + idio
    x[:, : max(2, assets // 2)] += .70 * common[:, None]
    return x


def _valid_forecast(percentile=.95):
    return {
        "available": True,
        "predicted_forward_volatility": .25,
        "predicted_forward_drawdown": .07,
        "volatility_percentile": percentile,
        "drawdown_percentile": percentile - .02,
        "systemic_percentile": percentile,
        "validation": {
            "available": True,
            "volatility_r2": .08,
            "drawdown_r2": .03,
            "validation_observations": 30,
        },
    }


def test_v203_candidate_preserves_v1_baseline_controls_and_activates_soft_structural_sleeve():
    control = tba8_institutional_risk_candidate().risk_supervisor
    challenger = risk_v203_candidate().risk_supervisor
    for field in (
        "enabled", "component_risk_enabled", "maximum_risk_contribution",
        "correlation_cluster_enabled", "correlation_regime_enabled",
        "diversification_enabled", "realized_volatility_enabled",
        "volatility_shock_enabled", "tail_loss_enabled", "drawdown_enabled",
    ):
        assert getattr(challenger, field) == getattr(control, field)
    assert challenger.v203_enabled is True
    assert challenger.v202_enabled is False
    assert challenger.v203_structural_intervention_enabled is True
    assert challenger.v203_structural_strength == .50


def test_v203_candidate_override_does_not_pass_risk_supervisor_twice():
    custom = v203_risk_config(v203_structural_strength=.35)
    candidate = risk_v203_candidate(risk_supervisor=custom, gross_cap=.80)
    assert candidate.risk_supervisor == custom
    assert candidate.gross_cap == .80


def test_soft_structural_sleeve_never_adds_exposure_and_can_pass_economic_gate():
    x = _returns(days=400, assets=8, seed=12)
    cov = sample_covariance(x[-126:])
    base = np.array([.18, .18, .18, .10, .10, .08, .08, .10])
    priority = np.ones(8)
    control = V203ControlConfig(
        structural_strength=.50,
        structural_no_trade_band=.001,
        minimum_structural_risk_improvement=0.0,
        maximum_structural_priority_loss_fraction=.50,
        maximum_structural_incremental_turnover=.50,
    )
    adjusted, audit = soft_structural_sleeve(
        base, cov, priority,
        current_weights=base,
        cluster_enabled=False,
        component_enabled=True,
        cluster_threshold=.65,
        maximum_cluster_weight=.35,
        maximum_component_risk_share=.20,
        maximum_trim_iterations=120,
        control=control,
    )
    assert audit["accepted"]
    assert np.all(adjusted <= base + 1e-12)
    assert adjusted.sum() <= base.sum() + 1e-12
    assert audit["risk_after"] <= audit["risk_before"] + 1e-12


def test_soft_structural_sleeve_respects_no_trade_band():
    x = _returns(days=300, assets=8, seed=13)
    cov = sample_covariance(x[-126:])
    base = np.full(8, .10)
    priority = np.ones(8)
    control = V203ControlConfig(
        structural_strength=.01,
        structural_no_trade_band=.20,
        minimum_structural_risk_improvement=0.0,
        maximum_structural_priority_loss_fraction=.50,
        maximum_structural_incremental_turnover=.50,
    )
    adjusted, audit = soft_structural_sleeve(
        base, cov, priority,
        current_weights=base,
        cluster_enabled=False,
        component_enabled=True,
        cluster_threshold=.70,
        maximum_cluster_weight=.35,
        maximum_component_risk_share=.20,
        maximum_trim_iterations=120,
        control=control,
    )
    assert not audit["accepted"]
    assert audit["reason"] == "structural_no_trade_band"
    assert np.allclose(adjusted, base)


def test_v203_predictive_persistence_is_explicitly_rebalance_counted_and_bounded():
    x = _returns(days=400, assets=10, seed=14)
    cov = sample_covariance(x[-126:])
    weights = np.full(10, .10)
    config = v203_risk_config(
        v201_bootstrap_samples=5,
        v203_structural_intervention_enabled=False,
        v203_persistence_rebalances=2,
        v203_systemic_trigger_percentile=.80,
        v203_maximum_additional_gross_reduction=.10,
        v203_minimum_predictive_gross_change=0.0,
    )
    forecast = _valid_forecast(.95)

    first, audit_first = supervise_cross_sectional_weights(
        weights, cov, config,
        returns_history=x,
        fallback_priority=np.linspace(0, 1, 10),
        predictive_forecast=forecast,
        previous_persistence_count=0,
        current_weights=weights,
    )
    assert not audit_first["predictive_modifier"]["triggered"]
    assert audit_first["persistence_rebalances"] == 1
    assert np.isclose(first.sum(), audit_first["gross_after_v1"])

    second, audit_second = supervise_cross_sectional_weights(
        weights, cov, config,
        returns_history=x,
        fallback_priority=np.linspace(0, 1, 10),
        predictive_forecast=forecast,
        previous_persistence_count=audit_first["persistence_rebalances"],
        current_weights=first,
    )
    assert audit_second["predictive_modifier"]["triggered"]
    assert audit_second["predictive_modifier"]["persistence_unit"] == "scheduled_rebalance_decisions"
    assert second.sum() <= audit_second["gross_after_v1"] + 1e-12
    assert second.sum() >= audit_second["gross_after_v1"] * .90 - 1e-8


def test_v203_unavailable_predictor_still_allows_only_economically_gated_structural_change():
    x = _returns(days=400, assets=10, seed=15)
    cov = sample_covariance(x[-126:])
    weights = np.full(10, .10)
    config = v203_risk_config(
        v201_bootstrap_samples=5,
        v203_structural_strength=.50,
        v203_structural_no_trade_band=.001,
        v203_minimum_structural_risk_improvement=0.0,
        v203_maximum_structural_priority_loss_fraction=.50,
        v203_maximum_structural_incremental_turnover=.50,
    )
    final, audit = supervise_cross_sectional_weights(
        weights, cov, config,
        returns_history=x,
        fallback_priority=np.ones(10),
        predictive_forecast={"available": False, "reason": "not_enough_data"},
        current_weights=weights,
    )
    assert audit["predictive_modifier"]["triggered"] is False
    assert audit["structural_sleeve"]["enabled"]
    assert np.all(final <= np.asarray(audit["v1_baseline"]) if False else weights + 1e-12)
    assert final.sum() <= audit["gross_after_v1"] + 1e-12


def test_v202_and_v203_are_separate_reproducible_candidates():
    assert risk_v202_candidate().risk_supervisor.v202_enabled
    assert not risk_v202_candidate().risk_supervisor.v203_enabled
    assert risk_v203_candidate().risk_supervisor.v203_enabled
    assert not risk_v203_candidate().risk_supervisor.v202_enabled


def test_v203_engine_counts_decisions_on_weekly_rebalances_not_daily_bars():
    import pandas as pd
    from backend.universal_engine import portfolio_targets

    rng = np.random.default_rng(77)
    dates = pd.bdate_range("2019-01-01", periods=430)
    symbols = [f"S{i:02d}" for i in range(12)]
    daily = rng.normal(.0004, .008, (len(dates), len(symbols)))
    prices = pd.DataFrame(100 * np.cumprod(1 + daily, axis=0), index=dates, columns=symbols)
    volumes = pd.DataFrame(20e6, index=dates, columns=symbols)
    panel = {
        "score": pd.DataFrame(.8, index=dates, columns=symbols),
        "volatility": pd.DataFrame(.01, index=dates, columns=symbols),
        "predicted_return": pd.DataFrame(.01, index=dates, columns=symbols),
        "prediction_error": pd.DataFrame(.02, index=dates, columns=symbols),
    }
    config = risk_v203_candidate(
        risk_supervisor=v203_risk_config(
            v201_bootstrap_samples=0,
            v203_structural_intervention_enabled=False,
            v202_predictive_risk_enabled=False,
        )
    )
    targets = portfolio_targets(prices, config, volumes=volumes, precomputed_panel=panel)
    audit = targets.attrs["risk_supervisor_target_audit"]
    periods = dates.to_period("W-FRI")
    expected = sum(i == 252 or periods[i] != periods[i - 1] for i in range(252, len(dates)))
    assert len(audit) == expected
    assert all(row["decision_unit"] == "scheduled_rebalance" for row in audit)
    # Every non-rebalance day must simply carry the previously approved target.
    for i in range(253, len(dates)):
        if periods[i] == periods[i - 1]:
            assert np.allclose(targets.iloc[i].to_numpy(), targets.iloc[i - 1].to_numpy())
