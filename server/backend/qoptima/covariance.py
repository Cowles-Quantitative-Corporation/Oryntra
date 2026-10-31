from __future__ import annotations

"""Common covariance interface for QOptima research.

The production dropdown calls this facade, while each estimator is implemented in
its own backend module under ``covariance_estimators``.  Sample covariance remains
an explicit benchmark.  Quantum density mode is a representation experiment, not
an assumed superior estimator.
"""
from typing import Any
import pandas as pd

from .covariance_estimators.common import ANNUALIZATION, CovarianceEstimate, matrix_diagnostics, nearest_psd
from .covariance_estimators.sample import estimate as _sample
from .covariance_estimators.shrinkage import estimate as _shrinkage
from .covariance_estimators.rmt import estimate as _rmt
from .covariance_estimators.density import estimate as _density

COVARIANCE_METHODS = ("sample", "shrinkage", "rmt", "quantum_density")


def sample_covariance(returns: pd.DataFrame, *, annualization: float = ANNUALIZATION) -> CovarianceEstimate:
    return _sample(returns, annualization=annualization)


def shrinkage_covariance(returns: pd.DataFrame, *, annualization: float = ANNUALIZATION, estimator: str = "ledoit_wolf") -> CovarianceEstimate:
    return _shrinkage(returns, annualization=annualization, estimator=estimator)


def rmt_filtered_covariance(returns: pd.DataFrame, *, annualization: float = ANNUALIZATION, noise_mode: str = "mean") -> CovarianceEstimate:
    return _rmt(returns, annualization=annualization, noise_mode=noise_mode)


def quantum_density_covariance(
    returns: pd.DataFrame, *, annualization: float = ANNUALIZATION, source_method: str = "sample",
    spectral_mix: float = 0.0, preserve_asset_variances: bool = True, source_parameters: dict[str, Any] | None = None,
) -> CovarianceEstimate:
    return _density(
        returns, annualization=annualization, source_method=source_method, spectral_mix=spectral_mix,
        preserve_asset_variances=preserve_asset_variances, source_parameters=source_parameters,
    )


def estimate_covariance(returns: pd.DataFrame, method: str, *, annualization: float = ANNUALIZATION, parameters: dict[str, Any] | None = None) -> CovarianceEstimate:
    clean_method = str(method or "shrinkage").strip().lower()
    params = dict(parameters or {})
    if clean_method == "sample":
        return _sample(returns, annualization=annualization)
    if clean_method in {"shrinkage", "ledoit_wolf", "ledoitwolf", "oas"}:
        estimator = params.get("estimator", "oas" if clean_method == "oas" else "ledoit_wolf")
        return _shrinkage(returns, annualization=annualization, estimator=str(estimator))
    if clean_method in {"rmt", "random_matrix", "random_matrix_theory"}:
        return _rmt(returns, annualization=annualization, noise_mode=str(params.get("noise_mode", "mean")))
    if clean_method in {"quantum_density", "density_matrix", "quantum_state_density"}:
        return _density(
            returns, annualization=annualization, source_method=str(params.get("source_method", "sample")),
            spectral_mix=float(params.get("spectral_mix", 0.0)),
            preserve_asset_variances=bool(params.get("preserve_asset_variances", True)),
            source_parameters=dict(params.get("source_parameters") or {}),
        )
    raise ValueError(f"unknown covariance method: {method}")


def compare_covariance_methods(
    returns: pd.DataFrame, *, methods: tuple[str, ...] = COVARIANCE_METHODS,
    method_parameters: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        method: estimate_covariance(returns, method, parameters=(method_parameters or {}).get(method)).as_dict(include_matrix=False)
        for method in methods
    }
