from copy import deepcopy
from dataclasses import replace
import numpy as np
import pandas as pd
import pytest

from backend.qoptima.forecast_contract import ForecastRecord, forecast_error_sample_matrix
from backend.qoptima.gamma_integration import (
    GammaContractError, prepare_gamma_inputs, gamma_score_rates,
    apply_gamma_risk205, validate_gamma_config,
)
from backend.qoptima.problem import QOptimaPortfolioProblem
from backend.qoptima.full_qrs import score_full_qrs
from backend.risk.state_time import validate_state_time
from backend.risk.v204 import V204BetaControlConfig, beta_regime_modifier_v204
from backend.risk.v205 import V205V1AControlConfig, v1a_regime_modifier_v205

SYMS = tuple(f'S{i}' for i in range(6))


def gamma_row(day, mean=.01, *, h=5, realized=True):
    origin = pd.Timestamp(day)
    cutoff = origin-pd.Timedelta(days=1)
    r = {'model_id': 'gamma_qpo_research_v1', 'model_version': 'test',
        'created_at': origin.isoformat(), 'information_cutoff': cutoff.isoformat(),
        'horizon_sessions': h, 'units': 'period_return',
        'values': {s: mean+.001*i for i,s in enumerate(SYMS)},
        'metadata': {'producer': 'gamma_qpo_research_v1', 'target_type': 'absolute',
            'return_convention': 'close_to_close', 'target_origin': origin.isoformat(),
            'feature_lag_sessions': 1, 'reference_definition': 'fixed_start_equal_weight_eligible_universe',
            'training_information_cutoff': cutoff.isoformat(), 'reference_training_cutoff': cutoff.isoformat(),
            'residual_status': 'bounded_ridge_OOF_residual', 'beta_status': 'unavailable', 'sec_status': 'unavailable'}}
    if realized:
        r['metadata']['target_end'] = (origin+pd.Timedelta(days=h)).isoformat()
        r.update(realized_outcome={s: mean+.002*i-.003 for i,s in enumerate(SYMS)},
                 realized_at=(origin+pd.Timedelta(days=h)).isoformat())
    return r


def prepared(rows=None, **kwargs):
    if rows is None:
        rows = [gamma_row('2020-01-10'), gamma_row('2020-01-01'), gamma_row('2020-02-01')]
    return prepare_gamma_inputs(rows, decision_time='2020-02-01', feature_cutoff='2020-01-31',
        symbols=SYMS, horizon_sessions=5, **kwargs)


def test_nonempty_error_ledger_regression():
    row = ForecastRecord.from_mapping(gamma_row('2020-01-01'))
    values, meta = forecast_error_sample_matrix([row], SYMS)
    assert values.shape == (1,6)
    assert meta['v1a_regime_tail_v1_labels'] == [None]
    assert forecast_error_sample_matrix([], SYMS)[0] is None


@pytest.mark.parametrize('realized', [False, True])
def test_annual_equivalent_preserves_values_and_lineage_without_transient_deepcopy(realized):
    from dataclasses import asdict
    from backend.qoptima.gamma_integration import _annual_equivalent
    raw = gamma_row('2020-01-01', realized=realized)
    raw['metadata']['beta_provenance'] = {'market_provenance':[{'source_prefix_sha256':'a'*64}]*62}
    record = ForecastRecord.from_mapping(raw)
    multiplier = 252.0/record.horizon_sessions
    original = asdict(record)
    original['values'] = {key:multiplier*value for key,value in record.values.items()}
    if record.realized_outcome is not None:
        original['realized_outcome'] = {key:multiplier*value for key,value in record.realized_outcome.items()}
    original['units'] = 'annualized_return'
    original['metadata'] = {**dict(record.metadata or {}), 'scoring_units':'linear_annual_equivalent_not_compound_return',
        'period_to_score_multiplier':multiplier, 'input_period_values':dict(record.values)}
    actual = _annual_equivalent(record)
    assert actual == original
    assert actual['metadata']['beta_provenance'] is record.metadata['beta_provenance']
    actual['values']['S0'] = 999
    actual['metadata']['input_period_values']['S0'] = 999
    assert record.values['S0'] == raw['values']['S0']


