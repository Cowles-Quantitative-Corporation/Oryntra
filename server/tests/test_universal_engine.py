from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from backend.alpha_evaluation import alpha_scorecard, regression_alpha
from backend.backtest import _check_exit, BacktestRequest, _run_one
from backend.portfolio_execution import simulate_book
from backend.universal_engine import UniversalConfig, portfolio_targets, scan_snapshot, signal_panel
from backend.universal_research import run_universal
from backend.setup_detector import detect_setup
from backend.routes.analysis import _compute_scan_artifacts
from backend.routes.universal import UniversalRequest, evaluate_upload
from backend.universal_position_policy import PositionPolicyConfig, evaluate_close, evaluate_precommitted_daily_bar, open_position, policy_contract
from backend.universal_market_context import CompletedMarketContext, MarketContextConfig, evaluate_market_context, market_context_contract
from backend.universal_peer_shock import PeerShockConfig, peer_shock_contract
from backend.universal_fundamentals import build_fundamental_acceleration_panel, build_fundamental_score_panel
from backend.universal_research_blueprint import research_blueprint
from backend.universal_taxonomy import FamilyExposure, family_shock_pressure, load_family_catalog, taxonomy_contract, validate_exposures
from backend.minerva import MINERVA_ID, MINERVA_STATUS, minerva_baseline, minerva_beta_ceiling_candidate, minerva_contract, minerva_economic_interactions_candidate, minerva_residual_momentum_candidate, minerva_score_persistence_candidate, tba1_ohlcv_structure_candidate, tba3_market_residual_target_candidate, tba4_completed_ic_gate_candidate, tba5_residual_momentum_candidate, tba6_residual_lifecycle_candidate


def histories(n=340):
    dates = pd.bdate_range("2015-01-01", periods=n)
    rng = np.random.default_rng(431)
    out = {}
    for symbol in ("AAA", "BBB", "CCC"):
        close = 100 * np.exp(np.cumsum(rng.normal(.001, .009, n)))
        opening = np.r_[close[0], close[:-1]]
        out[symbol] = pd.DataFrame({"Open": opening, "High": np.maximum(opening, close) * 1.01,
                                    "Low": np.minimum(opening, close) * .99,
                                    "Close": close, "Volume": 1e7}, index=dates)
    return out


def test_future_bars_cannot_change_features_targets_or_scan():
    h = histories(430)
    prices = pd.DataFrame({k: v.Close for k, v in h.items()})
    changed = prices.copy()
    changed.iloc[330:] *= np.linspace(1, 5, 100)[:, None]
    for key in signal_panel(prices):
        pd.testing.assert_frame_equal(signal_panel(prices)[key].iloc[:330], signal_panel(changed)[key].iloc[:330])
    pd.testing.assert_frame_equal(portfolio_targets(prices).iloc[:330], portfolio_targets(changed).iloc[:330])
    snapshot = scan_snapshot(h["AAA"].iloc[:320])
    assert snapshot["score"] == signal_panel(prices.iloc[:320])['score'].iloc[-1]['AAA']
    assert not portfolio_targets(prices).iloc[:252].to_numpy().any()


def test_residual_strength_requires_completed_benchmark_and_remains_causal():
    h = histories(520)
    prices = pd.DataFrame({k: v.Close for k, v in h.items()})
    market = pd.Series(np.linspace(-.001, .001, len(prices)), index=prices.index)
    config = UniversalConfig(trend_weight=.35, momentum_weight=.25, breakout_weight=.10,
                             pullback_weight=.10, residual_weight=.20)
    with pytest.raises(ValueError, match="benchmark"):
        signal_panel(prices, config)
    baseline = signal_panel(prices, config, market)
    changed = prices.copy()
    changed.iloc[400:] *= np.linspace(1, 3, len(changed) - 400)[:, None]
    altered = signal_panel(changed, config, market)
    for key in baseline:
        pd.testing.assert_frame_equal(baseline[key].iloc[:400], altered[key].iloc[:400])
    assert baseline["residual"].iloc[-1].notna().all()
    assert scan_snapshot(h["AAA"], config)["status"] == "benchmark_required_for_residual_strength"


def test_fundamental_scores_start_on_the_filing_date_and_require_explicit_research_data():
    dates = pd.bdate_range("2020-01-01", periods=30)
    events = [{"symbol": "AAA", "available_date": str(dates[12].date()), "revenue_growth": .30, "net_income_growth": .50}]
    panel = build_fundamental_score_panel(events, dates, ["AAA", "BBB"])
    assert panel.loc[:dates[11], "AAA"].eq(0).all()
    assert panel.loc[dates[12]:, "AAA"].gt(0).all()
    assert panel["BBB"].eq(0).all()
    h = histories(520)
    prices = pd.DataFrame({k: v.Close for k, v in h.items()})
    config = UniversalConfig(trend_weight=.36, momentum_weight=.315, breakout_weight=.135,
                             pullback_weight=.09, fundamental_weight=.10)
    with pytest.raises(ValueError, match="fundamental"):
        signal_panel(prices, config)
    scores = build_fundamental_score_panel([], prices.index, prices.columns)
    assert signal_panel(prices, config, fundamental_scores=scores)["fundamental"].eq(0).all().all()
    assert scan_snapshot(h["AAA"], config)["status"] == "fundamental_data_required"


