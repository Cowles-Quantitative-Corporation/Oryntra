from __future__ import annotations

from typing import Mapping, Optional, Sequence

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from .base import BaseRegimeCandidate, CandidatePrediction


class PCABlockCandidate(BaseRegimeCandidate):
    """Training-only PCA growth/inflation block + multinomial classifier."""

    name = "pca_block"
    family = "pca_block"

    def __init__(self, *, random_state: int = 17, C: float = 1.0, n_components: int = 1,
                 class_weight=None) -> None:
        super().__init__(random_state=random_state)
        self.C = float(C)
        self.n_components = max(1, int(n_components))
        self.class_weight = class_weight
        self.growth_cols_: list[str] = []
        self.inflation_cols_: list[str] = []
        self.growth_imputer_: Optional[SimpleImputer] = None
        self.inflation_imputer_: Optional[SimpleImputer] = None
        self.growth_scaler_: Optional[StandardScaler] = None
        self.inflation_scaler_: Optional[StandardScaler] = None
        self.growth_pca_: Optional[PCA] = None
        self.inflation_pca_: Optional[PCA] = None
        self.growth_sign_ = 1.0
        self.inflation_sign_ = 1.0
        self.classifier_: Optional[LogisticRegression] = None

    @staticmethod
    def _fit_block(X: pd.DataFrame, cols: Sequence[str], n_components: int):
        imp = SimpleImputer(strategy="median")
        scaler = StandardScaler()
        k = max(1, min(int(n_components), len(cols)))
        pca = PCA(n_components=k)
        arr = imp.fit_transform(X[list(cols)])
        arr = scaler.fit_transform(arr)
        score = pca.fit_transform(arr)
        return imp, scaler, pca, score

    @staticmethod
    def _orientation(score: np.ndarray, target_sign: np.ndarray, weights: np.ndarray) -> np.ndarray:
        arr = np.asarray(score, dtype=float)
        if arr.ndim == 1:
            arr = arr[:, None]
        signs = np.ones(arr.shape[1], dtype=float)
        for j in range(arr.shape[1]):
            xj = arr[:, j]
            mask = np.isfinite(xj) & np.isfinite(target_sign) & (weights > 0)
            if mask.sum() < 3:
                continue
            x, y, w = xj[mask], target_sign[mask], weights[mask]
            mx, my = np.average(x, weights=w), np.average(y, weights=w)
            cov = np.average((x - mx) * (y - my), weights=w)
            vx = np.average((x - mx) ** 2, weights=w)
            vy = np.average((y - my) ** 2, weights=w)
            if vx > 0 and vy > 0 and cov / np.sqrt(vx * vy) < 0:
                signs[j] = -1.0
        return signs

    def fit(self, X, y, groups, *, sample_weight=None):
        yv = np.asarray(y, dtype=int)
        w = np.ones(len(yv), dtype=float) if sample_weight is None else np.asarray(sample_weight, dtype=float)
        w = np.where(np.isfinite(w) & (w > 0), w, 0.0)
        self.growth_cols_ = [c for c in groups.get("growth", []) if c in X.columns]
        self.inflation_cols_ = [c for c in groups.get("inflation", []) if c in X.columns]
        if not self.growth_cols_ or not self.inflation_cols_:
            raise ValueError("PCA candidate requires non-empty growth and inflation feature blocks")
        self.growth_imputer_, self.growth_scaler_, self.growth_pca_, g = self._fit_block(X, self.growth_cols_, self.n_components)
        self.inflation_imputer_, self.inflation_scaler_, self.inflation_pca_, i = self._fit_block(X, self.inflation_cols_, self.n_components)
        growth_target = np.where(np.isin(yv, [0, 1]), 1.0, -1.0)
        inflation_target = np.where(np.isin(yv, [0, 2]), 1.0, -1.0)
        self.growth_sign_ = self._orientation(g, growth_target, w)
        self.inflation_sign_ = self._orientation(i, inflation_target, w)
        latent = np.column_stack([g * self.growth_sign_, i * self.inflation_sign_])
        mask = w > 0
        if mask.sum() < 20 or len(np.unique(yv[mask])) < 3:
            raise ValueError("PCA candidate lacks enough independent target events/classes")
        self.classifier_ = LogisticRegression(max_iter=1000, random_state=self.random_state, C=self.C,
                                              class_weight=self.class_weight)
        self.classifier_.fit(latent[mask], yv[mask], sample_weight=w[mask])
        self._fitted = True
        return self

    def _latent(self, X: pd.DataFrame) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("candidate not fitted")
        g = self.growth_pca_.transform(self.growth_scaler_.transform(self.growth_imputer_.transform(X.reindex(columns=self.growth_cols_)))) * self.growth_sign_
        i = self.inflation_pca_.transform(self.inflation_scaler_.transform(self.inflation_imputer_.transform(X.reindex(columns=self.inflation_cols_)))) * self.inflation_sign_
        return np.column_stack([g, i])

    def predict(self, X: pd.DataFrame) -> CandidatePrediction:
        latent = self._latent(X)
        raw = self.classifier_.predict_proba(latent)
        out = np.zeros((len(X), 4), dtype=float)
        for j, cls in enumerate(self.classifier_.classes_):
            out[:, int(cls)] = raw[:, j]
        return CandidatePrediction(self._prob_frame(out, X.index), None)
