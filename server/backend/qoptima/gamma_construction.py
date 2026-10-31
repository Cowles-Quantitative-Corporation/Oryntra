"""Explicit classical construction alternatives for Pre-Gamma research.

The original QPO encoding and quantum paths are not changed. These are bounded,
causal candidate searches, not quantum solvers or proofs of global feasibility.
Every accepted candidate retains the original Full-QRS and QRM definitions.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import replace
import itertools
from typing import Callable, Any
import numpy as np
from scipy.optimize import minimize
from .qrm import evaluate_risk, _correlation_stress_covariance, DEFAULT_QRM_GATES
from .qscan import scan_universe
from .scoring_memo import memoized_metrics_problem

POLICIES = frozenset({'legacy_exact', 'budget_completion_joint_reference', 'expanded_joint_reference'})


def gross_lattice_diagnostics(problem, selected, bits):
    selected = np.asarray(selected, dtype=int)
    frozen = np.asarray(problem.current_weights, dtype=float).copy(); frozen[selected] = 0
    step = float(problem.max_name_weight)/(2**int(bits)-1)
    totals = frozen.sum() + step*np.arange(len(selected)*(2**int(bits)-1)+1)
    tolerance = max(DEFAULT_QRM_GATES['gross_tolerance'], .03*problem.target_gross)
    errors = np.abs(totals-problem.target_gross)
    return {'fixed_unselected_gross': float(frozen.sum()), 'step': step,
            'target_gross': float(problem.target_gross), 'qrm_gross_tolerance': tolerance,
            'nearest_gross_error': float(errors.min()),
            'gross_feasible_level_count': int(np.sum(errors <= tolerance+1e-12)),
            'gross_feasible': bool(np.any(errors <= tolerance+1e-12)),
            'scope': 'gross_only_not_all_risk_constraints'}


def budget_completion_candidates(problem, selected, bits):
    """Finite union: one selected coordinate completes the exact residual budget.

    Each of the selected names takes a turn as completion coordinate; remaining
    coordinates retain the original grid. Unselected holdings remain frozen.
    Completion is part of the declared candidate domain, never an ex-post repair.
    """
    selected = tuple(int(i) for i in selected)
    if not selected or len(set(selected)) != len(selected) or any(i < 0 or i >= problem.n for i in selected):
        raise ValueError('Budget completion requires unique in-range selected indices')
    if not isinstance(bits, int) or not 1 <= bits <= 5 or bits*(len(selected)-1) > 16:
        raise ValueError('Budget completion finite domain exceeds the explicit 16-variable research bound')
    fixed = np.asarray(problem.current_weights, dtype=float).copy(); fixed[list(selected)] = 0
    cap = float(problem.max_name_weight); target = float(problem.target_gross)
    if np.any(fixed > cap+1e-10) or fixed.sum() > target+1e-10:
        return
    levels = np.linspace(0, cap, 2**bits)
    seen = set()
    for completion in selected:
        others = tuple(i for i in selected if i != completion)
        for coordinates in itertools.product(levels, repeat=len(others)):
            residual = target-float(fixed.sum())-sum(coordinates)
            if residual < -1e-10 or residual > cap+1e-10:
                continue
            w = fixed.copy(); w[list(others)] = coordinates; w[completion] = np.clip(residual, 0, cap)
            key = tuple(np.round(w, 12))
            if key not in seen:
                seen.add(key); yield w


def _project_budget(values, target, cap):
    x = np.asarray(values, dtype=float)
    if not np.isfinite(x).all() or target <= 0 or len(x)*cap < target-1e-10:
        raise ValueError('Infeasible capped simplex')
    low, high = float(x.min()-cap), float(x.max())
    for _ in range(100):
        mid = (low+high)/2
        if np.clip(x-mid, 0, cap).sum() > target: low = mid
        else: high = mid
    return np.clip(x-(low+high)/2, 0, cap)


def expanded_candidates(problem, selected, minimum_positions):
    """Deterministic all-universe feasibility proposals, bounded at 8 SLSQP fits.

    Covariance-cluster starts deliberately test whether the relative correlation
    stress constraint admits a portfolio outside the very narrow QScan lattice.
    These use decision-known covariance and forecasts, never realized outcomes.
    """
    n = problem.n; target = float(problem.target_gross); cap = float(problem.max_name_weight)
    minimum = max(int(minimum_positions), int(np.ceil(target/cap-1e-10)))
    if minimum > n:
        return
    cov = np.asarray(problem.covariance, dtype=float)
    vol = np.sqrt(np.maximum(np.diag(cov), 1e-12)); corr = cov/np.outer(vol,vol)
    starts = []
    def add_order(order, size):
        chosen = np.asarray(list(dict.fromkeys(map(int, order)))[:min(n,size)], dtype=int)
        if len(chosen)*cap < target-1e-10: return
        w = np.zeros(n); w[chosen] = target/len(chosen); starts.append(w)
    add_order(selected, len(selected))
    for size in sorted({minimum, min(n,minimum+1), min(n,2*minimum), n}):
        add_order(np.argsort(-np.asarray(problem.expected_returns), kind='stable'),size)
        add_order(np.argsort(vol, kind='stable'),size)
    for anchor in range(n):
        order = np.argsort(-corr[anchor],kind='stable')
        for size in sorted({minimum, min(n,minimum+1), min(n,2*minimum)}): add_order(order,size)
    starts += [_project_budget(np.asarray(problem.current_weights),target,cap),
               _project_budget((1/vol)/(1/vol).sum()*target,target,cap)]
    # Base covariance component cap is necessary; actual supervisor ensemble is
    # enforced again after transformation. Smooth constraints do not replace QRM.
    stress = _correlation_stress_covariance(cov)
    ratio2 = DEFAULT_QRM_GATES['max_correlation_stress_vol_ratio']**2
    def constraints(w):
        var = max(float(w@cov@w),1e-14)
        return np.r_[.24-float(w@w), (ratio2*var-float(w@stress@w))/var,
                     .30-(-DEFAULT_QRM_GATES['concentrated_other_shock']*target +
                     (DEFAULT_QRM_GATES['concentrated_other_shock']-DEFAULT_QRM_GATES['concentrated_top_shock'])*np.sort(w)[-3:].sum()),
                     .10-(w*(cov@w))/var]
    scores = [(float(np.minimum(constraints(w),0)@np.minimum(constraints(w),0)), i) for i,w in enumerate(starts)]
    fit_ids = [i for _,i in sorted(scores)[:4]]
    fit_ids += [i for _,i in sorted(scores,reverse=True)[:2]]
    fit_ids += [len(starts)-1,len(starts)-2]
    mu = np.asarray(problem.expected_returns)
    scale = max(float(np.std(mu)),.01)
    for i in dict.fromkeys(fit_ids):
        result = minimize(lambda w: float(.10*(w@cov@w)/max(float(np.mean(np.diag(cov))),1e-8)-mu@w/scale),
            starts[i], method='SLSQP', bounds=[(0,cap)]*n,
            constraints=[{'type':'eq','fun':lambda w: w.sum()-target}, {'type':'ineq','fun':constraints}],
            options={'maxiter':120,'ftol':1e-9})
        if result.success and np.isfinite(result.x).all() and abs(result.x.sum()-target) < 1e-7:
            starts.append(np.asarray(result.x))
    seen=set()
    for w in starts:
        key=tuple(np.round(w,12))
        if key not in seen: seen.add(key); yield w


def _cheap_qrm_gates(problem, w, stress_covariance):
    """Exact inexpensive QRM gates, before computing Full-QRS on rejected states."""
    cfg=DEFAULT_QRM_GATES
    normal=float(np.sqrt(max(0.0,float(w@problem.covariance@w))))
    stressed=float(np.sqrt(max(0.0,float(w@stress_covariance@w))))
    ratio=stressed/normal if normal>1e-12 else float('inf')
    order=np.argsort(-w);shock=np.full(problem.n,cfg['concentrated_other_shock'])
    shock[order[:min(cfg['concentrated_top_n'],problem.n)]]=cfg['concentrated_top_shock']
    return {
        'gross_ok':abs(float(w.sum())-problem.target_gross)<=max(cfg['gross_tolerance'],.03*problem.target_gross),
        'position_caps_ok':bool((w<=problem.max_name_weight+1e-8).all() and (w>=-1e-10).all()),
        'diversification_ok':int(np.sum(w>.005))>=min(problem.n,max(2,cfg['min_positions'])),
        'concentration_hhi_ok':float(np.square(w).sum())<=cfg['max_hhi']+1e-12,
        'correlation_stress_ok':ratio<=cfg['max_correlation_stress_vol_ratio']+1e-12,
        'concentrated_stress_ok':float(w@shock)>=-abs(cfg['max_concentrated_stress_loss'])}


def choose_joint_reference(problem, *, policy, qscan_assets, bits, minimum_positions=0,
                           risk_transform: Callable | None = None, finalist_limit=24, maximum_finalists=96):
    """Rank final approved portfolios; each transform receives identical prior state.

    Search is bounded: score proposals, transform pre-QRS-ranked batches of 24
    up to 96 candidates if no risky portfolio passes, then choose maximum final Full-QRS. No claim of global optimum is made.
    """
    if policy not in POLICIES-{'legacy_exact'}: raise ValueError('Unknown explicit Gamma construction policy')
    p = memoized_metrics_problem(problem)
    reserve = max(1, min(2, qscan_assets//3))
    if minimum_positions: reserve = min(reserve, max(0,qscan_assets-minimum_positions))
    qscan=scan_universe(p,max_assets=min(qscan_assets,p.n),candidate_reserve=reserve)
    selected=list(qscan['selected_indices'])
    candidates=(budget_completion_candidates(p,selected,bits) if policy=='budget_completion_joint_reference'
                else expanded_candidates(p,selected,minimum_positions))
    counts=Counter(); ranked=[]
    stress_covariance=_correlation_stress_covariance(np.asarray(p.covariance))
    for serial,w in enumerate(candidates):
        counts['proposals']+=1
        if np.count_nonzero(w>1e-12)<minimum_positions: counts['minimum_positions']+=1; continue
        cheap=_cheap_qrm_gates(p,w,stress_covariance)
        if not all(cheap.values()):
            counts['cheap_gate_rejected']+=1
            for gate,passed in cheap.items():
                if not passed:counts['pre_'+gate]+=1
            continue
        qrm=evaluate_risk(p,w)
        if not qrm['passes_qrm_structural_gates']:
            for gate,passed in qrm['structural_gates'].items():
                if not passed: counts['pre_'+gate]+=1
            continue
        score=qrm['metrics'].get('qrs')
        if score is None or not np.isfinite(score): raise ValueError('Invalid Full-QRS on viable construction candidate')
        # Candidate provenance can contain the full matured forecast ledger.
        # Keep only scalar score and weights for ordering, never N copies of
        # that immutable ledger in a thousands-state budget-completion search.
        ranked.append((float(score),serial,w,None))
    ranked.sort(key=lambda row:(-row[0],tuple(np.round(row[2],12))))
    finalists=ranked[:maximum_finalists] if risk_transform is not None else ranked
    accepted=[]
    from .gamma_integration import GammaQRMRejection
    for candidate_index,(pre_score,serial,w,pre_qrm) in enumerate(finalists):
        # Continue deterministic batches after a rejected top group, bounded
        # by 96 transforms. Stop only at batch boundaries once risky feasible.
        if risk_transform is not None and candidate_index and candidate_index % finalist_limit == 0 and any(np.isfinite(row[0]) for row in accepted):
            break
        counts['finalists_evaluated']+=1
        try:
            final,audit,state=(risk_transform(w.copy()) if risk_transform is not None else (w.copy(),None,None))
        except GammaQRMRejection as exc:
            counts['post_'+exc.stage]+=1; continue
        final=np.asarray(final,dtype=float)
        if audit and audit.get('scope')=='full_supervisor':
            component=audit.get('component_risk_diagnostics') or {}
            if component.get('supervisor_ensemble_within_limit') is not True:
                counts['post_component_risk_cap']+=1; continue
        gross=float(final.sum())
        if gross <= 1e-12:
            # An authorized Risk205 cash decision is allowed, but cannot acquire
            # a fictitious optimized risky QRS or beat a finite risky score.
            counts['risk205_cash']+=1; accepted.append((-np.inf,serial,final,audit,state,None)); continue
        final_problem=replace(problem,target_gross=gross)
        qrm=evaluate_risk(final_problem,final)
        if not qrm['passes_qrm_structural_gates']:
            counts['final_qrm_rejected']+=1; continue
        score=qrm['metrics'].get('qrs')
        if score is None or not np.isfinite(score): raise ValueError('Invalid final Full-QRS in joint construction')
        accepted.append((float(score),serial,final,audit,state,None))
    diagnostics={'policy':policy,'backend':'bounded_classical_joint_full_qrs_reference',
        'quantum_execution':False,'global_optimality_claim':False,'qscan':qscan,
        'legacy_gross_lattice':gross_lattice_diagnostics(p,selected,bits),'counts':dict(counts),
        'pre_qrm_viable':len(ranked),'final_viable':len(accepted),'finalist_batch_size':finalist_limit,'maximum_finalists':maximum_finalists,
        'proposal_component_share_limit':.10,
        'proposal_constraint_scope':'default_base_covariance_heuristic_only_final_configured_ensemble_gate_is_authoritative',
        'selection_rule':'highest_final_Full_QRS_among_evaluated_QRM_and_Risk205_approved_candidates',
        'candidate_state_policy':'frozen_prior_per_candidate_commit_winner_once',
        'risk_limits_relaxed':False,'selected_indices':selected}
    if not accepted:
        from .backtest_overlay import QPOResearchRejection
        raise QPOResearchRejection('No viable portfolio in bounded joint reference search; global infeasibility is not established',
            {'rejection_reason':'NO_VIABLE_BOUNDED_JOINT_CANDIDATE','solver_diagnostics':diagnostics,'failed_gates':dict(counts)})
    accepted.sort(key=lambda row:(-row[0],tuple(np.round(row[2],12))));score,serial,final,audit,state,qrm=accepted[0]
    if float(final.sum())>1e-12:
        qrm=evaluate_risk(replace(problem,target_gross=float(final.sum())),final)
    diagnostics.update(status='VIABLE_CLASSICAL_JOINT_REFERENCE',winner_id=f'GAMMA-JOINT-{serial}',
        qrm_passes=len(accepted),qrm=qrm,joint_risk205_audit=audit,joint_risk_state=state,
        final_qrs=None if not np.isfinite(score) else score)
    if audit is not None:
        audit['policy']='candidate_level_risk205_final_qrm_joint_reference'
        audit['persistence_advancement']='once_for_selected_winner_only'
    return final,diagnostics