def test_gamma_fresh_absolute_inputs_linear_units_and_sorted_matured_ledger():
    output = prepared()
    assert [r['created_at'][:10] for r in output['history_records']] == ['2020-01-01','2020-01-10']
    assert len(output['pit']['historical_realized_records']) == 2
    assert output['current_records'][0]['values']['S0'] == pytest.approx(.01*252/5)
    assert output['current_records'][0]['units'] == 'annualized_return'
    assert output['audit']['error_records'] == 2


@pytest.mark.parametrize('mutation', ['relative','same_day','warmup','double_conversion','missing_reference','duplicate_current','no_error'])
def test_gamma_rejects_incompatible_or_unready_inputs(mutation):
    rows = [gamma_row('2020-01-01'), gamma_row('2020-01-10'), gamma_row('2020-02-01')]
    if mutation == 'relative': rows[-1]['metadata']['target_type'] = 'relative'
    elif mutation == 'same_day': rows[-1]['information_cutoff'] = '2020-02-01'
    elif mutation == 'warmup': rows[-1]['metadata']['residual_status'] = 'warmup_mapped_Alpha_only'
    elif mutation == 'double_conversion': rows[-1]['units'] = 'annualized_return'
    elif mutation == 'missing_reference': del rows[-1]['metadata']['reference_training_cutoff']
    elif mutation == 'duplicate_current': rows.append(deepcopy(rows[-1]))
    elif mutation == 'no_error': rows = rows[-1:]
    with pytest.raises((GammaContractError, ValueError)):
        prepared(rows)


def test_gamma_required_optional_sleeves_and_unknown_keys_fail_closed():
    with pytest.raises(GammaContractError, match='Beta'):
        prepared(required_beta=True)
    with pytest.raises(GammaContractError, match='SEC'):
        prepared(required_sec=True)
    with pytest.raises(GammaContractError, match='Unknown'):
        validate_gamma_config({'unsupported': 1})


@pytest.mark.parametrize('bad_time', ['2030-01-01', '2020-01-01T17:00:00Z', None, '', 'not-a-date'])
def test_state_timestamp_rejects_future_and_invalid(bad_time):
    age, blockers = validate_state_time(bad_time, '2020-01-01T16:00:00Z', prefix='v1a', maximum_age_sessions=5)
    assert blockers
    assert age is None


def test_state_timestamp_handles_timezone_equivalence_and_staleness():
    assert validate_state_time('2020-01-01T11:00:00-05:00','2020-01-01T16:00:00Z',prefix='v1a',maximum_age_sessions=5) == (0,[])
    assert 'v1a_state_stale' in validate_state_time('2019-01-01','2020-01-01',prefix='v1a',maximum_age_sessions=5)[1]


def test_beta_and_v1a_gates_reject_future_direct_states():
    beta = dict(p_growth_up_inflation_up=0, p_growth_up_inflation_down=0,
        p_growth_down_inflation_up=1,p_growth_down_inflation_down=0,
        data_quality=1,uncertainty=0,calibrated=True,production_frozen=True,as_of_session='2030-01-01')
    bm=beta_regime_modifier_v204(beta,control=V204BetaControlConfig(persistence_rebalances=1),current_session='2020-01-01')
    assert 'beta_state_future' in bm['blockers'] and bm['gross_modifier']==1
    state=dict(gamma=.9,es_calibrated=-.1,positive_definite=True,signed_control=False,information_cutoff='2030-01-01')
    vm=v1a_regime_modifier_v205(state,weights=np.full(6,1/6),covariance=np.eye(6)*.01,
        returns_history=np.random.default_rng(1).normal(0,.01,(50,6)),control=V205V1AControlConfig(),current_session='2020-01-01')
    assert 'v1a_state_future' in vm['blockers'] and vm['gross_modifier']==1


