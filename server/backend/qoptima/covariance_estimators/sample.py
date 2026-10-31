from __future__ import annotations
import numpy as np
import pandas as pd
from .common import ANNUALIZATION, CovarianceEstimate, base_provenance, clean_returns, matrix_diagnostics, nearest_psd


def estimate(returns: pd.DataFrame, *, annualization: float = ANNUALIZATION, **_: object) -> CovarianceEstimate:
    clean = clean_returns(returns)
    raw = clean.cov().to_numpy(dtype=float) * float(annualization)
    cov, projection = nearest_psd(raw, floor=1e-12)
    symbols = tuple(str(x) for x in clean.columns)
    params = {"ddof": 1, "annualization": float(annualization)}
    diagnostics = matrix_diagnostics(cov, symbols=symbols)
    diagnostics["psd_projection"] = projection
    return CovarianceEstimate("sample", cov, symbols, float(annualization), params, diagnostics,
                              base_provenance(clean, "sample", params))
