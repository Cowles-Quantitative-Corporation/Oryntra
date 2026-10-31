from __future__ import annotations

"""QOptima hardware-scale QAOA planning utilities.

This module intentionally separates *planning* from paid execution.  It mirrors the
shape of IBM's public utility-scale QAOA workflow (Estimator/Cobyla iterations,
then a Sampler validation pass), while keeping all broker and QPU execution gates
outside this file.
"""

from dataclasses import dataclass
import math
from typing import Any
import numpy as np

from .provider_metadata import current_ibm_metadata, planning_prices, processor_capacity
from .hardware_compile import compile_qaoa_proxy

_SNAPSHOT = current_ibm_metadata(refresh_runtime=False)
_PRICES = planning_prices(_SNAPSHOT)
_CAPS = processor_capacity(_SNAPSHOT)
IBM_PAYG_PER_SECOND = float(_PRICES.get("payg", 1.60))
IBM_FLEX_PER_SECOND = float(_PRICES.get("flex", 1.20))
IBM_PREMIUM_PER_SECOND = float(_PRICES.get("premium", 0.80))
IBM_OPEN_MONTHLY_SECONDS = float(_SNAPSHOT.get("open_plan_qpu_seconds_per_28_days", 600.0))
IBM_NIGHTHAWK_PROGRAMMABLE_QUBITS = int(_CAPS.get("nighthawk_r2", 120))
IBM_HERON_PROGRAMMABLE_QUBITS = int(_CAPS.get("heron", 156))


@dataclass(frozen=True)
class HardwareQAOASpec:
    total_symbols: int
    qscan_assets: int
    bits_per_asset: int = 4
    include_selection_qubit: bool = True
    auxiliary_qubits: int = 0
    qaoa_reps: int = 1
    optimizer_evaluations: int = 28
    restarts: int = 1
    frontier_points: int = 5
    estimator_shots: int = 1000
    final_sampler_shots: int = 10000
    mitigation_multiplier: float = 1.0
    interaction_density: float = 0.25
    runtime_mode: str = "job"
    planning_price_per_second: float = IBM_PAYG_PER_SECOND
    local_ram_gb: float | None = None
    backend_name: str | None = None
    compile_offline: bool = False
    transpile_optimization_level: int = 2
    covariance_method: str = "unspecified"
    resilience_alpha: float = 1.0


def _statevector_bytes(qubits: int) -> int:
    # Python integers keep this exact even when the result is astronomically large.
    return 16 * (1 << max(0, int(qubits)))


def _human_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB"]
    x = float(value)
    unit = units[0]
    for unit in units:
        if x < 1024.0 or unit == units[-1]:
            break
        x /= 1024.0
    if x >= 100:
        return f"{x:,.0f} {unit}"
    if x >= 10:
        return f"{x:,.1f} {unit}"
    return f"{x:,.2f} {unit}"


def _hardware_fit(logical_qubits: int) -> dict[str, Any]:
    n = int(logical_qubits)
    nighthawk = n <= IBM_NIGHTHAWK_PROGRAMMABLE_QUBITS
    heron = n <= IBM_HERON_PROGRAMMABLE_QUBITS
    if n <= 0:
        recommendation = "No hardware problem yet"
    elif nighthawk:
        recommendation = "Nighthawk r2 or Heron"
    elif heron:
        recommendation = "Heron 156q class"
    else:
        recommendation = "Does not fit a current single IBM 120/156 programmable-qubit QPU"
    return {
        "nighthawk_120q": nighthawk,
        "heron_156q": heron,
        "recommended": recommendation,
    }