def test_minerva_filing_acceleration_is_causal_decaying_and_opt_in_for_ridge():
    dates = pd.bdate_range("2020-01-01", periods=40)
    events = [
        {"symbol": "AAA", "available_date": str(dates[8].date()), "revenue_growth": .10, "net_income_growth": .10},
        {"symbol": "AAA", "available_date": str(dates[16].date()), "revenue_growth": .50, "net_income_growth": .50},
    ]
    acceleration = build_fundamental_acceleration_panel(events, dates, ["AAA", "BBB"], decay_sessions=5)
    assert acceleration.loc[:dates[15], "AAA"].eq(0).all()  # First public filing establishes the baseline.
    assert acceleration.loc[dates[16], "AAA"] > 0
    assert acceleration.loc[dates[17], "AAA"] < acceleration.loc[dates[16], "AAA"]
    h = histories(850)
    prices = pd.DataFrame({name: frame.Close for name, frame in h.items()})
    opens = pd.DataFrame({name: frame.Open for name, frame in h.items()})
    volumes = pd.DataFrame({name: frame.Volume for name, frame in h.items()})
    quality = pd.DataFrame(.5, index=prices.index, columns=prices.columns)
    changes = pd.DataFrame(.25, index=prices.index, columns=prices.columns)
    config = UniversalConfig(alpha_model="walk_forward_ridge", ridge_training_sessions=252,
                             ridge_retrain_sessions=21, ridge_horizon_sessions=5, ridge_penalty=10,
                             fundamental_quality_overlay_weight=.20,
                             fundamental_acceleration_overlay_weight=.20)
    panel = signal_panel(prices, config, fundamental_scores=quality, fundamental_acceleration_scores=changes,
                         opens=opens, volumes=volumes)
    expected = (.60 * panel["learned"].iloc[-1] + .20 * quality.iloc[-1] + .20 * changes.iloc[-1]).clip(-1, 1)
    pd.testing.assert_series_equal(panel["score"].iloc[-1], expected, check_names=False)
    with pytest.raises(ValueError, match="acceleration"):
        signal_panel(prices, config, fundamental_scores=quality, opens=opens, volumes=volumes)


def test_minerva_corporate_overlay_requires_explicit_observed_coverage_mask():
    h = histories(850)
    prices = pd.DataFrame({name: frame.Close for name, frame in h.items()})
    benchmark = prices.mean(axis=1).pct_change(fill_method=None)
    quality = pd.DataFrame(.10, index=prices.index, columns=prices.columns)
    config = minerva_baseline(fundamental_quality_overlay_weight=.20, ridge_training_sessions=252, ridge_penalty=10)
    with pytest.raises(ValueError, match="fundamental_observed"):
        run_universal(h, config, benchmark, pd.Series(0., index=prices.index), evaluation_start=str(prices.index[380].date()),
                      fundamental_scores=quality)
    sparse = pd.DataFrame(False, index=prices.index, columns=prices.columns)
    sparse.iloc[-1] = True
    with pytest.raises(ValueError, match="coverage"):
        run_universal(h, config, benchmark, pd.Series(0., index=prices.index), evaluation_start=str(prices.index[380].date()),
                      fundamental_scores=quality, fundamental_observed=sparse)


def test_minerva_baseline_is_frozen_price_only_and_not_release_approved():
    config = minerva_baseline()
    assert config.alpha_model == "walk_forward_ridge"
    assert config.ridge_training_sessions == 756
    assert config.maximum_positions == 24
    assert config.fundamental_quality_overlay_weight == 0
    assert config.fundamental_acceleration_overlay_weight == 0
    contract = minerva_contract()
    assert contract["id"] == MINERVA_ID
    assert contract["status"] == MINERVA_STATUS == "candidate_not_release_approved"


def test_minerva_residual_momentum_uses_only_completed_benchmark_history():
    h = histories(850)
    prices = pd.DataFrame({name: frame.Close for name, frame in h.items()})
    opens = pd.DataFrame({name: frame.Open for name, frame in h.items()})
    volumes = pd.DataFrame({name: frame.Volume for name, frame in h.items()})
    benchmark = prices.mean(axis=1).pct_change(fill_method=None)
    config = minerva_residual_momentum_candidate(ridge_training_sessions=252, ridge_penalty=10)
    with pytest.raises(ValueError, match="benchmark"):
        signal_panel(prices, config, opens=opens, volumes=volumes)
    panel = signal_panel(prices, config, benchmark, opens=opens, volumes=volumes)
    altered = prices.copy()
    altered.iloc[720:] *= np.linspace(1, 3, len(altered) - 720)[:, None]
    changed = signal_panel(altered, config, benchmark, opens=opens, volumes=volumes)
    pd.testing.assert_frame_equal(panel["score"].iloc[:715], changed["score"].iloc[:715])
    assert panel["learned"].iloc[-1].abs().sum() > 0


def test_minerva_score_persistence_is_causal_and_explicitly_tunable():
    h = histories(850)
    prices = pd.DataFrame({name: frame.Close for name, frame in h.items()})
    opens = pd.DataFrame({name: frame.Open for name, frame in h.items()})
    volumes = pd.DataFrame({name: frame.Volume for name, frame in h.items()})
    config = minerva_score_persistence_candidate(ridge_training_sessions=252, ridge_penalty=10)
    panel = signal_panel(prices, config, opens=opens, volumes=volumes)
    altered = prices.copy()
    altered.iloc[720:] *= np.linspace(1, 3, len(altered) - 720)[:, None]
    changed = signal_panel(altered, config, opens=opens, volumes=volumes)
    pd.testing.assert_frame_equal(panel["score"].iloc[:715], changed["score"].iloc[:715])
    assert config.ridge_score_smoothing == .50
    with pytest.raises(ValueError, match="ridge"):
        UniversalConfig(ridge_score_smoothing=1)


def test_minerva_economic_interactions_are_causal_and_opt_in():
    h = histories(850)
    prices = pd.DataFrame({name: frame.Close for name, frame in h.items()})
    opens = pd.DataFrame({name: frame.Open for name, frame in h.items()})
    volumes = pd.DataFrame({name: frame.Volume for name, frame in h.items()})
    config = minerva_economic_interactions_candidate(ridge_training_sessions=252, ridge_penalty=10)
    baseline = signal_panel(prices, config, opens=opens, volumes=volumes)
    changed = prices.copy()
    changed.iloc[720:] *= np.linspace(1, 3, len(changed) - 720)[:, None]
    altered = signal_panel(changed, config, opens=opens, volumes=volumes)
    pd.testing.assert_frame_equal(baseline["score"].iloc[:715], altered["score"].iloc[:715])
    assert config.ridge_economic_interactions is True


