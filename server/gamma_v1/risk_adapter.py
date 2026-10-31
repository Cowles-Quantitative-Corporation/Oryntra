"""Precisely scoped downstream state ablations; never alter upstream search."""
from dataclasses import replace

def install_state_policy(policy):
    if policy not in ['native','off','tail_gamma_only']:raise ValueError('Unsupported state policy')
    if policy=='native':return lambda:None
    from backend.risk import v205
    original=v205.v1a_regime_modifier_v205
    original_stress=v205.v1a_stress_diagnostics_v205
    def wrapper(state,**kwargs):
        if policy=='off':
            result=original(state,**{**kwargs,'control':replace(kwargs['control'],v1a_enabled=False)})
        else:
            # This worker runs in its own process. Restore even after an exception.
            v205.v1a_stress_diagnostics_v205=lambda *a,**k:{'available':False,'reason':'preregistered_tail_gamma_only_structural_stress_not_in_state_cut'}
            try:result=original(state,**kwargs)
            finally:v205.v1a_stress_diagnostics_v205=original_stress
        return {**result,'gamma_v1_state_policy':policy}
    v205.v1a_regime_modifier_v205=wrapper
    def uninstall():
        v205.v1a_regime_modifier_v205=original
        v205.v1a_stress_diagnostics_v205=original_stress
    return uninstall
