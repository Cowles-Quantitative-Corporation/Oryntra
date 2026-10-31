from dataclasses import replace
from copy import deepcopy
import numpy as np
import pytest
from test_pre_gamma_domains import domain_problem
from backend.qoptima.gamma_construction import budget_completion_candidates, gross_lattice_diagnostics, choose_joint_reference
from backend.qoptima.qrm import evaluate_risk


def test_budget_completion_solves_drifted_lattice_without_relaxing_caps_or_frozen_holdings():
    p=replace(domain_problem(n=10,cap=.30),current_weights=np.r_[np.zeros(6),[.04,.02,.02,.06]])
    assert not gross_lattice_diagnostics(p,range(6),2)['gross_feasible']
    states=list(budget_completion_candidates(p,range(6),2))
    assert states and len(states)==len({tuple(np.round(w,12)) for w in states})
    for w in states:
        assert w.sum()==pytest.approx(1,abs=1e-10)
        assert np.all(w>=0) and np.all(w<=.30+1e-10)
        np.testing.assert_array_equal(w[6:],p.current_weights[6:])
    final,meta=choose_joint_reference(p,policy='budget_completion_joint_reference',qscan_assets=6,bits=2)
    assert evaluate_risk(p,final)['passes_qrm_structural_gates']
    assert meta['quantum_execution'] is False and meta['risk_limits_relaxed'] is False


def test_impossible_frozen_holdings_cannot_be_hidden_by_budget_completion():
    p=replace(domain_problem(n=7,cap=.30),current_weights=np.r_[np.zeros(6),.31])
    assert list(budget_completion_candidates(p,range(6),2))==[]


def test_joint_search_uses_final_score_and_frozen_state_with_actual_current_cost():
    p=domain_problem(n=6,cap=.30);prior={'count':3};seen=[]
    def transform(w):
        state=deepcopy(prior);seen.append(state['count']);state['count']+=1
        final=w*(.40 if w[0]>.15 else .80)
        return final,{'scope':'state_overlay_only','state_before':prior.copy(),'actual_turnover_l1':float(np.abs(final-p.current_weights).sum())},state
    final,meta=choose_joint_reference(p,policy='budget_completion_joint_reference',qscan_assets=6,bits=2,risk_transform=transform)
    assert prior=={'count':3} and set(seen)=={3} and meta['joint_risk_state']=={'count':4}
    qrm=evaluate_risk(replace(p,target_gross=float(final.sum())),final)
    assert qrm['passes_qrm_structural_gates'] and meta['final_qrs']==pytest.approx(qrm['metrics']['qrs'])
    assert meta['joint_risk205_audit']['actual_turnover_l1']==pytest.approx(np.abs(final-p.current_weights).sum())


def test_expanded_reference_can_find_correlated_cluster_outside_tight_pool():
    p=domain_problem(n=24,cap=1/11,rho=.5)
    final,meta=choose_joint_reference(p,policy='expanded_joint_reference',qscan_assets=12,bits=1,minimum_positions=11)
    assert final.sum()==pytest.approx(1) and final.max()<=1/11+1e-8
    assert evaluate_risk(p,final)['passes_qrm_structural_gates'] and meta['global_optimality_claim'] is False


def test_final_component_cap_is_enforced_not_merely_reported():
    from backend.qoptima.backtest_overlay import QPOResearchRejection
    p=domain_problem(n=12)
    def transform(w):
        return w,{'scope':'full_supervisor','component_risk_diagnostics':{'base_covariance_within_limit':True,'supervisor_ensemble_within_limit':False}},{}
    with pytest.raises(QPOResearchRejection,match='global infeasibility is not established') as exc:
        choose_joint_reference(p,policy='expanded_joint_reference',qscan_assets=12,bits=1,minimum_positions=11,risk_transform=transform)
    assert exc.value.research_diagnostics['failed_gates']['post_component_risk_cap']>0


