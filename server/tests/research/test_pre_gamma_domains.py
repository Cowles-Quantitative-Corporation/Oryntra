from dataclasses import replace
import numpy as np
import pytest

from backend.qoptima.problem import QOptimaPortfolioProblem
from backend.qoptima.backtest_overlay import _choose_qoptima, _replay_viability
from backend.qoptima.full_qrs import build_exact_full_qrs_diagonal_objective, score_full_qrs
from backend.qoptima.gamma_integration import apply_gamma_risk205


def domain_problem(n=12, cap=1/11, rho=.6):
    covariance = .04 * (rho*np.ones((n,n))+(1-rho)*np.eye(n))
    return QOptimaPortfolioProblem(symbols=tuple(f'D{i}' for i in range(n)),
        current_weights=np.zeros(n), expected_returns=np.linspace(.10,.15,n),
        covariance=covariance, target_gross=1, max_name_weight=cap,
        risk_free_rate=.04, transaction_cost_bps=5, horizon_sessions=21,
        full_qrs_enabled=True, full_qrs_bootstrap_reps=0,
        full_qrs_stress_covariances={'BASE': covariance, 'STRESS': covariance*1.1})


def choose(p, minimum=11, bits=1):
    return _choose_qoptima(p, qscan_assets=min(p.n,12), bits=bits, backend='exact', seed=19,
        full_qrs_primary=True, full_qrs_exact_variable_cap=12,
        full_qrs_decision_mode='exact_reference', simulator_audit=False,
        minimum_positive_positions=minimum)


def test_broad_domain_has_twelve_exact_portfolios_and_preserves_literal_score():
    p = domain_problem()
    objective = build_exact_full_qrs_diagonal_objective(p, list(range(12)), bits_per_asset=1,
        viability_callback=lambda w: _replay_viability(p,w,11)['passes_qrm_structural_gates'])
    feasible = np.flatnonzero(objective.feasible_table())
    assert len(feasible) == 12
    for index in feasible:
        w = objective.full_weights([(index >> i)&1 for i in range(12)])
        assert np.count_nonzero(w) == 11 and w.sum() == pytest.approx(1)
        literal = score_full_qrs(p,w,margin_feasible=True,bootstrap_seed=41)['qrs']
        assert objective.qrs_table()[index] == pytest.approx(literal)
        assert objective.raw_energy_table()[index] == pytest.approx(-literal)


def test_insufficient_cardinality_is_rejected_before_qrm_scoring(monkeypatch):
    import backend.qoptima.backtest_overlay as replay
    monkeypatch.setattr(replay,'evaluate_risk',lambda *a,**kw: pytest.fail('must reject before Full-QRS scoring'))
    result = _replay_viability(domain_problem(),np.r_[np.full(10,1/11),0,0],11)
    assert not result['passes_qrm_structural_gates']
    assert result['domain_rejected_before_full_qrs']


def test_broad_exact_domain_rejects_all_qrm_failures_without_fallback():
    with pytest.raises(ValueError,match='gate_rejections=.*correlation_stress_ok'):
        choose(domain_problem(rho=.1))


def test_opt_in_cash_policy_uses_actual_rejected_exact_domain():
    from backend.qoptima.backtest_overlay import _choose_with_rejection_policy
    p=domain_problem(rho=.1)
    target,metadata,event=_choose_with_rejection_policy(p,policy='cash_on_qrm_rejection',
        context={'decision_date':'2020-01-31','portfolio_path':'qoptima'},
        qscan_assets=12,bits=1,backend='exact',seed=19,full_qrs_primary=True,
        full_qrs_exact_variable_cap=12,full_qrs_decision_mode='exact_reference',
        simulator_audit=False,minimum_positive_positions=11)
    assert np.array_equal(target,np.zeros(12))
    assert metadata['status']=='POLICY_CASH_ON_QRM_REJECTION'
    assert event['failed_gates']['correlation_stress_ok']==12
    assert not event['optimized_qrs_available']


def test_broad_candidate_reserve_keeps_all_eleven_drifted_holdings_selected():
    p = domain_problem(n=14)
    held = np.r_[np.linspace(.04,.085,11),0,0,0]
    _w, metadata = choose(replace(p,current_weights=held))
    assert set(np.flatnonzero(held)).issubset(metadata['qscan']['selected_indices'])
    assert metadata['qscan']['research_domain']['candidate_reserve'] == 1
    assert metadata['full_qrs_optimization']['validation_reference']['variables'] == 12


@pytest.mark.parametrize('scope,n,cap,minimum,bits',[
    ('state_overlay_only',6,.30,0,2),
    ('full_supervisor',12,1/11,11,1),
])
def test_both_declared_pre_gamma_domains_use_actual_exact_core_and_risk(scope,n,cap,minimum,bits):
    p = domain_problem(n=n,cap=cap)
    proposal, metadata = choose(p,minimum=minimum,bits=bits)
    rng = np.random.default_rng(22)
    market = rng.normal(0,.007,(252,1))
    history = market + rng.normal(0,.004,(252,n))
    final,audit,_state = apply_gamma_risk205(p,proposal,returns_history=history,
        feature_cutoff='2020-01-31',scope=scope,actual_transaction_cost_bps=5)
    assert metadata['status'] == 'VIABLE_EXACT_FULL_QRS_REFERENCE'
    assert audit['scope'] == scope
    assert audit['final_qrm']['passes_qrm_structural_gates']
    assert np.all(final <= proposal+1e-10)
    assert audit['component_risk_diagnostics']['positive_positions'] == np.count_nonzero(final > 1e-12)
    if minimum:
        assert np.count_nonzero(proposal > 1e-12) >= minimum
        assert audit['component_risk_diagnostics']['supervisor_ensemble_maximum_share'] is not None
    else:
        assert not audit['inherited_v203_supervisor_applied']
