from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd

from .conditioned import regime_conditioned_research, strategy_return_alignment_contract
from .config import (
    BETA_V1_CONFIG,
    BETA_CANDIDATE_SCHEMA_VERSION,
    BETA_TRAINING_CODE_REVISION,
    BETA_CALIBRATION_CONTRACT,
)
from .consensus import ConsensusRepository, SurpriseBuilder
from .features import BetaFeatureEngine, FeatureBundle
from .promotion import incremental_portfolio_evidence
from .grading import evaluate_beta_policy
from .labels import INT_TO_REGIME, REGIME_NAMES, RegimeLabeler
from .ledger import ResearchLedger
from .repository import DEFAULT_SERIES_POLICIES, POLICY_BY_ID, MacroRepository
from .types import ArtifactStatus, BetaState
from .walkforward import FrozenBetaModel, WalkForwardResult, WalkForwardStudy


def _json_dump(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(payload,indent=2,sort_keys=True,default=str),encoding="utf-8")
    tmp.replace(path)



def _joblib_dump_atomic(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp")
    if tmp.exists():
        tmp.unlink()
    joblib.dump(obj,tmp)
    tmp.replace(path)


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


class BetaService:
    """Beta v1 research service with explicit candidate-vs-production artifacts."""

    def __init__(self, root: str|Path, *, fred_api_key: Optional[str]=None, strict_pit: bool=True, random_state: int=17, experiment_recorder=None, z_window: int=252, z_min_periods: int=60) -> None:
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True)
        self.random_state=int(random_state); self.experiment_recorder=experiment_recorder
        self.macro=MacroRepository(self.root/"beta.sqlite",fred_api_key=fred_api_key,strict=strict_pit)
        self.consensus=ConsensusRepository(self.root/"beta.sqlite")
        self.surprises=SurpriseBuilder(self.macro,self.consensus)
        self.features=BetaFeatureEngine(z_window=z_window,z_min_periods=z_min_periods); self.labeler=RegimeLabeler(); self.ledger=ResearchLedger(self.root/"research_ledger.jsonl")
        self._model: Optional[FrozenBetaModel]=None
        self._executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix="cqc-beta-sync")
        self._jobs={}; self._job_lock=threading.Lock()

    @staticmethod
    def _fingerprint_frame(frame: pd.DataFrame) -> str:
        d=hashlib.sha256(); d.update("|".join(map(str,frame.columns)).encode()); d.update(pd.util.hash_pandas_object(frame,index=True).to_numpy(dtype="uint64",copy=False).tobytes()); return d.hexdigest()

    @staticmethod
    def _file_sha256(path: Path) -> str:
        d=hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda:handle.read(1024*1024),b""):
                d.update(chunk)
        return d.hexdigest()

    def _record_control_experiment(self, **kwargs):
        if self.experiment_recorder is None: return None
        try: return self.experiment_recorder(**kwargs)
        except Exception as exc:
            self.ledger.append("beta_control_experiment_record_error",{"error":str(exc)}); return None

    @property
    def candidate_path(self): return self.root/"candidate_beta_v1.joblib"
    @property
    def candidate_meta_path(self): return self.root/"candidate_beta_v1.metadata.json"
    @property
    def frozen_path(self): return self.root/"frozen_beta_v1.joblib"
    @property
    def diagnostics_path(self): return self.root/"diagnostics.json"
    @property
    def oos_path(self): return self.root/"oos_states.csv"
    @property
    def grade_path(self): return self.root/"policy_evaluation.json"
    @property
    def conditioned_path(self): return self.root/"conditioned.json"
    @property
    def promotion_path(self): return self.root/"promotion_incremental.json"

    def _invalidate_candidate_dependent_outputs(self) -> None:
        # Conditioned/portfolio evidence is tied to one exact candidate artifact.
        # A retrain must never inherit promotion evidence from the prior candidate.
        for path in (self.conditioned_path,self.promotion_path,self.grade_path):
            _unlink_if_exists(path)

    def sync_macro(self, *, force_full: bool=False) -> dict:
        result=self.macro.sync_all(DEFAULT_SERIES_POLICIES,force_full=force_full); self.ledger.append("beta_macro_sync",result); return result

    def start_macro_sync(self, *, force_full: bool=False) -> dict:
        job_id=uuid.uuid4().hex
        with self._job_lock: self._jobs[job_id]={"id":job_id,"status":"queued","force_full":bool(force_full)}
        def work():
            with self._job_lock: self._jobs[job_id]["status"]="running"
            try:
                result=self.sync_macro(force_full=force_full)
                with self._job_lock: self._jobs[job_id].update(status="done",result=result)
            except Exception as exc:
                with self._job_lock: self._jobs[job_id].update(status="failed",error=f"{type(exc).__name__}: {exc}")
        self._executor.submit(work); return dict(self._jobs[job_id])

    def sync_job_status(self, job_id: str) -> dict:
        with self._job_lock:
            if job_id not in self._jobs: raise KeyError(job_id)
            return dict(self._jobs[job_id])

    def import_consensus_csv(self, csv_text_or_path) -> dict:
        result=self.consensus.import_csv(csv_text_or_path); self.ledger.append("beta_consensus_import",{"inserted":result["inserted"],"rejected_count":len(result["rejected"])}); return result

    def feature_bundle(self, market_prices: pd.DataFrame, *, stock_context: Optional[pd.DataFrame]=None) -> FeatureBundle:
        return self.features.build(self.macro,market_prices,surprise_builder=self.surprises,stock_context=stock_context)

    @staticmethod
    def _series_span(series: pd.Series) -> dict:
        valid=series.dropna()
        return {
            "non_null_sessions": int(len(valid)),
            "first_session": (pd.Timestamp(valid.index.min()).date().isoformat() if len(valid) else None),
            "last_session": (pd.Timestamp(valid.index.max()).date().isoformat() if len(valid) else None),
        }

    def training_readiness(
        self,
        market_prices: pd.DataFrame,
        *,
        stock_context: Optional[pd.DataFrame]=None,
        min_train_sessions: int=756,
        calibration_sessions: int=126,
        final_holdout_sessions: int=252,
    ) -> dict:
        """Preflight the exact feature/label panel used by the walk-forward study.

        Earlier 5.4 builds only reported that each macro series existed.  A failed
        or incremental sync could therefore look ``READY`` while the independent
        label anchors did not contain enough historical vintages.  This diagnostic
        measures usable labels on the actual market session index before fitting.
        """
        bundle=(self.feature_bundle(market_prices) if stock_context is None else self.feature_bundle(market_prices,stock_context=stock_context))
        labels=self.labeler.build(bundle.target_frame,event_mask=bundle.target_event_mask)
        y=labels.labels
        event_mask=labels.event_mask.reindex(bundle.frame.index).fillna(False).astype(bool)
        labeled=y.notna()
        total_events=int((event_mask & labeled).sum())
        wf=BETA_V1_CONFIG["walk_forward"]
        minimum_events=int(wf["minimum_train_macro_events"]+wf["minimum_calibration_macro_events"]+wf["minimum_holdout_macro_events"])
        required_panel=int(min_train_sessions)+int(calibration_sessions)+int(final_holdout_sessions)+1
        target_columns={c:self._series_span(bundle.target_frame[c]) for c in bundle.target_frame.columns}
        growth_count=labels.growth_signal_count
        inflation_count=labels.inflation_signal_count
        anchor_history={}
        for sid in BETA_V1_CONFIG["target_definition"]["growth_anchor_series"]+BETA_V1_CONFIG["target_definition"]["inflation_anchor_series"]:
            events=self.macro.revisions(sid)
            anchor_history[sid]={
                "revision_rows":int(len(events)),
                "observation_count":int(len({e.observation_date for e in events})),
                "first_observation":(min((e.observation_date for e in events),default=None).isoformat() if events else None),
                "last_observation":(max((e.observation_date for e in events),default=None).isoformat() if events else None),
                "first_eligible_session":(min((e.eligible_session for e in events),default=None).isoformat() if events else None),
                "last_eligible_session":(max((e.eligible_session for e in events),default=None).isoformat() if events else None),
            }
        market_attrs=dict(getattr(market_prices,"attrs",{}) or {})
        market_verified=(str(market_attrs.get("provenance","")).lower()=="oryntra_canonical" and str(market_attrs.get("price_basis","")).lower() in {"adjusted","adjusted_close","total_return","adjusted_or_total_return","mixed_adjusted_or_total_return"})
        diag={
            "market_sessions":int(len(bundle.frame)),
            "market_first_session":(pd.Timestamp(bundle.frame.index.min()).date().isoformat() if len(bundle.frame) else None),
            "market_last_session":(pd.Timestamp(bundle.frame.index.max()).date().isoformat() if len(bundle.frame) else None),
            "market_provenance_verified_adjusted":bool(market_verified),
            "labeled_sessions":int(labeled.sum()),
            "required_labeled_sessions":int(min_train_sessions),
            "label_coverage_pct":float(labeled.mean()*100.0) if len(labeled) else 0.0,
            "first_labeled_session":(pd.Timestamp(y[labeled].index.min()).date().isoformat() if labeled.any() else None),
            "last_labeled_session":(pd.Timestamp(y[labeled].index.max()).date().isoformat() if labeled.any() else None),
            "independent_macro_events":total_events,
            "minimum_independent_macro_events":minimum_events,
            "panel_sessions_required_for_train_calibration_holdout":required_panel,
            "growth_signal_sessions_ge_2":int((growth_count>=2).sum()),
            "inflation_signal_sessions_ge_2":int((inflation_count>=2).sum()),
            "target_columns":target_columns,
            "anchor_history":anchor_history,
            "pit":self.macro.integrity_report(),
        }
        blockers=[]
        if not market_verified:blockers.append("market_provenance_not_verified_adjusted")
        if len(bundle.frame)<required_panel:blockers.append("insufficient_total_session_history")
        if int(labeled.sum())<int(min_train_sessions):blockers.append("insufficient_labeled_session_history")
        if total_events<minimum_events:blockers.append("insufficient_independent_macro_events")
        diag["blockers"]=blockers
        diag["ready_for_walkforward"]=not blockers
        return diag

    def repair_target_history(self) -> dict:
        """Force a full PIT backfill of only the independent regime-label anchors."""
        ids=list(dict.fromkeys(BETA_V1_CONFIG["target_definition"]["growth_anchor_series"]+BETA_V1_CONFIG["target_definition"]["inflation_anchor_series"]))
        policies=[POLICY_BY_ID[sid] for sid in ids]
        result=self.macro.sync_all(policies,force_full=True)
        self.ledger.append("beta_target_history_repair",{"series":ids,"result":result})
        return result

    def repair_missing_required_history(self) -> dict:
        """Backfill required macro series absent from the persistent PIT ledger.

        This is primarily a migration repair for 5.4.1-5.4.5 databases. Those
        builds could mark macro integrity READY from the daily observation-dated
        series even though revisable ALFRED rows had never been inserted.
        """
        report=self.macro.integrity_report()
        ids=[sid for sid in report.get("missing_required_series",[]) if sid in POLICY_BY_ID]
        if not ids:
            return {"results":[],"errors":[],"series":[]}
        policies=[POLICY_BY_ID[sid] for sid in ids]
        result=self.macro.sync_all(policies,force_full=True)
        payload={"series":ids,"result":result}
        self.ledger.append("beta_missing_required_history_repair",payload)
        return {**result,"series":ids}

    @staticmethod
    def format_training_readiness_error(diag: dict) -> str:
        anchors=diag.get("anchor_history") or {}
        weak=[]
        for sid,row in anchors.items():
            if int(row.get("observation_count") or 0)<12:
                weak.append(f"{sid}:{int(row.get('observation_count') or 0)} obs")
        target_cols=diag.get("target_columns") or {}
        empty=[name for name,row in target_cols.items() if int(row.get("non_null_sessions") or 0)==0]
        bits=[
            "Beta training readiness failed",
            f"labeled sessions {int(diag.get('labeled_sessions') or 0)}/{int(diag.get('required_labeled_sessions') or 0)}",
            f"market sessions {int(diag.get('market_sessions') or 0)}/{int(diag.get('panel_sessions_required_for_train_calibration_holdout') or 0)} minimum panel",
            f"independent macro events {int(diag.get('independent_macro_events') or 0)}/{int(diag.get('minimum_independent_macro_events') or 0)}",
            f"growth-valid sessions {int(diag.get('growth_signal_sessions_ge_2') or 0)}",
            f"inflation-valid sessions {int(diag.get('inflation_signal_sessions_ge_2') or 0)}",
        ]
        if weak:bits.append("sparse anchors "+", ".join(weak))
        if empty:bits.append("empty target transforms "+", ".join(empty))
        bits.append("blockers: "+", ".join(diag.get("blockers") or ["unknown"]))
        return "; ".join(bits)

    def risk_free_daily(self, sessions: pd.DatetimeIndex) -> pd.Series:
        panel=self.macro.pit_transform_panel(pd.DatetimeIndex(sessions),policies=[p for p in DEFAULT_SERIES_POLICIES if p.series_id=="DGS3MO"])
        level=panel.get("DGS3MO",pd.DataFrame(index=sessions)).get("level",pd.Series(index=sessions,dtype=float)).reindex(sessions).ffill()
        return ((1.0+level.astype(float)/100.0)**(1.0/252.0)-1.0).rename("risk_free_daily")

    def _states_from_result(self, result: WalkForwardResult, bundle: FeatureBundle, labels) -> pd.DataFrame:
        # Development evidence (event rows) + final holdout daily probabilities.
        dev=result.development_oos.copy(); hold=result.final_holdout.copy()
        states=pd.concat([dev,hold]).sort_index(); states=states[~states.index.duplicated(keep="last")]
        pcols=[f"p_{r}" for r in REGIME_NAMES]; arr=states[pcols].to_numpy()
        if "dominant_regime" not in states: states["dominant_regime"]=[INT_TO_REGIME[int(i)] for i in arr.argmax(axis=1)]
        sc=np.zeros(len(states));
        if len(states)>1: sc[1:]=0.5*np.abs(np.diff(arr,axis=0)).sum(axis=1)
        states["state_change_score"]=sc
        # HMM transition is only directly available for development winner events;
        # final holdout daily transition is reproduced by frozen predict below.
        win=result.candidates[result.winner_name]
        states["transition_probability"]=win.transition_probability.reindex(states.index)
        q=np.clip(arr,1e-12,1.0); states["uncertainty"]=-np.sum(q*np.log(q),axis=1)/np.log(4.0)
        states["growth_score"]=labels.growth_score.reindex(states.index); states["inflation_score"]=labels.inflation_score.reindex(states.index)
        states["economic_label"]=labels.labels.reindex(states.index); states["effective_macro_event"]=labels.event_mask.reindex(states.index).fillna(False)
        states["data_quality"]=bundle.data_quality.reindex(states.index); states["candidate"]=result.winner_name; states["candidate_family"]=result.winner_family; states["calibrated"]=True
        # Recompute holdout through frozen model so HMM transitions are present and calibration path exactly matches artifact.
        forward=bundle.frame.loc[bundle.frame.index>result.frozen_model.training_end]
        pred=result.frozen_model.predict(forward)
        overlap=states.index.intersection(pred.index)
        for col in ("transition_probability","state_change_score","uncertainty"):
            states.loc[overlap,col]=pred.loc[overlap,col]
        return states

    def run_study(self, market_prices: pd.DataFrame, *, stock_context: Optional[pd.DataFrame]=None, min_train_sessions=756, calibration_sessions=126, test_sessions=126, final_holdout_sessions=252) -> dict:
        bundle=(self.feature_bundle(market_prices) if stock_context is None else self.feature_bundle(market_prices,stock_context=stock_context))
        labels=self.labeler.build(bundle.target_frame,event_mask=bundle.target_event_mask)
        readiness=self.training_readiness(market_prices,stock_context=stock_context,min_train_sessions=min_train_sessions,calibration_sessions=calibration_sessions,final_holdout_sessions=final_holdout_sessions)
        if not readiness.get("ready_for_walkforward"):
            raise ValueError(self.format_training_readiness_error(readiness))
        study=WalkForwardStudy(min_train_sessions=min_train_sessions,calibration_sessions=calibration_sessions,test_sessions=test_sessions,final_holdout_sessions=final_holdout_sessions,random_state=self.random_state)
        self.ledger.append("beta_walk_forward_start",{
            "training_code_revision":BETA_TRAINING_CODE_REVISION,
            "candidate_schema_version":BETA_CANDIDATE_SCHEMA_VERSION,
            "calibration_contract":BETA_CALIBRATION_CONTRACT,
            "market_sessions":int(len(bundle.frame)),
            "final_holdout_sessions":int(final_holdout_sessions),
        })
        try:
            result=study.run(bundle.frame,labels.labels,bundle.groups,event_mask=labels.event_mask)
        except Exception as exc:
            self.ledger.append("beta_walk_forward_failed",{
                "training_code_revision":BETA_TRAINING_CODE_REVISION,
                "candidate_schema_version":BETA_CANDIDATE_SCHEMA_VERSION,
                "error":f"{type(exc).__name__}: {exc}",
            })
            raise
        artifact_id=uuid.uuid4().hex
        result.frozen_model.artifact_id=artifact_id
        result.frozen_model.candidate_schema_version=BETA_CANDIDATE_SCHEMA_VERSION
        result.frozen_model.training_code_revision=BETA_TRAINING_CODE_REVISION
        result.frozen_model.calibration_contract=BETA_CALIBRATION_CONTRACT
        states=self._states_from_result(result,bundle,labels); summary=result.summary()
        consensus_report=self.surprises.coverage_report(metrics=BETA_V1_CONFIG['consensus']['required_candidate_metrics'])
        summary.update({"feature_count":len(bundle.frame.columns),"target_label_session_coverage":float(labels.labels.notna().mean()),"training_readiness":readiness,"consensus_estimates":self.consensus.count(),"consensus_coverage":consensus_report,"pit":self.macro.integrity_report(),"provenance":bundle.provenance,"config":BETA_V1_CONFIG})
        summary["runtime"]={
            "training_code_revision":BETA_TRAINING_CODE_REVISION,
            "candidate_schema_version":BETA_CANDIDATE_SCHEMA_VERSION,
            "calibration_contract":BETA_CALIBRATION_CONTRACT,
        }
        # Candidate-dependent promotion evidence belongs to the previous artifact and
        # must not leak into a fresh research candidate.
        self._invalidate_candidate_dependent_outputs()
        # Candidate artifact is intentionally NOT the production frozen model.
        _joblib_dump_atomic(result.frozen_model,self.candidate_path)
        candidate_sha=self._file_sha256(self.candidate_path)
        summary["artifact"].update({"sha256":candidate_sha,"bytes":int(self.candidate_path.stat().st_size)})
        _json_dump(self.candidate_meta_path,summary["artifact"])
        states_tmp=self.oos_path.with_suffix(self.oos_path.suffix+".tmp")
        states.to_csv(states_tmp,index_label="session"); states_tmp.replace(self.oos_path)
        _json_dump(self.diagnostics_path,summary); self._model=result.frozen_model
        policy=evaluate_beta_policy(summary,pit_report=self.macro.integrity_report(),consensus_report=consensus_report,provenance=bundle.provenance)
        _json_dump(self.grade_path,policy)
        self.ledger.append("beta_walk_forward_study",{
            "winner":result.winner_name,"winner_family":result.winner_family,
            "candidate_artifact":str(self.candidate_path),"artifact_id":artifact_id,
            "candidate_schema_version":BETA_CANDIDATE_SCHEMA_VERSION,
            "training_code_revision":BETA_TRAINING_CODE_REVISION,
            "calibration":summary.get("final_holdout",{}).get("calibration",{}),
            "production_frozen":False,"holdout_metrics":result.frozen_model.holdout_metrics,
            "effective_samples":result.event_diagnostics,
        })
        self._record_control_experiment(experiment_type="beta_regime",status="done",config={"code_version":BETA_TRAINING_CODE_REVISION,"candidate_schema_version":BETA_CANDIDATE_SCHEMA_VERSION,"winner":result.winner_name,"final_holdout_sessions":int(final_holdout_sessions)},dataset_fingerprint=self._fingerprint_frame(bundle.frame),dataset_start=str(bundle.frame.index.min().date()),dataset_end=str(bundle.frame.index.max().date()),symbols=list(BetaFeatureEngine.MARKET_TICKERS),sample_count=int(result.event_diagnostics["final_holdout_events"]),artifact_path=str(self.candidate_path),metrics={"winner":result.winner_name,"holdout":result.frozen_model.holdout_metrics,"robustness":result.robustness},notes="Beta v1 candidate: development walk-forward selection plus untouched final holdout; event-session scoring; rank-preserving temperature calibration.")
        return {"summary":summary,"winner":result.winner_name,"winner_family":result.winner_family,"final_holdout":summary.get("final_holdout"),"policy_evaluation":policy,"artifact_status":self.artifact_status().to_dict(),"latest_oos_state":states.iloc[-1].to_dict(),"artifact_id":artifact_id,"training_code_revision":BETA_TRAINING_CODE_REVISION,"candidate_schema_version":BETA_CANDIDATE_SCHEMA_VERSION}

    def load_model(self, *, production_only: bool=False) -> FrozenBetaModel:
        if production_only:
            if not self.frozen_path.exists() or not self.artifact_status().production_frozen:
                raise RuntimeError("No currently policy-valid production-frozen Beta v1 artifact exists")
            return joblib.load(self.frozen_path)
        if self._model is not None: return self._model
        path=self.frozen_path if self.frozen_path.exists() and self.artifact_status().production_frozen else self.candidate_path
        if not path.exists(): raise RuntimeError("Beta v1 has not been trained; run the study first")
        self._model=joblib.load(path); return self._model

    # Backward compatibility; now accurately returns candidate if production not promoted.
    def load_frozen(self): return self.load_model(production_only=False)

    def oos_states(self) -> pd.DataFrame:
        if not self.oos_path.exists(): return pd.DataFrame()
        return pd.read_csv(self.oos_path,parse_dates=["session"]).set_index("session")

    def latest_state(self, market_prices: pd.DataFrame, *, stock_context: Optional[pd.DataFrame]=None) -> BetaState:
        model=self.load_model(); bundle=(self.feature_bundle(market_prices) if stock_context is None else self.feature_bundle(market_prices,stock_context=stock_context)); labels=self.labeler.build(bundle.target_frame,event_mask=bundle.target_event_mask); latest=pd.Timestamp(bundle.frame.index[-1]); hist=self.oos_states()
        if latest<=model.holdout_end and not hist.empty:
            available=hist.loc[hist.index<=latest]
            if available.empty: raise RuntimeError("No OOS Beta state available")
            row=available.iloc[-1]; asof=pd.Timestamp(available.index[-1])
        else:
            # Include calibration/holdout history after training_end so HMM filtering
            # reaches the current session through the same chronological state path.
            forward=bundle.frame.loc[bundle.frame.index>model.training_end]
            if forward.empty: raise RuntimeError("No features after candidate training end")
            pred=model.predict(forward); pred["growth_score"]=labels.growth_score.reindex(pred.index); pred["inflation_score"]=labels.inflation_score.reindex(pred.index); pred["data_quality"]=bundle.data_quality.reindex(pred.index)
            row=pred.iloc[-1]; asof=pd.Timestamp(pred.index[-1])
        return BetaState(as_of_session=asof.date(),growth_score=float(row.get("growth_score",np.nan)),inflation_score=float(row.get("inflation_score",np.nan)),p_growth_up_inflation_up=float(row["p_growth_up_inflation_up"]),p_growth_up_inflation_down=float(row["p_growth_up_inflation_down"]),p_growth_down_inflation_up=float(row["p_growth_down_inflation_up"]),p_growth_down_inflation_down=float(row["p_growth_down_inflation_down"]),uncertainty=float(row["uncertainty"]),state_change_score=float(row["state_change_score"]),transition_probability=float(row["transition_probability"]) if pd.notna(row.get("transition_probability")) else None,dominant_regime=str(row["dominant_regime"]),candidate=str(row.get("candidate",model.winner_name)),calibrated=bool(row.get("calibrated",True)),data_quality=float(row.get("data_quality",np.nan)),feature_count=len(bundle.frame.columns))

    def run_conditioned_research(self, strategy_returns: pd.DataFrame, *, return_convention: str, risk_free_daily: Optional[pd.Series]=None, turnover: Optional[pd.DataFrame]=None, cost_bps_per_turnover=0.0, bootstrap_reps=500, study_metadata: Optional[dict]=None) -> dict:
        status=self.artifact_status()
        if not status.trained or not status.candidate_diagnostics_match:
            raise RuntimeError("Current Beta candidate artifact and diagnostics are not a matching current schema-2 training run; retrain Beta before conditioned strategy research")
        alignment=strategy_return_alignment_contract(return_convention)
        if not alignment.get("declared"):
            raise ValueError(f"Beta incremental research requires causal return convention {alignment.get('required_return_convention')}")
        states=self.oos_states(); model=self.load_model()
        if states.empty: raise RuntimeError("Run Beta study before conditioned strategy research")
        rf=risk_free_daily if risk_free_daily is not None else self.risk_free_daily(pd.DatetimeIndex(strategy_returns.index))
        conditioned=regime_conditioned_research(strategy_returns,states,return_convention=return_convention,risk_free_daily=rf,turnover=turnover,cost_bps_per_turnover=cost_bps_per_turnover,bootstrap_reps=bootstrap_reps,seed=self.random_state)
        promotion=incremental_portfolio_evidence(strategy_returns,states,holdout_start=model.holdout_start,return_convention=return_convention,risk_free_daily=rf,policy=BETA_V1_CONFIG["proposed_promotion_policy"],seed=self.random_state)
        artifact_id=str(getattr(model,"artifact_id","") or "")
        conditioned["_artifact"]={"artifact_id":artifact_id,"training_code_revision":BETA_TRAINING_CODE_REVISION,"candidate_schema_version":BETA_CANDIDATE_SCHEMA_VERSION}
        conditioned["_study"]={"alignment":alignment,**(study_metadata or {})}
        promotion["artifact_id"]=artifact_id; promotion["training_code_revision"]=BETA_TRAINING_CODE_REVISION; promotion["candidate_schema_version"]=BETA_CANDIDATE_SCHEMA_VERSION
        promotion["study_metadata"]={"alignment":alignment,**(study_metadata or {})}
        _json_dump(self.conditioned_path,conditioned); _json_dump(self.promotion_path,promotion)
        summary=self.diagnostics(); consensus_report=self.surprises.coverage_report(metrics=BETA_V1_CONFIG['consensus']['required_candidate_metrics']); policy=evaluate_beta_policy(summary,pit_report=self.macro.integrity_report(),consensus_report=consensus_report,provenance=summary.get("provenance"),conditioned=conditioned,promotion_evidence=promotion)
        _json_dump(self.grade_path,policy)
        promoted=False
        if policy["production_frozen_eligible"]:
            # Only the exact already-tested candidate is copied; no refit after holdout.
            shutil.copy2(self.candidate_path,self.frozen_path); promoted=True
            self.ledger.append("beta_v1_production_frozen",{"artifact":str(self.frozen_path),"artifact_id":artifact_id,"candidate_sha256":self._file_sha256(self.candidate_path),"holdout_start":model.holdout_start.isoformat(),"policy":policy["policy_name"],"training_code_revision":BETA_TRAINING_CODE_REVISION})
        self.ledger.append("beta_conditioned_strategy_research",{"strategies":[k for k in conditioned.keys() if not str(k).startswith("_")],"return_convention":return_convention,"alignment":alignment,"promotion_pass_any":promotion.get("pass_any",False),"production_promoted":promoted,"study_metadata":study_metadata or {}})
        return {"conditioned":conditioned,"promotion_incremental":promotion,"policy_evaluation":policy,"artifact_status":self.artifact_status().to_dict()}

    def artifact_status(self) -> ArtifactStatus:
        summary=json.loads(self.diagnostics_path.read_text()) if self.diagnostics_path.exists() else {}
        pit=self.macro.integrity_report(); crep=self.surprises.coverage_report(metrics=BETA_V1_CONFIG['consensus']['required_candidate_metrics'])
        prov=(summary.get("provenance") or {}).get("market",{}) if isinstance(summary,dict) else {}
        real_market=str(prov.get("provenance","")).lower()=="oryntra_canonical" and str(prov.get("price_basis","")).lower() in {"adjusted","adjusted_close","total_return","adjusted_or_total_return","mixed_adjusted_or_total_return"}
        real_macro=bool(pit.get("pit_integrity") and pit.get("real_fred_provenance"))
        cons=bool(int(crep.get("matched_events",0))>=BETA_V1_CONFIG["consensus"]["minimum_matched_events"] and float(crep.get("event_coverage",0))>=BETA_V1_CONFIG["consensus"]["minimum_event_coverage"] and int(crep.get("growth_metrics_with_consensus",0))>=BETA_V1_CONFIG["consensus"]["minimum_growth_metrics"] and int(crep.get("inflation_metrics_with_consensus",0))>=BETA_V1_CONFIG["consensus"]["minimum_inflation_metrics"])
        holdout=bool((summary.get("final_holdout") or {}).get("diagnostics")) if isinstance(summary,dict) else False

        # Candidate identity is explicit in 5.4.8. The sidecar, diagnostics and
        # binary artifact must describe the same exact training run; otherwise the
        # system fails closed and asks for a retrain rather than mixing stale files.
        diag_art=(summary.get("artifact") or {}) if isinstance(summary,dict) else {}
        meta={}
        if self.candidate_meta_path.exists():
            try: meta=json.loads(self.candidate_meta_path.read_text())
            except Exception: meta={}
        candidate_exists=self.candidate_path.exists()
        diag_id=str(diag_art.get("artifact_id") or "")
        meta_id=str(meta.get("artifact_id") or "")
        diag_schema=int(diag_art.get("candidate_schema_version",0) or 0)
        meta_schema=int(meta.get("candidate_schema_version",0) or 0)
        diag_revision=str(diag_art.get("training_code_revision") or "")
        meta_revision=str(meta.get("training_code_revision") or "")
        diag_contract=str(diag_art.get("calibration_contract") or "")
        meta_contract=str(meta.get("calibration_contract") or "")
        schema=int(meta_schema or diag_schema)
        revision=str(meta_revision or diag_revision)
        contract=str(meta_contract or diag_contract)
        candidate_sha=str(meta.get("sha256") or diag_art.get("sha256") or "")
        actual_sha=""
        candidate_readable=False
        model_id=""; model_schema=0; model_revision=""; model_contract=""
        if candidate_exists:
            try:
                actual_sha=self._file_sha256(self.candidate_path)
                model=joblib.load(self.candidate_path)
                candidate_readable=True
                model_id=str(getattr(model,"artifact_id","") or "")
                model_schema=int(getattr(model,"candidate_schema_version",0) or 0)
                model_revision=str(getattr(model,"training_code_revision","") or "")
                model_contract=str(getattr(model,"calibration_contract","") or "")
                # Prefer the binary's own identity; the sidecar/diagnostics must agree.
                if model_id: meta_id=model_id if not meta_id else meta_id
                if model_schema: schema=model_schema
                if model_revision: revision=model_revision
                if model_contract: contract=model_contract
            except Exception:
                candidate_readable=False

        candidate_diagnostics_match=bool(
            candidate_exists and candidate_readable and diag_id and meta_id and model_id
            and diag_id==meta_id==model_id
            and model_schema>=BETA_CANDIDATE_SCHEMA_VERSION
            and meta_schema>=BETA_CANDIDATE_SCHEMA_VERSION
            and diag_schema>=BETA_CANDIDATE_SCHEMA_VERSION
            and model_revision==BETA_TRAINING_CODE_REVISION
            and meta_revision==BETA_TRAINING_CODE_REVISION
            and diag_revision==BETA_TRAINING_CODE_REVISION
            and model_contract==BETA_CALIBRATION_CONTRACT
            and meta_contract==BETA_CALIBRATION_CONTRACT
            and diag_contract==BETA_CALIBRATION_CONTRACT
            and candidate_sha and actual_sha and candidate_sha==actual_sha
            and candidate_sha==str(diag_art.get("sha256") or "")
        )
        calibration_present=bool((summary.get("final_holdout") or {}).get("calibration")) if isinstance(summary,dict) else False
        current_candidate=bool(candidate_diagnostics_match and calibration_present)
        legacy_candidate=bool(candidate_exists and not current_candidate)

        promotion_evidence=json.loads(self.promotion_path.read_text()) if self.promotion_path.exists() else None
        if promotion_evidence and str(promotion_evidence.get("artifact_id") or "")!=str(model_id or ""):
            promotion_evidence=None
        promotion=bool(promotion_evidence and promotion_evidence.get("pass_any"))
        conditioned=json.loads(self.conditioned_path.read_text()) if self.conditioned_path.exists() else None
        if conditioned:
            c_art=(conditioned.get("_artifact") or {}) if isinstance(conditioned,dict) else {}
            if str(c_art.get("artifact_id") or "")!=str(model_id or ""):
                conditioned=None
        policy=(evaluate_beta_policy(
            summary,pit_report=pit,consensus_report=crep,provenance=summary.get("provenance"),
            conditioned=conditioned,promotion_evidence=promotion_evidence,
        ) if candidate_exists and holdout else {"gates":{},"blockers":[]})
        gates=policy.get("gates") or {}
        probability_quality=bool(gates.get("untouched_holdout_probability_quality",False) and current_candidate)
        robustness_ready=bool(gates.get("hyperparameter_robustness",False) and current_candidate)
        frozen_file=bool(self.frozen_path.exists())
        frozen_matches_candidate=False
        if frozen_file and actual_sha:
            try: frozen_matches_candidate=(self._file_sha256(self.frozen_path)==actual_sha)
            except Exception: frozen_matches_candidate=False
        frozen_valid=bool(frozen_file and frozen_matches_candidate and current_candidate and policy.get("production_frozen_eligible",False))
        blockers=[]
        for ok,name in [(candidate_exists,"not_trained"),(real_market,"no_real_adjusted_oryntra_market_data"),(real_macro,"no_real_pit_macro_data"),(holdout,"final_holdout_not_complete")]:
            if not ok: blockers.append(name)
        if candidate_exists and not candidate_readable:
            blockers.append("candidate_artifact_unreadable_retrain_required")
        if candidate_exists and candidate_readable and not candidate_diagnostics_match:
            blockers.append("candidate_diagnostics_mismatch_retrain_required")
        if candidate_exists and holdout:
            gate_to_blocker={
                "target_input_series_separation":"target_input_separation_failed",
                "untouched_holdout_probability_quality":"probability_holdout_gate_failed",
                "minimum_effective_macro_events":"minimum_effective_macro_events_failed",
                "hyperparameter_robustness":"hyperparameter_robustness_failed",
                "consensus_surprise_coverage":"consensus_surprise_coverage_incomplete",
                "strategy_return_alignment_declared":"strategy_return_alignment_not_declared",
                "incremental_A_plus_C_or_A_plus_B_plus_C":"incremental_portfolio_gate_not_passed",
            }
            for gate, blocker in gate_to_blocker.items():
                if gate in gates and not gates[gate] and blocker not in blockers:
                    blockers.append(blocker)
        elif not cons and "consensus_surprise_coverage_incomplete" not in blockers:
            blockers.append("consensus_surprise_coverage_incomplete")
        if legacy_candidate and "legacy_beta_candidate_retrain_required" not in blockers:
            blockers.append("legacy_beta_candidate_retrain_required")
            probability_quality=False
        if frozen_file and not frozen_valid and "production_frozen_artifact_policy_invalid" not in blockers:
            blockers.append("production_frozen_artifact_policy_invalid")
        return ArtifactStatus(
            code_complete=True,trained=bool(candidate_exists and candidate_readable),production_frozen=frozen_valid,
            real_market_data=real_market,real_macro_data=real_macro,consensus_promotion_ready=cons,
            holdout_complete=holdout,probability_quality_ready=probability_quality,
            robustness_ready=robustness_ready,promotion_gate_complete=promotion,blockers=tuple(dict.fromkeys(blockers)),
            candidate_schema_version=int(schema),training_code_revision=revision,
            candidate_artifact_id=str(model_id or meta_id),diagnostics_artifact_id=diag_id,
            candidate_diagnostics_match=bool(candidate_diagnostics_match),
        )

    def diagnostics(self) -> dict:
        if not self.diagnostics_path.exists(): return {"status":"not_run","pit":self.macro.integrity_report(),"consensus_estimates":self.consensus.count(),"consensus_coverage":self.surprises.coverage_report(metrics=BETA_V1_CONFIG['consensus']['required_candidate_metrics']),"artifact_status":self.artifact_status().to_dict() if self.candidate_path.exists() else None,"ledger":self.ledger.verify()}
        data=json.loads(self.diagnostics_path.read_text()); data["ledger"]=self.ledger.verify(); return data

    def grade(self) -> dict:
        if not self.diagnostics_path.exists():
            return {"model":"beta_v1","status":"not_evaluated","downstream_eligible":False,"production_frozen_eligible":False,"blockers":["walk_forward_study_not_run"],"important":"Research status only; no invented official score."}
        summary=json.loads(self.diagnostics_path.read_text())
        artifact_id=str((summary.get("artifact") or {}).get("artifact_id") or "")
        conditioned=json.loads(self.conditioned_path.read_text()) if self.conditioned_path.exists() else None
        if conditioned:
            c_art=(conditioned.get("_artifact") or {}) if isinstance(conditioned,dict) else {}
            if str(c_art.get("artifact_id") or "")!=artifact_id: conditioned=None
        promotion=json.loads(self.promotion_path.read_text()) if self.promotion_path.exists() else None
        if promotion and str(promotion.get("artifact_id") or "")!=artifact_id: promotion=None
        policy=evaluate_beta_policy(
            summary,pit_report=self.macro.integrity_report(),
            consensus_report=self.surprises.coverage_report(metrics=BETA_V1_CONFIG['consensus']['required_candidate_metrics']),
            provenance=summary.get("provenance"),conditioned=conditioned,promotion_evidence=promotion,
        )
        _json_dump(self.grade_path,policy)
        return policy
