"""Separate causal Beta research producer for Gamma; never promotes native Beta.

Family selection uses one chronological train/calibration/selection split before
the first emitted state. The selected variant is then fixed and refitted annually
on expanding prefixes with a separate trailing calibration block. This deliberately
differs from exporting the retrospectively selected native development OOS panel.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import time
from typing import Any, Callable

import joblib
import numpy as np
import pandas as pd

from ..models.beta.calibration import TemporalMulticlassCalibrator, multiclass_diagnostics
from ..models.beta.labels import REGIME_NAMES
from ..models.beta.walkforward import default_candidate_factories

GAMMA_BETA_VERSION = "gamma_beta_causal_research_1"


class GammaBetaUnavailable(ValueError):
    def __init__(self, message: str, diagnostics: dict | None = None):
        super().__init__(message)
        self.gamma_beta_diagnostics = diagnostics or {"status": "blocked", "reason": message}


@dataclass(frozen=True)
class GammaBetaConfig:
    pre_evaluation_state_sessions: int = 756
    min_train_sessions: int = 756
    calibration_sessions: int = 126
    selection_sessions: int = 126
    refit_sessions: int = 252
    min_train_events: int = 48
    min_calibration_events: int = 12
    min_selection_events: int = 8
    seed: int = 170205

    def __post_init__(self):
        for field in self.__dataclass_fields__:
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"Gamma Beta {field} must be a positive integer")
        if self.min_train_sessions < 756 or self.calibration_sessions < 126 or self.selection_sessions < 126:
            raise ValueError("Gamma Beta preserves minimum 756/126/126 train/calibration/selection session requirements")
        if self.min_train_events < 48 or self.min_calibration_events < 12 or self.min_selection_events < 8:
            raise ValueError("Gamma Beta cannot relax native independent-event minima")


def _hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _frame_hash(frame: pd.DataFrame | pd.Series) -> str:
    h = sha256()
    if isinstance(frame, pd.DataFrame):
        h.update(json.dumps(list(frame.columns)).encode())
    h.update(pd.util.hash_pandas_object(frame, index=True).values.tobytes())
    return h.hexdigest()


def _code_hash() -> str:
    root = Path(__file__).resolve().parents[1]
    names = ["qoptima/gamma_beta.py", "qoptima/gamma_beta_sources.py", "models/beta/features.py",
             "models/beta/calibration.py", "models/beta/labels.py", "models/beta/walkforward.py",
             "models/beta/candidates/base.py", "models/beta/candidates/pca.py",
             "models/beta/candidates/gmm.py", "models/beta/candidates/hmm.py"]
    return _hash({name: sha256((root/name).read_bytes()).hexdigest() for name in names})


def _close_clock(day) -> str:
    # This is a conservative upper bound for same-session open-state features
    # and labels consumed by fitting, not a claim of historical artifact issuance.
    return (pd.Timestamp(day).normalize()+pd.Timedelta(hours=16)).tz_localize("America/New_York").tz_convert("UTC").isoformat()


def _emit(callback, event: dict) -> None:
    if callback is not None:
        callback(event)


def _cache(path: Path, expected: str):
    meta_path = path.with_suffix(".json")
    if not path.is_file() or not meta_path.is_file():
        return None
    metadata = json.loads(meta_path.read_text())
    if metadata.get("fingerprint") != expected or metadata.get("sha256") != sha256(path.read_bytes()).hexdigest():
        raise GammaBetaUnavailable(f"Gamma Beta cache identity mismatch: {path.name}")
    # Only our separately generated, hash-verified research cache is loaded.
    return joblib.load(path)


def _save(path: Path, value, fingerprint: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    joblib.dump(value, temporary)
    temporary.replace(path)
    path.with_suffix(".json").write_text(json.dumps({"fingerprint": fingerprint,
        "sha256": sha256(path.read_bytes()).hexdigest(), "version": GAMMA_BETA_VERSION}, indent=2)+"\n")


def _write_json(path: Path, value, *, indent: int | None = None) -> None:
    """Atomically publish JSON without allocating a second whole-records string."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=indent, separators=None if indent else (",", ":"), default=str)
        stream.write("\n")
    temporary.replace(path)


