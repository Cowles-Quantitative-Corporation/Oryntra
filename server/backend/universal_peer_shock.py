"""Causal, data-driven peer-shock controls for Universal V2 research."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any


@dataclass(frozen=True)
class PeerShockConfig:
    """Tunable correlated-peer stress overlay; disabled by default."""

    enabled: bool = False
    correlation_lookback_sessions: int = 63
    shock_return_sessions: int = 5
    minimum_peer_correlation: float = .60
    peer_shock_return: float = -.06
    minimum_shocked_peers: int = 2
    exposure_multiplier: float = .50

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if name != "enabled" and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if not (21 <= self.correlation_lookback_sessions <= 252 and isinstance(self.correlation_lookback_sessions, int)):
            raise ValueError("Correlation lookback must be an integer from 21 through 252")
        if not (1 <= self.shock_return_sessions <= 21 and isinstance(self.shock_return_sessions, int)):
            raise ValueError("Shock return window must be an integer from 1 through 21")
        if not (.10 <= self.minimum_peer_correlation <= .99 and -.50 <= self.peer_shock_return < 0):
            raise ValueError("Peer correlation or shock return is outside its research range")
        if not (1 <= self.minimum_shocked_peers <= 24 and isinstance(self.minimum_shocked_peers, int)):
            raise ValueError("Minimum shocked peers must be an integer from 1 through 24")
        if not (0 <= self.exposure_multiplier <= 1):
            raise ValueError("Peer-shock exposure multiplier must be in [0, 1]")


def peer_shock_contract(config: PeerShockConfig = PeerShockConfig()) -> dict[str, Any]:
    return {
        "id": "universal_peer_shock_v1",
        "status": "disabled_by_default" if not config.enabled else "active_research_target_overlay",
        "configuration": asdict(config),
        "definition": "For each stock, use only trailing return correlations and completed peer returns; reduce that stock only when the declared number of sufficiently correlated peers cross the shock threshold.",
        "invariants": [
            "Peer relationships are estimated from prices available through the decision close.",
            "A single peer decline cannot de-risk another stock when the configured confirmation count is higher.",
            "The overlay reduces the next-open target; it never creates an intraday fill or a forecast claim.",
            "The overlay is research-only and cannot submit broker orders.",
        ],
    }
