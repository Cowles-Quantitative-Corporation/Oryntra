from __future__ import annotations

from typing import Any, Callable
import numpy as np

from .backends.local_simulator import select_local_backend
from .backends.deep_qiskit import QiskitDeepQAOA
from .backends.virtual_qpu import VirtualQPUBackend
from .encoding.allocation_qubo import build_allocation_qubo
from .matched_solvers import compare_matched_solvers
from .problem import QOptimaPortfolioProblem


def _normalize_with_caps(weights: np.ndarray, target: float, cap: float) -> np.ndarray:
    w = np.clip(np.asarray(weights, dtype=float), 0.0, cap)
    for _ in range(80):
        total = float(w.sum())
        if abs(total - target) <= 1e-10:
            break
        if total > target and total > 1e-12:
            w *= target / total
        else:
            room = np.maximum(cap - w, 0.0)
            if room.sum() <= 1e-12:
                break
            w += min(target - total, room.sum()) * room / room.sum()
        w = np.clip(w, 0.0, cap)
    return w


def _subproblem(full: QOptimaPortfolioProblem, indices: list[int], risk_aversion: float) -> QOptimaPortfolioProblem:
    """Exact reduced objective with selected/frozen covariance cross terms retained.

    Let s be selected weights and f be frozen current weights.  For the full risk
    term lambda*w' Sigma w, the selected linear term becomes

        mu_s' s - 2 lambda s' Sigma_sf f

    while mu_f'f - lambda f'Sigma_ff f is a constant.  Incorporating both makes
    the reduced utility exactly equal to the full utility for every selected
    candidate when unselected holdings stay frozen.
    """
    selected = np.asarray(indices, dtype=int)
    fixed = np.asarray([i for i in range(full.n) if i not in set(indices)], dtype=int)
    current_full = np.asarray(full.current_weights, dtype=float)
    mu_full = np.asarray(full.expected_returns, dtype=float)
    opt_cov = np.asarray(full.optimization_covariance, dtype=float)
    lam = float(risk_aversion)

    current = current_full[selected]
    fixed_w = current_full[fixed]
    selected_target = float(full.target_gross - fixed_w.sum())
    selected_target = max(0.0, selected_target)

    mu_selected = mu_full[selected].copy()
    constant_offset = 0.0
    if len(fixed):
        sigma_sf = opt_cov[np.ix_(selected, fixed)]
        sigma_ff = opt_cov[np.ix_(fixed, fixed)]
        mu_selected = mu_selected - 2.0 * lam * (sigma_sf @ fixed_w)
        constant_offset = float(mu_full[fixed] @ fixed_w - lam * (fixed_w @ sigma_ff @ fixed_w))

    fragility_components = None
    if full.fragility_components:
        fragility_components = {
            name: np.asarray(matrix, dtype=float)[np.ix_(selected, selected)]
            for name, matrix in full.fragility_components.items()
        }
    effective_covariance = None
    if full.effective_covariance is not None:
        effective_covariance = np.asarray(full.effective_covariance, dtype=float)[np.ix_(selected, selected)]
    alpha_covariance = None
    if full.alpha_covariance is not None:
        alpha_covariance = np.asarray(full.alpha_covariance, dtype=float)[np.ix_(selected, selected)]
    full_qrs_scenarios = None
    if full.full_qrs_stress_covariances:
        full_qrs_scenarios = {
            name: np.asarray(matrix, dtype=float)[np.ix_(selected, selected)]
            for name, matrix in full.full_qrs_stress_covariances.items()
        }
    forecast_error_samples = None
    if full.forecast_error_samples is not None:
        forecast_error_samples = np.asarray(full.forecast_error_samples, dtype=float)[:, selected]

    return QOptimaPortfolioProblem(
        symbols=tuple(full.symbols[i] for i in selected),
        current_weights=current,
        expected_returns=mu_selected,
        covariance=np.asarray(full.covariance, dtype=float)[np.ix_(selected, selected)],
        target_gross=selected_target,
        max_name_weight=float(full.max_name_weight),
        risk_aversion=lam,
        turnover_penalty=float(full.turnover_penalty),
        risk_free_rate=float(full.risk_free_rate),
        transaction_cost_bps=float(full.transaction_cost_bps),
        horizon_sessions=int(full.horizon_sessions),
        fragility_enabled=bool(full.fragility_enabled),
        effective_covariance=effective_covariance,
        alpha_covariance=alpha_covariance,
        resilience_alpha=float(full.resilience_alpha),
        fragility_components=fragility_components,
        fragility_weights=dict(full.fragility_weights or {}),
        fragility_metadata=dict(full.fragility_metadata or {}),
        covariance_method=str(full.covariance_method),
        covariance_provenance=dict(full.covariance_provenance or {}),
        full_qrs_enabled=bool(full.full_qrs_enabled),
        lending_rate=full.lending_rate,
        borrowing_rate=full.borrowing_rate,
        full_qrs_beta=float(full.full_qrs_beta),
        full_qrs_bootstrap_reps=int(full.full_qrs_bootstrap_reps),
        full_qrs_quantile_confidence=float(full.full_qrs_quantile_confidence),
        forecast_error_samples=forecast_error_samples,
        full_qrs_stress_covariances=full_qrs_scenarios,
        full_qrs_stress_metadata=dict(full.full_qrs_stress_metadata or {}),
        objective_constant_offset=constant_offset,
    )


