"""Ordinary Control registry entry for the existing causal Gamma/QPO prototype."""
from __future__ import annotations

from .qoptima_family import QOptimaAdapterSpec

PRE_GAMMA_IDS = frozenset({"pre_gamma", "pre_gamma_state_overlay", "pre_gamma_full_risk", "pre_gamma_budget_joint", "pre_gamma_expanded_joint", "pre_gamma_adaptive_joint"})


def pre_gamma_policy(model_id: str) -> dict:
    """Explicit research variants; never an automatic relaxation of a failure."""
    if model_id in {"pre_gamma_budget_joint", "pre_gamma_expanded_joint", "pre_gamma_adaptive_joint"}:
        concentrated = model_id == "pre_gamma_budget_joint"
        baseline = pre_gamma_policy("pre_gamma_state_overlay" if concentrated else "pre_gamma_full_risk")
        construction = "budget_completion_joint_reference" if concentrated else "expanded_joint_reference"
        return {**baseline, "variant": construction + ("_adaptive_alpha" if model_id == "pre_gamma_adaptive_joint" else ""),
                "qpo_overrides": {**baseline["qpo_overrides"], "gamma_construction_policy": construction},
                "gamma_config_overrides": {"alpha_weighting": "matured_feature_ic"} if model_id == "pre_gamma_adaptive_joint" else {}}
    if model_id == "pre_gamma_full_risk":
        return {"variant": "broader_native_qpo_full_risk205", "risk_scope": "full_supervisor",
                "requires_v205": True, "qpo_overrides": {
                    "replay_qscan_assets": 12, "replay_bits_per_asset": 1, "max_name_weight": 1.0 / 11.0,
                    "target_gross_override": 1.0, "start_from_cash": True,
                    "full_qrs_exact_variable_cap": 12, "full_qrs_decision_mode": "exact_reference",
                    "replay_min_positive_positions": 11}}
    if model_id == "pre_gamma_state_overlay":
        return {"variant": "concentrated_native_qpo_state_overlay", "risk_scope": "state_overlay_only",
                "requires_v205": True, "qpo_overrides": {
                    "replay_qscan_assets": 6, "replay_bits_per_asset": 2, "max_name_weight": .30,
                    "target_gross_override": 1.0, "start_from_cash": True,
                    "full_qrs_exact_variable_cap": 12, "full_qrs_decision_mode": "exact_reference"}}
    if model_id != "pre_gamma":
        raise ValueError(f"Unknown Pre-Gamma variant: {model_id}")
    return {"variant": "legacy_native_qpo_full_risk205", "risk_scope": "full_supervisor",
            "requires_v205": False, "qpo_overrides": {}}


def pre_gamma_descriptor(**_):
    return {"id": "pre_gamma", "engine": "Gamma causal forecasts -> native QPO", "research_only": True}


def pre_gamma_state_overlay_descriptor(**_):
    return {**pre_gamma_descriptor(), "id": "pre_gamma_state_overlay", **pre_gamma_policy("pre_gamma_state_overlay")}


def pre_gamma_full_risk_descriptor(**_):
    return {**pre_gamma_descriptor(), "id": "pre_gamma_full_risk", **pre_gamma_policy("pre_gamma_full_risk")}


MODEL_SPECS = (
    QOptimaAdapterSpec(
        "pre_gamma",
        "Pre-Gamma",
        "Current causal Gamma forecast/QPO research prototype. Native QPO construction, not Universal Phase 2; selecting Risk V2.0.5 requests the full post-QPO supervisor and fails visibly if infeasible.",
        pre_gamma_descriptor,
        family="pre_gamma",
        generation="pre-gamma-0.4.3-feature-qa",
    ),
    QOptimaAdapterSpec(
        "pre_gamma_state_overlay", "Pre-Gamma · concentrated + state overlay",
        "Six-candidate native QPO, 2 bits per name, 30% name cap, plus explicitly limited Risk205 state overlay. Not the full supervisor or Universal Phase 2. Requires Risk V2.0.5 selected.",
        pre_gamma_state_overlay_descriptor, family="pre_gamma", generation="pre-gamma-0.4.3-feature-qa",
    ),
    QOptimaAdapterSpec(
        "pre_gamma_full_risk", "Pre-Gamma · broader + full Risk205",
        "Twelve-candidate native QPO, 1 bit per name, 1/11 name cap and eleven positive positions, followed by full unchanged Risk205. Explicit 11-of-12 allocation domain; no relaxed fallback. Requires Risk V2.0.5 selected.",
        pre_gamma_full_risk_descriptor, family="pre_gamma", generation="pre-gamma-0.4.3-feature-qa",
    ),
)


MODEL_SPECS += tuple(QOptimaAdapterSpec(
    model_id, label,
    "Separately selectable research repair. Causal Gamma and unchanged Full-QRS/QRM risk limits, exact budget proposals, candidate-level Risk205 with final validation. Bounded classical reference search, not quantum execution or proof of global optimality.",
    lambda _model_id=model_id, **_: {"id":_model_id,"engine":"Gamma causal forecasts -> bounded classical Full-QRS reference","quantum_execution":False,"research_only":True,**pre_gamma_policy(_model_id)},
    family="pre_gamma",generation="pre-gamma-0.5.0-model-repair")
    for model_id,label in (
        ("pre_gamma_budget_joint","Pre-Gamma · budget-complete joint reference"),
        ("pre_gamma_expanded_joint","Pre-Gamma · expanded joint Risk205 reference"),
        ("pre_gamma_adaptive_joint","Pre-Gamma · adaptive Alpha joint reference")))
