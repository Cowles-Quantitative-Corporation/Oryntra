from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class MacroRevision:
    series_id: str
    observation_date: date
    realtime_start: date
    realtime_end: Optional[date]
    value: float
    release_at: datetime
    available_to_model_at: datetime
    eligible_session: date
    source: str = "FRED/ALFRED"
    fetched_at: Optional[datetime] = None
    availability_precision: str = "date_only_conservative"

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        for key in ("observation_date", "realtime_start", "realtime_end", "eligible_session"):
            value = out.get(key)
            out[key] = value.isoformat() if value is not None else None
        for key in ("release_at", "available_to_model_at", "fetched_at"):
            value = out.get(key)
            out[key] = value.isoformat() if value is not None else None
        return out


@dataclass(frozen=True)
class ConsensusEstimate:
    metric: str
    release_at: datetime
    collected_at: datetime
    expected: float
    source: str
    event_id: str
    units: str

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["release_at"] = self.release_at.isoformat()
        out["collected_at"] = self.collected_at.isoformat()
        return out


@dataclass(frozen=True)
class BetaState:
    as_of_session: date
    growth_score: float
    inflation_score: float
    p_growth_up_inflation_up: float
    p_growth_up_inflation_down: float
    p_growth_down_inflation_up: float
    p_growth_down_inflation_down: float
    uncertainty: float
    state_change_score: float
    transition_probability: Optional[float]
    dominant_regime: str
    candidate: str
    calibrated: bool
    data_quality: float
    feature_count: int
    version: str = "beta-v1"

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["as_of_session"] = self.as_of_session.isoformat()
        return out


@dataclass(frozen=True)
class ArtifactStatus:
    code_complete: bool
    trained: bool
    production_frozen: bool
    real_market_data: bool
    real_macro_data: bool
    consensus_promotion_ready: bool
    holdout_complete: bool
    probability_quality_ready: bool
    robustness_ready: bool
    promotion_gate_complete: bool
    blockers: tuple[str, ...]
    candidate_schema_version: int = 0
    training_code_revision: str = ""
    candidate_artifact_id: str = ""
    diagnostics_artifact_id: str = ""
    candidate_diagnostics_match: bool = False

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["blockers"] = list(self.blockers)
        return out
