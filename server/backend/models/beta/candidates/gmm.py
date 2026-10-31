from __future__ import annotations

from typing import Mapping, Optional, Sequence

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.mixture import GaussianMixture

from .base import BaseRegimeCandidate, CandidatePrediction


class GMMCandidate(BaseRegimeCandidate):
    name = "gmm"
    family = "gmm"

    def __init__(self, *, random_state: int = 17, max_components: int = 12, covariance_type: str = "diag") -> None:
        super().__init__(random_state=random_state)
        self.max_components = int(max_components)
        self.covariance_type = str(covariance_type)
        self.reducer_: Optional[PCA] = None
        self.model_: Optional[GaussianMixture] = None
        self.mapping_: dict[int, int] = {}

    def fit(self, X, y, groups, *, sample_weight=None):
        arr = self._fit_preprocess(X)
        ncomp = max(1, min(self.max_components, arr.shape[1], max(1, arr.shape[0] - 1)))
        self.reducer_ = PCA(n_components=ncomp, random_state=self.random_state)
        z = self.reducer_.fit_transform(arr)
        self.model_ = GaussianMixture(n_components=4, covariance_type=self.covariance_type, reg_covar=1e-5, max_iter=500, n_init=5, random_state=self.random_state)
        self.model_.fit(z)
        comp_probs = self.model_.predict_proba(z)
        w = None if sample_weight is None else np.asarray(sample_weight, dtype=float)
        self.mapping_ = self._map_components_to_labels(comp_probs, np.asarray(y, dtype=int), w)
        self._fitted = True
        return self

    def predict(self, X: pd.DataFrame) -> CandidatePrediction:
        if not self._fitted or self.reducer_ is None or self.model_ is None:
            raise RuntimeError("candidate not fitted")
        z = self.reducer_.transform(self._transform(X))
        p = self._reorder_probabilities(self.model_.predict_proba(z), self.mapping_)
        return CandidatePrediction(self._prob_frame(p, X.index), None)
