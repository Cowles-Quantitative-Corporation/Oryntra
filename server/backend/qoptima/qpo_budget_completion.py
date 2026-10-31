"""QPO residual-budget classical finite reference; original scores and risk gates.

Only the finite search domain changes. QScan, native expected returns, Full-QRS,
V1A and the original select-then-state-overlay sequence remain authoritative.
This is exact enumeration of a declared classical domain, not QPU execution.
"""
from collections import Counter
import numpy as np
from .gamma_construction import budget_completion_candidates, gross_lattice_diagnostics, _cheap_qrm_gates
from .qrm import evaluate_risk, _correlation_stress_covariance
from .qscan import scan_universe
from .scoring_memo import memoized_metrics_problem


def choose_qpo_budget_completion(problem, *, qscan_assets, bits, minimum_positions=0):
    p = memoized_metrics_problem(problem)
    reserve = max(1, min(2, qscan_assets // 3))
    if minimum_positions:
        reserve = min(reserve, max(0, qscan_assets-minimum_positions))
    qscan = scan_universe(p, max_assets=min(qscan_assets,p.n), candidate_reserve=reserve)
    selected = list(qscan['selected_indices'])
    stress = _correlation_stress_covariance(np.asarray(p.covariance))
    counts = Counter(); best = None
    for serial, w in enumerate(budget_completion_candidates(p,selected,bits)):
        counts['candidate_count'] += 1
        if np.count_nonzero(w > 1e-12) < minimum_positions:
            counts['minimum_positions'] += 1
            continue
        cheap = _cheap_qrm_gates(p,w,stress)
        if not all(cheap.values()):
            for gate, passed in cheap.items():
                if not passed: counts[gate] += 1
            continue
        qrm = evaluate_risk(p,w)
        if not qrm['passes_qrm_structural_gates']:
            for gate, passed in qrm['structural_gates'].items():
                if not passed: counts[gate] += 1
            continue
        score = qrm['metrics'].get('qrs')
        if score is None or not np.isfinite(score):
            raise ValueError('Invalid Full-QRS on viable residual-budget candidate')
        counts['viable_count'] += 1
        # Stable first enumerated candidate wins exact numeric ties.
        if best is None or score > best[0]:
            best = (float(score),serial,w.copy())
    meta = {'policy':'qpo_budget_completion_reference',
        'backend':'classical_residual_budget_finite_reference',
        'quantum_execution':False,'global_continuous_optimality_claim':False,
        'finite_domain_exact_enumeration':True,
        'domain':'union_of_six_completion_coordinates_other_five_two_bit_grid_frozen_unselected',
        'qscan':qscan, 'selected_indices':selected,
        'legacy_gross_lattice':gross_lattice_diagnostics(p,selected,bits),
        'counts':dict(counts),'risk_limits_relaxed':False,
        'selection_rule':'maximum_original_Full_QRS_among_QRM_approved_declared_domain_then_original_Risk205_state_overlay',
        'candidate_level_risk205':False,'ex_post_repair':False}
    if best is None:
        from .backtest_overlay import QPOResearchRejection
        raise QPOResearchRejection('No viable residual-budget candidate; no risk gate relaxation',
            {'rejection_reason':'NO_VIABLE_RESIDUAL_BUDGET_CANDIDATE','solver_diagnostics':meta,'failed_gates':dict(counts)})
    score, serial, w = best
    qrm = evaluate_risk(p,w)
    meta.update(status='VIABLE_QPO_RESIDUAL_BUDGET_REFERENCE',winner_id=f'QPO-BUDGET-{serial}',
        qrm_passes=counts['viable_count'],qrm=qrm,pre_overlay_qrs=score)
    return w,meta