def test_tba1_ohlcv_structure_candidate_is_causal_and_requires_real_daily_extremes():
    h = histories(850)
    prices = pd.DataFrame({name: frame.Close for name, frame in h.items()})
    opens = pd.DataFrame({name: frame.Open for name, frame in h.items()})
    volumes = pd.DataFrame({name: frame.Volume for name, frame in h.items()})
    highs = pd.DataFrame({name: frame.High for name, frame in h.items()})
    lows = pd.DataFrame({name: frame.Low for name, frame in h.items()})
    config = tba1_ohlcv_structure_candidate(ridge_training_sessions=252, ridge_penalty=10)
    with pytest.raises(ValueError, match="high and low"):
        signal_panel(prices, config, opens=opens, volumes=volumes)
    baseline = signal_panel(prices, config, opens=opens, volumes=volumes, learning_highs=highs, learning_lows=lows)
    changed_highs = highs.copy()
    changed_highs.iloc[720:] *= 1.2
    altered = signal_panel(prices, config, opens=opens, volumes=volumes,
                           learning_highs=changed_highs, learning_lows=lows)
    pd.testing.assert_frame_equal(baseline["score"].iloc[:720], altered["score"].iloc[:720])
    assert config.ridge_include_ohlcv_structure is True


def test_tba3_market_residual_target_requires_completed_market_history_and_is_causal():
    h = histories(850)
    prices = pd.DataFrame({name: frame.Close for name, frame in h.items()})
    opens = pd.DataFrame({name: frame.Open for name, frame in h.items()})
    volumes = pd.DataFrame({name: frame.Volume for name, frame in h.items()})
    benchmark = prices.mean(axis=1).pct_change(fill_method=None)
    config = tba3_market_residual_target_candidate(ridge_training_sessions=252, ridge_penalty=10)
    with pytest.raises(ValueError, match="benchmark"):
        signal_panel(prices, config, opens=opens, volumes=volumes)
    baseline = signal_panel(prices, config, benchmark, opens=opens, volumes=volumes)
    changed = prices.copy()
    changed.iloc[720:] *= np.linspace(1, 3, len(changed) - 720)[:, None]
    altered = signal_panel(changed, config, benchmark, opens=opens, volumes=volumes)
    pd.testing.assert_frame_equal(baseline["score"].iloc[:715], altered["score"].iloc[:715])
    assert config.ridge_target_market_residual is True


def test_tba4_completed_ic_gate_uses_only_finished_labels():
    h = histories(850)
    prices = pd.DataFrame({name: frame.Close for name, frame in h.items()})
    opens = pd.DataFrame({name: frame.Open for name, frame in h.items()})
    volumes = pd.DataFrame({name: frame.Volume for name, frame in h.items()})
    config = tba4_completed_ic_gate_candidate(ridge_training_sessions=252, ridge_penalty=10)
    baseline = signal_panel(prices, config, opens=opens, volumes=volumes)
    changed = prices.copy()
    changed.iloc[720:] *= np.linspace(1, 3, len(changed) - 720)[:, None]
    altered = signal_panel(changed, config, opens=opens, volumes=volumes)
    pd.testing.assert_frame_equal(baseline["score"].iloc[:715], altered["score"].iloc[:715])
    assert config.ridge_ic_gate is True
    with pytest.raises(ValueError, match="information-coefficient"):
        tba4_completed_ic_gate_candidate(ridge_ic_lookback_sessions=20)


def test_tba5_residual_momentum_is_the_existing_completed_market_feature_pair():
    config = tba5_residual_momentum_candidate()
    assert config.ridge_residual_momentum_21 is True
    assert config.ridge_residual_momentum_63 is True
    assert config.ridge_ic_gate is False


def test_tba6_enables_the_existing_causal_lifecycle_without_retuning_it():
    config = tba6_residual_lifecycle_candidate()
    assert config.position_policy.enabled is True
    assert config.position_policy.initial_stop_volatility == 2.5
    assert config.position_policy.first_take_profit_r == 2.0


def test_minerva_beta_ceiling_requires_completed_benchmark_and_only_scales_down():
    dates = pd.bdate_range("2018-01-01", periods=340)
    market = pd.Series(.001 + .003 * np.sin(np.arange(len(dates)) / 9), index=dates)
    prices = pd.DataFrame({"AAA": 100 * (1 + market).cumprod(), "BBB": 100 * (1 + market * 1.2).cumprod()}, index=dates)
    ordinary = portfolio_targets(prices, UniversalConfig(entry_threshold=0, rebalance="daily"))
    config = minerva_beta_ceiling_candidate(alpha_model="handcrafted", trend_weight=.40, momentum_weight=.35,
                                            breakout_weight=.15, pullback_weight=.10, entry_threshold=0, rebalance="daily",
                                            maximum_portfolio_market_beta=.25)
    with pytest.raises(ValueError, match="benchmark"):
        portfolio_targets(prices, config)
    capped = portfolio_targets(prices, config, market)
    assert capped.sum(axis=1).iloc[-1] < ordinary.sum(axis=1).iloc[-1]
    assert capped.sum(axis=1).max() <= ordinary.sum(axis=1).max() + 1e-12


def test_walk_forward_ridge_never_trains_on_future_bars_or_runs_as_a_single_stock_scanner():
    h = histories(850)
    prices = pd.DataFrame({name: frame.Close for name, frame in h.items()})
    opens = pd.DataFrame({name: frame.Open for name, frame in h.items()})
    volumes = pd.DataFrame({name: frame.Volume for name, frame in h.items()})
    config = UniversalConfig(alpha_model="walk_forward_ridge", ridge_training_sessions=252,
                             ridge_retrain_sessions=21, ridge_horizon_sessions=5, ridge_penalty=10)
    baseline = signal_panel(prices, config, opens=opens, volumes=volumes)
    changed = prices.copy()
    changed.iloc[720:] *= np.linspace(1, 3, len(changed) - 720)[:, None]
    altered = signal_panel(changed, config, opens=opens, volumes=volumes)
    pd.testing.assert_frame_equal(baseline["score"].iloc[:715], altered["score"].iloc[:715])
    assert baseline["learned"].iloc[-1].abs().sum() > 0
    assert scan_snapshot(h["AAA"], config)["status"] == "universe_required_for_walk_forward_ridge"
    with pytest.raises(ValueError, match="open and volume"):
        portfolio_targets(prices, config)


