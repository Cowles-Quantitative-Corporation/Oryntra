from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from .calibration import TemporalMulticlassCalibrator, multiclass_diagnostics
from .candidates import GMMCandidate, GaussianHMMCandidate, PCABlockCandidate
from .candidates.base import BaseRegimeCandidate
from .labels import INT_TO_REGIME, REGIME_NAMES
from .config import (
    BETA_V1_CONFIG,
    BETA_CANDIDATE_SCHEMA_VERSION,
    BETA_TRAINING_CODE_REVISION,
    BETA_CALIBRATION_CONTRACT,
)

CandidateFactory = Callable[[], BaseRegimeCandidate]


def default_candidate_factories(random_state: int = 17) -> Dict[str, CandidateFactory]:
    """Pre-registered neighboring variants for robustness, not post-hoc tuning."""
    return {
        "pca_c0.5": lambda: PCABlockCandidate(random_state=random_state, C=0.5),
        "pca_c1.0": lambda: PCABlockCandidate(random_state=random_state, C=1.0),
        "pca_c2.0": lambda: PCABlockCandidate(random_state=random_state, C=2.0),
        # Neighboring higher-capacity variants remain deliberately small: two
        # components per economic block and balanced event classes. They are
        # preregistered before the untouched holdout and therefore can compete
        # without post-hoc holdout tuning.
        "pca2_bal_c0.5": lambda: PCABlockCandidate(random_state=random_state, C=0.5, n_components=2, class_weight="balanced"),
        "pca2_bal_c1.0": lambda: PCABlockCandidate(random_state=random_state, C=1.0, n_components=2, class_weight="balanced"),
        "pca2_bal_c2.0": lambda: PCABlockCandidate(random_state=random_state, C=2.0, n_components=2, class_weight="balanced"),
        "gmm_pc6": lambda: GMMCandidate(random_state=random_state, max_components=6, covariance_type="diag"),
        "gmm_pc10": lambda: GMMCandidate(random_state=random_state, max_components=10, covariance_type="diag"),
        "gmm_pc14": lambda: GMMCandidate(random_state=random_state, max_components=14, covariance_type="diag"),
        "hmm_pc6": lambda: GaussianHMMCandidate(random_state=random_state, max_components=6, max_iter=45),
        "hmm_pc10": lambda: GaussianHMMCandidate(random_state=random_state, max_components=10, max_iter=45),
        "hmm_pc14": lambda: GaussianHMMCandidate(random_state=random_state, max_components=14, max_iter=45),
    }


def _family(name: str) -> str:
    if name.startswith(("pca_", "pca2_")):
        return "pca_block"
    if name.startswith("gmm_"):
        return "gmm"
    if name.startswith("hmm_"):
        return "hmm"
    return name


def _state_stability(probabilities: pd.DataFrame) -> dict:
    if probabilities.empty:
        return {"switch_rate": np.nan, "median_run_length": np.nan, "mean_state_change": np.nan}
    pcols = [f"p_{x}" for x in REGIME_NAMES]
    dom = probabilities[pcols].to_numpy().argmax(axis=1)
    if len(dom) <= 1:
        switch_rate, runs = 0.0, [len(dom)]
    else:
        switches = dom[1:] != dom[:-1]
        switch_rate = float(np.mean(switches))
        runs, start = [], 0
        for i, sw in enumerate(switches, start=1):
            if sw:
                runs.append(i - start); start = i
        runs.append(len(dom) - start)
    arr = probabilities[pcols].to_numpy()
    tv = 0.5 * np.abs(np.diff(arr, axis=0)).sum(axis=1) if len(arr) > 1 else np.array([0.0])
    return {"switch_rate": switch_rate, "median_run_length": float(np.median(runs)), "mean_state_change": float(np.mean(tv))}


