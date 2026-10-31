from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .config import V1AConfig, V1A_MODEL_SCHEMA, V1A_SPEC_VERSION
from .features import current_feature_vector
from .model import V1ANet, require_torch, torch


@dataclass(frozen=True)
class V1AStateSnapshot:
    schema: str
    spec_version: str
    artifact_sha256: str
    information_cutoff: str
    horizon_sessions: int
    p: list[float]
    eigenvalues: list[float]
    var: float
    es: float
    es_calibrated: float
    gamma: float
    trace: float
    min_eigenvalue: float
    positive_definite: bool
    probe_names: list[str]
    observables: list[float]
    signed_control: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_payload(path: Path):
    require_torch()
    try:
        return torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:  # older torch
        return torch.load(path, map_location="cpu")


def load_v1a_artifact(path: str | Path) -> tuple[V1ANet, dict[str, Any], float, str]:
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"V1A artifact not found: {target}")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    payload = _load_payload(target)
    meta = dict(payload.get("metadata") or {})
    if meta.get("schema") != V1A_MODEL_SCHEMA:
        raise ValueError(f"unsupported V1A artifact schema: {meta.get('schema')}")
    if str(meta.get("spec_version") or "") != V1A_SPEC_VERSION:
        raise ValueError(f"unsupported V1A spec version: {meta.get('spec_version')}; expected {V1A_SPEC_VERSION}")
    cfg = V1AConfig.from_mapping(meta.get("config"))
    probes = np.asarray(meta.get("probes"), dtype=float)
    model_class = V1ANet
    if meta.get("gamma_v1_head") == "supported":
        if float(payload.get("es_calibration",1.0)) != 1.0:
            raise ValueError("Supported Gamma V1 head requires preregistered no-gap calibration; legacy scaling can violate support")
        from gamma_v1.model import SupportedV1ANet
        model_class = SupportedV1ANet
    elif meta.get("gamma_v1_head") not in (None,"legacy"):
        raise ValueError("Unrecognized Gamma V1 tail head")
    model = model_class(
        len(meta.get("feature_names") or []), cfg, probes,
        signed_control=bool(meta.get("signed_control", False)),
    )
    model.load_state_dict(payload["state_dict"])
    model.eval()
    return model, meta, float(payload.get("es_calibration", 1.0)), digest