def test_portfolio_targets_can_reuse_an_exact_causal_panel_without_changing_targets():
    h = histories(850)
    prices = pd.DataFrame({name: frame.Close for name, frame in h.items()})
    opens = pd.DataFrame({name: frame.Open for name, frame in h.items()})
    volumes = pd.DataFrame({name: frame.Volume for name, frame in h.items()})
    config = UniversalConfig(alpha_model="walk_forward_ridge", ridge_training_sessions=252,
                             ridge_retrain_sessions=21, ridge_horizon_sessions=5, ridge_penalty=10)
    panel = signal_panel(prices, config, opens=opens, volumes=volumes)
    pd.testing.assert_frame_equal(portfolio_targets(prices, config, opens=opens, volumes=volumes),
                                  portfolio_targets(prices, config, opens=opens, volumes=volumes,
                                                    precomputed_panel=panel))
    with pytest.raises(ValueError, match="missing score"):
        portfolio_targets(prices, precomputed_panel={"volatility": panel["volatility"]})


def test_peer_shock_reduces_only_confirmed_correlated_peer_exposure():
    dates = pd.bdate_range("2020-01-01", periods=330)
    base = 100 * np.exp(np.cumsum(np.random.default_rng(44).normal(.002, .005, len(dates))))
    prices = pd.DataFrame({"AAA": base, "BBB": base, "CCC": base}, index=dates)
    prices.iloc[-5:, 1:] *= .88
    baseline = UniversalConfig(entry_threshold=0, rebalance="daily")
    shocked = UniversalConfig(entry_threshold=0, rebalance="daily", peer_shock=PeerShockConfig(enabled=True, minimum_peer_correlation=.2,
                                peer_shock_return=-.05, minimum_shocked_peers=2, exposure_multiplier=0))
    ordinary = portfolio_targets(prices, baseline)
    adjusted = portfolio_targets(prices, shocked)
    assert adjusted.iloc[-1]["AAA"] < ordinary.iloc[-1]["AAA"]
    assert peer_shock_contract(shocked.peer_shock)["status"] == "active_research_target_overlay"


def test_scanner_uses_same_engine_and_full_warmup():
    h = histories()["AAA"]
    _, _, setup, _, _ = _compute_scan_artifacts(h, "AAA", "universal_v2")
    expected = scan_snapshot(h.tail(320))
    assert setup["universal_evidence"] == expected
    assert detect_setup({}, h.tail(180), pattern_mode="universal_v2")["setup_type"] == "NO_TRADE"


def test_risk_constraints_and_column_order_invariance():
    p = pd.DataFrame({k: v.Close for k, v in histories().items()})
    c = UniversalConfig(name_cap=.2, gross_cap=.5)
    a = portfolio_targets(p, c)
    assert a.max().max() <= .2 + 1e-12
    assert a.sum(axis=1).max() <= .5 + 1e-12
    pd.testing.assert_frame_equal(a, portfolio_targets(p[p.columns[::-1]], c)[p.columns])
    concentrated = portfolio_targets(p, UniversalConfig(maximum_positions=1, entry_threshold=0))
    assert (concentrated.gt(0).sum(axis=1) <= 1).all()
    with pytest.raises(ValueError, match="maximum_positions"):
        UniversalConfig(maximum_positions=0)


def test_relative_rank_selection_is_causal_and_keeps_an_absolute_weakness_floor():
    dates = pd.bdate_range("2018-01-01", periods=340)
    prices = pd.DataFrame({
        "LEADER": 100 * np.exp(np.arange(len(dates)) * .003),
        "RUNNER_UP": 100 * np.exp(np.arange(len(dates)) * .002),
        "WEAK": 100 * np.exp(-np.arange(len(dates)) * .001),
    }, index=dates)
    config = UniversalConfig(selection_mode="relative_rank", minimum_relative_rank=.5,
                             maximum_positions=2, rebalance="daily")
    targets = portfolio_targets(prices, config)
    assert targets.iloc[-1]["LEADER"] > 0
    assert targets.iloc[-1]["RUNNER_UP"] > 0
    assert targets.iloc[-1]["WEAK"] == 0
    changed = prices.copy()
    changed.iloc[320:] *= 2
    pd.testing.assert_frame_equal(targets.iloc[:320], portfolio_targets(changed, config).iloc[:320])
    with pytest.raises(ValueError, match="selection_mode"):
        UniversalConfig(selection_mode="anything")


def test_annual_volatility_cap_excludes_high_volatility_names_without_changing_default():
    dates = pd.bdate_range("2018-01-01", periods=340)
    prices = pd.DataFrame({
        "STABLE": 100 * np.exp(np.arange(len(dates)) * .003),
        "WILD": 100 * np.exp(np.cumsum(.006 + .05 * np.sin(np.arange(len(dates))))),
    }, index=dates)
    capped = portfolio_targets(prices, UniversalConfig(entry_threshold=0, rebalance="daily",
                                                        maximum_asset_annual_volatility=.10))
    uncapped = portfolio_targets(prices, UniversalConfig(entry_threshold=0, rebalance="daily"))
    assert capped.iloc[252:]["WILD"].eq(0).all()
    assert uncapped.iloc[252:]["WILD"].gt(0).any()
    with pytest.raises(ValueError, match="maximum_asset_annual_volatility"):
        UniversalConfig(maximum_asset_annual_volatility=.05)


def test_book_no_free_daily_rebalancing_and_cash_conservation():
    dates = pd.bdate_range("2020-01-01", periods=30)
    price = np.r_[np.full(22, 100), np.full(8, 200)]
    h = {"AAA": pd.DataFrame({"Open": price, "Close": price, "Volume": 1e9}, index=dates)}
    target = pd.DataFrame(0.0, index=dates, columns=["AAA"])
    target.iloc[20:] = .5
    book = simulate_book(h, target, UniversalConfig(initial_equity=1000, cost_bps=0, impact_bps=0, trade_buffer=0))
    assert len(book["fills"]) == 1
    assert book["fills"][0]["date"] == str(dates[21].date())
    assert book["net"].iloc[22] == pytest.approx(.5)
    assert book["equity"].iloc[-1] == pytest.approx(1500)
    assert book["cash"] == pytest.approx(500)
    assert book["held"].iloc[-1, 0] == pytest.approx(2 / 3)