def problem(**kwargs):
    sigma=(.7*np.ones((6,6))+.3*np.eye(6))*.04
    defaults=dict(symbols=SYMS,current_weights=np.full(6,.15),expected_returns=np.linspace(.1,.15,6),
        covariance=sigma,target_gross=.9,max_name_weight=.3,horizon_sessions=21,
        full_qrs_enabled=True,full_qrs_stress_covariances={'BASE':sigma,'STRESS':1.2*sigma},
        transaction_cost_bps=5,full_qrs_bootstrap_reps=0)
    defaults.update(kwargs)
    return QOptimaPortfolioProblem(**defaults)


@pytest.mark.parametrize('positive', [True,False])
def test_linear_annual_equivalent_matches_period_v1a_qrs_in_both_branches(positive):
    k=12.
    w=np.array([.20,.15,.10,.15,.15,.15])
    base=problem(v1a_enabled=True, v1a_risk_budget_annual=.15,
                 expected_returns=np.full(6, .50 if positive else -.50),
                 risk_free_rate=.04, lending_rate=.04, borrowing_rate=.08,
                 forecast_error_samples=np.random.default_rng(5).normal(-.02,.015,(35,6)),
                 v1a_forecast_error_covariance_floor=1e-8)
    period=replace(base, expected_returns=base.expected_returns/k,covariance=base.covariance/k,
        full_qrs_stress_covariances={n:m/k for n,m in base.full_qrs_stress_covariances.items()},
        risk_free_rate=base.risk_free_rate/k,lending_rate=base.lending_rate/k,borrowing_rate=base.borrowing_rate/k,
        transaction_cost_bps=base.transaction_cost_bps/k,forecast_error_samples=base.forecast_error_samples/k,
        v1a_risk_budget_annual=base.v1a_risk_budget_annual/np.sqrt(k),
        v1a_forecast_error_covariance_floor=base.v1a_forecast_error_covariance_floor/k**2)
    a,b=score_full_qrs(base,w),score_full_qrs(period,w)
    assert a['positive_numerator'] is positive
    assert a['qrs']==pytest.approx(np.sqrt(k)*b['qrs'], rel=1e-10)
    assert a['forecast_error_tail']['value']==pytest.approx(k*b['forecast_error_tail']['value'], rel=1e-10)


def test_financing_cost_conversion_does_not_compound_expected_mean():
    rates=gamma_score_rates(21,.05,.09,5)
    assert rates['risk_free_rate']/12 == pytest.approx(1.05**(1/12)-1)
    assert rates['transaction_cost_bps']==60


def test_postrisk_uses_approved_gross_and_advances_counters_once(monkeypatch):
    import backend.universal_risk_supervisor as risk
    calls=[]
    def mock(weights,cov,cfg,**kw):
        calls.append(kw)
        return weights*.8, {'persistence_count':3,'beta_persistence_rebalances':4,'v1a_persistence_rebalances':5}
    monkeypatch.setattr(risk,'supervise_cross_sectional_weights',mock)
    p=problem()
    state={'predictive_persistence_count':2,'beta_persistence_count':3,'v1a_persistence_count':4}
    saved=deepcopy(state)
    w,audit,next_state=apply_gamma_risk205(p,p.current_weights,returns_history=np.zeros((90,6)),
        feature_cutoff='2020-01-31',state=state,actual_transaction_cost_bps=5,
        config_overrides={'maximum_risk_contribution': .25})
    assert len(calls)==1 and state==saved
    assert np.allclose(w,p.current_weights*.8)
    assert audit['final_qrm']['passes_qrm_structural_gates']
    assert audit['approved_final_gross']==pytest.approx(.72)
    assert audit['actual_one_event_transaction_cost']==pytest.approx(.18*5/10000)
    assert next_state['beta_persistence_count']==4