def _backend(
    name: str,
    *,
    price_per_second: float,
    deep_work_target_seconds: float = 90.0,
    qaoa_reps_max: int = 3,
    qaoa_maxiter: int = 48,
    qiskit_qubit_cap: int = 24,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
):
    clean = str(name or "virtual_qpu").strip().lower()
    if clean in {"virtual", "virtual_qpu", "qpu_emulator"}:
        return VirtualQPUBackend(price_per_second=price_per_second)
    if clean in {"deep", "deep_qiskit", "qiskit_deep", "deep_research"}:
        return QiskitDeepQAOA(
            target_work_seconds=deep_work_target_seconds,
            max_reps=qaoa_reps_max,
            maxiter_per_restart=qaoa_maxiter,
            requested_qubit_cap=qiskit_qubit_cap,
            progress_callback=progress_callback,
        )
    return select_local_backend(clean)


def _full_objective_at_lambda(problem: QOptimaPortfolioProblem, weights: np.ndarray, risk_aversion: float) -> float:
    """Evaluate the same full-portfolio objective represented by a reduced QUBO.

    QPO scans a grid of risk-aversion values.  The parent problem stores only its
    baseline lambda, so using ``problem.objective`` here would compare the QUBO
    against the wrong full objective for every non-baseline grid point.
    """
    w = np.asarray(weights, dtype=float).reshape(-1)
    current = np.asarray(problem.current_weights, dtype=float).reshape(-1)
    mu = np.asarray(problem.expected_returns, dtype=float).reshape(-1)
    cov = np.asarray(problem.optimization_covariance, dtype=float)
    return float(
        mu @ w
        - float(risk_aversion) * (w @ cov @ w)
        - float(problem.turnover_penalty) * np.square(w - current).sum()
    )


def _raw_feasibility(problem: QOptimaPortfolioProblem, w: np.ndarray) -> dict[str, Any]:
    w = np.asarray(w, dtype=float)
    gross_error = float(w.sum() - problem.target_gross)
    return {
        "long_only": bool(np.all(w >= -1e-10)),
        "name_caps": bool(np.all(w <= float(problem.max_name_weight) + 1e-10)),
        "gross": bool(abs(gross_error) <= 1e-6),
        "gross_error": gross_error,
        "feasible": bool(
            np.all(w >= -1e-10)
            and np.all(w <= float(problem.max_name_weight) + 1e-10)
            and abs(gross_error) <= 1e-6
        ),
    }


