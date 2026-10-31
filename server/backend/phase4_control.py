"""Private CQC research-control registry for Oryntra Phase 4.

This module deliberately contains *configuration assembly*, not execution or
broker access.  It lets the private control plane select the alpha/base model,
portfolio-construction layer, risk supervisor, Phase 3 diagnostics and
benchmark independently while preserving the frozen public/product profiles.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
from typing import Any

from .minerva import tba8_institutional_risk_candidate
from .models.registry import build_model_config, get_model_spec, registry_payload as model_registry_payload, registry_fingerprint as model_registry_fingerprint
from .control_benchmarks import parse_benchmark_spec
from .universal_engine import ENGINE_ID, ENGINE_VERSION, UniversalConfig
from .universal_factor_model import FactorModelConfig
from .universal_optimizer import PortfolioOptimizerConfig
from .universal_phase3 import Phase3Config
from .universal_risk_supervisor import RiskSupervisorConfig, v201_risk_config, v202_risk_config, v203_risk_config, v204_risk_config, v205_risk_config


CONTROL_VERSION = "5.4.13-control"

def _alpha_models_payload() -> dict[str, dict[str, Any]]:
    """Compatibility mapping generated from the dynamic model registry."""
    payload = model_registry_payload()
    return {item["id"]: item for item in payload["models"]}


CONSTRUCTION_MODES: dict[str, dict[str, Any]] = {
    "phase15": {
        "label": "Phase 1.5 construction",
        "description": "TBA/Minerva portfolio construction without the Phase 2 factor optimizer.",
        "phase2": False,
    },
    "phase2": {
        "label": "Phase 2 factor + optimizer",
        "description": "Factor-risk model and constrained portfolio optimizer before independent risk supervision.",
        "phase2": True,
    },
}

RISK_MODELS: dict[str, dict[str, Any]] = {
    "none": {"label": "No supervisor", "description": "Research control only; no portfolio risk supervisor."},
    "v1": {"label": "Existing V1", "description": "Current frozen TBA8 supervisor baseline."},
    "v201": {"label": "V2.0.1 rejected", "description": "Rejected fragility branch retained only for reproducibility."},
    "v202": {"label": "V2.0.2 research", "description": "V1-anchored predictive overlay research candidate."},
    "v203": {"label": "V2.0.3 research", "description": "Soft structural/predictive V2.0.3 research candidate."},
    "v204": {"label": "V2.0.4 Beta-aware research", "description": "V2.0.3 baseline plus bounded Model C/Beta macro de-risking; never increases exposure."},
    "v205": {"label": "V2.0.5 Beta + V1A state-aware research", "description": "V2.0.3 baseline plus Beta macro and QOptima V1A tail/stress de-risking; max-combined, never additive or risk-increasing.", "research_only_backtest": True},
}

BENCHMARKS: dict[str, dict[str, Any]] = {
    "spy": {"label": "SPY", "components": {"SPY": 1.0}},
    "qqq": {"label": "QQQ", "components": {"QQQ": 1.0}},
    "spy_qqq_equal": {"label": "SPY / QQQ equal weight", "components": {"SPY": 0.5, "QQQ": 0.5}},
    "iwm": {"label": "IWM", "components": {"IWM": 1.0}},
    "vxus": {"label": "VXUS", "components": {"VXUS": 1.0}},
    "balanced_global_equal": {
        "label": "SPY / QQQ / IWM / VXUS equal weight",
        "components": {"SPY": 0.25, "QQQ": 0.25, "IWM": 0.25, "VXUS": 0.25},
    },
}

DEFAULT_TEST_SUITES: dict[str, dict[str, Any]] = {
    "phase4_smoke": {
        "label": "Phase 4 control-plane smoke",
        "kind": "pytest",
        "targets": [
            "tests/test_phase4_control.py",
            "tests/test_phase4_ledger.py",
            "tests/test_phase4_routes.py",
            "tests/test_universal_risk_v203.py",
            "tests/test_universal_phase2_integration.py",
            "tests/test_universal_phase3.py",
        ],
    },
    "risk_v203": {
        "label": "Risk V2.0.3 verification",
        "kind": "pytest",
        "targets": ["tests/test_universal_risk_v203.py", "tests/test_universal_risk_v202.py", "tests/test_universal_risk_supervisor.py"],
    },
    "risk_v204": {
        "label": "Risk V2.0.4 + Beta verification",
        "kind": "pytest",
        "targets": ["tests/test_risk_v204.py", "tests/test_beta_v54.py", "tests/test_universal_risk_v203.py"],
    },
    "risk_v205": {
        "label": "Risk V2.0.5 Beta + V1A verification",
        "kind": "pytest",
        "targets": ["tests/test_risk_v205.py", "tests/test_risk_v204.py", "tests/test_qoptima_v1a.py", "tests/test_universal_risk_v203.py"],
    },
    "phase2": {
        "label": "Phase 2 factor + optimizer",
        "kind": "pytest",
        "targets": ["tests/test_universal_factor_model.py", "tests/test_universal_optimizer.py", "tests/test_universal_phase2_integration.py"],
    },
    "phase3": {
        "label": "Phase 3 diagnostics",
        "kind": "pytest",
        "targets": ["tests/test_universal_phase3.py"],
    },
    "research_core": {
        "label": "Research core regression",
        "kind": "pytest",
        "targets": [
            "tests/test_research_pipeline.py",
            "tests/test_research_governance.py",
            "tests/test_universal_engine.py",
            "tests/test_universal_yearly_protocol.py",
            "tests/test_universal_risk_supervisor.py",
            "tests/test_universal_risk_v202.py",
            "tests/test_universal_risk_v203.py",
            "tests/test_universal_factor_model.py",
            "tests/test_universal_optimizer.py",
            "tests/test_universal_phase2_integration.py",
            "tests/test_universal_phase3.py",
        ],
    },
    "full_server": {
        "label": "Full server test suite",
        "kind": "pytest",
        "targets": ["tests"],
    },
    "paired_v203": {
        "label": "TBA8 V1 vs V2.0.3 paired study",
        "kind": "study",
        "tool": "tools/run_v203_paired_risk_study.py",
        "requires_frozen_panel": True,
    },
    "phase2_paired": {
        "label": "Phase 2 paired study",
        "kind": "study",
        "tool": "tools/run_phase2_paired_study.py",
        "requires_frozen_panel": True,
    },
    "phase3_branches": {
        "label": "Phase 1.5 / 1.5+3 / 1.5+2+3",
        "kind": "study",
        "tool": "tools/run_phase3_branch_study.py",
        "requires_frozen_panel": True,
    },
    "alpha_v1": {
        "label": "Alpha V1 verification",
        "kind": "pytest",
        "targets": ["tests/test_alpha_v1.py", "tests/test_alpha_v1_integration.py"],
    },
    "alpha_v11": {
        "label": "Alpha V1.1 robust verification",
        "kind": "pytest",
        "targets": ["tests/test_alpha_v1.py", "tests/test_alpha_v11.py", "tests/test_alpha_v1_integration.py"],
    },
    "alpha_v1_paired": {
        "label": "TBA8 vs Alpha V1 paired study",
        "kind": "study",
        "tool": "tools/run_alpha_v1_paired_study.py",
        "requires_frozen_panel": True,
    },
    "alpha_v11_paired": {
        "label": "TBA8 / Minerva / Alpha V1 / Alpha V1.1 paired study",
        "kind": "study",
        "tool": "tools/run_alpha_v11_paired_study.py",
        "requires_frozen_panel": True,
    },
    "alpha_v12_paired": {
        "label": "TBA8 / Alpha V1 / V1.1 / V1.2 paired study",
        "kind": "study",
        "tool": "tools/run_alpha_v12_paired_study.py",
        "requires_frozen_panel": True,
    },
    "alpha_v12_ablation": {
        "label": "Alpha V1.2 architecture ablation",
        "kind": "study",
        "tool": "tools/run_alpha_v12_ablation_study.py",
        "requires_frozen_panel": True,
    },
    "alpha_v12_fundamentals_ablation": {
        "label": "Alpha V1.2 SEC PIT fundamentals ON/OFF",
        "kind": "study",
        "tool": "tools/run_alpha_v12_fundamentals_ablation.py",
        "requires_frozen_panel": True,
    },
    "alpha_v1_ablation": {
        "label": "Alpha V1 sleeve ablation",
        "kind": "study",
        "tool": "tools/run_alpha_v1_ablation_study.py",
        "requires_frozen_panel": True,
    },
    "alpha_v1_sensitivity": {
        "label": "Alpha V1 parameter sensitivity",
        "kind": "study",
        "tool": "tools/run_alpha_v1_sensitivity_study.py",
        "requires_frozen_panel": True,
    },
    "exposure_efficiency": {
        "label": "Exposure-efficiency diagnostic",
        "kind": "study",
        "tool": "tools/run_exposure_efficiency_study.py",
        "requires_frozen_panel": True,
    },
    "custom_yearly": {
        "label": "Custom model / risk / benchmark yearly study",
        "kind": "custom_study",
        "tool": "tools/run_control_plane_study.py",
        "requires_frozen_panel": True,
    },
    "interactive_backtest": {
        "label": "Interactive current-data backtest",
        "kind": "interactive_backtest",
        "hidden": True,
    },
    "current_market": {
        "label": "Current market model snapshot",
        "kind": "current_market",
        "hidden": True,
    },
    "beta_incremental_backtest": {
        "label": "Beta incremental portfolio backtest",
        "kind": "beta_incremental_backtest",
        "hidden": True,
    },
}


def _risk_config(risk_model: str) -> RiskSupervisorConfig:
    key = str(risk_model or "v1").strip().lower()
    if key == "none":
        return RiskSupervisorConfig(enabled=False)
    if key == "v1":
        return tba8_institutional_risk_candidate().risk_supervisor
    if key == "v201":
        return v201_risk_config()
    if key == "v202":
        return v202_risk_config()
    if key == "v203":
        return v203_risk_config()
    if key == "v204":
        return v204_risk_config()
    if key == "v205":
        return v205_risk_config()
    raise ValueError(f"Unknown risk model: {risk_model}")


def build_control_config(*, alpha_model: str = "tba8", construction: str = "phase15",
                         risk_model: str = "v203", phase3_enabled: bool = True,
                         overrides: dict[str, Any] | None = None) -> UniversalConfig:
    """Assemble one private research config with independently selected layers.

    ``alpha_model`` is retained as the wire/API field name for backwards
    compatibility, but its value is now a dynamic Control model id.
    """
    model_key = str(alpha_model).strip().lower()
    construction_key = str(construction).strip().lower()
    risk_key = str(risk_model).strip().lower()
    model_spec = get_model_spec(model_key)  # validate against registry
    if getattr(model_spec, "role", "portfolio") != "portfolio":
        raise ValueError(f"{model_spec.label} is a {model_spec.role} model and does not directly produce portfolio targets")
    if construction_key not in CONSTRUCTION_MODES:
        raise ValueError(f"Unknown construction mode: {construction}")
    if risk_key not in RISK_MODELS:
        raise ValueError(f"Unknown risk model: {risk_model}")

    base = build_model_config(model_key)
    phase2 = CONSTRUCTION_MODES[construction_key]["phase2"]
    values: dict[str, Any] = {
        "research_profile": "control_plane",
        "risk_supervisor": _risk_config(risk_key),
        "factor_model": FactorModelConfig(enabled=bool(phase2)),
        "portfolio_optimizer": PortfolioOptimizerConfig(enabled=bool(phase2)),
        "phase3": Phase3Config(enabled=bool(phase3_enabled)),
    }
    if overrides:
        protected = {"research_profile"}
        for key, value in overrides.items():
            if key in protected:
                continue
            values[key] = value
    return replace(base, **values)


def resolve_benchmark(benchmark: str) -> dict[str, Any]:
    key = str(benchmark or "").strip()
    if key in BENCHMARKS:
        return {"id": key, **BENCHMARKS[key]}
    parsed = parse_benchmark_spec(key)
    return {
        "id": parsed["id"], "label": parsed["label"],
        "components": parsed["components"], "kind": parsed["kind"],
        "spec": parsed["spec"],
    }


def control_manifest(*, alpha_model: str, construction: str, risk_model: str,
                     phase3_enabled: bool, benchmark: str, overrides: dict[str, Any] | None = None,
                     comparison_benchmarks: list[str] | None = None) -> dict[str, Any]:
    benchmark_definition = resolve_benchmark(benchmark)
    comparisons = [resolve_benchmark(item) for item in (comparison_benchmarks or [benchmark])][:12]
    config = build_control_config(
        alpha_model=alpha_model, construction=construction, risk_model=risk_model,
        phase3_enabled=phase3_enabled, overrides=overrides,
    )
    return {
        "phase4_version": CONTROL_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "alpha_model_id": alpha_model,
        "construction_id": construction,
        "risk_model_id": risk_model,
        "phase3_enabled": bool(phase3_enabled),
        "benchmark_id": benchmark_definition["id"],
        "benchmark": benchmark_definition,
        "comparison_benchmarks": comparisons,
        "configuration": asdict(config),
        "config_fingerprint": config.fingerprint,
        "research_only": True,
    }


def code_fingerprint() -> str:
    """Fingerprint the private research path used by prospective candidates."""
    root = Path(__file__).resolve().parent
    modules = (
        "universal_engine.py", "universal_learning.py", "universal_risk_supervisor.py",
        "universal_risk_v201.py", "universal_risk_v202.py", "universal_risk_v203.py", "universal_risk_v204.py", "universal_risk_v205.py",
        "universal_factor_model.py", "universal_optimizer.py", "universal_phase3.py",
        "universal_research.py", "portfolio_execution.py", "minerva.py", "alpha_v1.py", "alpha_v12.py", "beta_v1.py",
        "phase4_control.py", "phase4_ledger.py", "phase4_runner.py", "phase4_scheduler.py",
        "automation_orchestrator.py", "ibkr_adapter.py",
    )
    digest = hashlib.sha256()
    for name in modules:
        path = root / name
        digest.update(name.encode("utf-8"))
        if path.exists():
            digest.update(path.read_bytes())
    digest.update(b"backend.models")
    digest.update(model_registry_fingerprint().encode("ascii"))
    return digest.hexdigest()


def registry_payload() -> dict[str, Any]:
    model_payload = model_registry_payload()
    alpha_models = {item["id"]: item for item in model_payload["models"]}
    return {
        "version": CONTROL_VERSION,
        "models": model_payload["models"],
        "alpha_models": alpha_models,  # compatibility for older desktop clients
        "model_registry_fingerprint": model_payload["registry_fingerprint"],
        "construction_modes": CONSTRUCTION_MODES,
        "risk_models": RISK_MODELS,
        "benchmarks": BENCHMARKS,
        "test_suites": DEFAULT_TEST_SUITES,
        "defaults": {
            "alpha_model": model_payload["default_model"],
            "construction": "phase15",
            "risk_model": "v203",
            "phase3_enabled": True,
            "benchmark": "balanced_global_equal",
        },
    }
