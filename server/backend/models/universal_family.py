"""Adapters exposing the existing Universal/Minerva research models to Control.

The underlying builders live in :mod:`backend.minerva` and are not moved.  This
preserves frozen research provenance while giving new Control clients one stable
model-discovery contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..minerva import (
    alpha_v1_candidate,
    alpha_v11_candidate,
    minerva_baseline,
    minerva_economic_interactions_candidate,
    minerva_residual_momentum_candidate,
    minerva_score_persistence_candidate,
    tba1_ohlcv_structure_candidate,
    tba2_qlib_risk_candidate,
    tba3_market_residual_target_candidate,
    tba4_completed_ic_gate_candidate,
    tba5_residual_momentum_candidate,
    tba6_residual_lifecycle_candidate,
    tba8_institutional_risk_candidate,
    tba9_integrity_candidate,
)


@dataclass(frozen=True)
class AdapterSpec:
    id: str
    label: str
    description: str
    builder: Callable
    family: str = "universal"
    status: str = "research"
    generation: str = "legacy"
    default: bool = False
    supports_phase2: bool = True
    supports_phase3: bool = True
    supports_phase4: bool = True


MODEL_SPECS = (
    AdapterSpec(
        "minerva_v1", "Minerva V1", "Frozen walk-forward ridge baseline.", minerva_baseline,
        status="frozen_baseline", generation="minerva",
    ),
    AdapterSpec(
        "minerva_residual_momentum", "Minerva + residual momentum",
        "Frozen Minerva baseline with the historical residual-momentum candidate sleeve.",
        minerva_residual_momentum_candidate, generation="minerva",
    ),
    AdapterSpec(
        "minerva_score_persistence", "Minerva + score persistence",
        "Minerva candidate with causal score smoothing.", minerva_score_persistence_candidate,
        generation="minerva",
    ),
    AdapterSpec(
        "minerva_economic_interactions", "Minerva + economic interactions",
        "Minerva candidate with named causal economic interaction features.",
        minerva_economic_interactions_candidate, generation="minerva",
    ),
    AdapterSpec("tba1", "TBA1 OHLCV structure", "TBA1 compact OHLCV feature sleeve.", tba1_ohlcv_structure_candidate, generation="tba"),
    AdapterSpec("tba2", "TBA2 Qlib-risk construction", "TBA2 portfolio construction research path.", tba2_qlib_risk_candidate, generation="tba"),
    AdapterSpec("tba3", "TBA3 market-residual target", "TBA3 residualized forward-target candidate.", tba3_market_residual_target_candidate, generation="tba"),
    AdapterSpec("tba4", "TBA4 completed-IC gate", "TBA4 completed trailing rank-IC gate candidate.", tba4_completed_ic_gate_candidate, generation="tba"),
    AdapterSpec("tba5", "TBA5 residual momentum", "TBA5 21/63-session residual momentum candidate.", tba5_residual_momentum_candidate, generation="tba"),
    AdapterSpec("tba6", "TBA6 residual lifecycle", "TBA5 plus the causal lifecycle policy.", tba6_residual_lifecycle_candidate, generation="tba"),
    AdapterSpec(
        "tba8", "TBA8 institutional alpha", "Current institutional research baseline with persistent residual forecasts.",
        tba8_institutional_risk_candidate, generation="tba", default=True,
    ),
    AdapterSpec(
        "tba9_integrity", "TBA9 integrity protocol", "TBA8 economics under the point-in-time integrity protocol.",
        tba9_integrity_candidate, generation="tba", status="integrity_protocol",
    ),
    AdapterSpec(
        "alpha_v1", "Alpha V1 cross-sectional", "Independent causal cross-sectional relative-return model with uncertainty.",
        alpha_v1_candidate, generation="alpha", status="review_candidate",
    ),
    AdapterSpec(
        "alpha_v11", "Alpha V1.1 robust cross-sectional",
        "Alpha V1 economic sleeves with overlap-aware IC evidence and robust daily cross-sectional calibration.",
        alpha_v11_candidate, generation="alpha", status="review_candidate",
    ),
)