def _stable_generation(directory: Path, records: list[dict], prepared_key: str) -> dict:
    """Preserve the actual first reconstruction time for identical complete output.

    Fit-cache reuse alone is insufficient: prediction inputs, source lineage,
    artifact bytes, or code can change without changing every fitted snapshot.
    Bind all those dependencies plus the complete emitted record content. The
    wall-clock generation time is audit metadata, never a historical cutoff.
    """
    digest = sha256()
    for record in records:
        # Hash one bounded daily record at a time, not a second 150 MB string.
        digest.update(json.dumps(record, sort_keys=True, separators=(",", ":"), default=str).encode())
        digest.update(b"\n")
    record_digest = digest.hexdigest()
    identity = _hash({"prepared_prefix": prepared_key, "record_content_sha256": record_digest})
    path = directory / f"generation_{identity}.json"
    reused = path.is_file()
    if reused:
        try:
            metadata = json.loads(path.read_text())
            stamp = pd.Timestamp(metadata["generated_at"])
            if (metadata.get("identity") != identity or metadata.get("prepared_prefix") != prepared_key
                    or metadata.get("record_content_sha256") != record_digest
                    or metadata.get("version") != GAMMA_BETA_VERSION
                    or pd.isna(stamp) or stamp.tzinfo is None):
                raise ValueError("invalid generation manifest")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise GammaBetaUnavailable(f"Gamma Beta generation cache identity mismatch: {path.name}") from exc
    else:
        metadata = {"identity": identity, "prepared_prefix": prepared_key,
                    "record_content_sha256": record_digest, "version": GAMMA_BETA_VERSION,
                    "generated_at": datetime.now(timezone.utc).isoformat()}
        _write_json(path, metadata, indent=2)
    return {**metadata, "cache_reused": reused, "manifest_path": str(path)}


def _event_index(prepared, dates):
    return dates[prepared["event_mask"].reindex(dates).fillna(False).astype(bool)
                 & prepared["labels"].reindex(dates).notna()]


def _fit_candidate(prepared, factory, *, base_end: int, calibration_end: int,
                   config: GammaBetaConfig, seed: int) -> dict:
    frame, labels = prepared["frame"], prepared["labels"]
    base = frame.iloc[:base_end]
    calibration = frame.iloc[base_end:calibration_end]
    if len(base) < config.min_train_sessions or len(calibration) < config.calibration_sessions:
        raise GammaBetaUnavailable("Insufficient historical Beta fit/calibration sessions")
    base_events = _event_index(prepared, base.index)
    cal_events = _event_index(prepared, calibration.index)
    if len(base_events) < config.min_train_events or len(cal_events) < config.min_calibration_events:
        raise GammaBetaUnavailable("Insufficient independent historical Beta fit/calibration events")
    if labels.loc[base_events].nunique() < 3:
        raise GammaBetaUnavailable("Historical Beta base training has fewer than three target classes")
    # Whole-panel filtering in the ordinary feature builder would use future
    # missingness. Feature eligibility is decided on this base prefix alone.
    columns = [col for col in base if base[col].notna().sum() >= max(10, int(len(base)*.02))]
    if not columns:
        raise GammaBetaUnavailable("Historical Beta training has no observed feature columns")
    groups = {key: [col for col in cols if col in columns] for key, cols in prepared["groups"].items()}
    model = factory()
    weights = (prepared["event_mask"].reindex(base.index).fillna(False) & labels.reindex(base.index).notna()).astype(float)
    model.fit(base[columns], labels.reindex(base.index).fillna(0).astype(int), groups,
              sample_weight=weights.to_numpy())
    raw = model.predict(calibration[columns]).probabilities
    calibrator = TemporalMulticlassCalibrator(random_state=seed, min_rows=config.min_calibration_events)
    calibrator.fit(raw.loc[cal_events].to_numpy(), labels.loc[cal_events].astype(int).to_numpy())
    return {"model": model, "calibrator": calibrator, "columns": columns, "groups": groups,
            "base_end": base_end, "calibration_end": calibration_end,
            "training_information_cutoff": _close_clock(base.index[-1]),
            "calibration_information_cutoff": _close_clock(calibration.index[-1]),
            "training_sessions": len(base), "training_events": len(base_events),
            "calibration_sessions": len(calibration), "calibration_events": len(cal_events),
            "calibration_method": calibrator.method_, "calibrator_identity": calibrator.identity_,
            "calibration_temperature": float(calibrator.temperature_)}


