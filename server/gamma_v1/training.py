from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backend.qoptima.v1a.config import V1AConfig, V1A_MODEL_SCHEMA, V1A_SPEC_VERSION
from backend.qoptima.v1a.features import feature_groups_for_columns
from backend.qoptima.v1a.model import V1ANet, fz0_loss, require_torch, torch
from backend.qoptima.v1a.prototypes import anchor_probabilities, deterministic_prototypes, economic_probes


def _json_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _es_gap_calibration(y, v, e, config: V1AConfig) -> tuple[float, dict[str, Any]]:
    d = np.asarray(v - e, dtype=float)
    yv = np.asarray(y, dtype=float)
    vv = np.asarray(v, dtype=float)
    lo, hi = float(config.es_calibration_min), float(config.es_calibration_max)
    grid = np.geomspace(lo, hi, 257)
    losses = []
    for c in grid:
        ee = vv - c * d
        ind = (yv <= vv).astype(float)
        values = -(ind * (vv - yv)) / (float(config.tail_alpha) * ee) + vv / ee + np.log(-ee) - 1.0
        losses.append(float(np.mean(values)))
    losses_arr = np.asarray(losses)
    best_idx = int(np.nanargmin(losses_arr))
    best = float(grid[best_idx])
    reject = best_idx in {0, len(grid) - 1}
    # Ambiguity: materially separated grid points within tiny loss tolerance.
    near = np.flatnonzero(losses_arr <= losses_arr[best_idx] + 1e-6 * max(1.0, abs(losses_arr[best_idx])))
    if len(near) > 1 and (grid[int(near[-1])] / grid[int(near[0])]) > 1.5:
        reject = True
    return (1.0 if reject else best), {
        "accepted": not reject,
        "selected_c": 1.0 if reject else best,
        "raw_best_c": best,
        "grid_min": lo,
        "grid_max": hi,
        "criterion": "prequential_or_validation_FZ0_with_VaR_fixed",
        "scope": "rescales VaR-to-ES gap only; does not calibrate VaR",
    }


