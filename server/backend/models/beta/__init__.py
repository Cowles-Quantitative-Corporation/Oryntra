"""CQC Beta / Model C v1.

Independent point-in-time macro/regime research engine for Oryntra Control.
The package intentionally does not consume TBA8, Alpha, or Minerva forecasts.
"""

from .types import BetaState, MacroRevision, ConsensusEstimate
from .repository import MacroRepository, SeriesPolicy, DEFAULT_SERIES_POLICIES
from .consensus import ConsensusRepository, SurpriseBuilder
from .features import BetaFeatureEngine, FeatureBundle
from .labels import RegimeLabeler, REGIME_NAMES
from .walkforward import WalkForwardStudy, WalkForwardResult, FrozenBetaModel
from .service import BetaService

__version__ = "1.2.2-native"

__all__ = [
    "BetaState",
    "MacroRevision",
    "ConsensusEstimate",
    "MacroRepository",
    "SeriesPolicy",
    "DEFAULT_SERIES_POLICIES",
    "ConsensusRepository",
    "SurpriseBuilder",
    "BetaFeatureEngine",
    "FeatureBundle",
    "RegimeLabeler",
    "REGIME_NAMES",
    "WalkForwardStudy",
    "WalkForwardResult",
    "FrozenBetaModel",
    "BetaService",
]

from .alpha_context import AlphaAltContextConfig, build_alpha_alt_market_context
from .v1 import BETA_V1_ID, BETA_V1_VERSION, BetaV1Config, beta_v1_contract, beta_v1_state, beta_v1_risk_payload
