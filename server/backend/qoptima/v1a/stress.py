from __future__ import annotations

from typing import Any
import numpy as np


def sym(a: np.ndarray) -> np.ndarray:
    x = np.asarray(a, dtype=float)
    return (x + x.T) / 2.0


def additive_floor(a: np.ndarray, epsilon: float) -> np.ndarray:
    x = sym(a)
    return sym(x + max(float(epsilon), 0.0) * np.eye(x.shape[0]))


def _sqrt_and_inv_sqrt(a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    vals, vecs = np.linalg.eigh(sym(a))
    if np.any(vals <= 0):
        raise ValueError("V1A generalized stress construction requires SPD baseline covariance")
    root = sym((vecs * np.sqrt(vals)) @ vecs.T)
    inv_root = sym((vecs * (1.0 / np.sqrt(vals))) @ vecs.T)
    return root, inv_root


def soft_rmt_v1a_scenario(
    base_covariance: np.ndarray,
    raw_stress_covariance: np.ndarray,
    *,
    reliability_rho: float,
    gamma: float,
    effective_sample_count: int,
    additive_floor_epsilon: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    """V1A v1.6 stress amplification with Canonical risk as a Loewner floor.

    Canonical shrinkage is retained.  V1A only recovers positive raw-stress
    directions, and only up to a soft RMT-cleaned target that never exceeds the
    raw generalized eigenvalue.
    """
    eps = max(float(additive_floor_epsilon), 1e-16)
    base = additive_floor(base_covariance, eps)
    raw = additive_floor(raw_stress_covariance, eps)
    root, inv_root = _sqrt_and_inv_sqrt(base)
    G = sym(inv_root @ raw @ inv_root)
    g, U = np.linalg.eigh(G)
    rho = float(np.clip(reliability_rho, 0.0, 1.0))
    gam = float(np.clip(gamma, 0.0, 1.0))
    n = max(1, int(effective_sample_count))
    p = int(base.shape[0])
    tau_mp = float((1.0 + np.sqrt(float(p) / float(n))) ** 2)
    denom = np.square(g - 1.0) + (tau_mp - 1.0) ** 2
    psi = rho + (1.0 - rho) * np.divide(np.square(g - 1.0), np.maximum(denom, 1e-30))
    g_bar = 1.0 + psi * (g - 1.0)
    g_c = 1.0 + rho * (g - 1.0)
    g_v = g_c + gam * np.maximum(g_bar - g_c, 0.0)
    canonical = sym(root @ (U * g_c) @ U.T @ root)
    v1a = sym(root @ (U * g_v) @ U.T @ root)
    # Same fixed additive floor is applied to both proof-critical paths.
    canonical = additive_floor(canonical, eps)
    v1a = additive_floor(v1a, eps)
    diff_min = float(np.linalg.eigvalsh(sym(v1a - canonical)).min())
    return v1a, {
        "policy": "V1A_SOFT_RMT_STRESS_V1_6",
        "reliability_rho": rho,
        "gamma": gam,
        "effective_sample_count": n,
        "tau_mp": tau_mp,
        "generalized_eigenvalues_raw": g.tolist(),
        "generalized_eigenvalues_canonical": g_c.tolist(),
        "generalized_eigenvalues_soft_target": g_bar.tolist(),
        "generalized_eigenvalues_v1a": g_v.tolist(),
        "psi": psi.tolist(),
        "loewner_difference_min_eigenvalue": diff_min,
        "raw_ceiling_respected": bool(np.all(g_v <= np.maximum(g, g_c) + 1e-10)),
        "pit_rule": "tau_mp recomputed from data available at decision time only",
        "mp_limitation": "MP edge is a soft noise scale heuristic, not an exact null for dependent financial returns",
    }