def _event_spells(y: pd.Series) -> dict:
    y = y.dropna().astype(int)
    if y.empty:
        return {"event_count": 0, "class_counts": {}, "median_days_between_events": np.nan}
    gaps = np.diff(pd.DatetimeIndex(y.index).values).astype("timedelta64[D]").astype(float) if len(y) > 1 else np.array([])
    return {
        "event_count": int(len(y)),
        "class_counts": {str(int(k)): int(v) for k, v in y.value_counts().sort_index().items()},
        "median_days_between_events": float(np.median(gaps)) if len(gaps) else np.nan,
    }


def _baseline_diagnostics(y: pd.Series, train_y: pd.Series) -> dict:
    """Holdout baselines fixed without using holdout labels to set probabilities."""
    yy = y.dropna().astype(int)
    train = train_y.dropna().astype(int)
    if yy.empty:
        return {}
    n = len(yy)
    uniform = np.full((n, 4), 0.25, dtype=float)
    counts = np.bincount(train.to_numpy(), minlength=4).astype(float)
    # Laplace smoothing keeps log-loss finite if a historical class is absent.
    prior = (counts + 1.0) / (counts.sum() + 4.0)
    prior_p = np.tile(prior, (n, 1))
    majority = int(np.argmax(counts)) if counts.sum() else 0
    majority_p = np.full((n, 4), 1e-6, dtype=float)
    majority_p[:, majority] = 1.0 - 3e-6
    return {
        "uniform": multiclass_diagnostics(yy.to_numpy(), uniform).to_dict(),
        "train_class_prior": {
            **multiclass_diagnostics(yy.to_numpy(), prior_p).to_dict(),
            "probabilities": [float(x) for x in prior],
        },
        "train_majority": {
            **multiclass_diagnostics(yy.to_numpy(), majority_p).to_dict(),
            "majority_class": majority,
        },
    }


@dataclass
class CandidateOOSResult:
    name: str
    family: str
    probabilities: pd.DataFrame
    raw_probabilities: pd.DataFrame
    labels: pd.Series
    transition_probability: pd.Series
    diagnostics: dict
    stability: dict
    folds: List[dict]
    errors: List[str]

    def selection_key(self) -> tuple:
        return (float(self.diagnostics["log_loss"]), float(self.diagnostics["brier"]), float(self.diagnostics["ece"]), self.name)


@dataclass
class FrozenBetaModel:
    winner_name: str
    winner_family: str
    candidate: BaseRegimeCandidate
    calibrator: TemporalMulticlassCalibrator
    groups: Dict[str, List[str]]
    feature_columns: List[str]
    training_end: pd.Timestamp
    calibration_start: pd.Timestamp
    calibration_end: pd.Timestamp
    holdout_start: pd.Timestamp
    holdout_end: pd.Timestamp
    development_metrics: dict
    holdout_metrics: dict
    version: str = "beta-v1"
    candidate_schema_version: int = BETA_CANDIDATE_SCHEMA_VERSION
    training_code_revision: str = BETA_TRAINING_CODE_REVISION
    calibration_contract: str = BETA_CALIBRATION_CONTRACT
    artifact_id: str = ""

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        X = features.reindex(columns=self.feature_columns)
        pred = self.candidate.predict(X)
        raw = pred.probabilities.to_numpy()
        calibrated = self.calibrator.transform(raw)
        pcols = [f"p_{name}" for name in REGIME_NAMES]
        out = pd.DataFrame(calibrated, index=X.index, columns=pcols)
        out["dominant_regime"] = [INT_TO_REGIME[int(i)] for i in np.argmax(calibrated, axis=1)]
        state_change = np.zeros(len(out), dtype=float)
        if len(out) > 1:
            state_change[1:] = 0.5 * np.abs(np.diff(calibrated, axis=0)).sum(axis=1)
        out["state_change_score"] = state_change
        out["transition_probability"] = pred.transition_probability.reindex(out.index).astype(float) if pred.transition_probability is not None else np.nan
        q = np.clip(calibrated, 1e-12, 1.0)
        out["uncertainty"] = -np.sum(q * np.log(q), axis=1) / np.log(4.0)
        out["candidate"] = self.winner_name
        out["candidate_family"] = self.winner_family
        out["calibrated"] = not self.calibrator.identity_
        return out


