from __future__ import annotations
import numpy as np
import pandas as pd
from .common import ANNUALIZATION, CovarianceEstimate, base_provenance, clean_returns, matrix_diagnostics, nearest_psd, sym


def estimate(returns: pd.DataFrame, *, annualization: float = ANNUALIZATION, noise_mode: str = "mean", **_: object) -> CovarianceEstimate:
    clean = clean_returns(returns)
    x = clean.to_numpy(dtype=float)
    t, n = x.shape
    vol_daily = np.std(x, axis=0, ddof=1)
    if np.any(vol_daily <= 1e-14):
        raise ValueError("RMT covariance cannot estimate an asset with near-zero return variance")
    z = (x - x.mean(axis=0, keepdims=True)) / vol_daily
    corr = sym(np.cov(z, rowvar=False, ddof=1))
    vals, vecs = np.linalg.eigh(corr)
    q = float(n / max(t, 1))
    lambda_plus = float((1.0 + np.sqrt(q)) ** 2)
    noise = vals <= lambda_plus
    cleaned = vals.copy()
    warnings: list[str] = []
    if np.any(noise):
        if str(noise_mode).lower() == "zero":
            cleaned[noise] = 1e-12
        else:
            cleaned[noise] = max(1e-12, float(vals[noise].mean()))
    else:
        warnings.append("No eigenvalues were classified below the Marchenko-Pastur upper edge")
    corr_clean = sym((vecs * cleaned) @ vecs.T)
    d = np.sqrt(np.maximum(np.diag(corr_clean), 1e-12))
    corr_clean = corr_clean / np.maximum(np.outer(d, d), 1e-12)
    corr_clean, corr_projection = nearest_psd(corr_clean, floor=1e-12)
    vol_ann = vol_daily * np.sqrt(float(annualization))
    raw = corr_clean * np.outer(vol_ann, vol_ann)
    cov, projection = nearest_psd(raw, floor=1e-12)
    symbols = tuple(str(x) for x in clean.columns)
    params = {
        "annualization": float(annualization), "observations": int(t), "assets": int(n),
        "q_assets_over_observations": q, "marchenko_pastur_lambda_plus": lambda_plus,
        "noise_eigenvalues": int(noise.sum()), "signal_eigenvalues": int((~noise).sum()),
        "noise_mode": str(noise_mode).lower(),
    }
    diagnostics = matrix_diagnostics(cov, symbols=symbols)
    diagnostics.update({
        "sample_correlation_eigenvalues": vals.tolist(), "filtered_correlation_eigenvalues": cleaned.tolist(),
        "correlation_psd_projection": corr_projection, "psd_projection": projection,
    })
    if t <= n:
        warnings.append("Observations are not greater than assets; RMT separation is weak and should be treated cautiously")
    return CovarianceEstimate("rmt", cov, symbols, float(annualization), params, diagnostics,
                              base_provenance(clean, "rmt", params), tuple(warnings))