def estimate_hardware_qaoa(spec: HardwareQAOASpec) -> dict[str, Any]:
    provider_md = current_ibm_metadata(refresh_runtime=True)
    prices = planning_prices(provider_md)
    capacities = processor_capacity(provider_md)
    nighthawk_cap = int(capacities.get("nighthawk_r2", IBM_NIGHTHAWK_PROGRAMMABLE_QUBITS))
    heron_cap = int(capacities.get("heron", IBM_HERON_PROGRAMMABLE_QUBITS))
    total_symbols = max(0, int(spec.total_symbols))
    assets = 0 if total_symbols <= 0 else max(0, min(int(spec.qscan_assets), total_symbols))
    bits = max(1, min(8, int(spec.bits_per_asset)))
    aux = max(0, int(spec.auxiliary_qubits))

    allocation_qubits = assets * bits
    selection_qubits = assets if bool(spec.include_selection_qubit) else 0
    logical_qubits = allocation_qubits + selection_qubits + aux

    p = max(1, min(10, int(spec.qaoa_reps)))
    evaluations = max(1, int(spec.optimizer_evaluations))
    restarts = max(1, int(spec.restarts))
    frontier = max(1, int(spec.frontier_points))
    estimator_shots = max(1, int(spec.estimator_shots))
    sampler_shots = max(1, int(spec.final_sampler_shots))
    mitigation = max(1.0, float(spec.mitigation_multiplier))
    user_density = min(1.0, max(0.0, float(spec.interaction_density)))

    # The current binary-weight hardware encoding enforces gross exposure with a
    # squared linear penalty.  Expanding that square couples every allocation bit
    # to every other allocation bit, so the QUBO has an intrinsic dense floor
    # even if the financial covariance matrix itself is sparse.  Selection-link
    # penalties add one edge from every allocation bit to its asset's selection
    # bit.  Planning from a lower user-supplied density would materially
    # understate routing/two-qubit work.  Auxiliary/cardinality couplings are not
    # assumed here because their exact structure depends on the chosen converter.
    possible_pairs = logical_qubits * max(0, logical_qubits - 1) // 2
    allocation_pairs = allocation_qubits * max(0, allocation_qubits - 1) // 2
    selection_link_pairs = allocation_qubits if bool(spec.include_selection_qubit) else 0
    encoding_min_pairs = min(possible_pairs, allocation_pairs + selection_link_pairs) if possible_pairs else 0
    encoding_density_floor = (encoding_min_pairs / possible_pairs) if possible_pairs else 0.0
    density = max(user_density, encoding_density_floor)

    estimator_calls = frontier * restarts * evaluations
    sampler_calls = frontier
    primitive_calls = estimator_calls + sampler_calls

    raw_executions = estimator_calls * estimator_shots + sampler_calls * sampler_shots
    adjusted_executions = int(math.ceil(raw_executions * mitigation))

    # IBM's documented quick baseline is ~2 + 0.00035 * executions for one
    # uncomplicated sub-job.  Iterative job-mode QAOA normally incurs a fresh
    # sub-job overhead for each sequential Estimator evaluation.  The low estimate
    # assumes those evaluations can be grouped efficiently; the job-mode estimate
    # conservatively applies the ~2s overhead per primitive call.
    quick_execution_seconds = 0.00035 * adjusted_executions
    grouped_lower_bound_seconds = 2.0 + quick_execution_seconds
    job_mode_seconds = (2.0 * primitive_calls) + quick_execution_seconds

    # Give circuit depth some planning weight without claiming backend-calibrated
    # accuracy.  This is explicitly a research planning range, not an IBM quote.
    # Denser portfolio covariance QUBOs tend to require more routing/two-qubit work.
    topology_factor = 1.0 + min(2.5, density * max(0, logical_qubits - 20) / 40.0)
    depth_factor = max(1.0, 0.70 + 0.30 * p)
    depth_adjusted_execution = quick_execution_seconds * topology_factor * depth_factor
    if str(spec.runtime_mode or "job").strip().lower() == "session":
        # Session usage is active wall-clock reservation, so a local pre-submit
        # formula cannot know it exactly.  Report QPU-work floor + planning range.
        estimated_low = grouped_lower_bound_seconds
        estimated_high = max(estimated_low, 2.0 + depth_adjusted_execution * 1.35)
        billing_note = (
            "Premium session: IBM bills active session wall-clock time. The range shown is a QPU-work planning floor/range; "
            "actual session usage can be higher because classical optimizer latency also occurs while the QPU is reserved."
        )
    else:
        estimated_low = max(grouped_lower_bound_seconds, job_mode_seconds)
        estimated_high = max(estimated_low, (2.0 * primitive_calls) + depth_adjusted_execution * 1.35)
        billing_note = (
            "Job mode: estimate includes ~2s planning overhead per sequential primitive call plus IBM's documented execution baseline. "
            "Queue wait is not included in QPU usage."
        )

    price = max(0.0, float(spec.planning_price_per_second))
    cost_low = estimated_low * price
    cost_high = estimated_high * price

    # A cheap hardware-validation burst assumes QOptima performs parameter search
    # locally and sends only one final Sampler distribution to the QPU. It is not
    # a replacement for full hardware QAOA; it answers whether the final circuit
    # behaves plausibly on real hardware at much lower cost.
    validation_executions = int(math.ceil(sampler_shots * mitigation))
    validation_quick_seconds = 2.0 + (0.00035 * validation_executions)
    validation_low = validation_quick_seconds
    validation_high = max(validation_low, 2.0 + (0.00035 * validation_executions * topology_factor * depth_factor * 1.35))

    statevector_bytes = _statevector_bytes(logical_qubits)
    ram_bytes = None if spec.local_ram_gb is None else max(0.0, float(spec.local_ram_gb)) * (1024.0 ** 3)
    local_fraction = None if not ram_bytes else statevector_bytes / ram_bytes
    local_full_statevector_feasible = bool(ram_bytes and statevector_bytes <= 0.35 * ram_bytes)

    estimated_quadratic_terms = int(round(possible_pairs * density))

    n = int(logical_qubits)
    nighthawk_fit = n <= nighthawk_cap
    heron_fit = n <= heron_cap
    if n <= 0:
        recommendation = "No hardware problem yet"
    elif nighthawk_fit:
        recommendation = "Nighthawk-class or Heron-class by raw qubit count only"
    elif heron_fit:
        recommendation = "Heron-class by raw qubit count only"
    else:
        recommendation = "Does not fit the currently discovered/snapshotted single-QPU capacity"
    hardware = {
        "nighthawk_120q": nighthawk_fit,
        "heron_156q": heron_fit,
        "recommended": recommendation,
        "raw_qubit_fit_only": True,
        "nighthawk_capacity": nighthawk_cap,
        "heron_capacity": heron_cap,
    }
    if logical_qubits >= 80:
        scale_label = "hardware-scale"
    elif logical_qubits >= 40:
        scale_label = "intermediate / beyond normal laptop full-statevector target"
    else:
        scale_label = "local-validation scale"

    # IBM's public utility-scale QAOA example uses 100 graph nodes/qubits.
    ibm_100q_reference_ratio = (logical_qubits / 100.0) if logical_qubits else 0.0

    qubits_per_asset = bits + (1 if bool(spec.include_selection_qubit) else 0)
    nighthawk_asset_capacity = max(0, (nighthawk_cap - aux) // max(1, qubits_per_asset))
    heron_asset_capacity = max(0, (heron_cap - aux) // max(1, qubits_per_asset))

    # Keep a conservative local shadow configuration that uses the same financial
    # structure but stays at or below 24 variables for a 24 GB-class Mac.  The
    # hardware problem can be much larger; this shadow exists only for encoding
    # and regression validation.
    shadow_qubit_cap = 24
    shadow_bits = min(bits, 3)
    shadow_qpa = shadow_bits + (1 if bool(spec.include_selection_qubit) else 0)
    shadow_assets = 0 if total_symbols <= 0 else min(total_symbols, max(1, shadow_qubit_cap // max(1, shadow_qpa)))
    shadow_qubits = shadow_assets * shadow_qpa

    if logical_qubits <= 0:
        worth_it = "WAITING"
        worth_reason = "Enter a portfolio/candidate universe to size the QAOA problem."
    elif not hardware["heron_156q"]:
        worth_it = "REFORMULATE"
        worth_reason = "The problem exceeds the currently discovered/snapshotted single-QPU raw-qubit capacity; reduce assets/bits or decompose it."
    elif local_full_statevector_feasible and logical_qubits < 40:
        worth_it = "LOCAL FIRST"
        worth_reason = "This problem is still small enough for economical local validation; real QPU time is unlikely to be justified yet."
    elif logical_qubits >= 80:
        worth_it = "REAL-QPU CANDIDATE"
        worth_reason = "The formulation is hardware-scale and impractical for a full laptop statevector, while fitting a current IBM QPU by raw qubit count."
    else:
        worth_it = "COMPARE"
        worth_reason = "The problem is beyond the normal local target but not yet at the 80+ qubit hardware-scale reference; compare tensor/local methods against a QPU."

    search_space_log10 = logical_qubits * math.log10(2.0) if logical_qubits else 0.0
    compilation = {
        "available": False,
        "reason": "not_requested",
        "warning": "Raw qubit fit is not evidence that a dense QUBO routes well. Run offline compilation before treating a hardware plan as feasible.",
    }
    if bool(spec.compile_offline) and logical_qubits > 0:
        compilation = compile_qaoa_proxy(
            logical_qubits,
            qaoa_reps=p,
            interaction_density=density,
            backend_name=spec.backend_name,
            optimization_level=int(spec.transpile_optimization_level),
        )

    return {
        "scale_label": scale_label,
        "objective_contract": {
            "covariance_method": str(spec.covariance_method),
            "resilience_alpha": float(np.clip(spec.resilience_alpha, 0.0, 1.0)),
            "risk_matrix": "Sigma_alpha = (1-alpha) Sigma_base + alpha Sigma_eff",
            "planning_only": True,
            "note": "Hardware allocation encoding must use the same Sigma_alpha as local QPO; this planner does not execute a paid QPU job.",
        },
        "symbols_entered": total_symbols,
        "qscan_assets_planned": assets,
        "allocation_qubits": allocation_qubits,
        "selection_qubits": selection_qubits,
        "auxiliary_qubits": aux,
        "logical_qubits": logical_qubits,
        "required_programmable_qubits": logical_qubits,
        "hardware_fit": hardware,
        "provider_metadata": {
            "provider": provider_md.get("provider"),
            "verified_at": provider_md.get("verified_at"),
            "metadata_source": provider_md.get("metadata_source"),
            "sources": provider_md.get("sources"),
            "warnings": provider_md.get("warnings", []),
            "pricing_usd_per_second": prices,
        },
        "offline_compilation": compilation,
        "ibm_100q_reference_ratio": ibm_100q_reference_ratio,
        "search_space": {
            "binary_states_log10": search_space_log10,
            "allocation_levels_per_asset": 2 ** bits,
            "qubits_per_asset": qubits_per_asset,
        },
        "capacity": {
            "nighthawk_asset_capacity_at_current_encoding": nighthawk_asset_capacity,
            "heron_asset_capacity_at_current_encoding": heron_asset_capacity,
        },
        "shadow_recommendation": {
            "qubit_cap": shadow_qubit_cap,
            "assets": shadow_assets,
            "bits_per_asset": shadow_bits,
            "include_selection_qubit": bool(spec.include_selection_qubit),
            "logical_qubits": shadow_qubits,
        },
        "decision_support": {
            "qpu_worth_it": worth_it,
            "reason": worth_reason,
            "cost_warning": bool(cost_high >= 500.0),
            "routing_warning": bool(density >= 0.50 and logical_qubits >= 60),
            "encoding_density_floor_warning": bool(encoding_density_floor > user_density + 1e-12),
        },
        "statevector": {
            "bytes": statevector_bytes,
            "human": _human_bytes(statevector_bytes),
            "local_ram_gb": spec.local_ram_gb,
            "ram_multiple": local_fraction,
            "full_statevector_feasible_on_this_mac": local_full_statevector_feasible,
        },
        "qubo": {
            "user_interaction_density_assumption": user_density,
            "encoding_implied_min_density": encoding_density_floor,
            "effective_planning_density": density,
            # Backward-compatible alias now intentionally means the density used
            # for runtime/topology planning rather than the optimistic UI input.
            "interaction_density_assumption": density,
            "possible_pair_terms": possible_pairs,
            "allocation_all_to_all_pairs_from_gross_penalty": allocation_pairs,
            "selection_link_pairs": selection_link_pairs,
            "encoding_implied_min_pairs": encoding_min_pairs,
            "estimated_quadratic_terms": estimated_quadratic_terms,
            "topology_bottleneck": (
                "Squared gross-exposure penalty couples all allocation bits; "
                "raw qubit fit therefore does not imply efficient hardware routing."
                if allocation_qubits > 1 else None
            ),
        },
        "qaoa": {
            "reps_p": p,
            "parameters_per_circuit": 2 * p,
            "optimizer_evaluations_per_restart": evaluations,
            "restarts": restarts,
            "frontier_points": frontier,
            "estimator_calls": estimator_calls,
            "estimator_shots": estimator_shots,
            "final_sampler_calls": sampler_calls,
            "final_sampler_shots": sampler_shots,
            "primitive_calls": primitive_calls,
            "raw_executions": raw_executions,
            "mitigation_multiplier_assumption": mitigation,
            "adjusted_executions": adjusted_executions,
        },
        "usage": {
            "runtime_mode": str(spec.runtime_mode or "job").strip().lower(),
            "ibm_quick_execution_seconds": quick_execution_seconds,
            "estimated_qpu_seconds_low": estimated_low,
            "estimated_qpu_seconds_high": estimated_high,
            "planning_price_per_second": price,
            "estimated_cost_low_usd": cost_low,
            "estimated_cost_high_usd": cost_high,
            "open_plan_10min_share_low": estimated_low / IBM_OPEN_MONTHLY_SECONDS,
            "open_plan_10min_share_high": estimated_high / IBM_OPEN_MONTHLY_SECONDS,
            "billing_note": billing_note,
        },
        "validation_burst": {
            "description": "Local parameter search + one final real-hardware Sampler distribution",
            "executions": validation_executions,
            "estimated_qpu_seconds_low": validation_low,
            "estimated_qpu_seconds_high": validation_high,
            "estimated_cost_low_usd": validation_low * price,
            "estimated_cost_high_usd": validation_high * price,
            "full_qaoa_to_validation_cost_ratio_high": (cost_high / (validation_high * price)) if price > 0 and validation_high > 0 else None,
        },
        "ibm_plan_costs": {
            "payg": {
                "usd_per_second": float(prices.get("payg", IBM_PAYG_PER_SECOND)),
                "low": estimated_low * float(prices.get("payg", IBM_PAYG_PER_SECOND)),
                "high": estimated_high * float(prices.get("payg", IBM_PAYG_PER_SECOND)),
            },
            "flex": {
                "usd_per_second": float(prices.get("flex", IBM_FLEX_PER_SECOND)),
                "low": estimated_low * float(prices.get("flex", IBM_FLEX_PER_SECOND)),
                "high": estimated_high * float(prices.get("flex", IBM_FLEX_PER_SECOND)),
            },
            "premium": {
                "usd_per_second": float(prices.get("premium", IBM_PREMIUM_PER_SECOND)),
                "low": estimated_low * float(prices.get("premium", IBM_PREMIUM_PER_SECOND)),
                "high": estimated_high * float(prices.get("premium", IBM_PREMIUM_PER_SECOND)),
            },
        },
        "reference": {
            "architecture": "IBM-style QAOA: Estimator objective evaluations + COBYLA-style classical loop + final Sampler distribution",
            "utility_scale_reference": "IBM public tutorial scales QAOA to a 100-node / 100-qubit hardware problem",
            "note": "This is a planning estimator, not an IBM quote. Actual transpilation, mitigation, backend calibration, and session behavior change usage.",
        },
    }


def build_hardware_qaoa_blueprint(
    symbols: list[str],
    *,
    qscan_assets: int,
    bits_per_asset: int,
    include_selection_qubit: bool,
    auxiliary_qubits: int,
) -> dict[str, Any]:
    """Return a serializable architecture map for the hardware-scale QPO problem."""
    selected = [str(s).upper().strip() for s in symbols if str(s).strip()][: max(0, int(qscan_assets))]
    bits = max(1, int(bits_per_asset))
    rows = []
    cursor = 0
    for symbol in selected:
        allocation = list(range(cursor, cursor + bits))
        cursor += bits
        selection = None
        if include_selection_qubit:
            selection = cursor
            cursor += 1
        rows.append({"symbol": symbol, "allocation_qubits": allocation, "selection_qubit": selection})
    aux = list(range(cursor, cursor + max(0, int(auxiliary_qubits))))
    cursor += len(aux)
    return {
        "symbols": selected,
        "assets": rows,
        "auxiliary_qubits": aux,
        "logical_qubits": cursor,
        "meaning": {
            "allocation_qubits": "binary weight code for each QScan-selected security",
            "selection_qubit": "optional explicit include/exclude decision used for cardinality/replacement constraints",
            "auxiliary_qubits": "reserved slack/constraint variables when an exact QUBO conversion requires them",
        },
    }
