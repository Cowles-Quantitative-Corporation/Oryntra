from __future__ import annotations

import time
from typing import Any, Callable
import numpy as np

from .budget import estimate_future_qpu_usage
from .classical_baseline import solve_classical
from .deep_qrm import evaluate_risk_deep
from .evaluator import comparison, targeted_position_checks
from .portfolio import build_problem
from .qscan import scan_universe
from .qpo import generate_portfolio_candidates, _backend as _qpo_backend
from .qrs_directed import qrs_threshold_search
from .full_qrs import full_qrs_exact_qaoa_search, FULL_QRS_SCHEMA
from .qrm import evaluate_risk
from .backends.deep_qiskit import adaptive_statevector_qubit_cap
from .backends.diagonal_qaoa import ExactDiagonalQAOA


def _serialize_candidate(candidate: dict[str, Any], problem, qrm: dict[str, Any]) -> dict[str, Any]:
    w = np.asarray(candidate["weights"], dtype=float)
    return {
        "candidate_id": candidate["candidate_id"],
        "risk_aversion": candidate["risk_aversion"],
        "metrics": candidate["metrics"],
        "qrm": qrm,
        "weights": {problem.symbols[i]: float(w[i]) for i in range(problem.n) if w[i] > 1e-8},
        "quantum": candidate["quantum"],
        "raw_weights": {problem.symbols[i]: float(np.asarray(candidate.get("raw_weights", candidate["weights"]), dtype=float)[i]) for i in range(problem.n) if np.asarray(candidate.get("raw_weights", candidate["weights"]), dtype=float)[i] > 1e-8},
        "repair_applied": bool(candidate.get("repair_applied", False)),
        "repair": candidate.get("repair") or {},
    }


def _emit(callback: Callable[[dict[str, Any]], None] | None, **payload: Any) -> None:
    if callback is None:
        return
    try:
        callback(payload)
    except Exception:
        pass




def _candidate_quantum_variables(candidate: dict[str, Any]) -> int:
    """Return encoded variable count for either legacy QUBO or exact Full-QRS candidate.

    Full-QRS exact-diagonal candidates deliberately have ``quantum["qubo"] = None``
    because their cost Hamiltonian is not a quadratic QUBO.  Result rendering and
    future-hardware planning must therefore read the finite-domain variable count
    from the Full-QRS objective metadata instead of indexing the missing QUBO.
    """
    quantum = dict(candidate.get("quantum") or {})
    qubo = quantum.get("qubo")
    if isinstance(qubo, dict) and qubo.get("num_variables") is not None:
        try:
            return max(0, int(qubo.get("num_variables")))
        except Exception:
            pass
    search = quantum.get("full_qrs_search") or {}
    if isinstance(search, dict):
        meta = search.get("objective_metadata") or {}
        if isinstance(meta, dict) and meta.get("variables") is not None:
            try:
                return max(0, int(meta.get("variables")))
            except Exception:
                pass
    meta = quantum.get("metadata") or {}
    if isinstance(meta, dict):
        for key in ("variables", "qubits"):
            if meta.get(key) is not None:
                try:
                    return max(0, int(meta.get(key)))
                except Exception:
                    pass
        states = meta.get("basis_states")
        try:
            if states is not None and int(states) > 0:
                import math
                n = int(round(math.log2(int(states))))
                if 2 ** n == int(states):
                    return n
        except Exception:
            pass
    return 0


def _metric_rank_value(metrics: dict[str, Any]) -> float:
    for key in ("cost_adjusted_qrs", "qrs", "sharpe_ratio"):
        value = metrics.get(key)
        if value is not None and np.isfinite(value):
            return float(value)
    return -1e9

def _rank_key(item):
    c, r = item
    qrs = c["metrics"].get("cost_adjusted_qrs")
    if qrs is None:
        qrs = c["metrics"].get("qrs")
    sharpe = c["metrics"].get("sharpe_ratio")
    qrs = float(qrs) if qrs is not None and np.isfinite(qrs) else None
    sharpe = float(sharpe) if sharpe is not None and np.isfinite(sharpe) else -1e9
    resilience_score = qrs if qrs is not None else sharpe
    stress_loss = float((r.get("stress_tests", {}).get("concentrated_top_shock") or {}).get("portfolio_return", -1e9))
    deep = r.get("deep_research") or {}
    deep_p05 = ((deep.get("correlation_vol_stress") or {}).get("p05_return"))
    deep_p05 = float(deep_p05) if deep_p05 is not None and np.isfinite(deep_p05) else -1e9
    deep_pass = 1 if r.get("passes_qrm_deep_gates", r.get("passes_qrm_structural_gates")) else 0
    return (
        deep_pass,
        resilience_score,
        deep_p05,
        stress_loss,
        -float(c["metrics"].get("turnover_from_active") or 0.0),
    )


