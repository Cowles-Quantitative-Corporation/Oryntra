"""Conditional Beta-output replay with own counters; no V1A-dominance premise.

No raw Beta record is invented. Exogenous archived modifier diagnostics and
blockers are sufficient for this state-only branch's original arithmetic.
Any unsupported branch/provenance is a fatal error, never a cash policy event.
"""
from pathlib import Path
from copy import deepcopy
from dataclasses import asdict
import json,hashlib
import numpy as np
import pandas as pd


def key(x):return pd.to_datetime(x,utc=True).isoformat()


class BetaObservationAdapter:
    def __init__(self,path,expected_sha256):
        raw=Path(path).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=expected_sha256:raise ValueError('Beta observation archive identity mismatch')
        self.data=json.loads(raw);self.by_cutoff={};self.original_prior={};self.calls=[]
        count=0
        for row in self.data['states']:
            cutoff=key(row['feature_cutoff'])
            if cutoff in self.by_cutoff:raise ValueError('Duplicate Beta observation cutoff')
            if not row['beta_modifier_observation'].get('available'):raise ValueError('Missing original Beta observation')
            self.by_cutoff[cutoff]=row;self.original_prior[cutoff]=count
            count=int(row['beta_modifier_observation']['persistence_rebalances'])
        if len(self.by_cutoff)!=120:raise ValueError('Complete120-date Beta observation panel required')

    def beta(self,beta_state,*,control,previous_persistence_rebalances=0,current_session=None,v203_predictive_triggered=False):
        if beta_state is not None or v203_predictive_triggered or asdict(control)!=self.data['beta_control']:
            raise ValueError('Unsupported raw/mixed/state-only Beta configuration')
        cutoff=key(current_session)
        if cutoff not in self.by_cutoff:raise ValueError('No exact-cutoff Beta observation')
        prior=int(previous_persistence_rebalances)
        if prior!=previous_persistence_rebalances or prior<0 or prior>self.original_prior[cutoff]:
            raise ValueError('Beta counter reset/reachable-state bound violated')
        row=self.by_cutoff[cutoff];old=deepcopy(row['beta_modifier_observation'])
        # Match original source operation order exactly, including clipped quality.
        severity=float(np.clip((old['stress_score']-old['neutral_stress_score'])/max(1-old['neutral_stress_score'],1e-6),0,1))
        severity*=old['research_data_quality_scale']
        cut=severity*control.maximum_additional_gross_reduction
        if abs(cut-row['beta_unconditional_proposed_cut'])>1e-14:
            raise ValueError('Cached exogenous Beta formula mismatch')
        raw=cut>max(control.minimum_additional_gross_change,0.0)+1e-15
        count=prior+1 if raw else 0
        if old.get('blockers'):
            if old['reason']!='beta_gate_blocked':raise ValueError('Unknown cached Beta blocker branch')
            result={**old,'triggered':False,'gross_modifier':1.,'persistence_rebalances':0}
        else:
            base={k:v for k,v in old.items() if k not in ['triggered','reason','gross_modifier','proposed_cut',
                'additional_gross_reduction','production_frozen','artifact_sha256','blockers','persistence_rebalances']}
            base['persistence_rebalances']=count
            if not raw:
                result={**base,'triggered':False,'reason':'research_stress_at_or_below_neutral','blockers':[],
                        'gross_modifier':1.,'proposed_cut':cut}
            elif count<control.persistence_rebalances:
                result={**base,'triggered':False,'reason':'research_persistence_not_met','blockers':[],
                        'gross_modifier':1.,'proposed_cut':cut}
            else:
                if not old.get('triggered') or 'production_frozen' not in old or 'artifact_sha256' not in old:
                    raise ValueError('Missing provenance for newly reached Beta trigger; no fallback')
                result={**base,'triggered':True,'reason':'research_candidate_continuous_beta_stress','blockers':[],
                        'gross_modifier':1-cut,'additional_gross_reduction':cut,
                        'production_frozen':old['production_frozen'],'artifact_sha256':old['artifact_sha256']}
        self.calls.append({'cutoff':cutoff,'previous_counter':prior,'original_counter_bound':self.original_prior[cutoff],
            'next_counter':result['persistence_rebalances'],'triggered':result['triggered'],
            'reason':result['reason'],'gross_modifier':result['gross_modifier']})
        return result

    def install(self):
        import backend.risk.v204 as module
        self._module=module;self._original=module.beta_regime_modifier_v204
        module.beta_regime_modifier_v204=self.beta

    def uninstall(self):
        self._module.beta_regime_modifier_v204=self._original
