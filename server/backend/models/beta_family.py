"""Control registry adapter for CQC Model C / Beta V1.2.

Beta is deliberately registered beside proprietary portfolio models so it is
visible in the primary Control model workflow.  Its role is macro/regime: it
does not manufacture security forecasts or portfolio returns.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..beta_v1 import BetaV1Config


def beta_v1_candidate(**overrides):
    values = {"enabled": True}
    values.update(overrides)
    return BetaV1Config(**values)


@dataclass(frozen=True)
class BetaAdapterSpec:
    id: str
    label: str
    description: str
    builder: Callable
    family: str = "beta"
    status: str = "research"
    generation: str = "beta"
    default: bool = False
    supports_phase2: bool = False
    supports_phase3: bool = False
    supports_phase4: bool = False
    role: str = "macro_regime"


MODEL_SPECS = (
    BetaAdapterSpec(
        "beta_v1_macro_regime",
        "Beta / Model C v1.2",
        "Independent PIT probabilistic macro-regime engine (PCA/GMM/HMM); downstream input to Universal Risk V2.0.4.",
        beta_v1_candidate,
    ),
)