def _build_run_manifest(*, problem, provenance: dict[str, Any], spec: dict[str, Any], compute_depth: str, backend: str, qscan: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist the inputs required to reproduce/audit a QOptima research decision.

    This manifest is descriptive provenance only; it does not authorize execution.
    """
    cov = (provenance.get("covariance") or {})
    selected = cov.get("selected") or {}
    frag = provenance.get("fragility") or {}
    forecasts = provenance.get("forecasts") or {}
    current_fc = forecasts.get("current") or {}
    return {
        "schema": "cqc.qoptima.run_manifest.v1",
        "qoptima_version": "0.4.0",
        "code_track": "reviewed_research",
        "seed": int(spec.get("seed") or 41),
        "compute_depth": str(compute_depth),
        "backend": str(backend),
        "decision_time": provenance.get("decision_time"),
        "data_window": {
            "first_session": provenance.get("first_session"),
            "last_session": provenance.get("last_session"),
            "aligned_sessions": provenance.get("aligned_sessions"),
        },
        "symbols": list(problem.symbols),
        "qscan_selected_symbols": [problem.symbols[int(i)] for i in (qscan or {}).get("selected_indices", [])],
        "covariance": {
            "method": problem.covariance_method,
            "parameters": dict(selected.get("parameters") or selected.get("params") or {}),
            "diagnostics": dict(selected.get("diagnostics") or {}),
            "warnings": list(selected.get("warnings") or []),
            "provenance": dict(selected.get("provenance") or {}),
        },
        "resilience": {
            "alpha": float(problem.resilience_alpha),
            "coefficients": dict(problem.fragility_weights or {}),
            "weight_source": frag.get("weight_source"),
            "calibration": frag.get("calibration"),
            "fragility_bands": frag.get("fragility_bands"),
        },
        "forecast_contract": {
            "expected_return_source": provenance.get("expected_return_source"),
            "compatible_models": current_fc.get("compatible_models", []),
            "model_provenance": current_fc.get("provenance", []),
            "rejected": current_fc.get("rejected", []),
            "historical_error_covariance": forecasts.get("historical_error_covariance"),
        },
        "v1a": {
            "enabled": bool(getattr(problem, "v1a_enabled", False)),
            "state": dict(getattr(problem, "v1a_state_metadata", None) or {}),
            "gamma": float(getattr(problem, "v1a_gamma", 0.0)) if getattr(problem, "v1a_enabled", False) else None,
            "risk_budget_annual": float(getattr(problem, "v1a_risk_budget_annual", 0.15)),
            "risk_floor_ratio": float(getattr(problem, "v1a_risk_floor_ratio", 0.10)),
            "scale_tolerance": float(getattr(problem, "v1a_scale_tolerance", 0.03)),
            "neural_network_inside_quantum_circuit": False,
        },
        "objective": {
            "target_gross": float(problem.target_gross),
            "max_name_weight": float(problem.max_name_weight),
            "risk_aversion": float(problem.risk_aversion),
            "turnover_penalty": float(problem.turnover_penalty),
            "risk_free_rate": float(problem.risk_free_rate),
            "transaction_cost_bps": float(problem.transaction_cost_bps),
            "horizon_sessions": int(problem.horizon_sessions),
            "covariance_used": (
                "V1A v1.6: Canonical stress floor plus soft-RMT positive directional headroom conditioned by frozen PIT V1A state"
                if getattr(problem, "v1a_enabled", False)
                else "legacy Sigma_alpha retained for benchmark; primary full QRS uses (1-alpha)Sigma_base + alpha*worst(versioned stress scenario)"
            ),
            "full_qrs_enabled": bool(problem.full_qrs_enabled),
            "full_qrs_schema": FULL_QRS_SCHEMA,
            "full_qrs_beta": float(problem.full_qrs_beta),
            "lending_rate": float(problem.risk_free_rate if problem.lending_rate is None else problem.lending_rate),
            "borrowing_rate": float(problem.risk_free_rate if problem.borrowing_rate is None else problem.borrowing_rate),
            "scenario_set_version": dict(problem.full_qrs_stress_metadata or {}).get("scenario_set_version"),
        },
        "execution": {
            "broker_execution_enabled": False,
            "paid_qpu_submission_enabled": False,
        },
    }


def run_portfolio_burst(
    user_id: int,
    spec: dict[str, Any],
    *,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """QOptima 0.4.0 local-shadow path: QScan -> QPO -> full-QRS QAOA refinement -> QRM.

    Deep Research calibrates a fixed amount of genuine Qiskit/QAOA and Monte Carlo
    work against the current Mac. There is no wall-clock minimum or artificial delay.
    It remains research-only and cannot submit broker orders or paid quantum jobs.
    """
    started = time.perf_counter()
    compute_depth = str(spec.get("compute_depth") or "validation").strip().lower()
    deep_mode = compute_depth == "deep"
    local_effort_target = max(30.0, float(spec.get("local_effort_target_seconds") or 600.0)) if deep_mode else 0.0
    # Target is converted into fixed work units after benchmarking this Mac.
    # It is deliberately not a minimum/maximum wall-clock timer.
    qpo_budget = local_effort_target * 0.80 if deep_mode else 0.0
    qrm_budget = local_effort_target - qpo_budget if deep_mode else 0.0

    _emit(progress_callback, stage="building_problem", message="Building QOptima market/covariance problem", percent=2.0)
    current = {str(k).upper(): float(v) for k, v in (spec.get("current_weights") or {}).items() if float(v) >= 0}
    problem, provenance = build_problem(
        user_id,
        current_weights=current,
        candidate_symbols=list(spec.get("candidate_symbols") or []),
        provider=str(spec.get("provider") or "cache_only"),
        lookback_sessions=int(spec.get("lookback_sessions") or 126),
        expected_returns=spec.get("expected_returns"),
        max_name_weight=float(spec.get("max_name_weight") or 0.20),
        risk_aversion=float(spec.get("risk_aversion") or 3.0),
        turnover_penalty=float(spec.get("turnover_penalty") or 0.20),
        risk_free_rate=float(spec.get("risk_free_rate") or 0.0),
        borrowing_rate=(None if spec.get("borrowing_rate") is None else float(spec.get("borrowing_rate"))),
        fragility_enabled=bool(spec.get("fragility_enabled", True)),
        transaction_cost_bps=float(spec.get("transaction_cost_bps") or 0.0),
        covariance_method=str(spec.get("covariance_method") or "shrinkage"),
        covariance_parameters=dict(spec.get("covariance_parameters") or {}),
        covariance_compare=bool(spec.get("covariance_compare", True)),
        resilience_alpha=float(spec.get("resilience_alpha") if spec.get("resilience_alpha") is not None else 1.0),
        full_qrs_enabled=bool(spec.get("full_qrs_enabled", True)),
        full_qrs_beta=float(spec.get("full_qrs_beta") if spec.get("full_qrs_beta") is not None else 0.10),
        full_qrs_bootstrap_reps=int(spec.get("full_qrs_bootstrap_reps") if spec.get("full_qrs_bootstrap_reps") is not None else 256),
        full_qrs_quantile_confidence=float(spec.get("full_qrs_quantile_confidence") if spec.get("full_qrs_quantile_confidence") is not None else 0.90),
        full_qrs_max_condition_number=float(spec.get("full_qrs_max_condition_number") if spec.get("full_qrs_max_condition_number") is not None else 1.0e8),
        v1a_enabled=bool(spec.get("v1a_enabled", False)),
        v1a_artifact_path=spec.get("v1a_artifact_path"),
        v1a_feature_values=dict(spec.get("v1a_feature_values") or {}),
        v1a_es_budget=(None if spec.get("v1a_es_budget") is None else float(spec.get("v1a_es_budget"))),
        v1a_es_activation_max_ratio=(None if spec.get("v1a_es_activation_max_ratio") is None else float(spec.get("v1a_es_activation_max_ratio"))),
        v1a_risk_budget_annual=float(spec.get("v1a_risk_budget_annual") if spec.get("v1a_risk_budget_annual") is not None else 0.15),
        v1a_risk_floor_ratio=float(spec.get("v1a_risk_floor_ratio") if spec.get("v1a_risk_floor_ratio") is not None else 0.10),
        v1a_covariance_additive_floor=float(spec.get("v1a_covariance_additive_floor") if spec.get("v1a_covariance_additive_floor") is not None else 1e-8),
        v1a_forecast_error_covariance_floor=float(spec.get("v1a_forecast_error_covariance_floor") if spec.get("v1a_forecast_error_covariance_floor") is not None else 1e-8),
        v1a_forecast_error_covariance_window=int(spec.get("v1a_forecast_error_covariance_window") if spec.get("v1a_forecast_error_covariance_window") is not None else 60),
        v1a_forecast_error_min_history=int(spec.get("v1a_forecast_error_min_history") if spec.get("v1a_forecast_error_min_history") is not None else 12),
        v1a_quantile_block_stride=int(spec.get("v1a_quantile_block_stride") if spec.get("v1a_quantile_block_stride") is not None else 1),
        v1a_qaoa_low_tail_quantile=float(spec.get("v1a_qaoa_low_tail_quantile") if spec.get("v1a_qaoa_low_tail_quantile") is not None else 0.05),
        v1a_qaoa_phase_global_floor_ratio=float(spec.get("v1a_qaoa_phase_global_floor_ratio") if spec.get("v1a_qaoa_phase_global_floor_ratio") is not None else 0.10),
        v1a_qaoa_phase_absolute_floor=float(spec.get("v1a_qaoa_phase_absolute_floor") if spec.get("v1a_qaoa_phase_absolute_floor") is not None else 1e-8),
        v1a_qaoa_energy_cap=float(spec.get("v1a_qaoa_energy_cap") if spec.get("v1a_qaoa_energy_cap") is not None else 8.0),
        v1a_qaoa_infeasible_gap=float(spec.get("v1a_qaoa_infeasible_gap") if spec.get("v1a_qaoa_infeasible_gap") is not None else 1.0),
        v1a_scale_tolerance=float(spec.get("v1a_scale_tolerance") if spec.get("v1a_scale_tolerance") is not None else 0.03),
        fragility_downside_weight=float(spec.get("fragility_downside_weight") if spec.get("fragility_downside_weight") is not None else 0.0),
        fragility_correlation_weight=float(spec.get("fragility_correlation_weight") if spec.get("fragility_correlation_weight") is not None else 0.0),
        fragility_forecast_weight=float(spec.get("fragility_forecast_weight") if spec.get("fragility_forecast_weight") is not None else 0.0),
        fragility_regime_weight=float(spec.get("fragility_regime_weight") if spec.get("fragility_regime_weight") is not None else 0.0),
        fragility_correlation_blend=float(spec.get("fragility_correlation_blend") if spec.get("fragility_correlation_blend") is not None else 0.65),
        model_forecasts=spec.get("model_forecasts"),
        forecast_records=spec.get("forecast_records"),
        forecast_error_history=spec.get("forecast_error_history"),
        decision_time=spec.get("decision_time"),
        forecast_horizon_sessions=int(spec.get("forecast_horizon_sessions") or 252),
        require_adjusted_prices=bool(spec.get("require_adjusted_prices", False)),
        gamma_enabled=bool(spec.get("gamma_enabled", False)), gamma_config=dict(spec.get("gamma_config") or {}),
        gamma_min_error_records=int(spec.get("gamma_min_error_records", 2)),
        gamma_feature_cutoff=spec.get("gamma_feature_cutoff"),
        gamma_decision_lag_sessions=max(int(spec.get("gamma_decision_lag_sessions", 0)), int(bool(spec.get("gamma_risk205_enabled", False)))),
    )
    gamma_history = provenance.pop("_gamma_returns_history", None)

    _emit(progress_callback, stage="classical_baseline", message="Running classical benchmark", percent=5.0)
    classical = solve_classical(problem)
    classical_weights = np.asarray(classical["weights"], dtype=float)

    # Deep Research is always a LOCAL SHADOW of the hardware-scale plan.  Keep
    # the user's requested weight resolution and reduce QScan assets first, rather
    # than silently reducing bits/asset or attempting an unsafe full statevector.
    requested_bits = max(1, min(5, int(spec.get("bits_per_asset") or (3 if deep_mode else 2))))
    requested_qscan_assets = int(spec.get("qscan_max_assets") or 8)
    shadow_memory = None
    qscan_assets = requested_qscan_assets
    max_quantum_variables = int(spec.get("max_quantum_variables") or 24)
    qiskit_qubit_cap = int(spec.get("qiskit_qubit_cap") or 24)
    if deep_mode:
        shadow_memory = adaptive_statevector_qubit_cap(requested_cap=qiskit_qubit_cap)
        safe_cap = int(shadow_memory["safe_qubit_cap"])
        shadow_asset_cap = max(2, safe_cap // max(1, requested_bits))
        qscan_assets = min(requested_qscan_assets, shadow_asset_cap, problem.n)
        max_quantum_variables = min(max_quantum_variables, safe_cap)
        qiskit_qubit_cap = safe_cap
        _emit(
            progress_callback,
            stage="local_shadow_preflight",
            message=(
                f"Hardware plan preserved; local Deep shadow = up to {qscan_assets} assets × "
                f"{requested_bits} bits = {qscan_assets * requested_bits} qubits "
                f"(guarded cap {safe_cap})"
            ),
            percent=7.0,
            requested_qscan_assets=int(requested_qscan_assets),
            shadow_qscan_assets=int(qscan_assets),
            bits_per_asset=int(requested_bits),
            shadow_qubits=int(qscan_assets * requested_bits),
            memory_plan=shadow_memory,
        )

    _emit(progress_callback, stage="qscan", message="QScan ranking active holdings and outside candidates", percent=8.0)
    qscan = scan_universe(
        problem,
        max_assets=qscan_assets,
        candidate_reserve=int(spec.get("qscan_candidate_reserve") or 3),
    )

    backend = str(spec.get("backend") or ("deep_qiskit" if deep_mode else "virtual_qpu"))
    if deep_mode and backend in {"virtual_qpu", "virtual", "exact", "local_exact", "qiskit", "qiskit_statevector"}:
        # Deep mode is explicitly intended to exercise the real Qiskit quantum-circuit
        # simulator. Keep validation backends separate rather than pretending they are deep.
        backend = "deep_qiskit"

    def qpo_progress(payload: dict[str, Any]) -> None:
        fraction = payload.get("fraction")
        pct = 10.0 + (float(fraction) * 65.0 if fraction is not None else 0.0)
        _emit(progress_callback, **payload, percent=min(75.0, pct))

    qpo = generate_portfolio_candidates(
        problem,
        list(qscan["selected_indices"]),
        backend=backend,
        bits_per_asset=requested_bits,
        max_quantum_variables=max_quantum_variables,
        shots=int(spec.get("shots") or (8192 if deep_mode else 2048)),
        seed=int(spec.get("seed") or 41),
        gross_penalty=float(spec.get("gross_penalty") or 80.0),
        price_per_second=float(spec.get("price_per_second") or 1.60),
        deep_qpo_seconds=float(qpo_budget),
        qaoa_reps_max=int(spec.get("qaoa_reps_max") or 3),
        qaoa_maxiter=int(spec.get("qaoa_maxiter") or 48),
        qiskit_qubit_cap=qiskit_qubit_cap,
        progress_callback=qpo_progress,
    )
    if not qpo["candidates"]:
        raise ValueError("QPO did not produce a portfolio candidate")

    # Primary QAOA refinement: the literal Full Canonical QRS is the QAOA
    # cost Hamiltonian on the finite encoded portfolio domain.  This path does
    # not substitute threshold squaring or an active-set QUBO for the requested
    # score.  Because an arbitrary exact diagonal Hamiltonian requires 2^n cost
    # values in the current implementation, Full-QRS QAOA intentionally uses a
    # smaller cap than ordinary quadratic QPO.
    full_qrs_optimization: dict[str, Any] = {
        "enabled": bool(spec.get("full_qrs_primary_qaoa", True) and problem.full_qrs_enabled),
        "available": False,
        "schema": FULL_QRS_SCHEMA,
        "method": "exact_diagonal_full_canonical_qrs_qaoa",
    }
    if full_qrs_optimization["enabled"]:
        _emit(progress_callback, stage="full_qrs_preflight", message="Preparing literal Full-QRS exact-diagonal QAOA objective", percent=76.0)
        selected_indices = list(qscan["selected_indices"])
        exact_cap = max(4, min(int(spec.get("full_qrs_exact_variable_cap") or 12), max_quantum_variables))
        full_bits = max(1, min(requested_bits, max(1, exact_cap // max(1, len(selected_indices)))))
        full_vars = len(selected_indices) * full_bits
        if full_vars > exact_cap:
            full_qrs_optimization.update({
                "reason": "qscan_full_qrs_exact_diagonal_problem_exceeds_variable_cap",
                "variables": int(full_vars),
                "cap": int(exact_cap),
                "note": "Exact QRS_FULL phase-oracle construction is exponential; reduce QScan assets or bits.",
            })
        else:
            full_solver = ExactDiagonalQAOA(
                max_variables=int(exact_cap),
                reps=int(spec.get("full_qrs_qaoa_reps") or spec.get("qaoa_reps_max") or (3 if deep_mode else 2)),
                optimizer_evaluations=int(spec.get("full_qrs_qaoa_evaluations") or max(24, 2 * int(spec.get("qaoa_maxiter") or (96 if deep_mode else 48)))),
            )

            def _full_qrs_viability(w: np.ndarray):
                qr = evaluate_risk(problem, np.asarray(w, dtype=float))
                return bool(qr.get("passes_qrm_structural_gates", False)), {
                    "source": "QRM_STRUCTURAL_RESEARCH_GATE",
                    "model_version": "QRM_STRUCTURAL_V1",
                    "structural_gates": qr.get("structural_gates"),
                    "note": (
                        "This callback instantiates the m_v viability branch currently available in Control. "
                        "An exact broker portfolio-margin engine is not connected and is not being claimed."
                    ),
                }

            _emit(
                progress_callback,
                stage="full_qrs_qaoa",
                message=(
                    f"Evaluating complete Full-QRS table and V1A-normalized QAOA ({full_vars} variables, {2 ** full_vars:,} states)"
                    if getattr(problem, "v1a_enabled", False)
                    else f"Evaluating literal Full-QRS cost table and QAOA ({full_vars} variables, {2 ** full_vars:,} states)"
                ),
                percent=76.5,
                variables=int(full_vars),
                basis_states=int(2 ** full_vars),
            )
            full_qrs_optimization = {
                "enabled": True,
                "available": True,
                "variables": int(full_vars),
                "bits_per_asset": int(full_bits),
                "backend": full_solver.name,
                **full_qrs_exact_qaoa_search(
                    problem,
                    full_solver,
                    selected_indices=selected_indices,
                    bits_per_asset=full_bits,
                    shots=min(int(spec.get("shots") or 4096), 100000),
                    seed=int(spec.get("seed") or 41) + 5000,
                    viability_callback=_full_qrs_viability,
                    require_viability_gate=True,
                ),
            }
            best_full = full_qrs_optimization.get("best_observed")
            if best_full is not None:
                fw = np.asarray(best_full["weights"], dtype=float)
                existing = any(np.allclose(fw, np.asarray(c["weights"], dtype=float), atol=1e-10, rtol=0.0) for c in qpo["candidates"])
                if not existing:
                    full_candidate = {
                        "candidate_id": "QPO-FULL-QRS-EXACT",
                        "risk_aversion": None,
                        "weights": fw,
                        "metrics": problem.metrics(fw),
                        "raw_weights": fw.copy(),
                        "raw_metrics": problem.metrics(fw),
                        "repair_applied": False,
                        "repair": {
                            "displayed_portfolio_source": "literal_full_qrs_exact_diagonal_qaoa",
                            "note": "No post-solve gross repair; QRS_FULL is evaluated on the exact encoded state.",
                        },
                        "quantum": {
                            "backend": best_full.get("backend"),
                            "bits": best_full.get("bits"),
                            "energy": float(best_full.get("energy") or 0.0),
                            "execution_seconds": float(best_full.get("execution_seconds") or 0.0),
                            "metadata": {
                                **dict(best_full.get("backend_metadata") or {}),
                                "full_qrs_primary": True,
                                "full_qrs_schema": FULL_QRS_SCHEMA,
                                "full_qrs_score": best_full.get("full_qrs"),
                                "viability": best_full.get("viability_metadata"),
                                "complete_full_formula_in_raw_economic_energy": True,
                                "phase_table_policy": (
                                    "V1A_QAOA_LOW_ENERGY_LINEAR_SCALE_V1_6"
                                    if getattr(problem, "v1a_enabled", False)
                                    else "literal_negative_full_qrs"
                                ),
                                "active_set_surrogate": False,
                                "threshold_surrogate": False,
                            },
                            "qubo": None,
                            "full_qrs_search": {
                                "method": "exact_diagonal_full_canonical_qrs_qaoa",
                                "cost_operator": full_qrs_optimization.get("cost_operator"),
                                "objective_metadata": full_qrs_optimization.get("objective_metadata"),
                                "validation_reference": full_qrs_optimization.get("validation_reference"),
                            },
                        },
                    }
                    qpo["candidates"].append(full_candidate)
                    qpo["search_role"] = (
                        str(qpo.get("search_role") or "")
                        + "; primary QAOA uses literal Full Canonical QRS as exact finite-domain diagonal cost Hamiltonian"
                    )
                    qpo.setdefault("backend_names", [])
                    if best_full.get("backend") and best_full.get("backend") not in qpo["backend_names"]:
                        qpo["backend_names"].append(best_full.get("backend"))

    qrs_directed = {"enabled": bool(spec.get("qrs_directed_enabled", False)), "available": False}
    if bool(spec.get("qrs_directed_enabled", False)):
        direct_bits = max(1, min(requested_bits, max(1, max_quantum_variables // max(1, problem.n))))
        direct_vars = problem.n * direct_bits
        if direct_vars > max_quantum_variables:
            qrs_directed.update({
                "reason": "full_universe_qrs_directed_problem_exceeds_local_variable_cap",
                "variables": int(direct_vars), "cap": int(max_quantum_variables),
                "note": "QRS-directed threshold search uses the full portfolio so R(w)^2 and risk cross terms remain exact; it is not silently reduced by QScan.",
            })
        else:
            direct_backend_name = str(spec.get("qrs_directed_backend") or "exact")
            direct_solver = _qpo_backend(direct_backend_name, price_per_second=float(spec.get("price_per_second") or 1.60))
            qrs_directed = {
                "enabled": True, "available": True, "variables": int(direct_vars),
                "bits_per_asset": int(direct_bits), "backend": direct_backend_name,
                **qrs_threshold_search(
                    problem, direct_solver, bits_per_asset=direct_bits,
                    low=float(spec.get("qrs_threshold_low") or 0.0),
                    high=float(spec.get("qrs_threshold_high") or 3.0),
                    iterations=int(spec.get("qrs_threshold_iterations") or 6),
                    shots=min(int(spec.get("shots") or 2048), 8192), seed=int(spec.get("seed") or 41) + 7000,
                ),
            }

    run_manifest = _build_run_manifest(
        problem=problem, provenance=provenance, spec=spec, compute_depth=compute_depth, backend=backend, qscan=qscan
    )

    # Fast structural QRM on every candidate first.
    evaluated = []
    for candidate in qpo["candidates"]:
        qrm = evaluate_risk(problem, np.asarray(candidate["weights"], dtype=float))
        evaluated.append((candidate, qrm))

    # Deep QRM is reserved for the strongest candidates so the stress budget goes
    # into plausible winners rather than obviously inferior frontier points.
    if deep_mode:
        preliminary = sorted(evaluated, key=_rank_key, reverse=True)
        deep_count = min(3, len(preliminary))
        per_candidate_qrm = qrm_budget / max(1, deep_count)
        minimum_paths_total = max(100_000, int(spec.get("qrm_minimum_paths") or 150_000))
        per_candidate_paths = max(50_000, minimum_paths_total // max(1, deep_count))
        deep_ids = {id(c) for c, _ in preliminary[:deep_count]}
        refreshed: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for idx, (candidate, qrm) in enumerate(preliminary):
            if id(candidate) not in deep_ids:
                refreshed.append((candidate, qrm))
                continue

            def qrm_progress(payload: dict[str, Any], *, _idx=idx) -> None:
                # QRM occupies the final 20% of the progress bar.
                elapsed = float(payload.get("elapsed_seconds") or 0.0)
                local_fraction = min(1.0, float(payload.get("fraction") or 0.0))
                overall_fraction = (_idx + local_fraction) / max(1, deep_count)
                _emit(progress_callback, **payload, percent=77.0 + 20.0 * overall_fraction)

            _emit(
                progress_callback,
                stage="qrm_deep_start",
                message=f"QRM Deep candidate {idx+1}/{deep_count}: {candidate['candidate_id']}",
                percent=77.0 + 20.0 * (idx / max(1, deep_count)),
            )
            qrm = evaluate_risk_deep(
                problem,
                np.asarray(candidate["weights"], dtype=float),
                minimum_paths=per_candidate_paths,
                target_work_seconds=per_candidate_qrm,
                horizon_days=int(spec.get("qrm_horizon_days") or 21),
                seed=int(spec.get("seed") or 41) + 1000 + idx,
                progress_callback=qrm_progress,
            )
            refreshed.append((candidate, qrm))
        evaluated = refreshed

    viable = [
        (c, r)
        for c, r in evaluated
        if r.get("passes_qrm_deep_gates", r.get("passes_qrm_structural_gates", False))
    ]
    if not viable:
        candidate_rows = [_serialize_candidate(c, problem, r) for c, r in evaluated]
        candidate_rows.sort(
            key=lambda x: _metric_rank_value(x["metrics"]),
            reverse=True,
        )
        elapsed = time.perf_counter() - started
        _emit(progress_callback, stage="complete", message="QOptima complete: no candidate passed QRM", percent=100.0)
        return {
            "qoptima_version": "0.4.0",
            "mode": "QSCAN_QPO_QRM_DEEP_RESEARCH" if deep_mode else "QSCAN_QPO_QRM_VALIDATION",
            "compute_depth": compute_depth,
            "status": "NO_VIABLE_CANDIDATE",
            "execution_allowed": False,
            "portfolio_rows": [
                {
                    "symbol": problem.symbols[i],
                    "active_weight": float(problem.current_weights[i]),
                    "classical_weight": float(classical_weights[i]),
                    "qoptima_weight": None,
                    "qoptima_change": None,
                }
                for i in range(problem.n)
            ],
            "comparison": None,
            "classical": {
                "success": classical["success"],
                "message": classical["message"],
                "iterations": classical["iterations"],
                "metrics": classical["metrics"],
            },
            "qscan": qscan,
            "qpo": {
                "winner_id": None,
                "candidate_count": len(candidate_rows),
                "candidate_portfolios": candidate_rows,
                "risk_aversion_grid": qpo["risk_aversion_grid"],
                "search_role": qpo["search_role"],
            },
            "qrs_directed": qrs_directed,
            "full_qrs_optimization": full_qrs_optimization,
            "qrm": {
                "passes_qrm_structural_gates": False,
                "message": "No QPO candidate passed QRM. No candidate is eligible for downstream handoff.",
                "candidate_gate_results": [
                    {
                        "candidate_id": c["candidate_id"],
                        "passes": bool(r.get("passes_qrm_deep_gates", r.get("passes_qrm_structural_gates", False))),
                        "structural_gates": r.get("structural_gates"),
                        "deep_gates": r.get("deep_gates"),
                    }
                    for c, r in evaluated
                ],
            },
            "quantum": {
                "backend_names": qpo.get("backend_names", []),
                "native_portfolio_search": True,
                "deep_research": bool(deep_mode),
                "bits_per_asset": int(qpo["bits_per_asset"]),
                "selected_assets": len(qscan["selected_indices"]),
                "circuit_evaluations_total": int(qpo.get("circuit_evaluations_total") or 0),
                "mac_quantum_compute_seconds": float(qpo.get("mac_compute_seconds_total") or 0.0),
                "real_qpu_executed": False,
            },
            "targeted_position_checks": [],
            "provenance": {**provenance, "research_objective": spec.get("research_objective"), "run_manifest": run_manifest},
            "run_manifest": run_manifest,
            "wall_clock_seconds": float(elapsed),
            "selection_rule": "No selection because every candidate failed QRM.",
            "risk_handoff": {
                "status": "NO_VIABLE_CANDIDATE",
                "execution_allowed": False,
                "required_next_gates": ["Universal Risk V2.0.4", "final volatility ceiling", "Control approval"],
                "candidate_weights": {},
            },
        }
    pool = sorted(viable, key=_rank_key, reverse=True)
    winner, winner_qrm = pool[0]
    qoptima_weights = np.asarray(winner["weights"], dtype=float)

    risk205_audit = None
    if bool(spec.get("gamma_risk205_enabled", False)):
        from .gamma_integration import apply_gamma_risk205
        qoptima_weights, risk205_audit, _next_state = apply_gamma_risk205(
            problem, qoptima_weights, returns_history=gamma_history,
            feature_cutoff=provenance["research_clock"]["feature_cutoff"],
            beta_records=spec.get("gamma_beta_records"), config_overrides=spec.get("gamma_risk205_config"),
            state=spec.get("gamma_risk_state"), actual_transaction_cost_bps=float(spec.get("transaction_cost_bps", 0.0)),
            scope=str(spec.get("gamma_risk205_scope", "full_supervisor")),
        )
    metrics = comparison(problem, classical_weights, qoptima_weights)
    checks = targeted_position_checks(
        problem,
        qoptima_weights,
        minimum_change=float(spec.get("position_check_threshold") or 0.005),
        max_checks=int(spec.get("max_position_checks") or 8),
    )

    rows = []
    for i, symbol in enumerate(problem.symbols):
        rows.append({
            "symbol": symbol,
            "active_weight": float(problem.current_weights[i]),
            "classical_weight": float(classical_weights[i]),
            "qoptima_weight": float(qoptima_weights[i]),
            "qoptima_change": float(qoptima_weights[i] - problem.current_weights[i]),
        })

    candidate_rows = [_serialize_candidate(c, problem, r) for c, r in evaluated]
    candidate_rows.sort(
        key=lambda x: (
            1 if x["qrm"].get("passes_qrm_deep_gates", x["qrm"]["passes_qrm_structural_gates"]) else 0,
            _metric_rank_value(x["metrics"]),
        ),
        reverse=True,
    )

    # Virtual estimates are direct from VirtualQPU. For genuine Qiskit deep search,
    # estimate what the repeated circuit evaluations might cost on a future billed
    # QPU; this remains planning data, not an IBM quote.
    estimated_seconds = float(qpo.get("virtual_qpu_seconds_total") or 0.0)
    estimated_cost = float(qpo.get("virtual_qpu_cost_total_usd") or 0.0)
    hardware_validation_seconds = 0.0
    hardware_validation_cost = 0.0
    if estimated_seconds <= 0:
        variable_counts = [_candidate_quantum_variables(c) for c, _ in evaluated]
        max_vars = max([v for v in variable_counts if v > 0] or [len(qscan.get("selected_indices") or []) * int(qpo.get("bits_per_asset") or 1), 1])
        evaluations = max(1, int(qpo.get("circuit_evaluations_total") or len(evaluated)))
        estimate = estimate_future_qpu_usage(
            variables=max_vars,
            circuit_depth=max_vars * 8,
            shots=int(spec.get("shots") or (8192 if deep_mode else 2048)),
            price_per_second=float(spec.get("price_per_second") or 1.60),
            circuit_evaluations=evaluations,
            final_validation_circuits=min(6, len(evaluated)),
        )
        estimated_seconds = float(estimate["estimated_qpu_seconds"])
        estimated_cost = float(estimate["estimated_cost_usd"])
        hardware_validation_seconds = float(estimate["hardware_validation_seconds"])
        hardware_validation_cost = float(estimate["hardware_validation_cost_usd"])

    max_qpu_seconds = float(spec.get("max_qpu_seconds") or 120.0)
    quantum_summary = {
        "backend": winner["quantum"]["backend"],
        "backend_names": qpo.get("backend_names", []),
        "native_portfolio_search": True,
        "deep_research": bool(deep_mode),
        "bits_per_asset": int(qpo["bits_per_asset"]),
        "selected_assets": len(qscan["selected_indices"]),
        "quantum_variables": int(_candidate_quantum_variables(winner)),
        "winner_has_qubo": bool(isinstance((winner.get("quantum") or {}).get("qubo"), dict)),
        "winner_cost_operator": ((winner.get("quantum") or {}).get("full_qrs_search") or {}).get("cost_operator") or "quadratic_qubo",
        "winner_energy": float(winner["quantum"]["energy"]),
        "winner_metadata": winner["quantum"]["metadata"],
        "circuit_evaluations_total": int(qpo.get("circuit_evaluations_total") or 0),
        "mac_quantum_compute_seconds": float(qpo.get("mac_compute_seconds_total") or 0.0),
        "local_effort_target_seconds": float(local_effort_target),
        "local_shadow": {
            "enabled": bool(deep_mode),
            "requested_qscan_assets": int(requested_qscan_assets),
            "shadow_qscan_assets": int(qscan_assets),
            "bits_per_asset": int(requested_bits),
            "planned_shadow_qubits": int(qscan_assets * requested_bits),
            "memory_plan": shadow_memory,
            "hardware_problem_preserved": True,
            "note": "Local Deep Research validates a reduced shadow; hardware-scale QAOA planning remains separate.",
        },
        "estimated_full_hardware_qaoa_seconds": estimated_seconds,
        "estimated_full_hardware_qaoa_cost_usd": estimated_cost,
        "estimated_hardware_validation_seconds": hardware_validation_seconds,
        "estimated_hardware_validation_cost_usd": hardware_validation_cost,
        # Backward-compatible keys for existing result rendering.
        "estimated_qpu_seconds_total": estimated_seconds,
        "estimated_qpu_cost_total_usd": estimated_cost,
        "budget_gate": {
            "within_runtime_ceiling": estimated_seconds <= max_qpu_seconds,
            "max_qpu_seconds": max_qpu_seconds,
            "planning_only": True,
        },
    }

    elapsed = time.perf_counter() - started
    _emit(progress_callback, stage="complete", message=f"QOptima {'Deep Research' if deep_mode else 'Validation'} complete in {elapsed:.1f}s", percent=100.0)
    return {
        "qoptima_version": "0.4.0",
        "mode": "QSCAN_QPO_QRM_DEEP_RESEARCH" if deep_mode else "QSCAN_QPO_QRM_VALIDATION",
        "compute_depth": compute_depth,
        "execution_allowed": False,
        "portfolio_rows": rows,
        "comparison": metrics,
        "classical": {
            "success": classical["success"],
            "message": classical["message"],
            "iterations": classical["iterations"],
            "metrics": classical["metrics"],
        },
        "qscan": qscan,
        "qpo": {
            "winner_id": winner["candidate_id"],
            "candidate_count": len(candidate_rows),
            "candidate_portfolios": candidate_rows,
            "risk_aversion_grid": qpo["risk_aversion_grid"],
            "search_role": qpo["search_role"],
        },
        "qrs_directed": qrs_directed,
        "full_qrs_optimization": full_qrs_optimization,
        "qrm": winner_qrm,
        "gamma_risk205": risk205_audit,
        "quantum": quantum_summary,
        "targeted_position_checks": checks,
        "provenance": {**provenance, "research_objective": spec.get("research_objective"), "run_manifest": run_manifest},
        "run_manifest": run_manifest,
        "wall_clock_seconds": float(elapsed),
        "selection_rule": "Highest full canonical QRS among QRM-viable candidates when enabled; legacy QRS/Sharpe are fallbacks. Full-QRS QAOA refinement is primary and baseline mean-risk QPO remains a benchmark.",
        "risk_handoff": {
            "status": "POST_RISK205_RESEARCH_CANDIDATE" if risk205_audit else "CANDIDATE_ONLY",
            "execution_allowed": False,
            "required_next_gates": (["final volatility ceiling", "Control approval"] if risk205_audit else ["Universal Risk V2.0.4", "final volatility ceiling", "Control approval"]),
            "candidate_weights": {row["symbol"]: row["qoptima_weight"] for row in rows if row["qoptima_weight"] > 1e-8},
        },
    }