def infer_v1a_snapshot(
    artifact_path: str | Path,
    returns: pd.DataFrame,
    *,
    information_cutoff: str,
    feature_overrides: Mapping[str, float] | None = None,
    es_budget_override: float | None = None,
    r_max_override: float | None = None,
) -> V1AStateSnapshot:
    require_torch()
    model, meta, c_es, digest = load_v1a_artifact(artifact_path)
    artifact_cutoff_raw = meta.get("artifact_information_cutoff")
    if artifact_cutoff_raw is None:
        raise ValueError("V1A artifact lacks artifact_information_cutoff")
    decision = pd.Timestamp(information_cutoff)
    artifact_cutoff = pd.Timestamp(artifact_cutoff_raw)
    if decision.tzinfo is None:
        decision = decision.tz_localize("UTC")
    else:
        decision = decision.tz_convert("UTC")
    if artifact_cutoff.tzinfo is None:
        artifact_cutoff = artifact_cutoff.tz_localize("UTC")
    else:
        artifact_cutoff = artifact_cutoff.tz_convert("UTC")
    if not artifact_cutoff < decision:
        raise ValueError(
            f"V1A artifact information cutoff {artifact_cutoff.isoformat()} must strictly predate decision cutoff {decision.isoformat()}"
        )
    cfg = model.config
    names = [str(x) for x in meta.get("feature_names") or []]
    raw = current_feature_vector(returns, names, overrides=feature_overrides)
    mean = np.asarray(meta["feature_mean"], dtype=float)
    std = np.asarray(meta["feature_std"], dtype=float)
    x = (raw - mean) / np.where(std > 1e-10, std, 1.0)
    with torch.no_grad():
        out = model(torch.as_tensor(x[None, :], dtype=torch.float64))
    p = out["p"][0].cpu().numpy()
    eig = out["eigvals"][0].cpu().numpy()
    obs = out["observables"][0].cpu().numpy()
    v = float(out["var"][0].item())
    e = float(out["es"][0].item())
    d = v - e
    e_cal = float(v - float(c_es) * d)
    if meta.get("gamma_v1_head") == "supported":
        from gamma_v1.publishing import supported_tail_publication
        e_cal = supported_tail_publication(v,e,c_es)
    budget = float(cfg.es_budget if es_budget_override is None else es_budget_override)
    rmax = float(cfg.es_activation_max_ratio if r_max_override is None else r_max_override)
    ratio = (-e_cal) / max(budget, 1e-12)
    gamma = float(np.clip((ratio - 1.0) / max(rmax - 1.0, 1e-12), 0.0, 1.0))
    trace = float(p.sum())
    return V1AStateSnapshot(
        schema="cqc.qoptima.v1a_state_snapshot.v1",
        spec_version=str(meta.get("spec_version") or V1A_SPEC_VERSION),
        artifact_sha256=digest,
        information_cutoff=str(information_cutoff),
        horizon_sessions=int(cfg.horizon_sessions),
        p=[float(z) for z in p],
        eigenvalues=[float(z) for z in eig],
        var=v,
        es=e,
        es_calibrated=e_cal,
        gamma=gamma,
        trace=trace,
        min_eigenvalue=float(eig.min()),
        positive_definite=bool(float(eig.min()) > 0.0),
        probe_names=[str(x) for x in meta.get("probe_names") or []],
        observables=[float(z) for z in obs],
        signed_control=bool(meta.get("signed_control", False)),
    )


def select_causal_artifact(schedule: list[dict[str, Any]], decision_time: str) -> tuple[str | None, dict[str, Any]]:
    """Select the latest artifact whose training cutoff is strictly before decision_time."""
    import pandas as pd
    decision = pd.Timestamp(decision_time)
    if decision.tzinfo is None:
        decision = decision.tz_localize("UTC")
    else:
        decision = decision.tz_convert("UTC")
    eligible = []
    rejected = []
    for row in list(schedule or []):
        path = str(row.get("artifact_path") or row.get("path") or "").strip()
        if not path:
            continue
        try:
            _model, meta, _c, digest = load_v1a_artifact(path)
            cutoff_raw = meta.get("artifact_information_cutoff") or meta.get("training_information_cutoff") or meta.get("training_cutoff")
            if cutoff_raw is None:
                raise ValueError("artifact metadata lacks artifact_information_cutoff")
            cutoff = pd.Timestamp(cutoff_raw)
            if cutoff.tzinfo is None:
                cutoff = cutoff.tz_localize("UTC")
            else:
                cutoff = cutoff.tz_convert("UTC")
            if cutoff < decision:
                eligible.append((cutoff, path, digest, meta))
            else:
                rejected.append({"path": path, "artifact_information_cutoff": str(cutoff), "reason": "artifact_information_cutoff_not_before_decision"})
        except Exception as exc:
            rejected.append({"path": path, "reason": f"{type(exc).__name__}: {exc}"})
    if not eligible:
        return None, {"status": "NO_CAUSAL_ARTIFACT", "decision_time": str(decision), "rejected": rejected}
    eligible.sort(key=lambda x: x[0])
    cutoff, path, digest, meta = eligible[-1]
    return path, {
        "status": "CAUSAL_ARTIFACT_SELECTED",
        "decision_time": str(decision),
        "artifact_information_cutoff": str(cutoff),
        "artifact_sha256": digest,
        "spec_hash": meta.get("spec_hash"),
        "rejected": rejected,
    }
