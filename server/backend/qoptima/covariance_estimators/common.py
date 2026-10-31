from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import hashlib
import json
import numpy as np
import pandas as pd

ANNUALIZATION = 252.0


def sym(a: np.ndarray) -> np.ndarray:
    x = np.asarray(a, dtype=float)
    return (x + x.T) / 2.0


def nearest_psd(a: np.ndarray, floor: float = 0.0) -> tuple[np.ndarray, dict[str, float]]:
    x = sym(a)
    values, vectors = np.linalg.eigh(x)
    before_min = float(values.min() if values.size else 0.0)
    clipped = np.maximum(values, float(floor))
    out = sym((vectors * clipped) @ vectors.T)
    effect = float(np.linalg.norm(out - x, ord="fro"))
    denom = float(np.linalg.norm(x, ord="fro"))
    return out, {
        "min_eigenvalue_before": before_min,
        "min_eigenvalue_after": float(clipped.min() if clipped.size else 0.0),
        "frobenius_projection_change": effect,
        "relative_projection_change": effect / denom if denom > 0 else 0.0,
    }


def clean_returns(returns: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(returns, pd.DataFrame):
        returns = pd.DataFrame(returns)
    clean = returns.replace([np.inf, -np.inf], np.nan).dropna(how="any")
    if clean.shape[0] < 3 or clean.shape[1] < 2:
        raise ValueError("covariance estimation requires at least 3 aligned sessions and 2 assets")
    if clean.columns.duplicated().any():
        raise ValueError("covariance symbols must be unique")
    return clean.astype(float)


def matrix_diagnostics(matrix: np.ndarray, *, symbols: list[str] | tuple[str, ...]) -> dict[str, Any]:
    cov = sym(matrix)
    eig = np.linalg.eigvalsh(cov)
    diag = np.diag(cov)
    cond = float(np.linalg.cond(cov)) if np.all(np.isfinite(cov)) else float("inf")
    return {
        "shape": [int(cov.shape[0]), int(cov.shape[1])],
        "symbols": list(symbols),
        "trace": float(np.trace(cov)),
        "min_eigenvalue": float(eig.min() if eig.size else 0.0),
        "max_eigenvalue": float(eig.max() if eig.size else 0.0),
        "condition_number": cond,
        "min_variance": float(diag.min() if diag.size else 0.0),
        "max_variance": float(diag.max() if diag.size else 0.0),
        "psd_tolerance_pass": bool(float(eig.min() if eig.size else 0.0) >= -1e-10),
    }


@dataclass(frozen=True)
class CovarianceEstimate:
    method: str
    covariance: np.ndarray
    symbols: tuple[str, ...]
    annualization: float
    parameters: dict[str, Any]
    diagnostics: dict[str, Any]
    provenance: dict[str, Any]
    warnings: tuple[str, ...] = ()
    density_matrix: np.ndarray | None = None

    def as_dict(self, *, include_matrix: bool = False) -> dict[str, Any]:
        payload = {
            "method": self.method,
            "symbols": list(self.symbols),
            "annualization": float(self.annualization),
            "parameters": dict(self.parameters),
            "diagnostics": dict(self.diagnostics),
            "provenance": dict(self.provenance),
            "warnings": list(self.warnings),
        }
        if include_matrix:
            payload["covariance"] = np.asarray(self.covariance, dtype=float).tolist()
            if self.density_matrix is not None:
                payload["density_matrix"] = np.asarray(self.density_matrix, dtype=float).tolist()
        return payload


def base_provenance(clean: pd.DataFrame, method: str, params: dict[str, Any]) -> dict[str, Any]:
    raw = {
        "method": method,
        "symbols": [str(x) for x in clean.columns],
        "sessions": int(len(clean)),
        "first_session": str(clean.index[0]),
        "last_session": str(clean.index[-1]),
        "parameters": params,
    }
    raw["fingerprint"] = hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()[:24]
    return raw
