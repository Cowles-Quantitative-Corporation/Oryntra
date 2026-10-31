"""Private QOptima v0.4.0 Control routes.

QScan/QPO/QRM are research-only. No route in this module may place broker orders.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..internal_access import require_control_operator
from ..qoptima import QOPTIMA_VERSION
from ..qoptima.backends import (
    adaptive_statevector_qubit_cap,
    ibm_runtime_status,
    qiskit_available,
    system_memory_gb,
)
from ..qoptima.backends.local_simulator import qiskit_status
import sys
from ..qoptima.jobs import get_job, list_jobs, submit_portfolio_burst, submit_preset_backtest, request_cancel
from ..qoptima.portfolio import latest_active_portfolio, build_problem
from ..qoptima.training import training_blueprint
from ..qoptima.hardware_qaoa import HardwareQAOASpec, estimate_hardware_qaoa, build_hardware_qaoa_blueprint
from ..qoptima.hardware_compile import compile_financial_qubo_qaoa
from ..qoptima.encoding.hardware_allocation_qubo import build_hardware_allocation_qubo
from ..qoptima.backtest_overlay import run_qoptima_backtest_overlay
from ..qoptima.preset_backtest import seeded_universe, PRESET_VERSION

router = APIRouter()


class GammaResearchFields(BaseModel):
    """Opt-in research controls. No field authorizes broker/QPU execution."""
    model_config = ConfigDict(extra="forbid")
    gamma_enabled: bool = False
    gamma_decision_lag_sessions: int = Field(default=0, ge=0, le=1)
    gamma_feature_cutoff: str | None = Field(default=None, max_length=80)
    gamma_config: dict = Field(default_factory=dict)
    gamma_beta_records: list[dict] = Field(default_factory=list, max_length=100000)
    gamma_sec_cache_directory: str | None = Field(default=None, max_length=1200)
    gamma_sec_histories: dict | None = None
    gamma_min_error_records: int = Field(default=2, ge=2, le=100000)
    gamma_risk205_enabled: bool = False
    gamma_risk205_scope: Literal["full_supervisor", "state_overlay_only"] = "full_supervisor"
    gamma_risk205_config: dict = Field(default_factory=dict)
    gamma_risk_state: dict = Field(default_factory=dict)


class PortfolioBurstRequest(GammaResearchFields):
    model_config = ConfigDict(extra="forbid")
    current_weights: dict[str, float] = Field(min_length=2, max_length=40)
    candidate_symbols: list[str] = Field(default_factory=list, max_length=20)
    expected_returns: dict[str, float] | None = None
    research_objective: str | None = Field(default=None, max_length=2000)
    provider: str = "cache_only"
    lookback_sessions: int = Field(default=126, ge=63, le=504)
    max_name_weight: float = Field(default=0.20, gt=0, le=1)
    risk_aversion: float = Field(default=3.0, ge=0, le=100)
    turnover_penalty: float = Field(default=0.20, ge=0, le=100)
    risk_free_rate: float = Field(default=0.0, ge=-0.20, le=0.30)
    borrowing_rate: float | None = Field(default=None, ge=-0.20, le=1.00)
    transaction_cost_bps: float = Field(default=5.0, ge=0.0, le=1000.0)

    # Risk-matrix research method. Sample covariance remains a benchmark and can
    # be selected through the API for matched research, while the UI exposes the
    # three experimental/research modes requested for QOptima.
    covariance_method: str = "shrinkage"
    covariance_parameters: dict = Field(default_factory=dict)
    covariance_compare: bool = True
    resilience_alpha: float = Field(default=1.0, ge=0.0, le=1.0)

    # Canonical full-QRS objective used by the primary QAOA refinement.
    full_qrs_enabled: bool = True
    full_qrs_beta: float = Field(default=0.10, gt=0.0, lt=0.5)
    full_qrs_bootstrap_reps: int = Field(default=256, ge=0, le=5000)
    full_qrs_quantile_confidence: float = Field(default=0.90, ge=0.5, lt=1.0)
    full_qrs_max_condition_number: float = Field(default=1.0e8, ge=1.0e3, le=1.0e14)
    full_qrs_primary_qaoa: bool = True
    full_qrs_threshold_low: float = Field(default=0.0, ge=0.0, le=20.0)
    full_qrs_threshold_high: float = Field(default=3.0, ge=0.0, le=20.0)
    # Exact finite-domain Full-QRS QAOA controls. Legacy threshold/active-set
    # fields are retained for backward compatibility with saved payloads but are
    # no longer used by the primary Full-QRS optimizer.
    full_qrs_exact_variable_cap: int = Field(default=12, ge=4, le=20)
    full_qrs_qaoa_reps: int | None = Field(default=None, ge=1, le=5)
    full_qrs_qaoa_evaluations: int | None = Field(default=None, ge=12, le=5000)
    full_qrs_threshold_iterations: int = Field(default=6, ge=1, le=20)
    full_qrs_active_set_iterations: int = Field(default=4, ge=1, le=12)

    # Optional V1A-QRS v1.6 state layer. V1A is never trained inside a live
    # burst; the operator must choose a previously frozen artifact.
    v1a_enabled: bool = False
    v1a_artifact_path: str | None = Field(default=None, max_length=1200)
    v1a_feature_values: dict[str, float] = Field(default_factory=dict)
    v1a_es_budget: float | None = Field(default=None, gt=0.0, le=2.0)
    v1a_es_activation_max_ratio: float | None = Field(default=None, gt=1.0, le=20.0)
    v1a_risk_budget_annual: float = Field(default=0.15, gt=0.0, le=2.0)
    v1a_risk_floor_ratio: float = Field(default=0.10, gt=0.0, le=1.0)
    v1a_covariance_additive_floor: float = Field(default=1e-8, gt=0.0, le=1.0)
    v1a_forecast_error_covariance_floor: float = Field(default=1e-8, gt=0.0, le=1.0)
    v1a_forecast_error_covariance_window: int = Field(default=60, ge=12, le=504)
    v1a_forecast_error_min_history: int = Field(default=12, ge=6, le=504)
    v1a_quantile_block_stride: int = Field(default=1, ge=1, le=126)
    v1a_qaoa_low_tail_quantile: float = Field(default=0.05, gt=0.0, lt=0.5)
    v1a_qaoa_phase_global_floor_ratio: float = Field(default=0.10, gt=0.0, le=1.0)
    v1a_qaoa_phase_absolute_floor: float = Field(default=1e-8, gt=0.0, le=1.0)
    v1a_qaoa_energy_cap: float = Field(default=8.0, gt=0.0, le=1000.0)
    v1a_qaoa_infeasible_gap: float = Field(default=1.0, gt=0.0, le=1000.0)
    v1a_scale_tolerance: float = Field(default=0.03, ge=0.0, le=0.50)

    # Point-in-time model forecast contract. Legacy model_forecasts is diagnostic only.
    forecast_records: list[dict] | None = None
    forecast_error_history: list[dict] | None = None
    decision_time: str | None = None
    forecast_horizon_sessions: int = Field(default=252, ge=1, le=2520)
    require_adjusted_prices: bool = False

    # QOptima resilience / fragility research objective.
    fragility_enabled: bool = True
    fragility_downside_weight: float = Field(default=0.0, ge=0.0, le=10.0)
    fragility_correlation_weight: float = Field(default=0.0, ge=0.0, le=10.0)
    fragility_forecast_weight: float = Field(default=0.0, ge=0.0, le=10.0)
    fragility_regime_weight: float = Field(default=0.0, ge=0.0, le=10.0)
    fragility_correlation_blend: float = Field(default=0.65, ge=0.0, le=1.0)
    model_forecasts: dict[str, dict[str, float]] | None = None

    # Validation = fast architecture test. Deep = genuine local Qiskit circuit simulation.
    compute_depth: str = "validation"
    compute_target: str = "local_mac"

    # QScan: keep the expensive/native search compact.
    qscan_max_assets: int = Field(default=8, ge=3, le=12)
    qscan_candidate_reserve: int = Field(default=3, ge=0, le=6)

    # QPO: native discrete portfolio-weight search.
    bits_per_asset: int = Field(default=3, ge=1, le=4)
    max_quantum_variables: int = Field(default=24, ge=4, le=40)
    gross_penalty: float = Field(default=80.0, ge=0.1, le=10000)
    backend: str = "virtual_qpu"
    shots: int = Field(default=8192, ge=128, le=100000)
    seed: int = 41

    # Legacy QRS-directed threshold experiment retained for matched comparisons.
    # The primary refinement now uses the full canonical QRS when enabled.
    qrs_directed_enabled: bool = False
    qrs_directed_backend: str = "exact"
    qrs_threshold_low: float = Field(default=0.0, ge=0.0, le=20.0)
    qrs_threshold_high: float = Field(default=3.0, ge=0.0, le=20.0)
    qrs_threshold_iterations: int = Field(default=6, ge=1, le=20)

    # Calibrated local effort target. Not a minimum runtime.
    local_effort_target_seconds: float = Field(default=600.0, ge=30.0, le=3600.0)
    qaoa_reps_max: int = Field(default=3, ge=1, le=5)
    qaoa_maxiter: int = Field(default=48, ge=8, le=300)
    qiskit_qubit_cap: int = Field(default=24, ge=12, le=26)
    qrm_minimum_paths: int = Field(default=150000, ge=10000, le=5000000)
    qrm_horizon_days: int = Field(default=21, ge=5, le=126)

    # QPU planning only. Real IBM submission remains locked in v0.4.0.
    price_per_second: float = Field(default=1.60, ge=0, le=1000)
    max_qpu_seconds: float = Field(default=120.0, gt=0, le=100000)

    # Targeted post-optimization stock/risk bursts.
    position_check_threshold: float = Field(default=0.005, ge=0, le=0.25)
    max_position_checks: int = Field(default=8, ge=1, le=20)

    @field_validator("covariance_method")
    @classmethod
    def validate_covariance_method(cls, value: str) -> str:
        clean = str(value or "shrinkage").strip().lower()
        if clean not in {"sample", "shrinkage", "rmt", "quantum_density"}:
            raise ValueError("covariance_method must be sample, shrinkage, rmt, or quantum_density")
        return clean

    @field_validator("compute_depth")
    @classmethod
    def validate_depth(cls, value: str) -> str:
        clean = str(value or "validation").strip().lower()
        if clean not in {"validation", "deep"}:
            raise ValueError("compute_depth must be validation or deep")
        return clean

    @field_validator("compute_target")
    @classmethod
    def validate_target(cls, value: str) -> str:
        clean = str(value or "local_mac").strip().lower()
        if clean not in {"local_mac", "real_qpu_future"}:
            raise ValueError("compute_target must be local_mac or real_qpu_future")
        return clean


class HardwareQAOAEstimateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_weights: dict[str, float] = Field(default_factory=dict, max_length=200)
    candidate_symbols: list[str] = Field(default_factory=list, max_length=200)
    qscan_assets: int = Field(default=20, ge=1, le=60)
    candidate_reserve: int = Field(default=5, ge=0, le=30)
    bits_per_asset: int = Field(default=4, ge=1, le=8)
    include_selection_qubit: bool = True
    auxiliary_qubits: int = Field(default=0, ge=0, le=100)
    qaoa_reps: int = Field(default=1, ge=1, le=10)
    optimizer_evaluations: int = Field(default=28, ge=1, le=1000)
    restarts: int = Field(default=1, ge=1, le=50)
    frontier_points: int = Field(default=5, ge=1, le=20)
    estimator_shots: int = Field(default=1000, ge=1, le=1000000)
    final_sampler_shots: int = Field(default=10000, ge=1, le=1000000)
    mitigation_multiplier: float = Field(default=1.0, ge=1.0, le=20.0)
    interaction_density: float = Field(default=0.25, ge=0.0, le=1.0)
    runtime_mode: str = "job"
    planning_price_per_second: float = Field(default=1.60, ge=0.0, le=1000.0)
    backend_name: str | None = None
    compile_offline: bool = False
    transpile_optimization_level: int = Field(default=2, ge=0, le=3)
    covariance_method: str = "shrinkage"
    covariance_parameters: dict = Field(default_factory=dict)
    resilience_alpha: float = Field(default=1.0, ge=0.0, le=1.0)
    fragility_enabled: bool = True
    fragility_downside_weight: float = Field(default=0.0, ge=0.0, le=10.0)
    fragility_correlation_weight: float = Field(default=0.0, ge=0.0, le=10.0)
    fragility_forecast_weight: float = Field(default=0.0, ge=0.0, le=10.0)
    fragility_regime_weight: float = Field(default=0.0, ge=0.0, le=10.0)

    # Optional actual-financial-QUBO offline compilation. This may read cached
    # market history but never submits a QPU job. It exists to replace optimistic
    # proxy-routing claims with the real encoded interaction graph when requested.
    compile_financial_qubo: bool = False
    provider: str = "cache_only"
    lookback_sessions: int = Field(default=126, ge=63, le=504)
    expected_returns: dict[str, float] | None = None
    max_name_weight: float = Field(default=0.20, gt=0.0, le=1.0)
    risk_aversion: float = Field(default=3.0, ge=0.0, le=100.0)
    turnover_penalty: float = Field(default=0.20, ge=0.0, le=100.0)
    risk_free_rate: float = Field(default=0.0, ge=-0.20, le=0.30)
    borrowing_rate: float | None = Field(default=None, ge=-0.20, le=1.00)
    transaction_cost_bps: float = Field(default=5.0, ge=0.0, le=1000.0)
    target_positions: int | None = Field(default=None, ge=0, le=60)
    gross_penalty: float = Field(default=80.0, ge=0.1, le=10000.0)
    selection_link_penalty: float = Field(default=20.0, ge=0.0, le=10000.0)
    cardinality_penalty: float = Field(default=12.0, ge=0.0, le=10000.0)


class QOptimaBacktestOverlayRequest(GammaResearchFields):
    model_config = ConfigDict(extra="forbid")
    universe: list[str] = Field(default_factory=list, max_length=100)
    universe_snapshots: list[dict] = Field(default_factory=list, max_length=500)
    universe_max_staleness_days: int = Field(default=31, ge=0, le=3650)
    provider: str = "cache_only"
    evaluation_period: str = "1y"
    holdout_sessions: int = Field(default=0, ge=0, le=10000)
    run_covariance_benchmarks: bool = True
    run_alpha_frontier: bool = True
    evaluation_start: str | None = Field(default=None, max_length=80)
    evaluation_end: str | None = Field(default=None, max_length=80)
    lookback_sessions: int = Field(default=126, ge=63, le=504)
    rebalance_sessions: int = Field(default=21, ge=5, le=126)
    max_name_weight: float = Field(default=0.20, gt=0, le=1)
    risk_aversion: float = Field(default=3.0, ge=0, le=100)
    turnover_penalty: float = Field(default=0.20, ge=0, le=100)
    risk_free_rate: float = Field(default=0.0, ge=-0.20, le=0.30)
    transaction_cost_bps: float = Field(default=5.0, ge=0, le=1000)
    require_adjusted_prices: bool = True
    covariance_methods: list[str] = Field(default_factory=lambda: ["sample", "shrinkage", "rmt", "quantum_density"])
    selected_covariance_method: str = "shrinkage"
    covariance_parameters: dict = Field(default_factory=dict)
    covariance_parameters_by_method: dict[str, dict] = Field(default_factory=dict)
    alpha_values: list[float] = Field(default_factory=lambda: [0.0, 0.25, 0.5, 0.75, 1.0])
    true_qoptima_replay: bool = True
    replay_bits_per_asset: int = Field(default=2, ge=1, le=4)
    replay_qscan_assets: int = Field(default=6, ge=3, le=12)
    replay_min_positive_positions: int = Field(default=0, ge=0, le=100)
    replay_backend: str = "exact"
    seed: int = 41
    # Optional V1A-QRS v1.6 state layer. V1A is never trained inside a live
    # burst; the operator must choose a previously frozen artifact.
    v1a_enabled: bool = False
    v1a_artifact_path: str | None = Field(default=None, max_length=1200)
    v1a_feature_values: dict[str, float] = Field(default_factory=dict)
    v1a_es_budget: float | None = Field(default=None, gt=0.0, le=2.0)
    v1a_es_activation_max_ratio: float | None = Field(default=None, gt=1.0, le=20.0)
    v1a_risk_budget_annual: float = Field(default=0.15, gt=0.0, le=2.0)
    v1a_risk_floor_ratio: float = Field(default=0.10, gt=0.0, le=1.0)
    v1a_covariance_additive_floor: float = Field(default=1e-8, gt=0.0, le=1.0)
    v1a_forecast_error_covariance_floor: float = Field(default=1e-8, gt=0.0, le=1.0)
    v1a_forecast_error_covariance_window: int = Field(default=60, ge=12, le=504)
    v1a_forecast_error_min_history: int = Field(default=12, ge=6, le=504)
    v1a_quantile_block_stride: int = Field(default=1, ge=1, le=126)
    v1a_qaoa_low_tail_quantile: float = Field(default=0.05, gt=0.0, lt=0.5)
    v1a_qaoa_phase_global_floor_ratio: float = Field(default=0.10, gt=0.0, le=1.0)
    v1a_qaoa_phase_absolute_floor: float = Field(default=1e-8, gt=0.0, le=1.0)
    v1a_qaoa_energy_cap: float = Field(default=8.0, gt=0.0, le=1000.0)
    v1a_qaoa_infeasible_gap: float = Field(default=1.0, gt=0.0, le=1000.0)
    v1a_scale_tolerance: float = Field(default=0.03, ge=0.0, le=0.50)

    v1a_artifact_schedule: list[dict] = Field(default_factory=list, max_length=10000)
    forecast_records: list[dict] | None = None
    forecast_error_history: list[dict] | None = None
    forecast_horizon_sessions: int = Field(default=21, ge=1, le=252)
    full_qrs_primary_qaoa: bool = True
    full_qrs_beta: float = Field(default=0.10, gt=0.0, lt=0.5)
    full_qrs_bootstrap_reps: int = Field(default=128, ge=0, le=5000)
    full_qrs_quantile_confidence: float = Field(default=0.90, ge=0.5, lt=1.0)
    full_qrs_exact_variable_cap: int = Field(default=12, ge=4, le=20)
    full_qrs_qaoa_reps: int = Field(default=2, ge=1, le=5)
    full_qrs_qaoa_evaluations: int = Field(default=96, ge=12, le=5000)
    full_qrs_threshold_iterations: int = Field(default=4, ge=1, le=20)
    full_qrs_active_set_iterations: int = Field(default=3, ge=1, le=12)
    fragility_downside_weight: float = Field(default=0.0, ge=0, le=10)
    fragility_correlation_weight: float = Field(default=0.0, ge=0, le=10)
    fragility_regime_weight: float = Field(default=0.0, ge=0, le=10)
    fragility_correlation_blend: float = Field(default=0.65, ge=0, le=1)
    run_ablations: bool = True
    run_component_ablations: bool = True
    hardware_qscan_assets: int = Field(default=20, ge=1, le=60)
    hardware_bits_per_asset: int = Field(default=4, ge=1, le=8)
    hardware_selection_qubit: bool = True
    hardware_qaoa_reps: int = Field(default=1, ge=1, le=10)
    hardware_optimizer_evaluations: int = Field(default=28, ge=1, le=1000)
    hardware_frontier_points: int = Field(default=5, ge=1, le=20)
    hardware_estimator_shots: int = Field(default=1000, ge=1, le=1000000)
    hardware_sampler_shots: int = Field(default=10000, ge=1, le=1000000)
    hardware_mitigation_multiplier: float = Field(default=1.5, ge=1, le=20)
    planning_price_per_second: float = Field(default=1.60, ge=0, le=1000)


class QOptimaPresetBacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = "v1a_vs_canonical"
    universe_size: int = Field(default=75, ge=5, le=100)
    seed: int | None = None
    provider: str = "auto"

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        clean = str(value or "v1a_vs_canonical").strip().lower()
        allowed = {"v1a_vs_canonical", "canonical_full_qrs", "baseline_qpo"}
        if clean not in allowed:
            raise ValueError(f"model must be one of {sorted(allowed)}")
        return clean



@router.get("/status")
def status(request: Request):
    require_control_operator(request)
    memory = system_memory_gb()
    plan = adaptive_statevector_qubit_cap(requested_cap=24)
    qs = qiskit_status()
    return {
        "version": QOPTIMA_VERSION,
        "mode": "qscan_qpo_qrm_hardware_qaoa_planner",
        "local_exact_available": True,
        "virtual_qpu_available": True,
        "qiskit_available": qiskit_available(),
        "qiskit_status": qs,
        "runtime_python": sys.executable,
        "local_acceleration": "CPU/RAM statevector; core Qiskit does not use the Apple Metal GPU",
        "ibm": ibm_runtime_status(),
        "real_qpu_submission_enabled": False,
        "execution_allowed": False,
        "system_memory_gb": memory,
        "statevector_memory_plan": plan,
        "deep_defaults": {
            "local_effort_target_seconds": 600,
            "qiskit_qubit_cap": 24,
            "qaoa_reps_max": 3,
            "qrm_minimum_paths": 150000,
            "planning_note": "600 seconds is a calibration target for work units, not a runtime floor.",
        },
        "architecture": "Current portfolio -> QScan -> QPO/Full-QRS -> QRM; Gamma research inputs and post-selection Risk 2.0.5 are explicit opt-ins, not the legacy replay default",
        "gamma_research": {"available": True, "default_enabled": False, "performance_validated": False, "routes": ["bursts", "backtest-overlay"]},
        "engines": {
            "QScan": "examines the full active portfolio and chooses positions/candidates worth an expensive burst",
            "QPO": "uses the literal Full Canonical QRS finite-domain diagonal Hamiltonian as the primary QAOA objective when enabled; baseline mean-risk QPO remains a matched benchmark",
            "QRM": "runs structural risk plus fat-tail Monte Carlo/stress review before a candidate can leave QOptima",
        },
        "compute_layer": ["exact_reference", "virtual_qpu", "qiskit_statevector_shadow", "hardware_qaoa_planner", "ibm_hardware_executor_locked"],
    }


@router.post("/hardware-plan")
def hardware_plan(payload: HardwareQAOAEstimateRequest, request: Request):
    user = require_control_operator(request)
    active_symbols = []
    for raw in payload.current_weights:
        symbol = str(raw or "").upper().strip()
        if symbol and symbol not in active_symbols:
            active_symbols.append(symbol)
    candidate_symbols = []
    for raw in payload.candidate_symbols:
        symbol = str(raw or "").upper().strip()
        if symbol and symbol not in active_symbols and symbol not in candidate_symbols:
            candidate_symbols.append(symbol)
    symbols = active_symbols + candidate_symbols
    memory = system_memory_gb()
    plan = estimate_hardware_qaoa(HardwareQAOASpec(
        total_symbols=len(symbols),
        qscan_assets=payload.qscan_assets,
        bits_per_asset=payload.bits_per_asset,
        include_selection_qubit=payload.include_selection_qubit,
        auxiliary_qubits=payload.auxiliary_qubits,
        qaoa_reps=payload.qaoa_reps,
        optimizer_evaluations=payload.optimizer_evaluations,
        restarts=payload.restarts,
        frontier_points=payload.frontier_points,
        estimator_shots=payload.estimator_shots,
        final_sampler_shots=payload.final_sampler_shots,
        mitigation_multiplier=payload.mitigation_multiplier,
        interaction_density=payload.interaction_density,
        runtime_mode=payload.runtime_mode,
        planning_price_per_second=payload.planning_price_per_second,
        local_ram_gb=memory,
        backend_name=payload.backend_name,
        compile_offline=payload.compile_offline,
        transpile_optimization_level=payload.transpile_optimization_level,
        covariance_method=payload.covariance_method,
        resilience_alpha=payload.resilience_alpha,
    ))
    target_assets = min(len(symbols), payload.qscan_assets)
    candidate_take = min(len(candidate_symbols), payload.candidate_reserve, target_assets)
    active_take = min(len(active_symbols), max(0, target_assets - candidate_take))
    selected_symbols = active_symbols[:active_take] + candidate_symbols[:candidate_take]
    # Fill any unused slots from whichever pool still has names. This is only a
    # planner preview; the real QScan run ranks names using market/risk evidence.
    for symbol in active_symbols[active_take:] + candidate_symbols[candidate_take:]:
        if len(selected_symbols) >= target_assets:
            break
        if symbol not in selected_symbols:
            selected_symbols.append(symbol)
    if payload.compile_offline and payload.compile_financial_qubo and selected_symbols:
        try:
            selected_current = {s: float(payload.current_weights.get(s, 0.0)) for s in selected_symbols if s in active_symbols}
            # build_problem requires positive active gross; if QScan preview selected
            # only candidates, keep the first active symbol solely for financial
            # context rather than fabricate invested candidate weights.
            if sum(selected_current.values()) <= 1e-12 and active_symbols:
                first_active = active_symbols[0]
                selected_current[first_active] = float(payload.current_weights.get(first_active, 0.0))
                if first_active not in selected_symbols:
                    selected_symbols = [first_active] + selected_symbols[:-1]
            selected_candidates = [s for s in selected_symbols if s not in selected_current]
            problem, financial_provenance = build_problem(
                user["id"],
                current_weights=selected_current,
                candidate_symbols=selected_candidates,
                provider=payload.provider,
                lookback_sessions=payload.lookback_sessions,
                expected_returns=payload.expected_returns,
                max_name_weight=payload.max_name_weight,
                risk_aversion=payload.risk_aversion,
                turnover_penalty=payload.turnover_penalty,
                risk_free_rate=payload.risk_free_rate,
                transaction_cost_bps=payload.transaction_cost_bps,
                covariance_method=payload.covariance_method,
                covariance_parameters=payload.covariance_parameters,
                covariance_compare=False,
                resilience_alpha=payload.resilience_alpha,
                fragility_enabled=payload.fragility_enabled,
                fragility_downside_weight=payload.fragility_downside_weight,
                fragility_correlation_weight=payload.fragility_correlation_weight,
                fragility_forecast_weight=payload.fragility_forecast_weight,
                fragility_regime_weight=payload.fragility_regime_weight,
                require_adjusted_prices=False,
            )
            target_positions = payload.target_positions
            if target_positions is None and payload.include_selection_qubit:
                target_positions = max(1, sum(1 for v in selected_current.values() if float(v) > 1e-12))
            financial_qubo = build_hardware_allocation_qubo(
                problem,
                bits_per_asset=payload.bits_per_asset,
                include_selection_qubit=payload.include_selection_qubit,
                target_positions=target_positions,
                gross_penalty=payload.gross_penalty,
                selection_link_penalty=payload.selection_link_penalty,
                cardinality_penalty=payload.cardinality_penalty,
            )
            plan["offline_compilation"] = compile_financial_qubo_qaoa(
                financial_qubo,
                qaoa_reps=payload.qaoa_reps,
                backend_name=payload.backend_name,
                optimization_level=payload.transpile_optimization_level,
            )
            plan["offline_compilation"]["financial_problem"] = {
                "symbols": list(problem.symbols),
                "covariance_method": problem.covariance_method,
                "resilience_alpha": float(problem.resilience_alpha),
                "qubo": financial_qubo.as_dict(),
                "data_window": financial_provenance.get("returns_window") or financial_provenance.get("window"),
            }
        except Exception as exc:
            plan["offline_compilation"] = {
                "available": False,
                "reason": "financial_qubo_compile_failed",
                "error": str(exc),
                "actual_financial_qubo": True,
                "warning": "The actual financial-QUBO compile was requested but could not be completed. No hardware routing claim is made.",
            }

    plan["pool"] = {
        "active_symbols": len(active_symbols),
        "candidate_symbols": len(candidate_symbols),
        "candidate_reserve_requested": payload.candidate_reserve,
        "candidate_slots_planned": sum(1 for s in selected_symbols if s in candidate_symbols),
        "note": "Planning preview reserves candidate slots; the actual QScan burst ranks active and candidate names before QPO/QAOA.",
    }
    plan["blueprint"] = build_hardware_qaoa_blueprint(
        selected_symbols,
        qscan_assets=payload.qscan_assets,
        bits_per_asset=payload.bits_per_asset,
        include_selection_qubit=payload.include_selection_qubit,
        auxiliary_qubits=payload.auxiliary_qubits,
    )
    candidate_set = set(candidate_symbols)
    for row in plan.get("blueprint", {}).get("assets", []):
        row["role"] = "candidate" if row.get("symbol") in candidate_set else "active"
    plan["qoptima_objective"] = {
        "mode": "resilience_qrs" if payload.fragility_enabled else "standard_sharpe",
        "covariance_method": payload.covariance_method,
        "covariance_parameters": payload.covariance_parameters,
        "resilience_alpha": payload.resilience_alpha,
        "qaoa_quadratic_objective": "mu^T w - lambda * w^T Sigma_alpha w - turnover penalty",
        "ranking": "QRS=(mu^T w-rf)/sqrt(w^T Sigma_alpha w)" if payload.fragility_enabled else "Sharpe",
        "fragility_weights": {
            "downside": payload.fragility_downside_weight,
            "correlation_collapse": payload.fragility_correlation_weight,
            "forecast_disagreement": payload.fragility_forecast_weight,
            "regime": payload.fragility_regime_weight,
        },
    }
    plan["execution_enabled"] = False
    plan["safety"] = "Planning only; QOptima hardware QAOA cannot transmit broker orders and real IBM submission is not enabled by this endpoint."
    return plan


@router.get("/preset-backtest/universe")
def preset_backtest_universe(request: Request, seed: int = 41, universe_size: int = 75):
    require_control_operator(request)
    data = seeded_universe(int(seed), int(universe_size))
    return {"preset_version": PRESET_VERSION, **data}


@router.post("/preset-backtest")
def preset_backtest(payload: QOptimaPresetBacktestRequest, request: Request):
    user = require_control_operator(request)
    try:
        return submit_preset_backtest(user["id"], payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



@router.post("/backtest-overlay")
def backtest_overlay(payload: QOptimaBacktestOverlayRequest, request: Request):
    user = require_control_operator(request)
    try:
        return run_qoptima_backtest_overlay(user["id"], payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, request: Request):
    user = require_control_operator(request)
    try:
        return request_cancel(user["id"], int(job_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/training-blueprint")
def qoptima_training_blueprint(request: Request):
    require_control_operator(request)
    return training_blueprint()


@router.get("/active-portfolio")
def active_portfolio(request: Request):
    user = require_control_operator(request)
    return latest_active_portfolio(user["id"])


@router.post("/bursts")
def burst(payload: PortfolioBurstRequest, request: Request):
    user = require_control_operator(request)
    if payload.compute_target == "real_qpu_future":
        raise HTTPException(status_code=409, detail="Real QPU execution remains locked in QOptima 0.4. Use Hardware QAOA Planner for full-scale qubit/time/cost planning, or switch Compute Target to Local Mac Scan.")
    try:
        return submit_portfolio_burst(user["id"], payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/v1a-artifacts")
def v1a_artifacts(request: Request):
    require_control_operator(request)
    root = Path(__file__).resolve().parents[2] / "data" / "qoptima" / "v1a_preset_artifacts"
    rows = []
    if root.exists():
        from ..qoptima.v1a.inference import load_v1a_artifact
        for path in sorted(root.rglob("*.pt"), key=lambda x: x.stat().st_mtime, reverse=True)[:100]:
            try:
                _model, meta, _c, digest = load_v1a_artifact(path)
                rows.append({
                    "path": str(path),
                    "name": path.name,
                    "artifact_information_cutoff": meta.get("artifact_information_cutoff"),
                    "spec_hash": meta.get("spec_hash"),
                    "sha256": digest,
                    "signed_control": bool(meta.get("signed_control", False)),
                })
            except Exception as exc:
                rows.append({"path": str(path), "name": path.name, "error": str(exc)})
    return {"artifacts": rows}


@router.get("/jobs")
def jobs(request: Request, limit: int = 50):
    user = require_control_operator(request)
    return {"jobs": list_jobs(user["id"], limit)}


@router.get("/jobs/{job_id}")
def job(job_id: int, request: Request):
    user = require_control_operator(request)
    try:
        return get_job(user["id"], job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
