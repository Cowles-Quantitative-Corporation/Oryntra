from __future__ import annotations

from typing import Mapping, Optional, Sequence

import numpy as np
import pandas as pd
from scipy.special import logsumexp
from sklearn.decomposition import PCA
from sklearn.mixture import GaussianMixture

from .base import BaseRegimeCandidate, CandidatePrediction


class GaussianHMMCandidate(BaseRegimeCandidate):
    """Four-state diagonal Gaussian HMM fitted by Baum-Welch EM.

    Emission parameters and the transition matrix are learned from each training
    block. The economic label mapping uses training labels only after unsupervised
    fitting. `transition_probability` is the one-step expected probability of
    leaving the current hidden state under the learned transition matrix.
    """

    name = "hmm"
    family = "hmm"

    def __init__(
        self,
        *,
        random_state: int = 17,
        max_components: int = 10,
        max_iter: int = 60,
        tol: float = 1e-4,
        variance_floor: float = 1e-4,
    ) -> None:
        super().__init__(random_state=random_state)
        self.max_components = int(max_components)
        self.max_iter = int(max_iter)
        self.tol = float(tol)
        self.variance_floor = float(variance_floor)
        self.reducer_: Optional[PCA] = None
        self.pi_: Optional[np.ndarray] = None
        self.A_: Optional[np.ndarray] = None
        self.means_: Optional[np.ndarray] = None
        self.vars_: Optional[np.ndarray] = None
        self.mapping_: dict[int, int] = {}
        self.last_training_posterior_: Optional[np.ndarray] = None

    @staticmethod
    def _normalize(v: np.ndarray, axis: Optional[int] = None) -> np.ndarray:
        x = np.asarray(v, dtype=float)
        s = x.sum(axis=axis, keepdims=True)
        s[s <= 0] = 1.0
        return x / s

    def _emission_logp(self, z: np.ndarray) -> np.ndarray:
        # T x K diagonal Gaussian log likelihoods.
        diff = z[:, None, :] - self.means_[None, :, :]
        var = np.maximum(self.vars_, self.variance_floor)
        return -0.5 * (
            np.sum(np.log(2.0 * np.pi * var)[None, :, :], axis=2)
            + np.sum((diff ** 2) / var[None, :, :], axis=2)
        )

    def _forward_backward(self, z: np.ndarray):
        logB = self._emission_logp(z)
        logA = np.log(np.clip(self.A_, 1e-15, 1.0))
        logpi = np.log(np.clip(self.pi_, 1e-15, 1.0))
        T, K = logB.shape
        alpha = np.empty((T, K), dtype=float)
        alpha[0] = logpi + logB[0]
        for t in range(1, T):
            alpha[t] = logB[t] + logsumexp(alpha[t - 1][:, None] + logA, axis=0)
        ll = float(logsumexp(alpha[-1]))

        beta = np.zeros((T, K), dtype=float)
        for t in range(T - 2, -1, -1):
            beta[t] = logsumexp(logA + logB[t + 1][None, :] + beta[t + 1][None, :], axis=1)
        log_posterior = alpha + beta - ll
        posterior = np.exp(log_posterior)
        posterior = self._normalize(posterior, axis=1)

        xi_sum = np.zeros((K, K), dtype=float)
        for t in range(T - 1):
            logxi = (
                alpha[t][:, None] + logA + logB[t + 1][None, :] + beta[t + 1][None, :] - ll
            )
            xi = np.exp(logxi)
            denom = xi.sum()
            if denom > 0:
                xi_sum += xi / denom
        return ll, posterior, xi_sum

    def _initialize(self, z: np.ndarray) -> None:
        gmm = GaussianMixture(
            n_components=4,
            covariance_type="diag",
            reg_covar=self.variance_floor,
            n_init=3,
            random_state=self.random_state,
        ).fit(z)
        hard = gmm.predict(z)
        self.means_ = np.asarray(gmm.means_, dtype=float)
        self.vars_ = np.maximum(np.asarray(gmm.covariances_, dtype=float), self.variance_floor)
        self.pi_ = np.full(4, 1e-3, dtype=float)
        self.pi_[hard[0]] += 1.0
        self.pi_ = self._normalize(self.pi_)
        counts = np.full((4, 4), 1e-3, dtype=float)
        for a, b in zip(hard[:-1], hard[1:]):
            counts[int(a), int(b)] += 1.0
        self.A_ = self._normalize(counts, axis=1)

    def fit(self, X: pd.DataFrame, y: pd.Series, groups: Mapping[str, Sequence[str]], *, sample_weight=None):
        arr = self._fit_preprocess(X)
        ncomp = max(1, min(self.max_components, arr.shape[1], max(1, arr.shape[0] - 1)))
        self.reducer_ = PCA(n_components=ncomp, random_state=self.random_state)
        z = self.reducer_.fit_transform(arr)
        self._initialize(z)

        previous = -np.inf
        for _ in range(self.max_iter):
            ll, posterior, xi_sum = self._forward_backward(z)
            weights = posterior.sum(axis=0) + 1e-12
            self.pi_ = self._normalize(posterior[0] + 1e-6)
            self.A_ = self._normalize(xi_sum + 1e-6, axis=1)
            self.means_ = (posterior.T @ z) / weights[:, None]
            diff = z[:, None, :] - self.means_[None, :, :]
            self.vars_ = np.sum(posterior[:, :, None] * diff ** 2, axis=0) / weights[:, None]
            self.vars_ = np.maximum(self.vars_, self.variance_floor)
            if np.isfinite(previous) and abs(ll - previous) < self.tol * (1.0 + abs(previous)):
                break
            previous = ll

        _, posterior, _ = self._forward_backward(z)
        w = None if sample_weight is None else np.asarray(sample_weight, dtype=float)
        self.mapping_ = self._map_components_to_labels(posterior, np.asarray(y, dtype=int), w)
        self.last_training_posterior_ = posterior[-1].copy()
        self._fitted = True
        return self

    def _filter(self, z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        logB = self._emission_logp(z)
        B = np.exp(logB - logB.max(axis=1, keepdims=True))
        T, K = B.shape
        filtered = np.zeros((T, K), dtype=float)
        switch = np.zeros(T, dtype=float)
        prev = self.last_training_posterior_.copy() if self.last_training_posterior_ is not None else self.pi_.copy()
        for t in range(T):
            pred = prev @ self.A_
            post = pred * B[t]
            post = self._normalize(post)
            filtered[t] = post
            # One-step expected probability that the hidden state leaves its
            # current state, using the filtered current-state distribution.
            switch[t] = 1.0 - float(np.sum(post * np.diag(self.A_)))
            prev = post
        return filtered, np.clip(switch, 0.0, 1.0)

    def predict(self, X: pd.DataFrame) -> CandidatePrediction:
        if not self._fitted or self.reducer_ is None:
            raise RuntimeError("candidate not fitted")
        z = self.reducer_.transform(self._transform(X))
        comp, switch = self._filter(z)
        p = self._reorder_probabilities(comp, self.mapping_)
        return CandidatePrediction(
            probabilities=self._prob_frame(p, X.index),
            transition_probability=pd.Series(switch, index=X.index, name="transition_probability"),
        )