def test_initial_loss_counts_in_drawdown():
    from backend.qoptima.backtest_overlay import _metrics
    assert _metrics(pd.Series([-.1,0,0]))['max_drawdown']==pytest.approx(-.1)
    assert _metrics(pd.Series([-.1]))['max_drawdown']==pytest.approx(-.1)


@pytest.mark.parametrize('tz',[None,'UTC'])
def test_evaluation_end_includes_whole_daily_session(tz):
    from backend.qoptima.backtest_overlay import _limit_evaluation_prices
    index=pd.date_range('2020-01-01 04:00',periods=4,tz=tz)
    prices=pd.DataFrame({'A':[1,2,3,4]},index=index)
    trimmed=_limit_evaluation_prices(prices,'2020-01-03')
    assert len(trimmed)==3 and trimmed.iloc[-1,0]==3


def test_lagged_replay_covariance_and_holdings_do_not_see_entry_close(monkeypatch):
    import backend.qoptima.backtest_overlay as replay
    rng=np.random.default_rng(7)
    r=pd.DataFrame(rng.normal(0,.005,(100,6)),index=pd.bdate_range('2020-01-01',periods=100),columns=SYMS)
    captures=[]
    def build(hist,symbols,current,**kw):
        captures.append((hist.index[-1],kw['decision_time'],current.copy()))
        return problem(current_weights=current),{}
    monkeypatch.setattr(replay,'_make_problem',build)
    monkeypatch.setattr(replay,'solve_classical',lambda p:{'weights':np.full(6,.15)})
    monkeypatch.setattr(replay,'_choose_qoptima',lambda p,**kw:(np.full(6,.15),{'status':'test'}))
    kwargs=dict(method='sample',params={},alpha=1,lookback=84,rebalance=5,max_name=.3,risk_aversion=3,
        turnover_penalty=.2,risk_free_rate=0,transaction_cost_bps=5,correlation_blend=.65,true_replay=True,
        replay_qscan_assets=6,replay_bits=1,replay_backend='exact',seed=1,gamma_decision_lag_sessions=1)
    out1=replay._walk_forward_method(r,**kwargs)
    first=list(captures);captures.clear()
    changed=r.copy();changed.iloc[88,0]+=.30
    out2=replay._walk_forward_method(changed,**kwargs)
    assert first[2][0]==r.index[87] and first[2][1]==r.index[88]
    assert np.allclose(first[2][2],captures[2][2])
    assert not np.allclose(out1['rebalances'][1]['actual_entry_weights'],out2['rebalances'][1]['actual_entry_weights'])


def test_research_all_cash_risk_transition_does_not_invent_holdings(monkeypatch):
    import backend.qoptima.backtest_overlay as replay
    import backend.qoptima.gamma_integration as integration
    r = pd.DataFrame(0.0, index=pd.bdate_range('2020-01-01', periods=94), columns=SYMS)
    monkeypatch.setattr(replay, '_make_problem', lambda hist, symbols, current, **kw:
        (problem(current_weights=current), {}))
    monkeypatch.setattr(replay, 'solve_classical', lambda p: {'weights': np.zeros(6)})
    monkeypatch.setattr(replay, '_choose_qoptima', lambda p, **kw: (np.full(6,.15), {'status':'test'}))
    monkeypatch.setattr(integration, 'apply_gamma_risk205', lambda *a, **kw:
        (np.zeros(6), {'actual_one_event_transaction_cost':0.0, 'actual_turnover_l1':0.0}, {}))
    out = replay._walk_forward_method(r, method='sample', params={}, alpha=1, lookback=84,
        rebalance=5, max_name=.3, risk_aversion=3, turnover_penalty=.2,
        risk_free_rate=.04, transaction_cost_bps=5, correlation_blend=.65,
        true_replay=True, replay_qscan_assets=6, replay_bits=1,
        replay_backend='exact', seed=1, start_from_cash=True, gamma_risk205_enabled=True)
    assert len(out['rebalances']) == 2
    assert out['rebalances'][1]['actual_entry_weights'] == [0.0]*6
    assert out['rebalances'][1]['decision_known_weights'] == [0.0]*6
    assert out['rebalances'][1]['qoptima_turnover'] == 0.0
    assert out['legacy_cash_reset_audit']['qoptima_path_events'] == 0


