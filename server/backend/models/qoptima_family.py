"""Control registry adapter for QOptima as a backtestable portfolio engine.

QOptima is intentionally registered with role ``qpo_optimizer`` instead of
``portfolio``: Control may compare its realized portfolio path with Alpha/Beta
models, but must not route it through Universal's alpha-model builder or paper
candidate path.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Any


def qoptima_descriptor(**_: Any) -> dict[str, str]:
    return {"id": "qoptima_v1a_qpo", "engine": "QOptima 0.4", "state_model": "V1A-QRS v1.6"}


@dataclass(frozen=True)
class QOptimaAdapterSpec:
    id: str
    label: str
    description: str
    builder: Callable
    family: str = "qoptima"
    status: str = "experimental_research"
    generation: str = "qoptima-0.4"
    default: bool = False
    supports_phase2: bool = False
    supports_phase3: bool = False
    supports_phase4: bool = False
    role: str = "qpo_optimizer"


MODEL_SPECS = (
    QOptimaAdapterSpec(
        "qoptima_v1a_qpo",
        "QOptima V1A-QPO",
        "QScan -> V1A Full-QRS -> exact finite-domain QPO decisions with QRM and limited local QAOA audit. Self-contained QPO portfolio engine, not an Alpha/Beta forecast model.",
        qoptima_descriptor,
    ),
)
