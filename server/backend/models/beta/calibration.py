from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix, log_loss
from scipy.optimize import minimize_scalar


EPS = 1e-12


class TemporalMulticlassCalibrator:
    """Time-ordered, rank-preserving probability calibration.

    New Beta artifacts use scalar temperature scaling rather than a free multinomial
    remapping. Temperature scaling changes confidence but cannot change the raw
    winning class, which prevents a small calibration block from collapsing every
    future observation into one regime. Legacy joblib artifacts that contain the
    old multinomial ``model_`` remain readable through the compatibility branch in
    :meth:`transform`.

    The fitted temperature is accepted only when it improves calibration-block log
    loss versus identity. Otherwise the calibrator explicitly stays identity.
    """

    def __init__(self, *, random_state: int = 17, min_rows: int = 40) -> None:
        self.random_state = int(random_state)
        self.min_rows = int(min_rows)
        self.model_: Optional[LogisticRegression] = None
        self.identity_ = True
        self.temperature_ = 1.0
        self.method_ = "identity"
        self.fit_log_loss_identity_ = np.nan
        self.fit_log_loss_calibrated_ = np.nan

    @staticmethod
    def _features(p: np.ndarray) -> np.ndarray:
        q = np.clip(np.asarray(p, dtype=float), EPS, 1.0)
        q = q / q.sum(axis=1, keepdims=True)
        return np.log(q)

    @staticmethod
    def _temperature_transform(probabilities: np.ndarray, temperature: float) -> np.ndarray:
        p = np.asarray(probabilities, dtype=float)
        p = np.clip(p, EPS, 1.0)
        p = p / p.sum(axis=1, keepdims=True)
        t = max(float(temperature), 1e-6)
        logits = np.log(p) / t
        logits -= logits.max(axis=1, keepdims=True)
        q = np.exp(logits)
        q /= q.sum(axis=1, keepdims=True)
        return q

    def fit(self, probabilities: np.ndarray, y: Sequence[int]) -> "TemporalMulticlassCalibrator":
        p = np.asarray(probabilities, dtype=float)
        yv = np.asarray(y, dtype=int)
        valid = np.isfinite(p).all(axis=1) & np.isfinite(yv)
        p, yv = p[valid], yv[valid]
        self.model_ = None
        self.temperature_ = 1.0
        self.method_ = "identity"
        if len(yv) < self.min_rows or len(np.unique(yv)) < 3:
            self.identity_ = True
            return self
        self.fit_log_loss_identity_ = float(log_loss(yv, p, labels=[0, 1, 2, 3]))

        def objective(log_t: float) -> float:
            q = self._temperature_transform(p, float(np.exp(log_t)))
            return float(log_loss(yv, q, labels=[0, 1, 2, 3]))

        opt = minimize_scalar(objective, bounds=(np.log(0.10), np.log(50.0)), method="bounded")
        if opt.success and np.isfinite(opt.fun):
            candidate_t = float(np.exp(opt.x))
            candidate_ll = float(opt.fun)
            self.fit_log_loss_calibrated_ = candidate_ll
            if candidate_ll + 1e-6 < self.fit_log_loss_identity_:
                self.temperature_ = candidate_t
                self.identity_ = False
                self.method_ = "temperature"
                return self
        self.fit_log_loss_calibrated_ = self.fit_log_loss_identity_
        self.identity_ = True
        return self

    def transform(self, probabilities: np.ndarray) -> np.ndarray:
        p = np.asarray(probabilities, dtype=float)
        p = np.clip(p, EPS, 1.0)
        p = p / p.sum(axis=1, keepdims=True)
        if hasattr(self, "temperature_") and getattr(self, "method_", "identity") == "temperature" and not self.identity_:
            return self._temperature_transform(p, float(self.temperature_))
        if self.identity_:
            return p
        if self.model_ is None:
            return p
        raw = self.model_.predict_proba(self._features(p))
        out = np.full((len(p), 4), EPS, dtype=float)
        for j, cls in enumerate(self.model_.classes_):
            out[:, int(cls)] = raw[:, j]
        out = out / out.sum(axis=1, keepdims=True)
        return out


@dataclass
class CalibrationDiagnostics:
    brier: float
    log_loss: float
    ece: float
    normalized_entropy: float
    accuracy: float
    confusion_matrix: List[List[int]]
    reliability: Dict[str, List[dict]]
    dominant_prediction_share: float
    dominant_prediction_class: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "brier": self.brier,
            "log_loss": self.log_loss,
            "ece": self.ece,
            "normalized_entropy": self.normalized_entropy,
            "accuracy": self.accuracy,
            "confusion_matrix": self.confusion_matrix,
            "reliability": self.reliability,
            "dominant_prediction_share": self.dominant_prediction_share,
            "dominant_prediction_class": self.dominant_prediction_class,
        }


def _reliability_table(y: np.ndarray, p: np.ndarray, *, bins: int = 10) -> Dict[str, List[dict]]:
    edges = np.linspace(0.0, 1.0, bins + 1)
    result: Dict[str, List[dict]] = {}
    for cls in range(4):
        rows = []
        pred = p[:, cls]
        obs = (y == cls).astype(float)
        for b in range(bins):
            lo, hi = edges[b], edges[b + 1]
            mask = (pred >= lo) & (pred < hi if b < bins - 1 else pred <= hi)
            n = int(mask.sum())
            if n:
                rows.append(
                    {
                        "bin_low": float(lo),
                        "bin_high": float(hi),
                        "count": n,
                        "mean_probability": float(pred[mask].mean()),
                        "observed_frequency": float(obs[mask].mean()),
                    }
                )
        result[str(cls)] = rows
    return result


def multiclass_diagnostics(y: Sequence[int], probabilities: np.ndarray, *, bins: int = 10) -> CalibrationDiagnostics:
    yv = np.asarray(y, dtype=int)
    p = np.asarray(probabilities, dtype=float)
    p = np.clip(p, EPS, 1.0)
    p = p / p.sum(axis=1, keepdims=True)
    onehot = np.eye(4)[yv]
    brier = float(np.mean(np.sum((p - onehot) ** 2, axis=1)))
    ll = float(log_loss(yv, p, labels=[0, 1, 2, 3]))
    confidence = p.max(axis=1)
    prediction = p.argmax(axis=1)
    correct = (prediction == yv).astype(float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    ece = 0.0
    for b in range(bins):
        lo, hi = edges[b], edges[b + 1]
        mask = (confidence >= lo) & (confidence < hi if b < bins - 1 else confidence <= hi)
        if mask.any():
            ece += float(mask.mean()) * abs(float(confidence[mask].mean()) - float(correct[mask].mean()))
    entropy = -np.sum(p * np.log(p), axis=1) / np.log(4.0)
    cm = confusion_matrix(yv, prediction, labels=[0, 1, 2, 3]).astype(int).tolist()
    counts = np.bincount(prediction, minlength=4)
    dominant_class = int(np.argmax(counts)) if len(counts) else 0
    dominant_share = float(counts[dominant_class] / max(len(prediction), 1))
    return CalibrationDiagnostics(
        brier=brier,
        log_loss=ll,
        ece=float(ece),
        normalized_entropy=float(np.mean(entropy)),
        accuracy=float(np.mean(correct)),
        confusion_matrix=cm,
        reliability=_reliability_table(yv, p, bins=bins),
        dominant_prediction_share=dominant_share,
        dominant_prediction_class=dominant_class,
    )