def test_participation_and_missing_volume_are_enforced():
    h = histories()
    p = pd.DataFrame({k: v.Close for k, v in h.items()})
    targets = portfolio_targets(p)
    for frame in h.values():
        frame.Volume = 1
    book = simulate_book(h, targets, UniversalConfig(participation=.01))
    assert book["unfilled_notional"] > 0
    assert max(r["participation"] for r in book["fills"]) <= .01 + 1e-12
    for frame in h.values():
        frame.Volume = 0
    assert not simulate_book(h, targets, UniversalConfig())["fills"]


def test_episodes_charge_both_sides_and_do_not_count_open_positions():
    h = histories(60)
    for frame in h.values():
        frame[["Open", "Close"]] = 100
    target = pd.DataFrame(0.0, index=h["AAA"].index, columns=list(h))
    target.iloc[20:30, 0] = .25
    target.iloc[40:, 1] = .25
    book = simulate_book(h, target, UniversalConfig(initial_equity=1000, cost_bps=10, impact_bps=0))
    assert len(book["closed_episodes"]) == 1
    assert len(book["open_episodes"]) == 1
    assert book["closed_episodes"][0]["pnl"] == pytest.approx(-.5)
    assert book["win_rate_pct"] == 0


def test_alpha_recovers_known_excess_intercept_and_gate_counts_exact_years():
    dates = pd.bdate_range("2016-01-01", "2025-12-31")
    rng = np.random.default_rng(52)
    rf = pd.Series(.00005, index=dates)
    market = pd.Series(rng.normal(.0003, .01, len(dates)), index=dates)
    strategy = rf + .065 / 252 + .7 * (market - rf)
    result = alpha_scorecard(strategy, market, rf, 60)
    assert result["passes"]
    assert result["mean_annual_alpha_pct"] == pytest.approx(6.5)
    assert result["pooled"]["beta"] == pytest.approx(.7)
    assert result["complete_years"] == 10
    assert not alpha_scorecard(strategy.iloc[250:], market, rf, 60)["passes"]
    assert alpha_scorecard(strategy, market.iloc[10:], rf)["status"] == "not_measurable"


def test_alpha_hac_matches_statsmodels_when_available():
    sm = pytest.importorskip("statsmodels.api")
    dates = pd.bdate_range("2020-01-01", periods=300)
    rng = np.random.default_rng(31)
    market = pd.Series(rng.normal(0, .01, 300), index=dates)
    noise = pd.Series(rng.normal(0, .002, 300)).rolling(5, min_periods=1).mean().to_numpy()
    strategy = .0002 + .5 * market + noise
    rf = market * 0
    got = regression_alpha(strategy, market, rf)
    expected = sm.OLS(strategy, sm.add_constant(market)).fit(cov_type="HAC", cov_kwds={"maxlags": got["hac_lags"], "use_correction": True})
    assert got["alpha_t_hac"] == pytest.approx(expected.tvalues.iloc[0])


def test_report_fingerprint_covers_volume_and_benchmark():
    h = histories()
    a = run_universal(h)
    assert a["alpha"]["status"] == "not_measurable"
    h["AAA"].Volume *= 2
    b = run_universal(h)
    assert a["dataset_fingerprint"] != b["dataset_fingerprint"]


def test_stops_return_results_and_gap_at_open_is_not_idealized():
    trade = {"direction": "LONG", "entry": 100, "stop": 95, "target": 110, "candle_in": 0}
    result = _check_exit(trade, row=pd.Series({"Open": 90, "High": 111, "Low": 89, "Close": 105}), date=pd.Timestamp("2020-01-02"), candle_idx=1, max_hold=20, slippage_pct=0, commission_pct=.1, policy="stop_first")
    assert result["exit_reason"] == "STOP_GAP"
    assert result["raw_exit_price"] == 90
    assert result["pnl_pct"] == pytest.approx(-10.2)


def test_upload_contract_evaluates_shared_engine():
    h = histories()
    payload = {"histories": [{"ticker": k, "bars": [{"timestamp": str(day.date()), **{column.lower(): float(row[column]) for column in ("Open", "High", "Low", "Close", "Volume")}} for day, row in frame.iterrows()]} for k, frame in h.items()]}
    report = evaluate_upload(UniversalRequest(**payload))
    assert report["engine"] == "universal_v2"
    assert report["raw_market_data_persisted"] is False
    assert report["universe"]["sessions"] == 87
    assert report["latest_signals"]["AAA"]["status"] == "available"
    assert report["execution"]["fill_count"] > 0


def test_invalid_config_and_missing_price_fail_loudly():
    with pytest.raises(ValueError):
        UniversalConfig(vol_target=float("nan"))
    with pytest.raises(ValueError):
        UniversalConfig(gross_cap=2)
    h = histories()
    h["AAA"].iloc[300, h["AAA"].columns.get_loc("Close")] = np.nan
    with pytest.raises(ValueError):
        run_universal(h)


def test_entry_does_not_earn_the_pre_entry_overnight_gap():
    dates = pd.bdate_range("2020-01-01", periods=24)
    prices = np.r_[np.full(21, 100), np.full(3, 200)]
    h = {"AAA": pd.DataFrame({"Open": prices, "Close": prices, "Volume": 1e8}, index=dates)}
    targets = pd.DataFrame(0.0, index=dates, columns=["AAA"])
    targets.iloc[20:] = 1.0
    book = simulate_book(h, targets, UniversalConfig(initial_equity=1000, cost_bps=0, impact_bps=0))
    assert book["net"].sum() == pytest.approx(0)
    assert book["equity"].iloc[-1] == pytest.approx(1000)


def test_report_rejects_insufficient_history_after_end_cutoff():
    with pytest.raises(ValueError, match="warmup"):
        run_universal(histories(), evaluation_end="2015-02-01")


