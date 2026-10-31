from gamma_v1.risk_adapter import install_state_policy
from backend.risk import v205

def test_native_noop_and_off_scope():
    original=v205.v1a_regime_modifier_v205;stress=v205.v1a_stress_diagnostics_v205
    undo=install_state_policy('native');assert v205.v1a_regime_modifier_v205 is original;undo()
    undo=install_state_policy('off')
    try:
        out=v205.v1a_regime_modifier_v205(None,weights=None,covariance=None,returns_history=None,control=v205.V205V1AControlConfig())
        assert out['reason']=='v1a_disabled' and out['gross_modifier']==1
        assert v205.v1a_stress_diagnostics_v205 is stress
    finally:undo()
    assert v205.v1a_regime_modifier_v205 is original

def test_tail_only_restores_function_after_failure():
    original=v205.v1a_regime_modifier_v205;stress=v205.v1a_stress_diagnostics_v205
    undo=install_state_policy('tail_gamma_only')
    try:
        try:v205.v1a_regime_modifier_v205({'gamma':.4},weights=None,covariance=None,returns_history=None,control=None)
        except Exception:pass
        assert v205.v1a_stress_diagnostics_v205 is stress
    finally:undo()
    assert v205.v1a_regime_modifier_v205 is original