def test_preserved_legacy_cash_reset_is_explicitly_audited(monkeypatch):
    import backend.qoptima.backtest_overlay as replay
    r = pd.DataFrame(0.0, index=pd.bdate_range('2020-01-01', periods=94), columns=SYMS)
    monkeypatch.setattr(replay, '_make_problem', lambda hist, symbols, current, **kw:
        (problem(current_weights=current), {}))
    monkeypatch.setattr(replay, 'solve_classical', lambda p: {'weights': np.zeros(6)})
    monkeypatch.setattr(replay, '_choose_qoptima', lambda p, **kw: (np.zeros(6), {'status':'test'}))
    out = replay._walk_forward_method(r, method='sample', params={}, alpha=1, lookback=84,
        rebalance=5, max_name=.3, risk_aversion=3, turnover_penalty=.2,
        risk_free_rate=.04, transaction_cost_bps=5, correlation_blend=.65,
        true_replay=True, replay_qscan_assets=6, replay_bits=1,
        replay_backend='exact', seed=1, start_from_cash=True)
    assert out['legacy_cash_reset_audit']['qoptima_path_events'] == 1
    event = next(x for x in out['rebalances'][1]['legacy_cash_reset_events'] if x['portfolio']=='qoptima')
    assert event['weights_before'] == [0.0]*6
    assert sum(event['weights_after']) == pytest.approx(1)
    assert out['rebalances'][1]['actual_entry_weights'] == event['weights_after']
    assert out['rebalances'][1]['qoptima_turnover'] == pytest.approx(1)


def test_calendar_rejects_false_horizon_even_if_end_and_realized_agree():
    rows=[gamma_row('2020-01-01'),gamma_row('2020-01-10'),gamma_row('2020-02-01')]
    rows[0]['metadata']['target_end']='2020-01-05'
    rows[0]['realized_at']='2020-01-05'
    with pytest.raises(GammaContractError,match='duration'):
        prepared(rows,session_index=pd.date_range('2019-12-01','2020-02-10'))


def test_generic_beta_map_cannot_bypass_pit_provenance():
    from backend.qoptima.gamma_integration import select_gamma_beta_state
    assert select_gamma_beta_state([{'as_of_session':'2020-01-01','production_frozen':True}], '2020-01-02') is None


def test_gamma_cannot_silently_skip_first_unavailable_v1a_period():
    import backend.qoptima.backtest_overlay as replay
    r = pd.DataFrame(np.random.default_rng(17).normal(0, .005, (105, 6)),
        index=pd.bdate_range('2020-01-01', periods=105), columns=SYMS)
    with pytest.raises(ValueError, match='no causal V1A artifact'):
        replay._walk_forward_method(r, method='sample', params={}, alpha=1, lookback=84,
            rebalance=5, max_name=.3, risk_aversion=3, turnover_penalty=.2,
            risk_free_rate=.04, transaction_cost_bps=5, correlation_blend=.65,
            true_replay=True, replay_qscan_assets=6, replay_bits=1,
            replay_backend='exact', seed=1, gamma_enabled=True, v1a_enabled=True)


def test_gamma_cannot_report_empty_universe_coverage_as_numeric_result():
    import backend.qoptima.backtest_overlay as replay
    r = pd.DataFrame(np.nan, index=pd.bdate_range('2020-01-01', periods=105), columns=SYMS)
    with pytest.raises(ValueError, match='scored 0 of 21 sessions'):
        replay._walk_forward_method(r, method='sample', params={}, alpha=1, lookback=84,
            rebalance=5, max_name=.3, risk_aversion=3, turnover_penalty=.2,
            risk_free_rate=.04, transaction_cost_bps=5, correlation_blend=.65,
            true_replay=True, replay_qscan_assets=6, replay_bits=1,
            replay_backend='exact', seed=1, gamma_enabled=True)