def test_search_continues_when_only_lower_prescore_candidate_passes_final_gate():
    from backend.qoptima.gamma_integration import GammaQRMRejection
    p=domain_problem(n=6,cap=.30);calls=[]
    def transform(w):
        calls.append(w.copy())
        if len(calls)<=24:raise GammaQRMRejection('blocked',qrm={},stage='post_risk205')
        return w,{'scope':'state_overlay_only'},{}
    w,meta=choose_joint_reference(p,policy='budget_completion_joint_reference',qscan_assets=6,bits=2,risk_transform=transform)
    assert len(calls)==48 and meta['counts']['post_post_risk205']==24
    assert evaluate_risk(p,w)['passes_qrm_structural_gates']


def test_repaired_variants_have_explicit_honest_routing_and_separate_adaptation():
    from backend.models.pre_gamma import pre_gamma_policy
    from backend.models.registry import get_model_spec
    from backend.control_research import _model_settings_scope
    # Registry model identity and specific overrides preserve the old options.
    baseline=pre_gamma_policy('pre_gamma_full_risk')
    assert 'gamma_construction_policy' not in baseline['qpo_overrides']
    adaptive=pre_gamma_policy('pre_gamma_adaptive_joint')
    assert adaptive['gamma_config_overrides']=={'alpha_weighting':'matured_feature_ic'}
    for identity in ['pre_gamma_budget_joint','pre_gamma_expanded_joint','pre_gamma_adaptive_joint']:
        scope=_model_settings_scope(get_model_spec(identity),{'risk_model':'v205'},completed=True)
        assert scope['applied']['quantum_execution'] is False
        assert scope['applied']['construction'].endswith('joint_reference')
        assert scope['applied']['risk_model'].endswith('candidate_level_final_qrm')


def test_preselected_causal_beta_record_is_selection_idempotent():
    from backend.qoptima.gamma_integration import select_gamma_beta_state
    from _research_fixtures import _pit_beta_record
    from copy import deepcopy
    rows=[_pit_beta_record()];future=deepcopy(rows[0]);future.update(as_of='2026-01-01',available_at='2026-01-01')
    rows.append(future)
    chosen=select_gamma_beta_state(rows,'2025-01-06')
    assert chosen is not None
    assert select_gamma_beta_state([deepcopy(chosen)],'2025-01-06')==chosen
    assert select_gamma_beta_state([chosen],'2024-12-31') is None


def test_cheap_gates_are_literal_qrm_equivalent_on_random_and_zero_weights():
    from backend.qoptima.gamma_construction import _cheap_qrm_gates
    from backend.qoptima.qrm import _correlation_stress_covariance
    p=domain_problem(n=12);stress=_correlation_stress_covariance(p.covariance);rng=np.random.default_rng(17)
    for w in [np.zeros(12),*list(rng.uniform(0,.13,(40,12)))]:
        cheap=_cheap_qrm_gates(p,w,stress);actual=evaluate_risk(p,w)['structural_gates']
        assert all(value==actual[key] for key,value in cheap.items())


def test_candidate_ranking_does_not_retain_per_candidate_provenance_reports(monkeypatch):
    import backend.qoptima.gamma_construction as construction
    counter={'alive':0,'peak':0,'calls':0}
    class Report(dict):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs);counter['alive']+=1;counter['calls']+=1
            counter['peak']=max(counter['peak'],counter['alive'])
        def __del__(self):counter['alive']-=1
    def risk(problem,w):
        return Report(passes_qrm_structural_gates=True,structural_gates={},metrics={'qrs':float(np.arange(len(w))@w)})
    monkeypatch.setattr(construction,'evaluate_risk',risk)
    construction.choose_joint_reference(domain_problem(n=6,cap=.3),policy='budget_completion_joint_reference',qscan_assets=6,bits=2)
    assert counter['calls']>100
    assert counter['peak']<=4
