from dataclasses import replace
import numpy as np
import pandas as pd
import pytest
from test_pre_gamma_domains import domain_problem
from backend.qoptima.qpo_budget_completion import choose_qpo_budget_completion
from backend.qoptima.gamma_construction import budget_completion_candidates, gross_lattice_diagnostics
from backend.qoptima.backtest_overlay import _run_holding_window, _metrics, _make_problem, _choose_qoptima
from backend.qoptima.qrm import evaluate_risk
from backend.qoptima.full_qrs import score_full_qrs


def test_declared_budget_domain_exact_score_and_unchanged_qrm():
    p=replace(domain_problem(n=10,cap=.3),current_weights=np.r_[np.zeros(6),[.04,.02,.02,.06]])
    assert not gross_lattice_diagnostics(p,range(6),2)['gross_feasible']
    w,meta=choose_qpo_budget_completion(p,qscan_assets=6,bits=2)
    assert w.sum()==pytest.approx(1,abs=1e-10)
    assert evaluate_risk(p,w)['passes_qrm_structural_gates']
    selected=meta['selected_indices'];frozen=list(set(range(p.n))-set(selected))
    np.testing.assert_array_equal(w[frozen],p.current_weights[frozen])
    assert meta['quantum_execution'] is False and meta['risk_limits_relaxed'] is False
    viable=[]
    for c in budget_completion_candidates(p,selected,2):
        risk=evaluate_risk(p,c)
        if risk['passes_qrm_structural_gates']:viable.append(score_full_qrs(p,c,margin_feasible=True)['qrs'])
    assert meta['pre_overlay_qrs']==pytest.approx(max(viable))


def test_no_force_investment_on_other_failed_gates():
    from backend.qoptima.backtest_overlay import QPOResearchRejection
    with pytest.raises(QPOResearchRejection):
        choose_qpo_budget_completion(domain_problem(n=6,cap=.3,rho=.001),qscan_assets=6,bits=2)


def test_cash_zero_borrowing_4_and_cost_once_independent_accounting():
    for initial in [np.array([0.,0.]),np.array([.2,.3]),np.array([.7,.4])]:
        frame=pd.DataFrame([[.1,-.1],[-.05,.03],[.02,.04]])
        ret,end,_=_run_holding_window(initial,frame,risk_free_rate=.04,lending_rate=0.,borrowing_rate=.04,transaction_cost=.001)
        assets=initial.copy()*.999;cash=(1-initial.sum())*.999;equity=1;expected=[]
        for row in frame.to_numpy():
            assets*=1+row
            cash*=1 if cash>=0 else 1.04**(1/252)
            value=assets.sum()+cash;expected.append(value/equity-1);equity=value
        np.testing.assert_allclose(ret,expected,rtol=1e-12,atol=1e-15)
        np.testing.assert_allclose(end,assets/equity,rtol=1e-12,atol=1e-15)
        if initial.sum()==0:np.testing.assert_allclose(ret,[-.001,0,0],atol=1e-15)


def test_score_financing_separate_from_metric_benchmark():
    rng=np.random.default_rng(18);hist=pd.DataFrame(rng.normal(.001,.01,(252,6)),columns=list('ABCDEF'),index=pd.bdate_range('2020-01-01',periods=252))
    p,_=_make_problem(hist,list(hist.columns),np.full(6,1/6),covariance_method='shrinkage',covariance_parameters={'estimator':'ledoit_wolf'},alpha=1,max_name_weight=.3,risk_aversion=3,turnover_penalty=.2,risk_free_rate=.04,lending_rate=0.,borrowing_rate=.04,transaction_cost_bps=5,correlation_blend=.65,gamma_score_basis=True,required_forecast_horizon_sessions=21,full_qrs_enabled=True,full_qrs_bootstrap_reps=0)
    assert p.risk_free_rate==p.lending_rate==0
    assert p.borrowing_rate==pytest.approx(12*(1.04**(1/12)-1))
    w=np.full(6,.1);score=score_full_qrs(p,w)
    assert score['cash_lending_return']==0 if 'cash_lending_return' in score else p.lending_rate==0
    rets=pd.Series([.01,-.01,.02,-.001]*20)
    assert _metrics(rets,.04)['sharpe'] < _metrics(rets,0)['sharpe']


def test_forecast_disabled_budget_dispatch_is_explicit():
    p=domain_problem(n=6,cap=.3)
    w,meta=_choose_qoptima(p,qscan_assets=6,bits=2,backend='exact',seed=1,full_qrs_primary=True,full_qrs_decision_mode='exact_reference',simulator_audit=False,construction_policy='qpo_budget_completion_reference')
    assert meta['policy']=='qpo_budget_completion_reference'
    with pytest.raises(ValueError):
        _choose_qoptima(p,qscan_assets=6,bits=2,backend='exact',seed=1,full_qrs_primary=True,full_qrs_decision_mode='exact_reference',simulator_audit=False,construction_policy='qpo_budget_completion_reference',risk_transform=lambda w:w)
