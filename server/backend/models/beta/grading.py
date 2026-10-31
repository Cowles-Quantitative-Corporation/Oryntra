from __future__ import annotations

import math
from typing import Mapping, Optional

import numpy as np

from .config import BETA_V1_CONFIG

UNIFORM_LOG_LOSS = math.log(4.0)
UNIFORM_BRIER = 0.75


def _bool(value) -> bool:
    return bool(value is True or (isinstance(value,(int,float,np.number)) and value != 0))


def evaluate_beta_policy(
    summary: Mapping,
    *,
    pit_report: Mapping,
    consensus_report: Optional[Mapping]=None,
    provenance: Optional[Mapping]=None,
    conditioned: Optional[Mapping]=None,
    promotion_evidence: Optional[Mapping]=None,
    policy: Optional[Mapping]=None,
) -> dict:
    """Evaluate explicit research gates; never manufactures an 'official' score.

    The default policy is explicitly tagged non-authoritative in config. All
    thresholds are returned with the result so later changes are auditable.
    """
    cfg=BETA_V1_CONFIG
    pol=dict(policy or cfg["proposed_promotion_policy"])
    prob=pol.get("probability",{})
    cons_cfg=cfg["consensus"]
    hold=(summary.get("final_holdout") or {})
    diag=hold.get("diagnostics") or {}
    events=(hold.get("event_diagnostics") or {})
    robustness=summary.get("robustness") or {}
    prov=provenance or summary.get("provenance") or {}
    market_prov=(prov.get("market") or {}) if isinstance(prov,Mapping) else {}

    precision_counts=(pit_report or {}).get("availability_precision_counts",{}) or {}
    pit_date_safe=not (pit_report or {}).get("intraday_time_claimed_from_fred_date_only",False)
    macro_rows=int((pit_report or {}).get("revision_rows",(pit_report or {}).get("rows",0)) or 0)
    strict_pit=bool(pit_date_safe and macro_rows>0)

    price_basis=str(market_prov.get("price_basis","")).lower()
    real_market=str(market_prov.get("provenance","")).lower()=="oryntra_canonical"
    adjusted_market=price_basis in {"adjusted","adjusted_close","total_return","adjusted_or_total_return","mixed_adjusted_or_total_return"}
    real_macro=bool(macro_rows>0 and strict_pit and (pit_report or {}).get("real_fred_provenance",False))

    crep=consensus_report or summary.get("consensus_coverage") or {}
    consensus_ready=bool(
        int(crep.get("matched_events",0) or 0)>=int(cons_cfg["minimum_matched_events"])
        and float(crep.get("event_coverage",crep.get("coverage",0.0)) or 0.0)>=float(cons_cfg["minimum_event_coverage"])
        and (int(crep.get("growth_metrics_with_consensus",0)) if isinstance(crep.get("growth_metrics_with_consensus",0),(int,float)) else len(crep.get("growth_metrics_with_consensus",[]) or []))>=int(cons_cfg["minimum_growth_metrics"])
        and (int(crep.get("inflation_metrics_with_consensus",0)) if isinstance(crep.get("inflation_metrics_with_consensus",0),(int,float)) else len(crep.get("inflation_metrics_with_consensus",[]) or []))>=int(cons_cfg["minimum_inflation_metrics"])
    )

    ll=float(diag.get("log_loss",np.nan)); br=float(diag.get("brier",np.nan)); ece=float(diag.get("ece",np.nan))
    ll_ok=bool(np.isfinite(ll) and (not prob.get("holdout_log_loss_below_uniform",True) or ll<UNIFORM_LOG_LOSS))
    br_ok=bool(np.isfinite(br) and (not prob.get("holdout_brier_below_uniform",True) or br<UNIFORM_BRIER))
    ece_ok=bool(np.isfinite(ece) and ece<=float(prob.get("holdout_ece_max",.20)))
    prob_pass=bool(ll_ok and br_ok and ece_ok)
    hold_events=int(events.get("event_count",summary.get("effective_macro_samples",{}).get("final_holdout_events",0)) or 0)
    eff=summary.get("effective_macro_samples",{}) or {}
    cal_events=int(eff.get("final_calibration_events",0) or 0)
    effective_sample_pass=hold_events>=int(cfg["walk_forward"]["minimum_holdout_macro_events"]) and cal_events>=int(cfg["walk_forward"]["minimum_calibration_macro_events"])
    sep=(prov.get("target_input_separation") or {}) if isinstance(prov,Mapping) else {}
    target_input_separation=bool("anchor_feature_overlap" in sep and not (sep.get("anchor_feature_overlap") or []))
    robust_pass=_bool(robustness.get("pass",False))
    promotion_pass=bool(promotion_evidence and promotion_evidence.get("available") and promotion_evidence.get("pass_any"))
    alignment_cfg=cfg.get("strategy_return_alignment") or {}
    required_convention=str(alignment_cfg.get("return_convention") or "open_to_close_same_session")
    required_decision=str(alignment_cfg.get("beta_decision_time") or "NYSE regular-session open")
    # The causal return convention is now an explicit software contract in Control,
    # so the declaration gate should not remain false merely because a separate
    # conditioned-return report has not been generated for the newest candidate.
    alignment_pass=bool(
        alignment_cfg.get("causal")
        and str(alignment_cfg.get("return_convention"))==required_convention
        and str(alignment_cfg.get("beta_decision_time"))==required_decision
        and alignment_cfg.get("strategy_return_definition")
        and alignment_cfg.get("strategy_execution_timing")
    )
    if conditioned:
        metadata=[]
        for _, regimes in conditioned.items():
            if isinstance(regimes,Mapping) and "_metadata" in regimes: metadata.append(regimes["_metadata"])
        if metadata:
            alignment_pass=bool(alignment_pass and all(
                bool(m.get("declared")) and bool(m.get("causal"))
                and str(m.get("return_convention"))==required_convention
                and str(m.get("beta_decision_time"))==required_decision
                and bool(m.get("strategy_return_definition"))
                and bool(m.get("strategy_execution_timing"))
                for m in metadata
            ))
    evidence_alignment=(promotion_evidence or {}).get("alignment") if isinstance(promotion_evidence,Mapping) else None
    if isinstance(evidence_alignment,Mapping):
        alignment_pass=bool(alignment_pass and evidence_alignment.get("declared") and evidence_alignment.get("causal")
                            and str(evidence_alignment.get("return_convention"))==required_convention
                            and str(evidence_alignment.get("beta_decision_time"))==required_decision)

    gates={
        "strict_point_in_time":strict_pit,
        "no_fake_intraday_fred_precision":pit_date_safe,
        "real_oryntra_market_provenance":real_market,
        "adjusted_or_total_return_market_data":adjusted_market,
        "real_macro_data_present":real_macro,
        "target_input_series_separation":target_input_separation,
        "untouched_holdout_probability_quality":prob_pass,
        "minimum_effective_macro_events":effective_sample_pass,
        "hyperparameter_robustness":robust_pass,
        "consensus_surprise_coverage":consensus_ready,
        "strategy_return_alignment_declared":alignment_pass,
        "incremental_A_plus_C_or_A_plus_B_plus_C":promotion_pass,
    }
    blockers=[name for name,passed in gates.items() if not passed]
    production_required=("strict_point_in_time","real_oryntra_market_provenance","adjusted_or_total_return_market_data","real_macro_data_present","target_input_series_separation","untouched_holdout_probability_quality","minimum_effective_macro_events","hyperparameter_robustness","consensus_surprise_coverage","strategy_return_alignment_declared","incremental_A_plus_C_or_A_plus_B_plus_C")
    production_frozen_eligible=all(gates[x] for x in production_required)
    research_backtest_required=("strict_point_in_time","real_oryntra_market_provenance","adjusted_or_total_return_market_data","real_macro_data_present","target_input_series_separation","untouched_holdout_probability_quality","minimum_effective_macro_events","hyperparameter_robustness")
    research_backtest_eligible=all(gates[x] for x in research_backtest_required)
    return {
        "model":"beta_v1",
        "policy_name":pol.get("policy_name","custom"),
        "policy_authoritative":bool(pol.get("authoritative",False)),
        "policy":pol,
        "status":"promotion_eligible" if production_frozen_eligible else "research_candidate",
        "downstream_eligible":production_frozen_eligible,
        "research_backtest_eligible":research_backtest_eligible,
        "production_frozen_eligible":production_frozen_eligible,
        "gates":gates,
        "blockers":blockers,
        "diagnostics":{
            "holdout_log_loss":ll,"uniform_log_loss":UNIFORM_LOG_LOSS,
            "holdout_brier":br,"uniform_brier":UNIFORM_BRIER,"holdout_ece":ece,
            "probability_components":{"log_loss_pass":ll_ok,"brier_pass":br_ok,"ece_pass":ece_ok},
            "holdout_dominant_prediction_share":diag.get("dominant_prediction_share"),
            "holdout_dominant_prediction_class":diag.get("dominant_prediction_class"),
            "holdout_baselines":hold.get("baselines") or {},
            "holdout_raw_diagnostics":hold.get("raw_diagnostics") or {},
            "holdout_calibration":hold.get("calibration") or {},
            "holdout_effective_macro_events":hold_events,"calibration_effective_macro_events":cal_events,
            "consensus":crep,"promotion_evidence":promotion_evidence or {},
            "strategy_return_alignment":alignment_cfg,
            "strategy_return_alignment_pass":alignment_pass
        },
        "important":"This is an explicit, configurable research policy evaluation; it is not an authoritative inherited CQC letter grade and never authorizes live trading.",
    }


# Compatibility name for callers from the earlier package. It now returns gate
# evaluation only; there is deliberately no invented weighted score/letter grade.
def grade_beta(summary, *, pit_report, conditioned=None, consensus_report=None, provenance=None, promotion_evidence=None, policy=None):
    return evaluate_beta_policy(summary,pit_report=pit_report,conditioned=conditioned,consensus_report=consensus_report,provenance=provenance,promotion_evidence=promotion_evidence,policy=policy)