def _predictions(fit: dict, frame: pd.DataFrame, end: int):
    # HMM inference is filtered forward from its final training posterior;
    # include calibration history before selecting the requested daily rows.
    forward = fit["model"].predict(frame.iloc[fit["base_end"]:end][fit["columns"]])
    p = fit["calibrator"].transform(forward.probabilities.to_numpy())
    return pd.DataFrame(p, index=forward.probabilities.index, columns=REGIME_NAMES), forward.transition_probability


def _prefix_key(prepared, end: int, specification: dict) -> str:
    date = prepared["frame"].index[end-1]
    return _hash({"specification": specification, "feature_prefix": _frame_hash(prepared["frame"].iloc[:end]),
                  "label_prefix": _frame_hash(prepared["labels"].iloc[:end]),
                  "event_prefix": _frame_hash(prepared["event_mask"].iloc[:end]),
                  "source_prefix": prepared["lineage"].prefix_digest(date)})


def _build_records_from_prepared(prepared: dict, *, evaluation_start, evaluation_end,
    output_directory: str | Path, config: GammaBetaConfig, progress_callback=None) -> dict:
    cfg = config
    frame = prepared["frame"]
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise GammaBetaUnavailable("Gamma Beta requires unique chronological exchange sessions")
    if any(pd.Timestamp(day).normalize() not in frame.index for day in (evaluation_start, evaluation_end)):
        raise GammaBetaUnavailable("Gamma Beta source sessions do not cover both requested evaluation endpoints")
    first_evaluation = int(frame.index.searchsorted(pd.Timestamp(evaluation_start).normalize()))
    end = int(frame.index.searchsorted(pd.Timestamp(evaluation_end).normalize(), side="right"))
    if first_evaluation >= end or end > len(frame):
        raise GammaBetaUnavailable("Gamma Beta requested evaluation period has no source sessions")
    state_start = first_evaluation-cfg.pre_evaluation_state_sessions
    minimum = cfg.min_train_sessions+cfg.calibration_sessions+cfg.selection_sessions
    if state_start < minimum:
        raise GammaBetaUnavailable(f"Gamma Beta needs {minimum} pre-state sessions and {cfg.pre_evaluation_state_sessions} pre-evaluation state sessions; only {first_evaluation} are available")
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    code_hash = _code_hash()
    factories = default_candidate_factories(cfg.seed)
    selection_spec = {"version": GAMMA_BETA_VERSION, "config": asdict(cfg), "code_hash": code_hash,
                      "candidate_names": sorted(factories), "policy": "one_temporal_pre_state_selection_block_then_frozen_variant"}
    selection_key = _prefix_key(prepared, state_start, selection_spec)
    directory = output/selection_key[:24]
    directory.mkdir(parents=True, exist_ok=True)
    selection_start = state_start-cfg.selection_sessions
    base_end = selection_start-cfg.calibration_sessions
    selection_dates = frame.index[selection_start:state_start]
    selection_events = _event_index(prepared, selection_dates)
    if len(selection_events) < cfg.min_selection_events:
        raise GammaBetaUnavailable("Insufficient independent pre-state Beta selection events")
    trials = []
    for number, (name, factory) in enumerate(factories.items()):
        key = _hash({"selection_key": selection_key, "candidate": name})
        path = directory/f"selection_{name}.joblib"
        started = time.monotonic()
        trial = _cache(path, key)
        reused = trial is not None
        if trial is None:
            fit = _fit_candidate(prepared, factory, base_end=base_end, calibration_end=selection_start,
                                 config=cfg, seed=cfg.seed)
            predictions, _ = _predictions(fit, frame, state_start)
            metrics = multiclass_diagnostics(prepared["labels"].loc[selection_events].astype(int).to_numpy(),
                                             predictions.loc[selection_events].to_numpy()).to_dict()
            trial = {"candidate": name, "metrics": metrics,
                     "selection_events": len(selection_events), "fit": fit}
            _save(path, trial, key)
        trials.append({"candidate": name, "metrics": trial["metrics"], "cache_reused": reused,
                       "seconds": time.monotonic()-started})
        _emit(progress_callback, {"stage": "gamma_beta_selection", "candidate": name,
             "candidate_number": number+1, "candidate_count": len(factories), **trials[-1]})
    winner = min(trials, key=lambda x: (x["metrics"]["log_loss"], x["metrics"]["brier"], x["metrics"]["ece"], x["candidate"]))["candidate"]
    selection_cutoff = _close_clock(frame.index[state_start-1])
    records, snapshots = [], []
    pcols = list(REGIME_NAMES)
    for number, begin in enumerate(range(state_start, end, cfg.refit_sessions)):
        stop = min(end, begin+cfg.refit_sessions)
        key = _prefix_key(prepared, begin, {"selection_key": selection_key, "winner": winner,
                                          "annual_refit_seed": cfg.seed+number, "code_hash": code_hash})
        path = directory/f"snapshot_{frame.index[begin].date()}_{key[:12]}.joblib"
        started = time.monotonic()
        fit = _cache(path, key)
        reused = fit is not None
        if fit is None:
            # Fixed factory/seed semantics are stable across source extensions.
            factory = default_candidate_factories(cfg.seed+number)[winner]
            fit = _fit_candidate(prepared, factory, base_end=begin-cfg.calibration_sessions,
                                 calibration_end=begin, config=cfg, seed=cfg.seed+number)
            _save(path, fit, key)
        probabilities, transitions = _predictions(fit, frame, stop)
        snapshot = {"artifact_id": key, "artifact_path": str(path), "artifact_sha256": sha256(path.read_bytes()).hexdigest(),
                    "candidate": winner, "first_state_session": str(frame.index[begin].date()),
                    "last_state_session": str(frame.index[stop-1].date()), "selection_information_cutoff": selection_cutoff,
                    **{k:v for k,v in fit.items() if k not in {"model", "calibrator", "groups"}},
                    "cache_reused": reused, "seconds": time.monotonic()-started}
        snapshots.append(snapshot)
        for day in frame.index[begin:stop]:
            source = prepared["lineage"].at(day)
            p = probabilities.loc[day, pcols].to_numpy(dtype=float)
            q = np.clip(p, 1e-12, 1)
            row = {**source, "source": "alfred_pit_beta", "artifact_id": key,
                   "model_version": GAMMA_BETA_VERSION, "training_information_cutoff": fit["training_information_cutoff"],
                   "calibration_information_cutoff": fit["calibration_information_cutoff"],
                   "selection_information_cutoff": selection_cutoff, "probabilities": dict(zip(pcols, map(float,p))),
                   "candidate": winner, "calibrated": not fit["calibrator_identity"],
                   "calibration_attempted": True, "calibration_method": fit["calibration_method"],
                   "uncertainty": float(-np.sum(q*np.log(q))/np.log(4)),
                   "transition_probability": None if transitions is None else float(transitions.loc[day]),
                   "data_quality": float(frame.loc[day,fit["columns"]].notna().mean()),
                   "research_candidate": True, "production_frozen": False, "research_reconstruction": True,
                   "artifact_sha256": snapshot["artifact_sha256"],
                   "feature_schema_hash": _hash(fit["columns"]), "code_hash": code_hash,
                   "availability_semantics": "historical_simulation_at_policy_session_open_not_observed_historical_issuance"}
            records.append(row)
        _emit(progress_callback, {"stage": "gamma_beta_refit", **{k:v for k,v in snapshot.items() if k != "columns"}})
    from .gamma_forecast import _validated_beta_records
    accepted, rejected = _validated_beta_records(records)
    if rejected or len(accepted) != len(records):
        raise GammaBetaUnavailable("Fresh Gamma Beta records failed the strict timestamp/source contract", {"status":"blocked", "rejected": rejected[:20]})
    prepared_key = _prefix_key(prepared, end, {"selection_key": selection_key,
        "evaluation_start": str(pd.Timestamp(evaluation_start).date()),
        "evaluation_end": str(pd.Timestamp(evaluation_end).date()), "code_hash": code_hash})
    generation = _stable_generation(directory, records, prepared_key)
    for record in records:
        record["generated_at"] = generation["generated_at"]
    diagnostics = {"status": "ready_causal_research", "version": GAMMA_BETA_VERSION,
        "policy": selection_spec["policy"], "config": asdict(cfg), "selection_information_cutoff": selection_cutoff,
        "selection_key": selection_key, "selected_candidate": winner, "selection_trials": trials,
        "selection_events": len(selection_events), "records_emitted": len(records), "snapshot_count": len(snapshots),
        "first_state_session": str(frame.index[state_start].date()), "last_state_session": str(frame.index[end-1].date()),
        "evaluation_start": str(pd.Timestamp(evaluation_start).date()), "evaluation_end": str(pd.Timestamp(evaluation_end).date()),
        "pre_evaluation_state_sessions": first_evaluation-state_start, "artifacts": snapshots,
        "sources": prepared["diagnostics"], "cache_directory": str(directory),
        "generation": generation,
        "native_beta_runtime_modified": False, "imported_final_candidate_consumed": False,
        "limitations": ["new causal research policy; not the native retrospectively selected development OOS panel",
                        "family/variant selection is frozen before first emitted state; annual parameter/calibrator refits continue during evaluation",
                        "historical simulation timestamps describe information availability under documented source policies, not live historical issuance",
                        "daily FRED observation-dated and adjusted market snapshots lack independently verified historical revision/adjustment vintages",
                        "earlier unsupported states are absent; strict Gamma must exclude forecast origins without required Beta context",
                        "no production promotion, untouched full-period holdout, or validated investment-edge claim"]}
    _write_json(directory/"diagnostics.json", diagnostics, indent=2)
    _write_json(directory/"records.json", records)
    return {"records": records, "diagnostics": diagnostics}


def build_causal_beta_records(*, market_db_path, beta_db_path, stock_symbols,
    evaluation_start, evaluation_end, output_directory, config: GammaBetaConfig | dict | None = None,
    progress_callback: Callable[[dict], None] | None = None) -> dict:
    """Build independent Gamma Beta artifacts from readonly imported public data."""
    from .gamma_beta_sources import prepare_beta_inputs
    cfg = GammaBetaConfig(**config) if isinstance(config, dict) else (config or GammaBetaConfig())
    if not isinstance(cfg, GammaBetaConfig):
        raise ValueError("Gamma Beta config must be a mapping or GammaBetaConfig")
    _emit(progress_callback, {"stage":"gamma_beta_sources", "status":"preparing_readonly_public_inputs"})
    prepared = prepare_beta_inputs(market_db_path=market_db_path, beta_db_path=beta_db_path,
                                   stock_symbols=stock_symbols, evaluation_end=evaluation_end)
    return _build_records_from_prepared(prepared, evaluation_start=evaluation_start,
        evaluation_end=evaluation_end, output_directory=output_directory, config=cfg,
        progress_callback=progress_callback)
