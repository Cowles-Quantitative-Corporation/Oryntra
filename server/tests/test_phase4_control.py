from dataclasses import asdict

from backend.minerva import tba8_institutional_risk_candidate
from backend.phase4_control import build_control_config, code_fingerprint, registry_payload


def test_control_registry_exposes_independent_model_risk_benchmark_and_tests():
    payload = registry_payload()
    assert {"minerva_v1", "tba8"}.issubset(payload["alpha_models"])
    assert {"phase15", "phase2"}.issubset(payload["construction_modes"])
    assert {"none", "v1", "v201", "v202", "v203"}.issubset(payload["risk_models"])
    assert {"spy", "qqq", "spy_qqq_equal"}.issubset(payload["benchmarks"])
    assert {"phase4_smoke", "full_server", "paired_v203", "phase2_paired", "phase3_branches", "custom_yearly"}.issubset(payload["test_suites"])


def test_control_config_can_select_phase2_phase3_and_risk_independently_without_mutating_frozen_tba8():
    frozen = tba8_institutional_risk_candidate()
    custom = build_control_config(alpha_model="tba8", construction="phase2", risk_model="v203", phase3_enabled=True)
    assert custom.research_profile == "control_plane"
    assert custom.factor_model.enabled
    assert custom.portfolio_optimizer.enabled
    assert custom.phase3.enabled
    assert custom.risk_supervisor.v203_enabled
    assert not custom.risk_supervisor.v202_enabled
    assert frozen.research_profile != "control_plane"
    assert not frozen.factor_model.enabled
    assert not frozen.portfolio_optimizer.enabled


def test_control_config_supports_no_supervisor_research_control():
    config = build_control_config(alpha_model="minerva_v1", construction="phase15", risk_model="none", phase3_enabled=False)
    assert not config.risk_supervisor.enabled
    assert not config.factor_model.enabled
    assert not config.portfolio_optimizer.enabled
    assert not config.phase3.enabled


def test_phase4_code_fingerprint_is_sha256():
    value = code_fingerprint()
    assert len(value) == 64
    int(value, 16)
