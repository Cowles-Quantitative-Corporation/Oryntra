from __future__ import annotations

from statistics import NormalDist
from typing import Any
import numpy as np
import pandas as pd


def fz0_numpy(y, v, e, alpha: float) -> np.ndarray:
    y = np.asarray(y, dtype=float)
    v = np.asarray(v, dtype=float)
    e = np.asarray(e, dtype=float)
    ind = (y <= v).astype(float)
    return -(ind * (v - y)) / (float(alpha) * e) + v / e + np.log(-e) - 1.0


def var_es_diagnostics(y, v, e, alpha: float) -> dict[str, Any]:
    y = np.asarray(y, dtype=float)
    v = np.asarray(v, dtype=float)
    e = np.asarray(e, dtype=float)
    mask = np.isfinite(y) & np.isfinite(v) & np.isfinite(e) & (e < v) & (v < 0)
    y, v, e = y[mask], v[mask], e[mask]
    if len(y) == 0:
        return {"available": False}
    exceed = y <= v
    coverage = float(np.mean(exceed))
    if exceed.any():
        realized_es = float(np.mean(y[exceed]))
        predicted_es = float(np.mean(e[exceed]))
        es_abs_error = abs(realized_es - predicted_es)
    else:
        realized_es = predicted_es = es_abs_error = None
    return {
        "available": True,
        "rows": int(len(y)),
        "fz0_mean": float(np.mean(fz0_numpy(y, v, e, alpha))),
        "var_coverage": coverage,
        "coverage_abs_error": abs(coverage - float(alpha)),
        "exceedances": int(exceed.sum()),
        "realized_es_on_exceedances": realized_es,
        "predicted_es_on_exceedances": predicted_es,
        "es_abs_error": es_abs_error,
    }


def gate_a_tail_state(
    y,
    *,
    v1a_var,
    v1a_es,
    baseline_var,
    baseline_es,
    alpha: float,
    minimum_fz0_improvement: float,
    coverage_tolerance: float,
    es_tolerance: float,
) -> dict[str, Any]:
    if float(minimum_fz0_improvement) <= 0.0:
        return {
            "status": "DEVELOPMENT_ONLY_FZ0_HURDLE_NOT_FROZEN",
            "reason": "minimum_fz0_improvement must be strictly positive before confirmatory Gate A",
        }
    v1a = var_es_diagnostics(y, v1a_var, v1a_es, alpha)
    base = var_es_diagnostics(y, baseline_var, baseline_es, alpha)
    if not v1a.get("available") or not base.get("available"):
        return {"status": "INCONCLUSIVE", "reason": "missing_var_es_diagnostics", "v1a": v1a, "baseline": base}
    delta = float(v1a["fz0_mean"] - base["fz0_mean"])
    fz_pass = delta <= -float(minimum_fz0_improvement)
    coverage_pass = v1a["coverage_abs_error"] <= base["coverage_abs_error"] + float(coverage_tolerance)
    if v1a["es_abs_error"] is None or base["es_abs_error"] is None:
        es_pass = False
    else:
        es_pass = v1a["es_abs_error"] <= base["es_abs_error"] + float(es_tolerance)
    return {
        "status": "PASS" if fz_pass and coverage_pass and es_pass else "FAIL",
        "fz0_delta_v1a_minus_baseline": delta,
        "minimum_fz0_improvement": float(minimum_fz0_improvement),
        "fz0_pass": bool(fz_pass),
        "coverage_noninferiority_pass": bool(coverage_pass),
        "es_noninferiority_pass": bool(es_pass),
        "v1a": v1a,
        "baseline": base,
        "interpretation": "practical superiority in FZ0 plus VaR/ES non-inferiority safety tolerances",
    }


def rolling_historical_var_es(y: pd.Series, *, alpha: float = 0.05, window: int = 252, min_periods: int = 126) -> pd.DataFrame:
    values = pd.Series(y, dtype=float)
    var = values.shift(1).rolling(window, min_periods=min_periods).quantile(alpha)
    es = pd.Series(index=values.index, dtype=float)
    for i in range(len(values)):
        if not np.isfinite(var.iloc[i]):
            continue
        prior = values.iloc[max(0, i-window):i].dropna()
        tail = prior[prior <= var.iloc[i]]
        if len(tail):
            es.iloc[i] = float(tail.mean())
    return pd.DataFrame({"var": var, "es": es})


def ewma_gaussian_var_es(y: pd.Series, *, alpha: float = 0.05, decay: float = 0.94, initial_window: int = 63) -> pd.DataFrame:
    values = pd.Series(y, dtype=float)
    arr = values.to_numpy(dtype=float)
    variance = np.full(len(arr), np.nan)
    if len(arr) <= initial_window:
        return pd.DataFrame({"var": variance, "es": variance}, index=values.index)
    v = float(np.nanvar(arr[:initial_window], ddof=1))
    for i in range(initial_window, len(arr)):
        variance[i] = max(v, 1e-16)
        if np.isfinite(arr[i]):
            v = float(decay) * v + (1.0 - float(decay)) * float(arr[i]) ** 2
    nd = NormalDist()
    z = float(nd.inv_cdf(alpha))
    # phi(z) without scipy.
    phi = float(np.exp(-0.5 * z * z) / np.sqrt(2.0 * np.pi))
    sigma = np.sqrt(variance)
    var = z * sigma
    es = -(phi / float(alpha)) * sigma
    return pd.DataFrame({"var": var, "es": es}, index=values.index)


