from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
import numpy as np
import pandas as pd

SUPPORTED_UNITS = {"period_return", "annualized_return"}


def _ts(value: Any) -> pd.Timestamp:
    t = pd.Timestamp(value)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    else:
        t = t.tz_convert("UTC")
    return t


@dataclass(frozen=True)
class ForecastRecord:
    model_id: str
    model_version: str
    created_at: str
    information_cutoff: str
    horizon_sessions: int
    units: str
    values: dict[str, float]
    realized_outcome: dict[str, float] | None = None
    realized_at: str | None = None
    metadata: dict[str, Any] | None = None

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "ForecastRecord":
        if not isinstance(raw, dict):
            raise ValueError("forecast record must be an object")
        values = raw.get("values") or raw.get("forecasts")
        if not isinstance(values, dict) or not values:
            raise ValueError("forecast record requires non-empty values mapping")
        return cls(
            model_id=str(raw.get("model_id") or "").strip(),
            model_version=str(raw.get("model_version") or "").strip(),
            created_at=str(raw.get("created_at") or "").strip(),
            information_cutoff=str(raw.get("information_cutoff") or raw.get("as_of") or "").strip(),
            horizon_sessions=int(raw.get("horizon_sessions") or 0),
            units=str(raw.get("units") or "period_return").strip().lower(),
            values={str(k).upper().strip(): float(v) for k, v in values.items()},
            realized_outcome=(
                {str(k).upper().strip(): float(v) for k, v in raw.get("realized_outcome", {}).items()}
                if isinstance(raw.get("realized_outcome"), dict) else None
            ),
            realized_at=(str(raw.get("realized_at") or raw.get("outcome_time") or "").strip() or None),
            metadata=dict(raw.get("metadata") or {}),
        )

    def validate(self, *, decision_time: Any, symbols: Iterable[str], required_horizon_sessions: int | None = None) -> dict[str, Any]:
        if not self.model_id or not self.model_version:
            raise ValueError("forecast model_id and model_version are required")
        if self.units not in SUPPORTED_UNITS:
            raise ValueError(f"forecast units must be one of {sorted(SUPPORTED_UNITS)}")
        if self.horizon_sessions <= 0:
            raise ValueError("forecast horizon_sessions must be positive")
        decision = _ts(decision_time)
        created = _ts(self.created_at)
        cutoff = _ts(self.information_cutoff)
        if created > decision:
            raise ValueError(f"forecast {self.model_id} was created after the decision time")
        if cutoff > decision:
            raise ValueError(f"forecast {self.model_id} uses information after the decision time")
        if cutoff > created:
            raise ValueError(f"forecast {self.model_id} information_cutoff cannot be after created_at")
        if required_horizon_sessions is not None and int(required_horizon_sessions) != self.horizon_sessions:
            raise ValueError(
                f"forecast {self.model_id} horizon {self.horizon_sessions} != required {required_horizon_sessions}"
            )
        requested = [str(s).upper().strip() for s in symbols]
        missing = [s for s in requested if s not in self.values or not np.isfinite(self.values[s])]
        if missing:
            raise ValueError(f"forecast {self.model_id} missing aligned symbols: {missing[:8]}")
        realized_at = _ts(self.realized_at) if self.realized_at else None
        if self.realized_outcome is not None and realized_at is not None and realized_at < created:
            raise ValueError(f"forecast {self.model_id} realized_at cannot be before created_at")
        return {
            "model_id": self.model_id,
            "model_version": self.model_version,
            "created_at": created.isoformat(),
            "information_cutoff": cutoff.isoformat(),
            "decision_time": decision.isoformat(),
            "horizon_sessions": int(self.horizon_sessions),
            "units": self.units,
            "symbols": requested,
            "realized_outcome_attached": self.realized_outcome is not None,
            "realized_at": realized_at.isoformat() if realized_at is not None else None,
        }

    def realized_outcome_available_by(self, decision_time: Any) -> bool:
        """True only when the attached realized outcome was known by decision_time."""
        if self.realized_outcome is None or not self.realized_at:
            return False
        return _ts(self.realized_at) <= _ts(decision_time)

    def annualized_vector(self, symbols: Iterable[str]) -> np.ndarray:
        ordered = np.asarray([float(self.values[str(s).upper().strip()]) for s in symbols], dtype=float)
        if self.units == "annualized_return":
            return ordered
        # Period-return values are converted consistently to an annualized simple-return equivalent.
        h = float(self.horizon_sessions)
        clipped = np.maximum(ordered, -0.999999)
        return np.power(1.0 + clipped, 252.0 / h) - 1.0