@pytest.mark.parametrize('lag', [0, 1])
def test_auto_v1a_first_training_cutoff_obeys_research_clock(monkeypatch, lag):
    import backend.qoptima.backtest_overlay as replay
    import backend.qoptima.preset_backtest as preset
    prices = pd.DataFrame(100.0, index=pd.bdate_range('2018-01-01', periods=400), columns=SYMS)
    monkeypatch.setattr(replay, '_fetch_price_panel', lambda *a, **kw: (prices, {}))
    observed = {}
    def capture(*a, **kw):
        observed.update(kw)
        raise RuntimeError('training boundary captured')
    monkeypatch.setattr(preset, 'build_causal_v1a_artifact_schedule', capture)
    with pytest.raises(RuntimeError, match='training boundary captured'):
        replay.run_qoptima_backtest_overlay(1, dict(universe=list(SYMS), evaluation_period='3mo',
            lookback_sessions=84, v1a_enabled=True, v1a_auto_train=True,
            gamma_decision_lag_sessions=lag))
    # Scheduler chooses its training cutoff strictly BEFORE evaluation_start.
    assert observed['evaluation_start'] == prices.index[-63-lag-int(lag > 0)]


def test_lagged_universe_membership_cannot_use_entry_day_snapshot(monkeypatch):
    import backend.qoptima.backtest_overlay as replay
    r=pd.DataFrame(np.random.default_rng(2).normal(0,.005,(89,6)),
        index=pd.bdate_range('2020-01-01',periods=89),columns=SYMS)
    selected=[]
    def build(hist,symbols,current,**kw):
        selected.append(tuple(symbols))
        return problem(current_weights=current),{}
    monkeypatch.setattr(replay,'_make_problem',build)
    monkeypatch.setattr(replay,'solve_classical',lambda p:{'weights':np.full(6,.15)})
    monkeypatch.setattr(replay,'_choose_qoptima',lambda p,**kw:(np.full(6,.15),{'status':'test'}))
    schedule={str(r.index[82].date()):{'status':'eligible','symbols':list(SYMS)},
              str(r.index[83].date()):{'status':'eligible','symbols':list(SYMS[1:])},
              str(r.index[84].date()):{'status':'eligible','symbols':list(SYMS[1:])}}
    out=replay._walk_forward_method(r,method='sample',params={},alpha=1,lookback=84,rebalance=5,
        max_name=.3,risk_aversion=3,turnover_penalty=.2,risk_free_rate=0,transaction_cost_bps=5,
        correlation_blend=.65,true_replay=True,replay_qscan_assets=6,replay_bits=1,
        replay_backend='exact',seed=1,gamma_decision_lag_sessions=1,universe_schedule=schedule)
    assert selected==[SYMS,SYMS]
    assert out['rebalances'][0]['universe_membership_cutoff']==str(r.index[82])


def test_risk205_infeasible_component_cap_blocks_instead_of_vanishing_gross():
    p=problem()
    with pytest.raises(GammaContractError,match="at least 10"):
        apply_gamma_risk205(p,p.current_weights,returns_history=np.zeros((90,6)),feature_cutoff='2020-01-31')


def test_state_overlay_only_is_explicit_and_does_not_inherit_v203_cap():
    state={'gamma':.8,'es_calibrated':-.05,'positive_definite':True,'signed_control':False,'information_cutoff':'2020-01-31'}
    p=problem(v1a_state_metadata=state)
    w,audit,counters=apply_gamma_risk205(p,p.current_weights,
        returns_history=np.random.default_rng(5).normal(0,.01,(100,6)),feature_cutoff='2020-01-31',
        scope='state_overlay_only',actual_transaction_cost_bps=5)
    assert not audit['inherited_v203_supervisor_applied']
    assert audit['risk_audit']['baseline']=='QRM_approved_QPO_proposal_not_V203'
    assert audit['risk_audit']['state_combination']['combination_rule']=='max_not_sum'
    assert np.all(w<=p.current_weights) and np.allclose(w/w.sum(),p.current_weights/p.current_weights.sum())
    assert w.sum()<p.current_weights.sum() and counters['v1a_persistence_count']==1


