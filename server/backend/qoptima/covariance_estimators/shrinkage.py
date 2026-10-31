from __future__ import annotations
import numpy as np
import pandas as pd
from .common import ANNUALIZATION, CovarianceEstimate, base_provenance, clean_returns, matrix_diagnostics, nearest_psd


def estimate(returns: pd.DataFrame, *, annualization: float = ANNUALIZATION, estimator: str = "ledoit_wolf", **_: object) -> CovarianceEstimate:
    clean = clean_returns(returns)
    estimator_name = str(estimator or "ledoit_wolf").strip().lower()
    warnings: list[str] = []
    shrinkage = None
    if estimator_name in {"ledoit_wolf", "ledoitwolf", "lw"}:
        try:
            from sklearn.covariance import LedoitWolf
            fit = LedoitWolf(assume_centered=False).fit(clean.to_numpy(dtype=float))
            raw = np.asarray(fit.covariance_, dtype=float) * float(annualization)
            shrinkage = float(fit.shrinkage_)
            estimator_name = "ledoit_wolf"
        except Exception as exc:
            warnings.append(f"Ledoit-Wolf unavailable ({type(exc).__name__}); used OAS fallback")
            estimator_name = "oas"
    if estimator_name == "oas":
        from sklearn.covariance import OAS
        fit = OAS(assume_centered=False).fit(clean.to_numpy(dtype=float))
        raw = np.asarray(fit.covariance_, dtype=float) * float(annualization)
        shrinkage = float(fit.shrinkage_)
    if shrinkage is None:
        raise ValueError(f"unsupported shrinkage estimator: {estimator}")
    cov, projection = nearest_psd(raw, floor=1e-12)
    symbols = tuple(str(x) for x in clean.columns)
    params = {"estimator": estimator_name, "shrinkage": shrinkage, "annualization": float(annualization)}
    diagnostics = matrix_diagnostics(cov, symbols=symbols)
    diagnostics["psd_projection"] = projection
    return CovarianceEstimate("shrinkage", cov, symbols, float(annualization), params, diagnostics,
                              base_provenance(clean, "shrinkage", params), tuple(warnings))