@dataclass
class WalkForwardResult:
    winner_name: str
    winner_family: str
    candidates: Dict[str, CandidateOOSResult]
    frozen_model: FrozenBetaModel
    development_oos: pd.DataFrame
    final_holdout: pd.DataFrame
    development_labels: pd.Series
    holdout_labels: pd.Series
    robustness: dict
    event_diagnostics: dict
    final_holdout_raw_diagnostics: dict
    final_holdout_baselines: dict
    final_calibration: dict

    def summary(self) -> dict:
        return {
            "winner": self.winner_name,
            "winner_family": self.winner_family,
            "selection_rule": "development event-session OOS log loss; Brier then ECE; final holdout untouched",
            "candidate_selection_uses_strategy_returns": False,
            "artifact": {
                "artifact_id": self.frozen_model.artifact_id,
                "candidate_schema_version": int(getattr(self.frozen_model, "candidate_schema_version", 0) or 0),
                "training_code_revision": str(getattr(self.frozen_model, "training_code_revision", "legacy")),
                "calibration_contract": str(getattr(self.frozen_model, "calibration_contract", "legacy")),
            },
            "development": {
                "event_diagnostics": _event_spells(self.development_labels),
                "candidates": {
                    name: {
                        "family": r.family,
                        "diagnostics": r.diagnostics,
                        "raw_diagnostics": multiclass_diagnostics(
                            r.labels.to_numpy(), r.raw_probabilities.loc[r.labels.index].to_numpy()
                        ).to_dict(),
                        "stability": r.stability,
                        "fold_count": len(r.folds),
                        "folds": r.folds,
                        "errors": r.errors,
                    }
                    for name, r in self.candidates.items()
                },
            },
            "final_holdout": {
                "diagnostics": self.frozen_model.holdout_metrics,
                "raw_diagnostics": self.final_holdout_raw_diagnostics,
                "baselines": self.final_holdout_baselines,
                "calibration": self.final_calibration,
                "event_diagnostics": _event_spells(self.holdout_labels),
                "start": self.frozen_model.holdout_start.isoformat(),
                "end": self.frozen_model.holdout_end.isoformat(),
            },
            "robustness": self.robustness,
            "effective_macro_samples": self.event_diagnostics,
        }


