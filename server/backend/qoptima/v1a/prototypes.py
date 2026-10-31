from __future__ import annotations

import numpy as np


def deterministic_prototypes(x: np.ndarray, k: int, *, iterations: int = 40) -> np.ndarray:
    data = np.asarray(x, dtype=float)
    if data.ndim != 2 or len(data) < k:
        raise ValueError("prototype fitting requires rows >= latent state count")
    centered = data - data.mean(axis=0, keepdims=True)
    _u, _s, vh = np.linalg.svd(centered, full_matrices=False)
    axis = vh[0] if vh.size else np.ones(data.shape[1], dtype=float)
    scores = centered @ axis
    quantiles = np.linspace(0.0, 1.0, k + 2)[1:-1]
    targets = np.quantile(scores, quantiles)
    seeds = []
    used: set[int] = set()
    for target in targets:
        order = np.argsort(np.abs(scores - target))
        pick = next(int(i) for i in order if int(i) not in used)
        used.add(pick)
        seeds.append(data[pick].copy())
    centers = np.asarray(seeds, dtype=float)
    for _ in range(max(1, int(iterations))):
        dist2 = np.sum((data[:, None, :] - centers[None, :, :]) ** 2, axis=2)
        labels = np.argmin(dist2, axis=1)
        updated = centers.copy()
        for j in range(k):
            rows = data[labels == j]
            if len(rows):
                updated[j] = rows.mean(axis=0)
        if np.allclose(updated, centers, atol=1e-10, rtol=0.0):
            centers = updated
            break
        centers = updated
    # Deterministic label order along the first training-only principal axis.
    order = np.argsort(centers @ axis)
    return centers[order]


def anchor_probabilities(x: np.ndarray, centers: np.ndarray, *, temperature: float = 1.0) -> np.ndarray:
    data = np.asarray(x, dtype=float)
    c = np.asarray(centers, dtype=float)
    dist2 = np.sum((data[:, None, :] - c[None, :, :]) ** 2, axis=2)
    temp = max(float(temperature), 1e-8)
    logits = -dist2 / temp
    logits -= logits.max(axis=1, keepdims=True)
    p = np.exp(logits)
    p /= np.maximum(p.sum(axis=1, keepdims=True), 1e-300)
    return p


def economic_probes(
    centers: np.ndarray,
    feature_groups: dict[str, list[int]],
    *,
    probe_count: int = 6,
) -> tuple[np.ndarray, list[str]]:
    c = np.asarray(centers, dtype=float)
    k = c.shape[0]
    probes: list[np.ndarray] = []
    names: list[str] = []
    for name, idx in feature_groups.items():
        if not idx:
            continue
        score = c[:, np.asarray(idx, dtype=int)].mean(axis=1)
        score = score - float(score.mean())
        norm = float(np.linalg.norm(score))
        if norm <= 1e-12:
            continue
        probes.append(score / norm)
        names.append(str(name))
        if len(probes) >= int(probe_count):
            break
    # Deterministic fallback probes ensure the dimensional contract remains fixed.
    j = 0
    while len(probes) < int(probe_count):
        v = np.zeros(k, dtype=float)
        v[j % k] = 1.0
        v[(j + 1) % k] = -1.0
        v /= max(float(np.linalg.norm(v)), 1e-12)
        probes.append(v)
        names.append(f"fallback_contrast_{j+1}")
        j += 1
    return np.asarray(probes[:probe_count], dtype=float), names[:probe_count]