def train_v1a(
    features: pd.DataFrame,
    target_forward_return: pd.Series,
    *,
    config: V1AConfig | None = None,
    signed_control: bool = False,
    feature_groups: dict[str, list[int]] | None = None,
    target_maturities: pd.Series,
    information_cutoff,
    model_factory=None,
    calibrate_gap: bool = False,
) -> dict[str, Any]:
    """Train a frozen V1A artifact on a development/training sample.

    The function is deliberately separate from live QOptima bursts.  A live run
    loads a previously frozen artifact; it never retrains on the current burst.
    """
    require_torch()
    cfg = config or V1AConfig()
    cfg.validate()
    joined = features.join(target_forward_return.rename("__target__"), how="inner").dropna(how="any")
    if len(joined) < max(160, 10 * int(cfg.latent_states)):
        raise ValueError("V1A training requires at least 160 fully aligned observations")
    names = [str(c) for c in features.columns]
    X = joined[names].to_numpy(dtype=np.float64)
    y = joined["__target__"].to_numpy(dtype=np.float64)
    n = len(X)
    val_n = max(30, int(round(n * float(cfg.validation_fraction))))
    split = n - val_n
    maturity = target_maturities.reindex(joined.index)
    if maturity.isna().any() or (maturity > pd.Timestamp(information_cutoff)).any():
        raise ValueError("All labels must mature by artifact information cutoff")
    valid_train = np.flatnonzero((maturity.iloc[:split] < joined.index[split]).to_numpy())
    if not len(valid_train): raise ValueError("No training labels mature before validation begins")
    train_end = int(valid_train[-1]) + 1
    purge = split - train_end
    if train_end < 100:
        raise ValueError("V1A training split is too small after purge")

    mean = X[:train_end].mean(axis=0)
    std = X[:train_end].std(axis=0, ddof=0)
    std = np.where(std > 1e-10, std, 1.0)
    Xs = (X - mean) / std
    centers = deterministic_prototypes(Xs[:train_end], int(cfg.latent_states))
    anchors = anchor_probabilities(Xs, centers, temperature=float(cfg.prototype_temperature))
    groups = feature_groups or feature_groups_for_columns(names)
    probes, probe_names = economic_probes(centers, groups, probe_count=int(cfg.probe_count))

    torch.manual_seed(int(cfg.seed))
    np.random.seed(int(cfg.seed))
    model = (model_factory or V1ANet)(len(names), cfg, probes, signed_control=signed_control)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=float(cfg.learning_rate), weight_decay=float(cfg.weight_decay)
    )
    xt = torch.as_tensor(Xs[:train_end], dtype=torch.float64)
    yt = torch.as_tensor(y[:train_end], dtype=torch.float64)
    at = torch.as_tensor(anchors[:train_end], dtype=torch.float64)
    xv = torch.as_tensor(Xs[split:], dtype=torch.float64)
    yv = torch.as_tensor(y[split:], dtype=torch.float64)

    best_state = None
    best_val = float("inf")
    best_epoch = -1
    stale = 0
    for epoch in range(int(cfg.max_epochs)):
        model.train()
        optimizer.zero_grad(set_to_none=True)
        state = model.raw_state(xt)
        obs_mean = state["observables"].mean(dim=0).detach()
        obs_std = state["observables"].std(dim=0, unbiased=False).detach().clamp(min=1e-8)
        v, e, _z = model.predictions_from_state(state, obs_mean=obs_mean, obs_std=obs_std)
        fz = fz0_loss(yt, v, e, float(cfg.tail_alpha)).mean()
        kl = torch.sum(at * (torch.log(torch.clamp(at, min=1e-12)) - torch.log(torch.clamp(state["p"], min=1e-12))), dim=1).mean()
        ridge = torch.sum(model.var_head.weight ** 2) + torch.sum(model.es_head.weight ** 2)
        loss = fz + float(cfg.anchor_penalty) * kl + float(cfg.ridge_penalty) * ridge
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), float(cfg.gradient_clip))
        optimizer.step()

        model.eval()
        with torch.no_grad():
            tr_state = model.raw_state(xt)
            tr_mean = tr_state["observables"].mean(dim=0)
            tr_std = tr_state["observables"].std(dim=0, unbiased=False).clamp(min=1e-8)
            val_state = model.raw_state(xv)
            vv, ee, _ = model.predictions_from_state(val_state, obs_mean=tr_mean, obs_std=tr_std)
            val_loss = float(fz0_loss(yv, vv, ee, float(cfg.tail_alpha)).mean().item())
        if val_loss < best_val - 1e-8:
            best_val = val_loss
            best_epoch = epoch
            best_state = copy.deepcopy(model.state_dict())
            stale = 0
        else:
            stale += 1
            if stale >= int(cfg.patience):
                break

    if best_state is None:
        raise RuntimeError("V1A training failed to produce a finite validation model")
    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        train_state = model.raw_state(xt)
        obs_mean = train_state["observables"].mean(dim=0)
        obs_std = train_state["observables"].std(dim=0, unbiased=False).clamp(min=1e-8)
        model.set_observable_stats(obs_mean, obs_std)
        val = model(xv)
    v_np = val["var"].cpu().numpy()
    e_np = val["es"].cpu().numpy()
    c_es, cal_meta = (_es_gap_calibration(y[split:], v_np, e_np, cfg) if calibrate_gap else (1.0, {"accepted": False, "selected_c": 1.0, "criterion": "none_preregistered_no_posthoc_gap_calibration"}))

    artifact_information_cutoff = str(pd.Timestamp(information_cutoff))

    artifact_meta = {
        "schema": V1A_MODEL_SCHEMA,
        "spec_version": V1A_SPEC_VERSION,
        "config": cfg.as_dict(),
        "feature_names": names,
        "feature_mean": mean.tolist(),
        "feature_std": std.tolist(),
        "prototype_centers": centers.tolist(),
        "probe_names": probe_names,
        "probes": probes.tolist(),
        "signed_control": bool(signed_control),
        "es_gap_calibration": cal_meta,
        "best_validation_fz0": float(best_val),
        "best_epoch": int(best_epoch),
        "training_rows": int(train_end),
        "validation_rows": int(len(yv)),
        "training_feature_cutoff": str(joined.index[train_end - 1]),
        "training_information_cutoff": str(maturity.iloc[:train_end].max()),
        "artifact_information_cutoff": artifact_information_cutoff,
        "validation_start": str(joined.index[split]),
        "validation_end": str(joined.index[-1]),
        "purge_sessions": int(purge),
        "purge_contract": "training label maturity strictly precedes first validation feature cutoff",
        "validation_last_maturity": str(maturity.iloc[split:].max()),
        "holdout_status": "DEVELOPMENT_ARTIFACT_NOT_CONFIRMATORY",
    }
    artifact_meta["spec_hash"] = _json_hash(artifact_meta)
    return {"model": model, "metadata": artifact_meta, "es_calibration": float(c_es)}


def save_v1a_artifact(result: dict[str, Any], path: str | Path) -> Path:
    require_torch()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    model = result["model"]
    payload = {
        "metadata": dict(result["metadata"]),
        "es_calibration": float(result.get("es_calibration", 1.0)),
        "state_dict": model.state_dict(),
    }
    torch.save(payload, target)
    return target