def _matched_financial_result(
    full_problem: QOptimaPortfolioProblem,
    subproblem: QOptimaPortfolioProblem,
    qubo,
    selected_indices: list[int],
    risk_aversion: float,
    solver_result: dict[str, Any],
    *,
    gross_penalty: float,
) -> dict[str, Any]:
    """Decode one matched solver on the identical discrete QUBO.

    The comparison stays on the raw discrete sample.  We deliberately do not
    repair/renormalize here because that would change the objective being
    compared.  Full-portfolio QRS and feasibility are reported separately from
    QUBO energy.
    """
    out = dict(solver_result or {})
    bits = out.get("bits")
    if not out.get("available") or bits is None:
        return out
    try:
        selected = np.asarray(qubo.weights(np.asarray(bits, dtype=int)), dtype=float)
        full = np.asarray(full_problem.current_weights, dtype=float).copy()
        for local_i, global_i in enumerate(selected_indices):
            full[global_i] = selected[local_i]
        sub_feas = _raw_feasibility(subproblem, selected)
        full_gross_error = float(full.sum() - full_problem.target_gross)
        full_feasible = bool(
            np.all(full >= -1e-10)
            and np.all(full <= float(full_problem.max_name_weight) + 1e-10)
            and abs(full_gross_error) <= 1e-6
        )
        full_obj = _full_objective_at_lambda(full_problem, full, risk_aversion)
        direct_energy = float(-full_obj + float(gross_penalty) * full_gross_error**2)
        reported_energy = out.get("energy")
        out.update({
            "selected_weights": selected.tolist(),
            "full_weights": full.tolist(),
            "selected_feasibility": sub_feas,
            "full_feasibility": {"feasible": full_feasible, "gross_error": full_gross_error},
            "financial_metrics": full_problem.metrics(full),
            "full_objective_at_lambda": float(full_obj),
            "direct_qubo_energy_from_full_objective": direct_energy,
            "energy_identity_error": None if reported_energy is None else float(float(reported_energy) - direct_energy),
            "comparison_contract": "same raw discrete QUBO; no post-solve repair applied",
        })
    except Exception as exc:
        out["financial_decode_error"] = f"{type(exc).__name__}: {exc}"
    return out