def test_summary_keeps_both_endpoints_and_initial_loss():
    from backend.quant_research import _summary
    dates = pd.bdate_range("2020-01-01", periods=800)
    net = pd.Series(0.0, index=dates)
    net.iloc[0] = -.1
    report = _summary(net, net * 0, pd.DataFrame({"AAA": net * 0}))
    assert report["max_drawdown_pct"] == -10
    assert len(report["equity_curve"]) == 260
    assert report["equity_curve"][0]["date"] == str(dates[0].date())
    assert report["equity_curve"][-1]["date"] == str(dates[-1].date())


def test_eleven_years_cannot_pass_and_missing_win_rate_cannot_pass():
    dates = pd.bdate_range("2015-01-01", "2025-12-31")
    rng = np.random.default_rng(72)
    market = pd.Series(rng.normal(.0002, .01, len(dates)), index=dates)
    rf = market * 0
    strategy = .06 / 252 + .7 * market
    score = alpha_scorecard(strategy, market, rf, 60)
    assert score["complete_years"] == 11
    assert not score["passes"]
    assert not alpha_scorecard(strategy.loc["2016":], market, rf)["passes"]


def test_http_uploads_require_auth_record_derived_results_and_share_config(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import backend.database as database
    from backend.routes.auth import router as auth_router
    from backend.routes.universal import router
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "universal.db"))
    database.init_db()
    app = FastAPI()
    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(router, prefix="/api/universal")
    h = histories()
    payload = {"histories": [{"ticker": k, "bars": [{"timestamp": str(day.date()), **{column.lower(): float(row[column]) for column in ("Open", "High", "Low", "Close", "Volume")}} for day, row in frame.iterrows()]} for k, frame in h.items()], "configuration": {"entry_threshold": .3}}
    with TestClient(app) as client:
        assert client.post("/api/universal/run-upload", json=payload).status_code == 401
        signup = client.post("/api/auth/signup", json={"email": "universal@example.com", "password": "strong-password", "accept_legal": True})
        assert signup.status_code == 200, signup.text
        headers = {"Authorization": f"Bearer {signup.json()['token']}"}
        result = client.post("/api/universal/run-upload", json=payload, headers=headers)
        assert result.status_code == 200, result.text
        report = result.json()
        assert report["experiment_id"]
        scan = client.post("/api/universal/scan-upload", json={"history":payload["histories"][0],"configuration":payload["configuration"]}, headers=headers)
        assert scan.status_code == 200, scan.text
        assert scan.json()["config_fingerprint"] == report["config_fingerprint"]
        assert scan.json()["score"] == report["latest_signals"]["AAA"]["score"]
        bad = client.post("/api/universal/run-upload", json={**payload, "evaluation_end": "2015-01-30"}, headers=headers)
        assert bad.status_code == 400


def test_universal_routes_not_mounted_on_default_public_app():
    from backend.main import app
    assert not any(route.path.startswith("/api/universal/") for route in app.routes)


def test_pattern_lab_delivers_full_history_to_universal(monkeypatch, tmp_path):
    import asyncio
    import backend.database as database
    import backend.pattern_lab as lab
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "lab.db"))
    database.init_db()
    history = histories(390)["AAA"]
    async def load(*args):
        return history, "test", False
    monkeypatch.setattr(lab, "_load_history", load)
    original = lab.detect_setup
    lengths = []
    def detect(indicators, frame, **kwargs):
        if kwargs.get("pattern_mode") == "universal_v2":
            lengths.append(len(frame))
        return original(indicators, frame, **kwargs)
    monkeypatch.setattr(lab, "detect_setup", detect)
    asyncio.run(lab.run_pattern_lab({"tickers":["AAA"], "engine_modes":["universal_v2"], "min_history":280, "max_tests_per_ticker":3, "horizon_days":10, "bootstrap_samples":1}))
    assert lengths and min(lengths) >= 253


def test_quant_adapter_keeps_universal_configuration_and_skips_unused_macro(monkeypatch):
    import asyncio
    import backend.routes.quant as route
    from backend.quant_research import QuantConfig
    def fail():
        raise AssertionError("Universal must not load unused corporate or macro facts")
    monkeypatch.setattr(route, "get_corporate_repository", fail)
    config = QuantConfig(model="universal_v2", max_single_name_weight=.2)
    report = asyncio.run(route._evaluate_histories(histories(), config))
    assert report["engine_configuration"]["name_cap"] == .2
    assert report["macro_data"]["status"] == "not_used"
    assert report["alpha"]["status"] == "not_measurable"
    assert len(report["code_fingerprint"]) == 64


def test_fully_invested_zero_cost_book_tolerates_roundoff():
    h = histories(400)
    prices = pd.DataFrame({k:v.Close for k,v in h.items()})
    config = UniversalConfig(name_cap=1, vol_target=.5, cost_bps=0, impact_bps=0, trade_buffer=0)
    book = simulate_book(h, portfolio_targets(prices, config), config)
    assert book["cash"] >= 0
    assert np.isfinite(book["net"]).all()


def test_position_policy_is_disabled_by_default_and_never_widens_a_stop():
    config = PositionPolicyConfig()
    state = open_position("AAA", "2020-01-01", 100, .4, .02, config)
    directive = evaluate_close(state, {"Open": 100, "High": 125, "Low": 99, "Close": 120, "Volume": 300}, prior_close=100, signal_score=.3, daily_volatility=.02, average_volume=100, config=config)
    assert directive.action == "hold"
    assert directive.reason == "policy_disabled"
    assert directive.state.current_stop >= state.current_stop
    assert policy_contract(config)["status"] == "disabled_by_default"


def test_position_policy_respects_daily_bar_timing_and_spike_rules():
    config = PositionPolicyConfig(enabled=True, initial_stop_volatility=2, trailing_stop_volatility=2,
                                  first_take_profit_r=2, spike_return_volatility=2, spike_relative_volume=2)
    state = open_position("AAA", "2020-01-01", 100, .4, .02, config)
    gap = evaluate_precommitted_daily_bar(state, {"Open": 95, "High": 99, "Low": 90}, config)
    assert gap.action == "exit" and gap.reason == "hard_stop_gap" and gap.timing == "precommitted_daily_bar"
    target = evaluate_precommitted_daily_bar(state, {"Open": 101, "High": state.first_take_profit + 1, "Low": 100}, config)
    assert target.action == "trim" and target.reason == "first_take_profit" and target.timing == "precommitted_daily_bar"
    spike = evaluate_close(state, {"Open": 100, "High": 110, "Low": 99, "Close": 109, "Volume": 400}, prior_close=100, signal_score=.3, daily_volatility=.02, average_volume=100, config=config)
    assert spike.action == "trim" and spike.reason == "spike_exhaustion" and spike.timing == "next_open"