def test_gamma_dynamic_membership_fails_before_market_fetch(monkeypatch):
    from backend.qoptima import backtest_overlay as replay
    monkeypatch.setattr(replay,'_fetch_price_panel',lambda *a,**kw:pytest.fail('must not fetch incompatible Gamma universe'))
    with pytest.raises(ValueError,match='dynamic universe_snapshots'):
        replay.run_qoptima_backtest_overlay(1,{'gamma_enabled':True,'universe_snapshots':[{'symbols':['A']} ]})



def test_lagged_auto_schedule_is_accepted_at_first_feature_cutoff(monkeypatch, tmp_path):
    import json
    from pathlib import Path
    import backend.qoptima.backtest_overlay as replay
    import backend.qoptima.preset_backtest as preset
    import backend.qoptima.v1a.inference as inference
    prices = pd.DataFrame(100.0, index=pd.bdate_range('2015-01-01 04:00', periods=1200, normalize=False), columns=SYMS)
    monkeypatch.setattr(replay, '_fetch_price_panel', lambda *a, **kw: (prices, {}))
    stored = {}
    monkeypatch.setattr(preset, 'build_default_market_features', lambda r: r.copy())
    monkeypatch.setattr(preset, 'train_v1a', lambda features, targets, **kw:
        {'metadata': {'artifact_information_cutoff': str(features.index[-1]),
                      'config': kw['config'].as_dict(), 'feature_names': list(features.columns),
                      'signed_control': kw['signed_control']}})
    def save(result, path):
        stored[str(path)] = result['metadata']
        # The cache now validates temporary bytes before atomic publication.
        # Use a real tiny fake artifact so readback/rename preserve this test's
        # strict first-feature-cutoff assertions.
        Path(path).write_text(json.dumps(result['metadata']))
    monkeypatch.setattr(preset, 'save_v1a_artifact', save)
    fake_load = lambda path: (None, json.loads(Path(path).read_text()), None, 'test-digest')
    monkeypatch.setattr(inference, 'load_v1a_artifact', fake_load)
    monkeypatch.setattr(preset, 'load_v1a_artifact', fake_load)
    def first_decision(returns, **kw):
        first_feature_cutoff = returns.index[kw['lookback']-2]
        schedule = kw['v1a_artifact_schedule']
        path, meta = inference.select_causal_artifact(schedule, str(first_feature_cutoff))
        assert path is not None, meta
        artifact_cutoff = pd.Timestamp(meta['artifact_information_cutoff'])
        assert artifact_cutoff < first_feature_cutoff.tz_localize('UTC')
        # Preserve the strict contract: the same artifact is rejected at equality.
        same, reason = inference.select_causal_artifact(schedule, str(artifact_cutoff))
        assert same is None and reason['status'] == 'NO_CAUSAL_ARTIFACT'
        raise RuntimeError('strict first-decision selection verified')
    monkeypatch.setattr(replay, '_walk_forward_method', first_decision)
    with pytest.raises(RuntimeError, match='strict first-decision selection verified'):
        replay.run_qoptima_backtest_overlay(1, dict(universe=list(SYMS), evaluation_period='3mo',
            lookback_sessions=84, v1a_enabled=True, v1a_auto_train=True,
            v1a_initial_train_sessions=756, v1a_artifact_directory=str(tmp_path),
            gamma_decision_lag_sessions=1, run_covariance_benchmarks=False,
            covariance_methods=['shrinkage'], run_alpha_frontier=False))