def generate_portfolio_candidates(
    full_problem: QOptimaPortfolioProblem,
    selected_indices: list[int],
    *,
    backend: str = "virtual_qpu",
    bits_per_asset: int = 2,
    max_quantum_variables: int = 24,
    shots: int = 2048,
    seed: int = 41,
    gross_penalty: float = 80.0,
    price_per_second: float = 1.60,
    deep_qpo_seconds: float = 0.0,
    qaoa_reps_max: int = 3,
    qaoa_maxiter: int = 48,
    qiskit_qubit_cap: int = 24,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    matched_solver_comparison: bool = True,
) -> dict[str, Any]:
    if len(selected_indices) < 2:
        raise ValueError("QPO requires at least two selected assets")
    if len(set(selected_indices)) != len(selected_indices):
        raise ValueError("QPO selected_indices must be unique")
    max_bits = max(1, int(max_quantum_variables) // len(selected_indices))
    bits = max(1, min(int(bits_per_asset), max_bits, 5))

    base_lambda = max(0.01, float(full_problem.risk_aversion))
    clean_backend = str(backend or "virtual_qpu").strip().lower()
    deep_mode = clean_backend in {"deep", "deep_qiskit", "qiskit_deep", "deep_research"}
    multipliers = (0.30, 0.55, 0.85, 1.20, 1.80, 2.80) if deep_mode else (0.35, 0.65, 1.0, 1.6, 2.6)
    lambda_grid = sorted({round(base_lambda * x, 8) for x in multipliers})
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[float, ...]] = set()
    total_virtual_seconds = 0.0
    total_virtual_cost = 0.0
    total_mac_seconds = 0.0
    backend_names: set[str] = set()
    total_circuit_evaluations = 0
    per_slice = max(1.0, float(deep_qpo_seconds) / max(1, len(lambda_grid))) if deep_mode else 0.0

    fixed_indices = [i for i in range(full_problem.n) if i not in set(selected_indices)]
    fixed_weights = np.asarray(full_problem.current_weights, dtype=float)[fixed_indices]

    for run_idx, risk_aversion in enumerate(lambda_grid):
        problem = _subproblem(full_problem, selected_indices, risk_aversion)
        qubo = build_allocation_qubo(problem, bits_per_asset=bits, gross_penalty=gross_penalty)

        def backend_progress(payload: dict[str, Any]) -> None:
            if progress_callback is None:
                return
            local_fraction = float(payload.get("fraction") or 0.0)
            overall_fraction = (run_idx + min(1.0, max(0.0, local_fraction))) / max(1, len(lambda_grid))
            progress_callback({
                **payload,
                "stage": "qpo_deep",
                "lambda_index": int(run_idx + 1),
                "lambda_total": int(len(lambda_grid)),
                "risk_aversion": float(risk_aversion),
                "qpo_target_work_seconds": float(deep_qpo_seconds if deep_mode else 0.0),
                "fraction": float(overall_fraction),
            })

        solver = _backend(
            backend,
            price_per_second=price_per_second,
            deep_work_target_seconds=per_slice or 90.0,
            qaoa_reps_max=qaoa_reps_max,
            qaoa_maxiter=qaoa_maxiter,
            qiskit_qubit_cap=qiskit_qubit_cap,
            progress_callback=backend_progress,
        )
        if progress_callback is not None:
            progress_callback({
                "stage": "qpo_candidate_start",
                "message": f"QPO candidate {run_idx+1}/{len(lambda_grid)} · λ={risk_aversion:.3f} · {qubo.num_variables} variables",
                "lambda_index": int(run_idx + 1),
                "lambda_total": int(len(lambda_grid)),
            })
        result = solver.solve(qubo, shots=shots, seed=seed + run_idx)
        raw_selected = np.asarray(qubo.weights(result.bits), dtype=float)
        raw_full = np.asarray(full_problem.current_weights, dtype=float).copy()
        for local_i, global_i in enumerate(selected_indices):
            raw_full[global_i] = raw_selected[local_i]
        raw_feas = _raw_feasibility(problem, raw_selected)
        raw_full_objective = _full_objective_at_lambda(full_problem, raw_full, risk_aversion)
        expected_energy = float(
            -raw_full_objective
            + float(gross_penalty) * (float(raw_full.sum()) - float(full_problem.target_gross)) ** 2
        )
        energy_error = float(result.energy - expected_energy)

        if raw_feas["feasible"]:
            final_selected = raw_selected.copy()
            repaired = False
        else:
            final_selected = _normalize_with_caps(raw_selected, problem.target_gross, problem.max_name_weight)
            repaired = True
        full_weights = np.asarray(full_problem.current_weights, dtype=float).copy()
        for local_i, global_i in enumerate(selected_indices):
            full_weights[global_i] = final_selected[local_i]
        repaired_feas = _raw_feasibility(problem, final_selected)
        repaired_full_objective = _full_objective_at_lambda(full_problem, full_weights, risk_aversion)

        key = tuple(np.round(full_weights, 8))
        meta = dict(result.metadata or {})
        total_virtual_seconds += float(meta.get("virtual_qpu_seconds") or 0.0)
        total_virtual_cost += float(meta.get("virtual_qpu_cost_usd") or 0.0)
        total_mac_seconds += float(meta.get("mac_wall_seconds") or result.execution_seconds or 0.0)
        total_circuit_evaluations += int(meta.get("circuit_evaluations") or 0)
        backend_names.add(result.backend)

        matched = None
        if matched_solver_comparison:
            matched = compare_matched_solvers(
                qubo,
                qaoa_result={
                    "available": True,
                    "solver": "qaoa_or_selected_quantum_backend",
                    "energy": float(result.energy),
                    "bits": np.asarray(result.bits, dtype=int).tolist(),
                    "backend": result.backend,
                    "wall_seconds": float(result.execution_seconds),
                    "circuit_evaluations": int(meta.get("circuit_evaluations") or 0),
                    "estimated_qpu_seconds": float(meta.get("virtual_qpu_seconds") or 0.0),
                    "estimated_qpu_cost_usd": float(meta.get("virtual_qpu_cost_usd") or 0.0),
                },
                seed=seed + run_idx,
                exact_max_variables=18,
                milp_max_variables=36,
            )
            for key_name in ("exact", "mixed_integer", "heuristic", "qaoa"):
                if key_name in matched:
                    matched[key_name] = _matched_financial_result(
                        full_problem, problem, qubo, selected_indices, risk_aversion,
                        matched[key_name], gross_penalty=gross_penalty,
                    )
            available_rows = [matched.get(k) for k in ("exact", "mixed_integer", "heuristic") if (matched.get(k) or {}).get("available")]
            if available_rows:
                best_fin = min(available_rows, key=lambda row: float(row.get("energy", float("inf"))))
                matched["best_classical_financial"] = {
                    "solver": best_fin.get("solver"),
                    "energy": best_fin.get("energy"),
                    "feasible": (best_fin.get("full_feasibility") or {}).get("feasible"),
                    "qrs": (best_fin.get("financial_metrics") or {}).get("qrs"),
                    "cost_adjusted_qrs": (best_fin.get("financial_metrics") or {}).get("cost_adjusted_qrs"),
                    "wall_seconds": best_fin.get("wall_seconds"),
                }

        if key in seen:
            continue
        seen.add(key)
        metrics = full_problem.metrics(full_weights)
        candidates.append({
            "candidate_id": f"QPO-{len(candidates)+1:02d}",
            "risk_aversion": float(risk_aversion),
            "weights": full_weights,
            "metrics": metrics,
            "raw_weights": raw_full,
            "raw_metrics": full_problem.metrics(raw_full),
            "repair_applied": bool(repaired),
            "repair": {
                "raw_selected_feasibility": raw_feas,
                "repaired_selected_feasibility": repaired_feas,
                "raw_full_objective": raw_full_objective,
                "repaired_full_objective": repaired_full_objective,
                "displayed_portfolio_source": "repaired_from_qubo_sample" if repaired else "raw_qubo_sample",
            },
            "quantum": {
                "backend": result.backend,
                "bits": result.bits,
                "energy": float(result.energy),
                "execution_seconds": float(result.execution_seconds),
                "metadata": meta,
                "qubo": qubo.as_dict(),
                "energy_full_objective_expected": expected_energy,
                "energy_full_objective_error": energy_error,
                "matched_classical": matched,
            },
        })

    return {
        "candidates": candidates,
        "selected_indices": list(selected_indices),
        "fixed_indices": fixed_indices,
        "fixed_weights": fixed_weights.tolist(),
        "bits_per_asset": bits,
        "risk_aversion_grid": lambda_grid,
        "backend_names": sorted(backend_names),
        "virtual_qpu_seconds_total": float(total_virtual_seconds),
        "virtual_qpu_cost_total_usd": float(total_virtual_cost),
        "mac_compute_seconds_total": float(total_mac_seconds),
        "circuit_evaluations_total": int(total_circuit_evaluations),
        "deep_research": bool(deep_mode),
        "deep_qpo_target_work_seconds": float(deep_qpo_seconds if deep_mode else 0.0),
        "search_role": "reduced native discrete weight search with exact selected/frozen cross terms; raw QUBO and repaired portfolio are reported separately",
    }