def test_position_policy_calendar_exit_is_completed_data_only_and_tunable():
    config = PositionPolicyConfig(enabled=True, initial_stop_volatility=2, trailing_stop_volatility=8,
                                  first_take_profit_r=10, maximum_holding_sessions=30, signal_exit_score=-1,
                                  spike_return_volatility=12, calendar_exit_enabled=True,
                                  calendar_exit_start_month=12, calendar_exit_start_day=10,
                                  calendar_exit_rsi_threshold=70, calendar_exit_extension_volatility=1.5)
    state = open_position("AAA", "2020-01-01", 100, .4, .02, config)
    exit_directive = evaluate_close(state, {"Open": 100, "High": 108, "Low": 99, "Close": 107, "Volume": 100},
                                    prior_close=100, signal_score=.3, daily_volatility=.02, average_volume=100,
                                    calendar_date="2020-12-15", calendar_rsi=74, calendar_extension=1.8, config=config)
    assert exit_directive.action == "exit" and exit_directive.reason == "calendar_overbought_year_end"
    early = evaluate_close(state, {"Open": 100, "High": 108, "Low": 99, "Close": 107, "Volume": 100},
                           prior_close=100, signal_score=.3, daily_volatility=.02, average_volume=100,
                           calendar_date="2020-11-30", calendar_rsi=74, calendar_extension=1.8, config=config)
    assert early.action == "hold"
    with pytest.raises(ValueError, match="RSI"):
        evaluate_close(state, {"Open": 100, "High": 108, "Low": 99, "Close": 107, "Volume": 100},
                       prior_close=100, signal_score=.3, daily_volatility=.02, average_volume=100,
                       calendar_date="2020-12-15", config=config)


def test_enabled_position_policy_executes_in_the_v2_cash_ledger():
    dates = pd.bdate_range("2020-01-01", periods=30)
    opening = np.full(30, 100.0)
    h = {"AAA": pd.DataFrame({"Open": opening, "High": 101.0, "Low": 99.0,
                               "Close": opening, "Volume": 1e8}, index=dates)}
    h["AAA"].loc[dates[22], "Low"] = 90.0
    targets = pd.DataFrame(0.0, index=dates, columns=["AAA"])
    targets.iloc[20:] = .5
    config = UniversalConfig(initial_equity=1000, cost_bps=0, impact_bps=0, trade_buffer=0,
                             position_policy=PositionPolicyConfig(enabled=True, initial_stop_volatility=1,
                                                                   trailing_stop_volatility=8, first_take_profit_r=10,
                                                                   maximum_holding_sessions=30, signal_exit_score=-1,
                                                                   spike_return_volatility=12))
    scores = pd.DataFrame(.5, index=dates, columns=["AAA"])
    volatility = pd.DataFrame(.02, index=dates, columns=["AAA"])
    book = simulate_book(h, targets, config, policy_scores=scores, policy_volatility=volatility)
    assert any(event["reason"] == "hard_stop" and event["price"] == pytest.approx(98) for event in book["position_policy_events"])
    assert book["closed_episodes"]


def test_position_policy_rejects_bad_bars_and_blueprint_is_explicit_about_data_needs():
    state = open_position("AAA", "2020-01-01", 100, .4, .02)
    with pytest.raises(ValueError):
        evaluate_close(state, {"Open": 100, "High": 99, "Low": 98, "Close": 101, "Volume": 1}, prior_close=100, signal_score=.3, daily_volatility=.02, average_volume=1, config=PositionPolicyConfig())
    blueprint = research_blueprint()
    assert blueprint["maturity"] == "research_foundation"
    assert {item["id"] for item in blueprint["workstreams"]} >= {"position_lifecycle", "market_context_overlay", "multi_family_taxonomy", "events", "meta_label"}
    assert all(item["fixed_invariants"] for item in blueprint["workstreams"])


def test_position_policy_accepts_provider_roundoff_at_daily_extremes():
    state = open_position("AAA", "2020-01-01", 100, .4, .02)
    directive = evaluate_close(
        state,
        {"Open": 13.49203882964526, "High": 13.50076259123862,
         "Low": 13.279771804809572, "Close": 13.27977180480957, "Volume": 6601292},
        prior_close=13.4, signal_score=.3, daily_volatility=.02,
        average_volume=1_000_000, config=PositionPolicyConfig(),
    )
    assert directive.reason == "policy_disabled"


def test_blueprint_route_requires_auth_and_does_not_claim_live_activation(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import backend.database as database
    from backend.routes.auth import router as auth_router
    from backend.routes.universal import router
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "blueprint.db"))
    database.init_db()
    app = FastAPI()
    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(router, prefix="/api/universal")
    with TestClient(app) as client:
        assert client.get("/api/universal/blueprint").status_code == 401
        signup = client.post("/api/auth/signup", json={"email": "blueprint@example.com", "password": "strong-password", "accept_legal": True})
        response = client.get("/api/universal/blueprint", headers={"Authorization": f"Bearer {signup.json()['token']}"})
        taxonomy_response = client.get("/api/universal/taxonomy", headers={"Authorization": f"Bearer {signup.json()['token']}"})
        seed_response = client.get("/api/universal/taxonomy/AAPL", headers={"Authorization": f"Bearer {signup.json()['token']}"})
    assert response.status_code == 200, response.text
    assert response.json()["position_policy"]["status"] == "disabled_by_default"
    assert taxonomy_response.status_code == 200, taxonomy_response.text
    assert taxonomy_response.json()["status"] in {"catalog_ready_snapshot_required", "catalog_and_seed_ready"}
    assert seed_response.status_code == 200, seed_response.text
    assert any(edge["family_id"] == "information_technology" for edge in seed_response.json()["matches"][0]["memberships"])


