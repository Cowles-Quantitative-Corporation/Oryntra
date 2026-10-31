from __future__ import annotations
import numpy as np
import pandas as pd
from .common import ANNUALIZATION, CovarianceEstimate, base_provenance, clean_returns, matrix_diagnostics, nearest_psd, sym
from .sample import estimate as sample_estimate
from .shrinkage import estimate as shrinkage_estimate
from .rmt import estimate as rmt_estimate


def estimate(
    returns: pd.DataFrame, *, annualization: float = ANNUALIZATION, source_method: str = "sample",
    spectral_mix: float = 0.0, preserve_asset_variances: bool = True, source_parameters: dict | None = None, **_: object,
) -> CovarianceEstimate:
    clean = clean_returns(returns)
    source = str(source_method or "sample").strip().lower()
    source_parameters = dict(source_parameters or {})
    if source == "shrinkage":
        base_est = shrinkage_estimate(clean, annualization=annualization, **source_parameters)
    elif source == "rmt":
        base_est = rmt_estimate(clean, annualization=annualization, **source_parameters)
    else:
        base_est = sample_estimate(clean, annualization=annualization)
        source = "sample"
    sigma, projection = nearest_psd(np.asarray(base_est.covariance, dtype=float), floor=1e-12)
    trace = float(np.trace(sigma))
    if not np.isfinite(trace) or trace <= 0:
        raise ValueError("density-matrix covariance requires positive covariance trace")
    rho, rho_projection = nearest_psd(sym(sigma / trace), floor=0.0)
    rho = rho / float(np.trace(rho))
    mix = float(np.clip(spectral_mix, 0.0, 1.0))
    warnings = ["A density matrix is a state representation, not a demonstrated superior financial covariance estimator."]
    if mix > 0:
        n = sigma.shape[0]
        rho_used = (1.0 - mix) * rho + mix * np.eye(n, dtype=float) / float(n)
        regularization = "isotropic_density_mix"
        warnings.append("Isotropic density spectral regularization is experimental and must be validated out of sample.")
    else:
        rho_used = rho
        regularization = "none_representation_only"
        warnings.append("spectral_mix=0 reconstructs the source covariance; no predictive advantage is implied.")
    mapped = sym(trace * rho_used)
    variance_restore_effect = 0.0
    if mix > 0 and preserve_asset_variances:
        src_diag = np.maximum(np.diag(sigma), 1e-18)
        new_diag = np.maximum(np.diag(mapped), 1e-18)
        scale = np.sqrt(src_diag / new_diag)
        restored = sym(mapped * np.outer(scale, scale))
        variance_restore_effect = float(np.linalg.norm(restored - mapped, ord="fro"))
        mapped = restored
    mapped, mapped_projection = nearest_psd(mapped, floor=1e-12)
    # Persist a density matrix that corresponds exactly to the final covariance
    # after any variance restoration / PSD projection.  This avoids presenting a
    # pre-regularization quantum-state representation alongside a different
    # financial matrix.  Financial scale is carried separately by final_trace.
    final_trace = float(np.trace(mapped))
    if not np.isfinite(final_trace) or final_trace <= 0:
        raise ValueError("density-matrix mapping produced non-positive final covariance trace")
    rho_final, final_rho_projection = nearest_psd(sym(mapped / final_trace), floor=0.0)
    rho_final = rho_final / float(np.trace(rho_final))
    symbols = tuple(str(x) for x in clean.columns)
    reconstruction_error = float(np.linalg.norm(mapped - sigma, ord="fro"))
    source_norm = float(np.linalg.norm(sigma, ord="fro"))
    params = {
        "annualization": float(annualization), "source_method": source,
        "source_parameters": source_parameters, "spectral_regularization": regularization,
        "spectral_mix": mix, "preserve_asset_variances": bool(preserve_asset_variances),
    }
    diagnostics = matrix_diagnostics(mapped, symbols=symbols)
    diagnostics.update({
        "density_trace": float(np.trace(rho_final)),
        "density_min_eigenvalue": float(np.min(np.linalg.eigvalsh(rho_final))),
        "source_covariance_trace": trace, "mapped_covariance_trace": final_trace,
        "density_to_covariance_scale": final_trace,
        "final_density_psd_projection": final_rho_projection,
        "reconstruction_frobenius_error": reconstruction_error,
        "relative_reconstruction_error": reconstruction_error / source_norm if source_norm > 0 else 0.0,
        "variance_restore_frobenius_change": variance_restore_effect,
        "source_psd_projection": projection, "density_psd_projection": rho_projection,
        "mapped_psd_projection": mapped_projection, "representation_only": bool(mix == 0.0),
    })
    provenance = base_provenance(clean, "quantum_density", params)
    provenance["source_estimator"] = base_est.as_dict(include_matrix=False)
    return CovarianceEstimate("quantum_density", mapped, symbols, float(annualization), params, diagnostics,
                              provenance, tuple(warnings), density_matrix=rho_final)