def align_forecast_records(
    records: Iterable[dict[str, Any] | ForecastRecord] | None,
    *,
    decision_time: Any,
    symbols: Iterable[str],
    required_horizon_sessions: int | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    accepted: list[ForecastRecord] = []
    provenance: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for raw in list(records or []):
        try:
            record = raw if isinstance(raw, ForecastRecord) else ForecastRecord.from_mapping(raw)
            meta = record.validate(
                decision_time=decision_time,
                symbols=symbols,
                required_horizon_sessions=required_horizon_sessions,
            )
            accepted.append(record)
            provenance.append(meta)
        except Exception as exc:
            rejected.append({"error": str(exc), "record": str(raw)[:300]})
            if strict:
                raise
    matrix = None
    if accepted:
        matrix = np.vstack([r.annualized_vector(symbols) for r in accepted])
    return {
        "records": accepted,
        "matrix": matrix,
        "provenance": provenance,
        "rejected": rejected,
        "available": bool(accepted),
        "compatible_models": len(accepted),
    }


def forecast_error_covariance(
    records: Iterable[ForecastRecord],
    symbols: Iterable[str],
) -> tuple[np.ndarray | None, dict[str, Any]]:
    """Estimate model forecast-error covariance only when realized outcomes exist.

    Cross-model disagreement without subsequent realized errors is *not* treated as
    forecast-error covariance.  This function enforces that distinction.
    """
    syms = [str(s).upper().strip() for s in symbols]
    errors: list[np.ndarray] = []
    used: list[str] = []
    for record in records:
        if not record.realized_outcome:
            continue
        if any(s not in record.realized_outcome for s in syms):
            continue
        pred = record.annualized_vector(syms)
        realized = np.asarray([float(record.realized_outcome[s]) for s in syms], dtype=float)
        if record.units == "period_return":
            h = float(record.horizon_sessions)
            realized = np.power(1.0 + np.maximum(realized, -0.999999), 252.0 / h) - 1.0
        errors.append(realized - pred)
        used.append(f"{record.model_id}:{record.model_version}")
    if len(errors) < 2:
        return None, {
            "available": False,
            "reason": "need_at_least_two_point_in_time_forecasts_with_realized_outcomes",
            "records": len(errors),
        }
    matrix = np.vstack(errors)
    cov = np.cov(matrix, rowvar=False, ddof=1)
    return np.atleast_2d(cov), {
        "available": True,
        "records": len(errors),
        "models": used,
        "meaning": "realized forecast-error covariance, not raw cross-model disagreement",
    }



def forecast_error_sample_matrix(
    records: Iterable[ForecastRecord],
    symbols: Iterable[str],
) -> tuple[np.ndarray | None, dict[str, Any]]:
    """Return aligned PIT realized forecast-error samples for portfolio-level tails.

    Unlike ``forecast_error_covariance`` this preserves each historical error
    vector so QRS can evaluate the realized portfolio-level distribution
    ``w^T epsilon`` after weights are applied.
    """
    syms = [str(s).upper().strip() for s in symbols]
    errors: list[np.ndarray] = []
    used: list[str] = []
    regime_labels: list[str | None] = []
    information_cutoffs: list[str] = []
    realized_times: list[str | None] = []
    for record in records:
        if not record.realized_outcome:
            continue
        if any(s not in record.realized_outcome for s in syms):
            continue
        pred = record.annualized_vector(syms)
        realized = np.asarray([float(record.realized_outcome[s]) for s in syms], dtype=float)
        if record.units == "period_return":
            h = float(record.horizon_sessions)
            realized = np.power(1.0 + np.maximum(realized, -0.999999), 252.0 / h) - 1.0
        err = realized - pred
        if np.isfinite(err).all():
            errors.append(err)
            used.append(f"{record.model_id}:{record.model_version}")
            information_cutoffs.append(record.information_cutoff)
            realized_times.append(record.realized_at)
            raw_label = (record.metadata or {}).get("v1a_regime_tail_v1_label")
            regime_labels.append(str(raw_label).strip() if raw_label is not None and str(raw_label).strip() else None)
    if not errors:
        return None, {
            "available": False,
            "records": 0,
            "reason": "no_aligned_point_in_time_forecasts_with_realized_outcomes",
        }
    matrix = np.vstack(errors)
    return matrix, {
        "available": True,
        "records": int(len(matrix)),
        "models": used,
        "v1a_regime_tail_v1_labels": regime_labels,
        "record_information_cutoffs": information_cutoffs,
        "record_realized_at": realized_times,
        "regime_label_contract": "optional PIT label supplied by forecast producer under metadata.v1a_regime_tail_v1_label; absent labels fall back to the global conservative tail",
        "meaning": "annualized-equivalent realized forecast-error vectors used only after portfolio weights are applied",
    }

def select_point_in_time_forecasts(
    records: Iterable[dict[str, Any] | ForecastRecord] | None,
    *,
    decision_time: Any,
    symbols: Iterable[str],
    required_horizon_sessions: int | None = None,
    max_age_days: int | None = None,
) -> dict[str, Any]:
    """Select forecasts available at a historical decision without future leakage.

    The latest eligible record for each (model_id, model_version) is used for the
    current consensus.  All eligible records whose realized outcome was already
    available by the decision time are retained for historical error-covariance
    estimation.  Rejected/incompatible records remain explicit in provenance.
    """
    aligned = align_forecast_records(
        records,
        decision_time=decision_time,
        symbols=symbols,
        required_horizon_sessions=required_horizon_sessions,
        strict=False,
    )
    accepted: list[ForecastRecord] = list(aligned["records"])
    latest: dict[tuple[str, str], ForecastRecord] = {}
    decision = _ts(decision_time)
    stale: list[dict[str, Any]] = []
    for record in accepted:
        age_days = float((decision - _ts(record.created_at)).total_seconds() / 86400.0)
        if max_age_days is not None and age_days > float(max_age_days):
            stale.append({"model_id": record.model_id, "model_version": record.model_version, "created_at": record.created_at, "age_days": age_days})
            continue
        key = (record.model_id, record.model_version)
        prior = latest.get(key)
        if prior is None or _ts(record.created_at) > _ts(prior.created_at):
            latest[key] = record
    current = sorted(latest.values(), key=lambda r: (r.model_id, r.model_version))
    matrix = np.vstack([r.annualized_vector(symbols) for r in current]) if current else None
    historical_realized = [r for r in accepted if r.realized_outcome_available_by(decision_time)]
    error_cov, error_meta = forecast_error_covariance(historical_realized, symbols)
    consensus = np.mean(matrix, axis=0) if matrix is not None else None
    current_regime_labels = [
        str((r.metadata or {}).get("v1a_regime_tail_v1_label") or "").strip()
        for r in current
        if str((r.metadata or {}).get("v1a_regime_tail_v1_label") or "").strip()
    ]
    current_regime_label = None
    if current_regime_labels and len(set(current_regime_labels)) == 1:
        current_regime_label = current_regime_labels[0]
    return {
        "available": bool(current),
        "records": current,
        "matrix": matrix,
        "annualized_consensus": consensus,
        "compatible_models": len(current),
        "historical_realized_records": historical_realized,
        "v1a_current_regime_tail_v1_label": current_regime_label,
        "forecast_error_covariance": error_cov,
        "forecast_error_metadata": error_meta,
        "provenance": [
            r.validate(
                decision_time=decision_time,
                symbols=symbols,
                required_horizon_sessions=required_horizon_sessions,
            )
            for r in current
        ],
        "rejected": aligned["rejected"],
        "stale": stale,
        "max_age_days": max_age_days,
        "selection_rule": "latest non-stale eligible record per (model_id, model_version); no created_at/information_cutoff after decision_time",
    }