def direct_mlp_var_es_baseline(
    train_features: pd.DataFrame,
    train_target: pd.Series,
    eval_features: pd.DataFrame,
    *,
    alpha: float = 0.05,
    seed: int = 53,
    max_epochs: int = 500,
    patience: int = 50,
    weight_decay: float = 1e-4,
    learning_rate: float = 1e-3,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Strong direct classical z_t -> (VaR, ES) baseline for Gate A.

    This deliberately uses the same small 64->32 SiLU capacity class as V1A's
    trunk but has no latent matrix, prototypes, probes, or quantum-inspired
    structure.  It is fit only on the caller-provided training period and
    returns predictions for an already separated evaluation period.
    """
    try:
        import torch
        from torch import nn
        import torch.nn.functional as F
    except Exception as exc:  # pragma: no cover - environment guard
        raise RuntimeError("direct Gate-A MLP baseline requires PyTorch") from exc

    joined = train_features.join(pd.Series(train_target, name="__y__"), how="inner").dropna(how="any")
    if len(joined) < 120:
        raise ValueError("direct MLP baseline requires at least 120 aligned training rows")
    cols = [str(c) for c in train_features.columns]
    missing = [c for c in cols if c not in eval_features.columns]
    if missing:
        raise ValueError(f"evaluation features are missing baseline columns: {missing[:8]}")
    X = joined[cols].to_numpy(dtype=np.float64)
    y = joined["__y__"].to_numpy(dtype=np.float64)
    val_n = max(24, int(round(0.20 * len(X))))
    split = len(X) - val_n
    if split < 80:
        raise ValueError("direct MLP baseline training split is too small")
    mean = X[:split].mean(axis=0)
    std = X[:split].std(axis=0, ddof=0)
    std = np.where(std > 1e-10, std, 1.0)
    Xs = (X - mean) / std
    Xe = (eval_features[cols].to_numpy(dtype=np.float64) - mean) / std

    class DirectTailNet(nn.Module):
        def __init__(self, d: int):
            super().__init__()
            self.trunk = nn.Sequential(nn.Linear(d, 64), nn.SiLU(), nn.Linear(64, 32), nn.SiLU())
            self.v = nn.Linear(32, 1)
            self.e = nn.Linear(32, 1)

        def forward(self, x):
            h = self.trunk(x)
            vv = -F.softplus(self.v(h).squeeze(-1)) - 1e-6
            ee = vv - F.softplus(self.e(h).squeeze(-1))
            return vv, ee

    def loss_fn(yy, vv, ee):
        ind = (yy <= vv).to(yy.dtype)
        return (-(ind * (vv - yy)) / (float(alpha) * ee) + vv / ee + torch.log(-ee) - 1.0).mean()

    torch.manual_seed(int(seed))
    np.random.seed(int(seed))
    net = DirectTailNet(len(cols)).double()
    opt = torch.optim.AdamW(net.parameters(), lr=float(learning_rate), weight_decay=float(weight_decay))
    xt = torch.as_tensor(Xs[:split], dtype=torch.float64)
    yt = torch.as_tensor(y[:split], dtype=torch.float64)
    xv = torch.as_tensor(Xs[split:], dtype=torch.float64)
    yv = torch.as_tensor(y[split:], dtype=torch.float64)
    best = None
    best_loss = float("inf")
    stale = 0
    for epoch in range(int(max_epochs)):
        net.train(); opt.zero_grad(set_to_none=True)
        vv, ee = net(xt)
        loss = loss_fn(yt, vv, ee)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(net.parameters(), 5.0)
        opt.step()
        net.eval()
        with torch.no_grad():
            vvv, eee = net(xv)
            vl = float(loss_fn(yv, vvv, eee).item())
        if vl < best_loss - 1e-8:
            best_loss = vl
            best = {k: v.detach().clone() for k, v in net.state_dict().items()}
            stale = 0
        else:
            stale += 1
            if stale >= int(patience):
                break
    if best is None:
        raise RuntimeError("direct MLP baseline failed to fit")
    net.load_state_dict(best); net.eval()
    with torch.no_grad():
        ve, ee = net(torch.as_tensor(Xe, dtype=torch.float64))
    pred = pd.DataFrame({"var": ve.cpu().numpy(), "es": ee.cpu().numpy()}, index=eval_features.index)
    return pred, {
        "model": "DIRECT_FZ0_MLP_BASELINE_V1",
        "architecture": "raw_z->64_SiLU->32_SiLU->VaR_ES",
        "latent_matrix": False,
        "alpha": float(alpha),
        "seed": int(seed),
        "training_rows": int(split),
        "validation_rows": int(val_n),
        "best_validation_fz0": float(best_loss),
        "weight_decay": float(weight_decay),
        "learning_rate": float(learning_rate),
        "role": "mandatory strong direct classical Gate-A control",
    }


def strongest_gate_a_baseline(y, baselines: dict[str, pd.DataFrame], *, alpha: float) -> dict[str, Any]:
    """Select the strongest *predeclared* Gate-A baseline by OOS mean FZ0."""
    yy = pd.Series(y, dtype=float)
    rows: dict[str, Any] = {}
    best_name = None
    best_loss = float("inf")
    for name, frame in dict(baselines or {}).items():
        if not isinstance(frame, pd.DataFrame) or not {"var", "es"}.issubset(frame.columns):
            continue
        aligned = pd.concat([yy.rename("y"), frame[["var", "es"]]], axis=1).dropna()
        diag = var_es_diagnostics(aligned["y"], aligned["var"], aligned["es"], alpha)
        rows[str(name)] = diag
        if diag.get("available") and np.isfinite(diag.get("fz0_mean", np.nan)) and float(diag["fz0_mean"]) < best_loss:
            best_loss = float(diag["fz0_mean"]); best_name = str(name)
    return {"selected": best_name, "selected_fz0": None if best_name is None else best_loss, "baselines": rows}
