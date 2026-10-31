from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler

from ..labels import REGIME_NAMES


@dataclass
class CandidatePrediction:
    probabilities: pd.DataFrame
    transition_probability: Optional[pd.Series] = None


class BaseRegimeCandidate:
    name = "base"
    family = "base"

    def __init__(self, *, random_state: int = 17) -> None:
        self.random_state = int(random_state)
        self.columns_: List[str] = []
        self.imputer_: Optional[SimpleImputer] = None
        self.scaler_: Optional[StandardScaler] = None
        self._fitted = False

    def _fit_preprocess(self, X: pd.DataFrame) -> np.ndarray:
        self.columns_ = list(X.columns)
        self.imputer_ = SimpleImputer(strategy="median")
        self.scaler_ = StandardScaler()
        arr = self.imputer_.fit_transform(X[self.columns_])
        arr = self.scaler_.fit_transform(arr)
        return np.asarray(arr, dtype=float)

    def _transform(self, X: pd.DataFrame) -> np.ndarray:
        if self.imputer_ is None or self.scaler_ is None:
            raise RuntimeError("candidate not fitted")
        aligned = X.reindex(columns=self.columns_)
        return self.scaler_.transform(self.imputer_.transform(aligned))

    @staticmethod
    def _map_components_to_labels(
        component_probs: np.ndarray,
        y: np.ndarray,
        sample_weight: Optional[np.ndarray] = None,
    ) -> Dict[int, int]:
        """Map latent components to target economic regimes using training events only.

        The unsupervised candidate may fit on every daily observation, but economic
        component names are learned only from target/event observations with positive
        sample weight. This prevents carried-forward monthly targets from pretending to
        be thousands of independent macro releases.
        """
        probs = np.asarray(component_probs, dtype=float)
        y = np.asarray(y, dtype=int)
        if sample_weight is None:
            w = np.ones(len(y), dtype=float)
        else:
            w = np.asarray(sample_weight, dtype=float)
            if len(w) != len(y):
                raise ValueError("sample_weight length mismatch")
            w = np.where(np.isfinite(w) & (w > 0), w, 0.0)
        k = probs.shape[1]
        score = np.zeros((k, 4), dtype=float)
        for comp in range(k):
            for label in range(4):
                mask = (y == label) & (w > 0)
                if mask.any():
                    score[comp, label] = np.sum(probs[mask, comp] * w[mask])
        row, col = linear_sum_assignment(-score)
        mapping = {int(r): int(c) for r, c in zip(row, col)}
        unused_labels = [x for x in range(4) if x not in mapping.values()]
        for comp in range(k):
            if comp not in mapping:
                mapping[comp] = unused_labels.pop(0) if unused_labels else comp % 4
        return mapping

    @staticmethod
    def _reorder_probabilities(component_probs: np.ndarray, mapping: Mapping[int, int]) -> np.ndarray:
        p = np.asarray(component_probs, dtype=float)
        out = np.zeros((len(p), 4), dtype=float)
        for comp in range(p.shape[1]):
            out[:, int(mapping[comp])] += p[:, comp]
        denom = out.sum(axis=1, keepdims=True)
        denom[denom <= 0] = 1.0
        return out / denom

    @staticmethod
    def _prob_frame(p: np.ndarray, index: pd.Index) -> pd.DataFrame:
        p = np.asarray(p, dtype=float)
        p = np.clip(p, 1e-12, 1.0)
        p = p / p.sum(axis=1, keepdims=True)
        return pd.DataFrame(p, index=index, columns=[f"p_{name}" for name in REGIME_NAMES])

    def fit(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        groups: Mapping[str, Sequence[str]],
        *,
        sample_weight: Optional[pd.Series | np.ndarray] = None,
    ):
        raise NotImplementedError

    def predict(self, X: pd.DataFrame) -> CandidatePrediction:
        raise NotImplementedError