class WalkForwardStudy:
    """Development walk-forward selection + untouched final holdout.

    Candidate emissions may use daily state observations, but economic mapping,
    calibration, scoring and effective sample counts use independent macro-event
    sessions only. The final calibrator is learned from the *same final candidate*
    on a pre-holdout calibration block; it is never transferred from pooled fold models.
    """

    def __init__(self, *, min_train_sessions=756, calibration_sessions=126, test_sessions=126,
                 final_holdout_sessions=252, random_state=17, candidate_factories=None,
                 min_train_events=None, min_calibration_events=None, min_test_events=None, min_holdout_events=None) -> None:
        self.min_train_sessions = int(min_train_sessions)
        self.calibration_sessions = int(calibration_sessions)
        self.test_sessions = int(test_sessions)
        self.final_holdout_sessions = int(final_holdout_sessions)
        self.random_state = int(random_state)
        wf = BETA_V1_CONFIG["walk_forward"]
        self.min_train_events = int(wf["minimum_train_macro_events"] if min_train_events is None else min_train_events)
        self.min_calibration_events = int(wf["minimum_calibration_macro_events"] if min_calibration_events is None else min_calibration_events)
        self.min_test_events = int(wf["minimum_test_macro_events"] if min_test_events is None else min_test_events)
        self.min_holdout_events = int(wf["minimum_holdout_macro_events"] if min_holdout_events is None else min_holdout_events)
        self.candidate_factories = dict(candidate_factories or default_candidate_factories(random_state))

    def _folds(self, n: int) -> List[tuple[int, int]]:
        folds, train_end = [], self.min_train_sessions
        while train_end < n:
            test_end = min(n, train_end + self.test_sessions)
            if test_end <= train_end: break
            folds.append((train_end, test_end)); train_end = test_end
        return folds

    @staticmethod
    def _valid_event_index(index, y, event_mask):
        ev = event_mask.reindex(index).fillna(False).astype(bool)
        yy = y.reindex(index)
        return index[ev & yy.notna()]

    def run(self, X: pd.DataFrame, y: pd.Series, groups: Mapping[str, Sequence[str]], *, event_mask: Optional[pd.Series]=None) -> WalkForwardResult:
        X = X.sort_index().copy()
        y = y.reindex(X.index)
        if event_mask is None:
            event_mask = y.notna()
        event_mask = event_mask.reindex(X.index).fillna(False).astype(bool)
        labeled = y.notna()
        if labeled.sum() < self.min_train_sessions:
            raise ValueError(f"Insufficient labeled session history ({int(labeled.sum())}/{self.min_train_sessions} required)")
        if len(X) <= self.min_train_sessions + self.final_holdout_sessions + self.calibration_sessions:
            raise ValueError("Insufficient history for development + calibration + untouched holdout")
        total_events = int((event_mask & y.notna()).sum())
        if total_events < self.min_train_events + self.min_calibration_events + self.min_holdout_events:
            raise ValueError(
                f"Insufficient independent macro events ({total_events}); need at least "
                f"{self.min_train_events + self.min_calibration_events + self.min_holdout_events}"
            )

        holdout_start_pos = len(X) - self.final_holdout_sessions
        dev_X, dev_y, dev_event = X.iloc[:holdout_start_pos], y.iloc[:holdout_start_pos], event_mask.iloc[:holdout_start_pos]
        hold_X, hold_y, hold_event = X.iloc[holdout_start_pos:], y.iloc[holdout_start_pos:], event_mask.iloc[holdout_start_pos:]
        folds = self._folds(len(dev_X))
        if not folds:
            raise ValueError("No development walk-forward folds available")

        results: Dict[str, CandidateOOSResult] = {}
        for name, factory in self.candidate_factories.items():
            calibrated_blocks=[]; raw_blocks=[]; label_blocks=[]; transition_blocks=[]; fold_meta=[]; errors=[]
            for fold_id, (train_end, test_end) in enumerate(folds):
                calib_n = min(self.calibration_sessions, max(40, train_end // 4))
                base_end = train_end - calib_n
                X_base, y_base = dev_X.iloc[:base_end], dev_y.iloc[:base_end]
                X_cal, y_cal = dev_X.iloc[base_end:train_end], dev_y.iloc[base_end:train_end]
                X_test, y_test = dev_X.iloc[train_end:test_end], dev_y.iloc[train_end:test_end]
                w_base = (dev_event.iloc[:base_end] & y_base.notna()).astype(float)
                cal_events = self._valid_event_index(X_cal.index, y_cal, dev_event)
                test_events = self._valid_event_index(X_test.index, y_test, dev_event)
                if (len(test_events) < self.min_test_events or int(w_base.sum()) < self.min_train_events
                    or len(cal_events) < self.min_calibration_events
                    or y_base.loc[w_base > 0].nunique() < 3):
                    continue
                try:
                    model = factory().fit(X_base, y_base.fillna(0).astype(int), groups, sample_weight=w_base)
                    X_forward = pd.concat([X_cal, X_test])
                    forward = model.predict(X_forward)
                    raw_cal_events = forward.probabilities.loc[cal_events].to_numpy() if len(cal_events) else np.empty((0,4))
                    y_cal_events = y_cal.loc[cal_events].astype(int).to_numpy() if len(cal_events) else np.array([],dtype=int)
                    calibrator = TemporalMulticlassCalibrator(random_state=self.random_state+fold_id, min_rows=self.min_calibration_events).fit(raw_cal_events, y_cal_events)
                    raw_test_daily = forward.probabilities.loc[X_test.index].to_numpy()
                    p_test_daily = calibrator.transform(raw_test_daily)
                    pcols=[f"p_{r}" for r in REGIME_NAMES]
                    calibrated_blocks.append(pd.DataFrame(p_test_daily,index=X_test.index,columns=pcols))
                    raw_blocks.append(pd.DataFrame(raw_test_daily,index=X_test.index,columns=pcols))
                    label_blocks.append(y_test.loc[test_events].astype(int))
                    if forward.transition_probability is not None:
                        transition_blocks.append(forward.transition_probability.loc[X_test.index])
                    else:
                        transition_blocks.append(pd.Series(np.nan,index=X_test.index))
                    fold_meta.append({
                        "fold":fold_id,"base_train_end":X_base.index[-1].isoformat(),"base_event_count":int(w_base.sum()),
                        "calibration_event_count":int(len(cal_events)),"test_event_count":int(len(test_events)),
                        "test_start":X_test.index[0].isoformat(),"test_end":X_test.index[-1].isoformat(),
                        "calibrator_identity":calibrator.identity_,"calibrator_method":getattr(calibrator,"method_","legacy_multinomial"),
                        "calibrator_temperature":float(getattr(calibrator,"temperature_",1.0)),
                    })
                except Exception as exc:
                    errors.append(f"fold {fold_id}: {type(exc).__name__}: {exc}")
            if not calibrated_blocks:
                continue
            probs=pd.concat(calibrated_blocks).sort_index(); raw=pd.concat(raw_blocks).sort_index(); labels=pd.concat(label_blocks).sort_index().astype(int); transition=pd.concat(transition_blocks).sort_index()
            event_common=labels.index.intersection(probs.index)
            labels=labels.loc[event_common]
            diag_probs=probs.loc[event_common]
            results[name]=CandidateOOSResult(name,_family(name),probs,raw,labels,transition,multiclass_diagnostics(labels.to_numpy(),diag_probs.to_numpy()).to_dict(),_state_stability(probs),fold_meta,errors)
        if not results:
            raise RuntimeError("No candidate produced development OOS evidence")
        winner=min(results.values(),key=lambda r:r.selection_key())

        # Hyperparameter/family robustness from pre-registered neighboring variants.
        family_members=[r for r in results.values() if r.family==winner.family]
        rel=[]
        base_ll=float(winner.diagnostics["log_loss"])
        for r in family_members:
            rel.append({"variant":r.name,"log_loss":float(r.diagnostics["log_loss"]),"relative_deterioration":float((r.diagnostics["log_loss"]-base_ll)/max(abs(base_ll),1e-12))})
        neighboring=sum(1 for x in rel if x["variant"]!=winner.name)
        rob_cfg=BETA_V1_CONFIG["proposed_promotion_policy"]["robustness"]
        max_deterioration=float(rob_cfg["max_relative_log_loss_deterioration"])
        min_neighbors=int(rob_cfg["minimum_neighboring_variants"])
        close_neighbors=sum(1 for x in rel if x["variant"]!=winner.name and x["relative_deterioration"]<=max_deterioration)
        robustness={"winner_family":winner.family,"variants":rel,"neighboring_variants":neighboring,
                    "neighbors_within_threshold":close_neighbors,"max_relative_log_loss_deterioration":max_deterioration,
                    "minimum_neighboring_variants":min_neighbors,
                    "pass":bool(neighboring>=min_neighbors and close_neighbors>=min_neighbors)}

        # Fit the selected final variant ONLY on pre-holdout base data. Reserve a
        # later calibration block; never refit after seeing its outputs or holdout.
        final_train_end = holdout_start_pos - self.calibration_sessions
        if final_train_end <= 0:
            raise ValueError("No final base-training block")
        X_base, y_base = X.iloc[:final_train_end], y.iloc[:final_train_end]
        X_cal, y_cal = X.iloc[final_train_end:holdout_start_pos], y.iloc[final_train_end:holdout_start_pos]
        w_base=(event_mask.iloc[:final_train_end] & y_base.notna()).astype(float)
        if int(w_base.sum()) < self.min_train_events:
            raise ValueError("Final base block has insufficient independent macro events")
        final_candidate=self.candidate_factories[winner.name]().fit(X_base,y_base.fillna(0).astype(int),groups,sample_weight=w_base)
        X_forward=pd.concat([X_cal,hold_X])
        forward=final_candidate.predict(X_forward)
        cal_events=self._valid_event_index(X_cal.index,y_cal,event_mask)
        hold_events=self._valid_event_index(hold_X.index,hold_y,event_mask)
        if len(cal_events) < self.min_calibration_events:
            raise ValueError(f"Final calibration block has only {len(cal_events)} macro events")
        if len(hold_events) < self.min_holdout_events:
            raise ValueError(f"Final holdout has only {len(hold_events)} macro events")
        final_cal=TemporalMulticlassCalibrator(random_state=self.random_state,min_rows=self.min_calibration_events).fit(forward.probabilities.loc[cal_events].to_numpy(),y_cal.loc[cal_events].astype(int).to_numpy())
        hold_raw=forward.probabilities.loc[hold_events].to_numpy() if len(hold_events) else np.empty((0,4))
        hold_p=final_cal.transform(hold_raw) if len(hold_events) else hold_raw
        if len(hold_events):
            hold_raw_diag=multiclass_diagnostics(hold_y.loc[hold_events].astype(int).to_numpy(),hold_raw).to_dict()
            hold_diag=multiclass_diagnostics(hold_y.loc[hold_events].astype(int).to_numpy(),hold_p).to_dict()
        else:
            hold_raw_diag={"brier":np.nan,"log_loss":np.nan,"ece":np.nan,"accuracy":np.nan,"normalized_entropy":np.nan,"confusion_matrix":[],"reliability":{},"dominant_prediction_share":np.nan,"dominant_prediction_class":None}
            hold_diag={"brier":np.nan,"log_loss":np.nan,"ece":np.nan,"accuracy":np.nan,"normalized_entropy":np.nan,"confusion_matrix":[],"reliability":{}}
        train_event_y=y_base.loc[w_base>0].astype(int)
        baselines=_baseline_diagnostics(hold_y.loc[hold_events].astype(int),train_event_y)
        cal_meta={
            "method":getattr(final_cal,"method_","legacy_multinomial"),
            "identity":bool(final_cal.identity_),
            "temperature":float(getattr(final_cal,"temperature_",1.0)),
            "fit_log_loss_identity":float(getattr(final_cal,"fit_log_loss_identity_",np.nan)),
            "fit_log_loss_calibrated":float(getattr(final_cal,"fit_log_loss_calibrated_",np.nan)),
            "event_count":int(len(cal_events)),
            "class_counts":{str(int(k)):int(v) for k,v in y_cal.loc[cal_events].astype(int).value_counts().sort_index().items()},
        }
        frozen=FrozenBetaModel(winner.name,winner.family,final_candidate,final_cal,{k:list(v) for k,v in groups.items()},list(X.columns),pd.Timestamp(X_base.index[-1]),pd.Timestamp(X_cal.index[0]),pd.Timestamp(X_cal.index[-1]),pd.Timestamp(hold_X.index[0]),pd.Timestamp(hold_X.index[-1]),winner.diagnostics,hold_diag)

        # Store all daily holdout state probabilities (for portfolio evidence), while
        # probability-quality diagnostics above remain event-only.
        daily_hold_cal=final_cal.transform(forward.probabilities.loc[hold_X.index].to_numpy())
        pcols=[f"p_{r}" for r in REGIME_NAMES]
        final_holdout=pd.DataFrame(daily_hold_cal,index=hold_X.index,columns=pcols)
        final_holdout["dominant_regime"]=[INT_TO_REGIME[int(i)] for i in daily_hold_cal.argmax(axis=1)]
        final_holdout["phase"]="final_holdout"
        development=winner.probabilities.copy(); development["phase"]="development_oos"
        event_diag={"development_events":int(len(winner.labels)),"final_calibration_events":int(len(cal_events)),"final_holdout_events":int(len(hold_events)),"daily_sessions_total":int(len(X)),"note":"probability diagnostics count macro event sessions, not carried-forward daily labels"}
        return WalkForwardResult(
            winner.name,winner.family,results,frozen,development,final_holdout,winner.labels,
            hold_y.loc[hold_events].astype(int),robustness,event_diag,hold_raw_diag,baselines,cal_meta,
        )
