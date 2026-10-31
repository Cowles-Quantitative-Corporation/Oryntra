"""Universal Risk V2.0.4 — Beta-aware downstream risk layer."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Optional
import numpy as np
from .state_time import validate_state_time

RISK_MODEL_V204_ID="universal_stock_risk_supervisor_v2_0_4"
_STATES=("growth_up_inflation_up","growth_up_inflation_down","growth_down_inflation_up","growth_down_inflation_down")

@dataclass(frozen=True)
class V204BetaControlConfig:
    beta_enabled: bool=True
    require_production_frozen: bool=True
    require_calibrated: bool=True
    maximum_state_age_sessions: int=5
    minimum_data_quality: float=.60
    maximum_uncertainty: float=.70
    growth_down_inflation_up_weight: float=1.0
    growth_down_inflation_down_weight: float=.75
    growth_up_inflation_up_weight: float=.20
    growth_up_inflation_down_weight: float=0.0
    minimum_stress_score: float=.55
    transition_weight: float=.15
    state_change_weight: float=.10
    persistence_rebalances: int=2
    maximum_additional_gross_reduction: float=.10
    minimum_additional_gross_change: float=.015
    maximum_incremental_turnover: float=.10
    disagreement_guard_enabled: bool=True
    standalone_beta_stress_threshold: float=.70
    research_candidate_mode: bool=False
    def __post_init__(self):
        if not all(isinstance(x,bool) for x in (self.beta_enabled,self.require_production_frozen,self.require_calibrated,self.disagreement_guard_enabled,self.research_candidate_mode)): raise ValueError("V2.0.4 switches must be boolean")
        if not (1<=self.maximum_state_age_sessions<=63): raise ValueError("invalid Beta state age")
        if not (0<=self.minimum_data_quality<=1 and 0<=self.maximum_uncertainty<=1): raise ValueError("invalid Beta quality/uncertainty")
        if not (1<=self.persistence_rebalances<=8): raise ValueError("invalid Beta persistence")
        if not (0<=self.maximum_additional_gross_reduction<=.35): raise ValueError("invalid Beta gross reduction")
        if not (0<=self.minimum_additional_gross_change<=.20 and 0<=self.maximum_incremental_turnover<=.50): raise ValueError("invalid Beta trade bounds")

def _map(x):
    if x is None:return {}
    if hasattr(x,"to_dict"):return x.to_dict()
    if isinstance(x,Mapping):return x
    raise ValueError("beta_state must be mapping/BetaState")

def _ord(x):
    if x is None:return None
    if isinstance(x,datetime):x=x.date()
    if isinstance(x,date):return x.toordinal()
    try:return date.fromisoformat(str(x)[:10]).toordinal()
    except Exception:return None

def beta_regime_modifier_v204(beta_state: Any, *, control: V204BetaControlConfig, previous_persistence_rebalances:int=0, current_session:Any=None, v203_predictive_triggered:bool=False)->dict[str,Any]:
    if not control.beta_enabled:return {"available":False,"triggered":False,"reason":"beta_disabled","gross_modifier":1.0,"persistence_rebalances":0}
    s=_map(beta_state)
    if not s:return {"available":False,"triggered":False,"reason":"beta_state_missing","gross_modifier":1.0,"persistence_rebalances":0}
    probs={k:float(s[f"p_{k}"]) for k in _STATES}
    v=np.asarray(list(probs.values()))
    if not np.isfinite(v).all() or (v<0).any() or abs(v.sum()-1)>1e-4:raise ValueError("invalid Beta probabilities")
    quality=float(s.get("data_quality",np.nan)); uncertainty=float(s.get("uncertainty",np.nan))
    blockers=[]
    # Interactive Research may consume an audited candidate before production freeze.
    # Production/paper/live keep the strict default.  In research-candidate mode
    # calibrated probabilities already encode confidence, so entropy/uncertainty is
    # reported but is not a second hard gate that can make every historical session
    # silently collapse to the V2.0.3 fallback.
    if control.require_production_frozen and not control.research_candidate_mode and not bool(s.get("production_frozen",False)):blockers.append("beta_not_production_frozen")
    if control.require_calibrated and not bool(s.get("calibrated",False)):blockers.append("beta_not_calibrated")
    if not control.research_candidate_mode:
        if not np.isfinite(quality) or quality<control.minimum_data_quality:blockers.append("beta_data_quality_too_low")
    if (not control.research_candidate_mode) and (not np.isfinite(uncertainty) or uncertainty>control.maximum_uncertainty):blockers.append("beta_uncertainty_too_high")
    age,time_blockers=validate_state_time(s.get("as_of_session"),current_session,prefix="beta",maximum_age_sessions=control.maximum_state_age_sessions)
    blockers.extend(time_blockers)
    transition=float(s.get("transition_probability") or 0); change=float(s.get("state_change_score") or 0)
    stress=(probs["growth_down_inflation_up"]*control.growth_down_inflation_up_weight+probs["growth_down_inflation_down"]*control.growth_down_inflation_down_weight+probs["growth_up_inflation_up"]*control.growth_up_inflation_up_weight)
    if stress>=control.minimum_stress_score:stress+=control.transition_weight*np.clip(transition,0,1)+control.state_change_weight*np.clip(change,0,1)
    threshold=max(control.minimum_stress_score,control.standalone_beta_stress_threshold if control.disagreement_guard_enabled and not v203_predictive_triggered else control.minimum_stress_score)

    if control.research_candidate_mode:
        # Research-only continuous sensitivity mode.  A perfectly uninformative
        # four-state posterior has a deterministic neutral stress score.  Research
        # only de-risks when calibrated Model C probabilities imply *more* macro
        # stress than that neutral posterior; it can never increase exposure and the
        # same V2.0.4 maximum gross reduction remains binding.  This avoids treating
        # calibrated high entropy as a production-style hard lock while preserving
        # the production contract unchanged.
        # A calibrated four-state model is not generally centered on a uniform
        # 25/25/25/25 posterior.  Use the candidate's own training class-prior
        # probabilities as the research neutral point when available.  Falling
        # back to uniform keeps old artifacts compatible, but avoids the 5.4.12
        # failure mode where a prior-skewed calibrated candidate could remain
        # below the uniform stress score on every historical session and therefore
        # behave exactly like benchmark buy-and-hold.
        neutral_probs=s.get("research_neutral_probabilities")
        neutral_source=str(s.get("research_neutral_source") or "uniform_four_state")
        try:
            nv=np.asarray(neutral_probs,dtype=float).reshape(-1) if neutral_probs is not None else np.asarray([],dtype=float)
        except Exception:
            nv=np.asarray([],dtype=float)
        if len(nv)==4 and np.isfinite(nv).all() and (nv>=0).all() and nv.sum()>1e-12:
            nv=nv/nv.sum()
            neutral=float(
                nv[2]*control.growth_down_inflation_up_weight
                +nv[3]*control.growth_down_inflation_down_weight
                +nv[0]*control.growth_up_inflation_up_weight
                +nv[1]*control.growth_up_inflation_down_weight
            )
        else:
            nv=np.full(4,0.25,dtype=float)
            neutral_source="uniform_four_state_fallback"
            neutral=(control.growth_down_inflation_up_weight+control.growth_down_inflation_down_weight+control.growth_up_inflation_up_weight+control.growth_up_inflation_down_weight)/4.0
        severity=float(np.clip((stress-neutral)/max(1-neutral,1e-6),0,1))
        # Data quality remains a strict production gate.  In interactive research
        # it is instead a conservative confidence scaler: sparse historical
        # sessions can contribute, but their Beta effect is attenuated toward zero
        # rather than hard-falling back to 100% exposure.  Missing quality => zero
        # research effect.
        research_quality=float(np.clip(quality,0,1)) if np.isfinite(quality) else 0.0
        severity*=research_quality
        cut=severity*control.maximum_additional_gross_reduction
        raw=cut>max(control.minimum_additional_gross_change,0.0)+1e-15
        persistence=previous_persistence_rebalances+1 if raw else 0
        base={"available":True,"stress_score":float(stress),"threshold":float(neutral),"strict_threshold":float(threshold),"neutral_stress_score":float(neutral),"neutral_stress_source":neutral_source,"neutral_probabilities":[float(x) for x in nv],"research_data_quality_scale":float(research_quality),"research_candidate_mode":True,"persistence_rebalances":int(persistence),"probabilities":probs,"state_age_calendar_days":age,"uncertainty":uncertainty}
        if blockers:return {**base,"triggered":False,"reason":"beta_gate_blocked","blockers":blockers,"gross_modifier":1.0,"persistence_rebalances":0}
        if not raw:return {**base,"triggered":False,"reason":"research_stress_at_or_below_neutral","blockers":[],"gross_modifier":1.0,"proposed_cut":float(cut)}
        if persistence<control.persistence_rebalances:return {**base,"triggered":False,"reason":"research_persistence_not_met","blockers":[],"gross_modifier":1.0,"proposed_cut":float(cut)}
        return {**base,"triggered":True,"reason":"research_candidate_continuous_beta_stress","blockers":[],"gross_modifier":1-cut,"additional_gross_reduction":cut,"production_frozen":bool(s.get("production_frozen",False)),"artifact_sha256":s.get("artifact_sha256")}

    raw=stress>=threshold; persistence=previous_persistence_rebalances+1 if raw else 0
    base={"available":True,"stress_score":float(stress),"threshold":float(threshold),"persistence_rebalances":int(persistence),"probabilities":probs,"state_age_calendar_days":age}
    if blockers:return {**base,"triggered":False,"reason":"beta_gate_blocked","blockers":blockers,"gross_modifier":1.0,"persistence_rebalances":0}
    if not raw or persistence<control.persistence_rebalances:return {**base,"triggered":False,"reason":"stress_below_threshold" if not raw else "persistence_not_met","blockers":[],"gross_modifier":1.0}
    severity=float(np.clip((stress-threshold)/max(1-threshold,1e-6),0,1)); cut=severity*control.maximum_additional_gross_reduction
    if cut<control.minimum_additional_gross_change:return {**base,"triggered":False,"reason":"beta_no_trade_band","blockers":[],"gross_modifier":1.0,"proposed_cut":cut}
    return {**base,"triggered":True,"reason":"persistent_beta_macro_stress","blockers":[],"gross_modifier":1-cut,"additional_gross_reduction":cut,"production_frozen":bool(s.get("production_frozen",False)),"artifact_sha256":s.get("artifact_sha256")}

def apply_beta_overlay_v204(v203_weights:np.ndarray,beta_modifier:Mapping[str,Any],*,current_weights:Optional[np.ndarray]=None,control:V204BetaControlConfig)->tuple[np.ndarray,dict[str,Any]]:
    base=np.maximum(np.asarray(v203_weights,float).reshape(-1),0)
    if not beta_modifier.get("triggered",False) or base.sum()<=1e-12:return base.copy(),{"selected":"v203_fallback","gross_before":float(base.sum()),"gross_after":float(base.sum()),"incremental_turnover":0.0,"modifier":dict(beta_modifier)}
    mod=float(np.clip(beta_modifier.get("gross_modifier",1),1-control.maximum_additional_gross_reduction,1)); cand=base*mod
    ref=base if current_weights is None else np.maximum(np.asarray(current_weights,float).reshape(-1),0)
    incr=max(0,float(np.abs(cand-ref).sum()-np.abs(base-ref).sum()))
    if incr>control.maximum_incremental_turnover+1e-12:return base.copy(),{"selected":"v203_fallback","reason":"beta_incremental_turnover_gate","gross_before":float(base.sum()),"gross_after":float(base.sum()),"incremental_turnover":incr,"modifier":dict(beta_modifier)}
    cand=np.maximum(np.minimum(cand,base),0)
    return cand,{"selected":"beta_scaled_v203","reason":"beta_macro_overlay_accepted","gross_before":float(base.sum()),"gross_after":float(cand.sum()),"incremental_turnover":incr,"modifier":dict(beta_modifier)}

def v204_contract(control:V204BetaControlConfig=V204BetaControlConfig())->dict[str,Any]:
    return {"id":RISK_MODEL_V204_ID,"version":"2.0.4-research","baseline":"V2.0.3 unchanged before Beta overlay","beta_direction":"Beta -> Risk only","beta_can_increase_risk":False,"fallback":"V2.0.3","controls":control.__dict__.copy(),"research_only":True}
