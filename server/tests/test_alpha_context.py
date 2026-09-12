from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from backend.alpha_consistency import consistency_scorecard
from backend.portfolio_execution import simulate_book
from backend.universal_engine import UniversalConfig
from backend.universal_market_context import MarketContextConfig, apply_market_context, build_completed_market_observations


def fixture_returns(start="2014-01-01", end="2025-12-31"):
    dates = pd.bdate_range(start, end)
    rng = np.random.default_rng(183)
    b = pd.Series(rng.normal(.0003, .01, len(dates)), index=dates)
    rf = pd.Series(.00006, index=dates)
    s = rf + .07 / 252 + .8 * (b - rf)
    return s, b, rf


def test_prior_month_beta_and_complete_months_are_required():
    s, b, rf = fixture_returns()
    sample = s.loc["2020":"2021"]
    got = consistency_scorecard(sample, b, rf, prior_strategy=s.loc[:"2019"])
    assert got["two_year"]["passes"]
    assert got["pooled"]["alpha_pct"] == pytest.approx(7)
    assert got["monthly"][0]["beta"] == pytest.approx(.8)
    assert got["monthly"][0]["beta_estimated_through"] == "2019-12-31"
    no_prior = consistency_scorecard(sample, b, rf)
    assert not no_prior["two_year"]["passes"]
    assert no_prior["unavailable_months"] > 0
    changed = s.copy()
    changed.loc["2020-01"] += .01
    altered = consistency_scorecard(changed.loc["2020":"2021"], b, rf, prior_strategy=s.loc[:"2019"])
    assert altered["monthly"][0]["beta"] == got["monthly"][0]["beta"]
    # A dropped exchange session cannot disappear from the completeness gate.
    missing = consistency_scorecard(sample.drop(sample.index[10]), b, rf, prior_strategy=s.loc[:"2019"])
    assert not missing["two_year"]["passes"]


def test_negative_month_gate_is_residual_alpha_not_raw_return():
    s, b, rf = fixture_returns()
    s.loc["2020-02"] -= .015
    s.loc["2020-05"] -= .015
    s.loc["2021-02"] -= .015
    got = consistency_scorecard(s.loc["2020":"2021"], b, rf, prior_strategy=s.loc[:"2019"])
    assert got["negative_alpha_months"] >= 3
    assert not got["two_year"]["criteria"]["at_most_two_negative_alpha_months"]


def test_weak_years_are_double_weighted_without_modifying_regressions():
    s, b, rf = fixture_returns()
    alpha = pd.Series(.08 / 252, index=s.index)
    alpha.loc["2016"] = -.01 / 252
    alpha.loc["2017"] = .01 / 252
    s = rf + alpha + .8 * (b - rf)
    got = consistency_scorecard(s.loc["2016":], b, rf, prior_strategy=s.loc[:"2015"])
    assert got["mean_annual_alpha_pct"] == pytest.approx(6.4)
    assert got["stress_weighted_mean_alpha_pct"] == pytest.approx(64 / 12)
    assert got["ten_year"]["passes"]
    assert not consistency_scorecard(s.loc["2015":], b, rf)["ten_year"]["passes"]
    alpha.loc["2018"] = -.01 / 252
    got = consistency_scorecard((rf + alpha + .8 * (b - rf)).loc["2016":], b, rf)
    assert not got["ten_year"]["criteria"]["at_most_one_negative_year"]


def observations(dates):
    stamps = [(d + pd.Timedelta(hours=16)).tz_localize("America/New_York").isoformat() for d in dates]
    return pd.DataFrame({"market_return": -.12, "breadth": .25, "median_pairwise_correlation": .85,
                         "observed_at": stamps, "available_at": stamps, "decision_at": stamps,
                         "source_manifest_id": "synthetic-fixture", "lookback_sessions": 63}, index=dates)


def test_context_scales_once_uses_only_available_data_and_preserves_baseline():
    dates = pd.bdate_range("2020-01-01", periods=300)
    targets = pd.DataFrame(.5, index=dates, columns=["AAA"])
    obs = observations(dates)
    config = MarketContextConfig(enabled=True)
    adjusted, gate, audit = apply_market_context(targets, obs, config)
    assert (adjusted.iloc[252:] == .175).all().all()
    assert not gate.iloc[252:].any()
    assert all(r["timing"] == "next_open" for r in audit)
    baseline, _, _ = apply_market_context(targets, None, MarketContextConfig())
    pd.testing.assert_frame_equal(baseline, targets)
    future = obs.copy()
    future.loc[dates[285]:, "market_return"] = .2
    future.loc[dates[285]:, "breadth"] = .8
    future.loc[dates[285]:, "median_pairwise_correlation"] = .2
    pd.testing.assert_frame_equal(adjusted.iloc[:285], apply_market_context(targets, future, config)[0].iloc[:285])
    obs.loc[dates[252], "available_at"] = (pd.Timestamp(obs.iloc[252].decision_at) + pd.Timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="available"):
        apply_market_context(targets, obs, config)
    with pytest.raises(ValueError, match="requires"):
        apply_market_context(targets, None, config)
    with pytest.raises(ValueError):
        MarketContextConfig(minimum_confirming_signals=1)


def test_entry_gate_only_affects_next_open_and_release_allows_entries():
    dates = pd.bdate_range("2020-01-01", periods=40)
    h = {"AAA": pd.DataFrame({"Open": 100., "Close": 100., "Volume": 1e7}, index=dates)}
    targets = pd.DataFrame(0., index=dates, columns=["AAA"])
    targets.iloc[20:] = .5
    gates = pd.Series(True, index=dates)
    gates.iloc[20:25] = False
    cfg = UniversalConfig(cost_bps=10, impact_bps=0, initial_equity=1000)
    book = simulate_book(h, targets, cfg, entry_allowed=gates)
    assert len(book["fills"]) == 1
    assert book["fills"][0]["date"] == str(dates[26].date())
    assert book["fills"][0]["fees"] == pytest.approx(.5)
    assert book["cash"] == pytest.approx(499.5)


def test_study_market_observations_average_spy_qqq_and_do_not_use_future_bars():
    dates = pd.bdate_range("2020-01-01", periods=100)
    rng = np.random.default_rng(9)
    returns = rng.normal(.0003, .01, (100, 5))
    closes = pd.DataFrame(100 * np.exp(np.cumsum(returns, axis=0)), index=dates,
                          columns=["AAA", "BBB", "CCC", "SPY", "QQQ"])
    config = MarketContextConfig(enabled=True, market_lookback_sessions=21)
    baseline = build_completed_market_observations(closes, config, source_manifest_id="frozen-fixture")
    changed = closes.copy()
    changed.iloc[80:, :] *= np.linspace(1, 4, 20)[:, None]
    altered = build_completed_market_observations(changed, config, source_manifest_id="frozen-fixture")
    pd.testing.assert_frame_equal(baseline.loc[:dates[79]], altered.loc[:dates[79]])
    expected = ((closes.SPY.iloc[21] / closes.SPY.iloc[0] - 1) + (closes.QQQ.iloc[21] / closes.QQQ.iloc[0] - 1)) / 2
    assert baseline.iloc[0].market_return == pytest.approx(expected)
    assert baseline.iloc[0].observed_at == baseline.iloc[0].decision_at
