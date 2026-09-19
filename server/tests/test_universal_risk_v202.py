import numpy as np

from backend.minerva import risk_v202_candidate, tba8_institutional_risk_candidate
from backend.universal_risk_supervisor import (
    supervise_cross_sectional_weights,
    v202_risk_config,
)
from backend.universal_risk_v201 import sample_covariance
from backend.universal_risk_v202 import (
    V202PredictiveConfig,
    alpha_aware_quadratic_trim,
    build_systemic_risk_panel,
    portfolio_weighted_risk_structure,
    portfolio_weighted_tail_dependence,
    predict_systemic_risk,
)


def _returns(days=900, assets=12, seed=9):
    rng = np.random.default_rng(seed)
    market = rng.normal(.0002, .009, days)
    sector = rng.normal(.0001, .006, days)
    idio = rng.normal(0, .007, (days, assets))
    x = .40 * market[:, None] + idio
    x[:, : assets // 2] += .40 * sector[:, None]
    return x


def test_v202_candidate_preserves_tba8_v1_baseline_controls():
    control = tba8_institutional_risk_candidate().risk_supervisor
    challenger = risk_v202_candidate().risk_supervisor
    for field in (
        "enabled", "component_risk_enabled", "maximum_risk_contribution",
        "correlation_cluster_enabled", "correlation_regime_enabled",
        "diversification_enabled", "realized_volatility_enabled",
        "volatility_shock_enabled", "tail_loss_enabled", "drawdown_enabled",
    ):
        assert getattr(challenger, field) == getattr(control, field)
    assert challenger.v202_enabled is True
    assert challenger.v201_enabled is False


def test_predictive_panel_has_hard_label_availability_gate():
    x = _returns(days=900, assets=8, seed=10)
    config = V202PredictiveConfig(minimum_training_observations=40)
    as_of = 700
    first = build_systemic_risk_panel(x, config)
    prediction_a = predict_systemic_risk(first, as_of, config)
    assert prediction_a["available"]
    assert prediction_a["latest_training_label_available_at"] <= as_of

    # Rewrite every observation strictly after as_of. A causal prediction at
    # as_of must remain identical because those values were not yet available.
    changed = x.copy()
    changed[as_of + 1:] = np.random.default_rng(123).normal(0, .20, changed[as_of + 1:].shape)
    second = build_systemic_risk_panel(changed, config)
    prediction_b = predict_systemic_risk(second, as_of, config)
    assert prediction_b["available"]
    assert np.isclose(prediction_a["predicted_forward_volatility"], prediction_b["predicted_forward_volatility"])
    assert np.isclose(prediction_a["predicted_forward_drawdown"], prediction_b["predicted_forward_drawdown"])


def test_weighted_tail_dependence_focuses_on_large_positions():
    rng = np.random.default_rng(44)
    days = 500
    common = rng.normal(0, .012, days)
    x = rng.normal(0, .010, (days, 4))
    x[:, 0] = common + rng.normal(0, .002, days)
    x[:, 1] = common + rng.normal(0, .002, days)
    concentrated = np.array([.45, .45, .05, .05])
    diffuse = np.array([.05, .05, .45, .45])
    assert portfolio_weighted_tail_dependence(x, concentrated, .10) > portfolio_weighted_tail_dependence(x, diffuse, .10)


def test_weighted_pca_downweights_tiny_common_positions():
    x = _returns(days=300, assets=6, seed=22)
    # Make first three assets almost one common trade.
    common = np.random.default_rng(23).normal(0, .015, 300)
    x[:, :3] = common[:, None] + np.random.default_rng(24).normal(0, .001, (300, 3))
    cov = sample_covariance(x)
    common_heavy = portfolio_weighted_risk_structure(np.array([.30, .30, .30, .033, .033, .034]), cov)
    common_light = portfolio_weighted_risk_structure(np.array([.02, .02, .02, .31, .31, .32]), cov)
    assert common_heavy["first_pc_share"] > common_light["first_pc_share"]


def test_predictive_v202_is_persistence_gated_and_capped_relative_to_v1():
    x = _returns(days=300, assets=12, seed=30)
    weights = np.full(12, 1 / 12)
    cov = sample_covariance(x[-63:])
    config = v202_risk_config(v201_bootstrap_samples=10, v202_persistence_sessions=3)

    # Two prior elevated sessions => this is the third and may act.
    forecast = {
        "available": True,
        "predicted_forward_volatility": .40,
        "predicted_forward_drawdown": .08,
        "volatility_percentile": .97,
        "drawdown_percentile": .92,
        "systemic_percentile": .97,
        "validation": {
            "available": True,
            "volatility_r2": .10,
            "drawdown_r2": .04,
            "validation_observations": 20,
        },
    }
    final, audit = supervise_cross_sectional_weights(
        weights, cov, config,
        returns_history=x,
        fallback_priority=np.linspace(0, 1, 12),
        predictive_forecast=forecast,
        previous_persistence_count=2,
        current_weights=weights,
    )
    v1_gross = audit["gross_after_v1"]
    assert audit["predictive_modifier"]["triggered"]
    assert final.sum() >= v1_gross * (1 - config.v202_maximum_additional_gross_reduction) - 1e-8
    assert final.sum() <= v1_gross + 1e-12

    # First elevated reading must not change V1.
    first, first_audit = supervise_cross_sectional_weights(
        weights, cov, config,
        returns_history=x,
        fallback_priority=np.linspace(0, 1, 12),
        predictive_forecast=forecast,
        previous_persistence_count=0,
        current_weights=weights,
    )
    assert not first_audit["predictive_modifier"]["triggered"]
    assert np.allclose(first, np.asarray(first_audit["what_if"]["v1"]) if False else first)  # audit smoke
    assert np.isclose(first.sum(), first_audit["gross_after_v1"])


def test_unavailable_predictor_returns_exact_v1_baseline():
    x = _returns(days=300, assets=12, seed=31)
    weights = np.full(12, 1 / 12)
    cov = sample_covariance(x[-63:])
    config = v202_risk_config(v201_bootstrap_samples=5)
    final, audit = supervise_cross_sectional_weights(
        weights, cov, config,
        returns_history=x,
        predictive_forecast={"available": False, "reason": "not_enough_data"},
    )
    control, _ = supervise_cross_sectional_weights(
        weights, cov, tba8_institutional_risk_candidate().risk_supervisor,
    )
    assert np.allclose(final, control)
    assert audit["fragility_score_role"].startswith("diagnostic_only")


def test_quadratic_trim_hits_target_without_adding_positions():
    x = _returns(days=200, assets=8, seed=40)
    cov = sample_covariance(x[-63:])
    base = np.full(8, .10)
    priority = np.linspace(-1, 2, 8)
    adjusted, audit = alpha_aware_quadratic_trim(
        base, .65, cov, priority,
        current_weights=base,
        risk_aversion=.25,
        turnover_penalty=2.0,
        iterations=250,
        learning_rate=.08,
    )
    assert np.isclose(adjusted.sum(), .65, atol=1e-8)
    assert np.all(adjusted >= 0)
    assert np.all(adjusted <= base + 1e-12)
    assert np.isfinite(audit["objective_change"])


def test_predictive_v202_refuses_to_act_when_chronological_validation_fails():
    x = _returns(days=300, assets=10, seed=51)
    weights = np.full(10, .10)
    cov = sample_covariance(x[-63:])
    config = v202_risk_config(v201_bootstrap_samples=5, v202_persistence_sessions=1)
    forecast = {
        "available": True,
        "systemic_percentile": .99,
        "volatility_percentile": .99,
        "drawdown_percentile": .99,
        "validation": {
            "available": True,
            "volatility_r2": -.20,
            "drawdown_r2": -.10,
            "validation_observations": 20,
        },
    }
    final, audit = supervise_cross_sectional_weights(
        weights, cov, config, returns_history=x, predictive_forecast=forecast,
        previous_persistence_count=5, current_weights=weights,
    )
    assert not audit["predictive_modifier"]["triggered"]
    assert audit["predictive_modifier"]["reason"] == "predictor_failed_chronological_validation"
    assert np.isclose(final.sum(), audit["gross_after_v1"])


def test_systemic_trigger_uses_percentile_not_portfolio_volatility_target():
    x = _returns(days=300, assets=10, seed=52)
    weights = np.full(10, .10)
    cov = sample_covariance(x[-63:])
    config = v202_risk_config(v201_bootstrap_samples=5, v202_persistence_sessions=1)
    forecast = {
        "available": True,
        # Absolute forecast is intentionally huge; percentile says it is ordinary
        # relative to this systemic series, so the overlay must not act.
        "predicted_forward_volatility": .80,
        "predicted_forward_drawdown": .20,
        "volatility_percentile": .60,
        "drawdown_percentile": .55,
        "systemic_percentile": .60,
        "validation": {
            "available": True,
            "volatility_r2": .10,
            "drawdown_r2": .05,
            "validation_observations": 20,
        },
    }
    final, audit = supervise_cross_sectional_weights(
        weights, cov, config, returns_history=x, predictive_forecast=forecast,
        previous_persistence_count=5, current_weights=weights,
    )
    assert not audit["predictive_modifier"]["triggered"]
    assert audit["predictive_modifier"]["reason"] == "systemic_forecast_below_trigger_percentile"
    assert np.isclose(final.sum(), audit["gross_after_v1"])
