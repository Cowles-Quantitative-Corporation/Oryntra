import numpy as np

from backend.universal_optimizer import PortfolioOptimizerConfig, optimize_portfolio


def _snapshot():
    exposures = np.array([
        [1.2, 1.0, 1.0, 0.0],
        [1.1, .8, 1.0, 0.0],
        [.9, -.4, 0.0, 1.0],
        [.8, -.6, 0.0, 1.0],
    ])
    factor_names = ["market", "momentum", "sector:Tech", "sector:Financials"]
    f = np.diag([.00008, .00004, .00003, .00003])
    idio = np.full(4, .00003)
    asset_cov = exposures @ f @ exposures.T + np.diag(idio)
    return {
        "available": True,
        "factor_names": factor_names,
        "exposures": exposures,
        "factor_covariance": f,
        "idiosyncratic_variance": idio,
        "asset_covariance": asset_cov,
    }


def test_optimizer_obeys_long_only_gross_name_sector_and_turnover_constraints():
    proposed = np.array([.30, .25, .20, .15])
    current = np.array([.20, .20, .20, .20])
    config = PortfolioOptimizerConfig(
        enabled=True,
        maximum_sector_weight=.40,
        maximum_one_way_turnover=.25,
        minimum_portfolio_change=0.0,
        maximum_absolute_style_exposure=None,
        iterations=120,
    )
    final, audit = optimize_portfolio(
        proposed,
        factor_snapshot=_snapshot(),
        expected_alpha=np.array([.02, .01, .04, .03]),
        prediction_error=np.full(4, .02),
        fallback_priority=np.ones(4),
        current_weights=current,
        gross_cap=.80,
        name_cap=.25,
        maximum_market_beta=1.0,
        adv_dollars=np.full(4, 100_000_000.0),
        capital=1_000_000.0,
        participation=.02,
        config=config,
    )
    assert np.all(final >= -1e-12)
    assert np.all(final <= .25 + 1e-10)
    assert final.sum() <= .80 + 1e-10
    assert final[:2].sum() <= .40 + 1e-8
    assert np.abs(final - current).sum() <= .25 + 1e-8
    assert audit["enabled"]


def test_optimizer_falls_back_when_factor_model_is_unavailable():
    proposed = np.array([.2, .2, .2])
    final, audit = optimize_portfolio(
        proposed,
        factor_snapshot={"available": False, "reason": "test"},
        expected_alpha=None,
        prediction_error=None,
        fallback_priority=np.ones(3),
        current_weights=np.zeros(3),
        gross_cap=1.0,
        name_cap=.5,
        maximum_market_beta=None,
        adv_dollars=None,
        capital=1_000_000,
        participation=.02,
        config=PortfolioOptimizerConfig(enabled=True),
    )
    assert np.allclose(final, proposed)
    assert not audit["accepted"]
    assert audit["reason"] == "factor_model_unavailable"