def test_upload_contract_accepts_explicit_disabled_or_research_policy_config():
    h = histories()
    payload = {"histories": [{"ticker": k, "bars": [{"timestamp": str(day.date()), **{column.lower(): float(row[column]) for column in ("Open", "High", "Low", "Close", "Volume")}} for day, row in frame.iterrows()]} for k, frame in h.items()], "configuration": {"position_policy": {"enabled": True, "maximum_holding_sessions": 42}, "market_context": {"enabled": True, "caution_exposure_multiplier": .65}}}
    request = UniversalRequest(**payload)
    assert request.configuration.position_policy.enabled is True
    assert request.configuration.position_policy.maximum_holding_sessions == 42
    assert request.configuration.market_context.caution_exposure_multiplier == .65
    with pytest.raises(ValueError, match="availability-dated observations"):
        evaluate_upload(request)
    request.configuration = replace(request.configuration, position_policy=PositionPolicyConfig())
    with pytest.raises(ValueError, match="requires availability-dated"):
        evaluate_upload(request)


def test_market_context_requires_confirming_stress_and_stays_disabled_by_default():
    context = CompletedMarketContext("2024-01-02", market_return=-.12, breadth=.25, median_pairwise_correlation=.85, source_manifest_id="test")
    assert evaluate_market_context(context).regime == "neutral"
    configured = evaluate_market_context(context, MarketContextConfig(enabled=True))
    assert configured.regime == "risk_off"
    assert configured.entry_allowed is False and configured.exposure_multiplier == .35
    noisy = CompletedMarketContext("2024-01-03", market_return=-.20, breadth=.70, median_pairwise_correlation=.20)
    assert evaluate_market_context(noisy, MarketContextConfig(enabled=True)).regime == "neutral"
    volatile = CompletedMarketContext("2024-01-04", market_return=.01, breadth=.25, median_pairwise_correlation=.20,
                                      market_annual_volatility=.50)
    volatility_config = MarketContextConfig(enabled=True, caution_market_annual_volatility=.25,
                                            risk_off_market_annual_volatility=.40)
    assert evaluate_market_context(volatile, volatility_config).regime == "risk_off"
    with pytest.raises(ValueError, match="supplied together"):
        MarketContextConfig(caution_market_annual_volatility=.25)
    assert market_context_contract(MarketContextConfig(enabled=True))["status"] == "active_research_target_overlay"


def test_multi_family_taxonomy_is_valid_and_does_not_double_count_parent_child_branches():
    catalog = load_family_catalog()
    assert taxonomy_contract()["family_count"] >= 50
    exposures = [
        FamilyExposure("id-a", "AAA", "semiconductors", 1, 1.1, .9, "test", "2024-01-02"),
        FamilyExposure("id-a", "AAA", "ai_compute", .8, .7, .8, "test", "2024-01-02"),
        FamilyExposure("id-a", "AAA", "long_duration_equity", 1, 1.2, .9, "test", "2024-01-02"),
    ]
    validate_exposures(exposures, catalog)
    pressure = family_shock_pressure(exposures, {"semiconductors": -.10, "ai_compute": -.20, "long_duration_equity": -.05}, catalog)
    assert [item["root_id"] for item in pressure["contributions"]] == ["structural_theme", "economic_sector", "rate_credit_sensitivity"]
    assert pressure["pressure"] > 0


def test_taxonomy_builder_requires_and_writes_a_complete_dated_snapshot(tmp_path):
    universe = tmp_path / "universe.csv"
    memberships = tmp_path / "memberships.csv"
    output = tmp_path / "snapshot.json"
    universe.write_text("security_id,symbol,market_cap,as_of_date,exchange,country,asset_type\n1,AAA,200,2024-01-02,XNYS,US,common_stock\n2,BBB,100,2024-01-02,XNAS,US,common_stock\n", encoding="utf-8")
    memberships.write_text("security_id,symbol,family_id,membership_weight,downside_sensitivity,confidence,source,as_of_date\n1,AAA,semiconductors,1,1.1,.9,test,2024-01-02\n2,BBB,software,1,.8,.9,test,2024-01-02\n", encoding="utf-8")
    result = subprocess.run([sys.executable, "tools/build_universal_taxonomy.py", "--universe-csv", str(universe), "--memberships-csv", str(memberships), "--output", str(output), "--limit", "2"], cwd=Path(__file__).parents[1], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(output.read_text(encoding="utf-8"))
    assert snapshot["coverage"] == {"securities": 2, "exposures": 2, "families": 2}


def test_taxonomy_builder_can_make_an_automatic_financedatabase_seed(tmp_path):
    root = tmp_path / "finance"
    equity_dir = root / "database" / "equities"
    equity_dir.mkdir(parents=True)
    (equity_dir / "NMS.csv").write_text("symbol,name,summary,currency,sector,industry_group,industry,exchange,mic,market,country,state,city,zipcode,website,market_cap,isin,cusip,figi,composite_figi,shareclass_figi,delisted\nAAA,AI Chips Corp,Artificial intelligence semiconductor company,USD,Information Technology,Semiconductors & Equipment,Semiconductors,NMS,XNAS,Nasdaq,United States,,,,,Mega Cap,,,,FIGI-A,,False\nBBB,Example Bank,Regional bank,USD,Financials,Banks,Banks,NYS,XNYS,NYSE,United States,,,,,Large Cap,,,,FIGI-B,,False\n", encoding="utf-8")
    output = tmp_path / "seed.json"
    result = subprocess.run([sys.executable, "tools/build_universal_taxonomy.py", "--finance-database-root", str(root), "--output", str(output), "--limit", "2"], cwd=Path(__file__).parents[1], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(output.read_text(encoding="utf-8"))
    assert snapshot["source_metadata"]["mode"] == "automated_financedatabase_seed"
    assert snapshot["coverage"]["securities"] == 2
    assert {edge["family_id"] for edge in snapshot["exposures"] if edge["symbol"] == "AAA"} >= {"us_equity", "information_technology", "semiconductors", "ai_compute"}
