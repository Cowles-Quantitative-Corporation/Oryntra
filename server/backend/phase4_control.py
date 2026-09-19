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

from .minerva import minerva_baseline, tba8_institutional_risk_candidate
from .universal_engine import ENGINE_ID, ENGINE_VERSION, UniversalConfig
from .universal_factor_model import FactorModelConfig
from .universal_optimizer import PortfolioOptimizerConfig
from .universal_phase3 import Phase3Config
from .universal_risk_supervisor import RiskSupervisorConfig, v201_risk_config, v202_risk_config, v203_risk_config


CONTROL_VERSION = "4.0.0-research"

ALPHA_MODELS: dict[str, dict[str, Any]] = {
    "minerva_v1": {
        "label": "Minerva V1 frozen baseline",
        "description": "Frozen walk-forward ridge baseline used as the long-run research control.",
        "public": False,
    },
    "tba8": {
        "label": "TBA8 institutional alpha path",
        "description": "Persistent residual forecasts with causal confidence/capacity controls.",
        "public": False,
    },
}

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
}

BENCHMARKS: dict[str, dict[str, Any]] = {
    "spy": {"label": "SPY", "components": {"SPY": 1.0}},
    "qqq": {"label": "QQQ", "components": {"QQQ": 1.0}},
    "spy_qqq_equal": {"label": "SPY / QQQ equal weight", "components": {"SPY": 0.5, "QQQ": 0.5}},
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
    "custom_yearly": {
        "label": "Custom model / risk / benchmark yearly study",
        "kind": "custom_study",
        "tool": "tools/run_control_plane_study.py",
        "requires_frozen_panel": True,
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
    raise ValueError(f"Unknown risk model: {risk_model}")


def build_control_config(*, alpha_model: str = "tba8", construction: str = "phase15",
                         risk_model: str = "v203", phase3_enabled: bool = True,
                         overrides: dict[str, Any] | None = None) -> UniversalConfig:
    """Assemble one private research config with independently selected layers."""
    alpha_key = str(alpha_model).strip().lower()
    construction_key = str(construction).strip().lower()
    risk_key = str(risk_model).strip().lower()
    if alpha_key not in ALPHA_MODELS:
        raise ValueError(f"Unknown alpha model: {alpha_model}")
    if construction_key not in CONSTRUCTION_MODES:
        raise ValueError(f"Unknown construction mode: {construction}")
    if risk_key not in RISK_MODELS:
        raise ValueError(f"Unknown risk model: {risk_model}")

    base = minerva_baseline() if alpha_key == "minerva_v1" else tba8_institutional_risk_candidate()
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


def control_manifest(*, alpha_model: str, construction: str, risk_model: str,
                     phase3_enabled: bool, benchmark: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    if benchmark not in BENCHMARKS:
        raise ValueError(f"Unknown benchmark: {benchmark}")
    config = build_control_config(
        alpha_model=alpha_model,
        construction=construction,
        risk_model=risk_model,
        phase3_enabled=phase3_enabled,
        overrides=overrides,
    )
    return {
        "phase4_version": CONTROL_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "alpha_model_id": alpha_model,
        "construction_id": construction,
        "risk_model_id": risk_model,
        "phase3_enabled": bool(phase3_enabled),
        "benchmark_id": benchmark,
        "benchmark": BENCHMARKS[benchmark],
        "configuration": asdict(config),
        "config_fingerprint": config.fingerprint,
        "research_only": True,
    }


def code_fingerprint() -> str:
    """Fingerprint the private research path used by prospective candidates."""
    root = Path(__file__).resolve().parent
    modules = (
        "universal_engine.py", "universal_learning.py", "universal_risk_supervisor.py",
        "universal_risk_v201.py", "universal_risk_v202.py", "universal_risk_v203.py",
        "universal_factor_model.py", "universal_optimizer.py", "universal_phase3.py",
        "universal_research.py", "portfolio_execution.py", "minerva.py",
        "phase4_control.py", "phase4_ledger.py", "phase4_runner.py", "phase4_scheduler.py",
    )
    digest = hashlib.sha256()
    for name in modules:
        path = root / name
        digest.update(name.encode("utf-8"))
        if path.exists():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def registry_payload() -> dict[str, Any]:
    return {
        "version": CONTROL_VERSION,
        "alpha_models": ALPHA_MODELS,
        "construction_modes": CONSTRUCTION_MODES,
        "risk_models": RISK_MODELS,
        "benchmarks": BENCHMARKS,
        "test_suites": DEFAULT_TEST_SUITES,
        "defaults": {
            "alpha_model": "tba8",
            "construction": "phase15",
            "risk_model": "v203",
            "phase3_enabled": True,
            "benchmark": "spy_qqq_equal",
        },
    }
