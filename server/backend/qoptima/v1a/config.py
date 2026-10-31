from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

V1A_SPEC_VERSION = "cqc.qoptima.v1a_qrs.v1.6"
V1A_MODEL_SCHEMA = "cqc.qoptima.v1a_model.v1"
V1A_FREEZE_VERSION = "V1A_QRS_1_6_FREEZE"
REGIME_TAIL_VERSION = "REGIME_TAIL_V1"
GATE_B_BOOTSTRAP_VERSION = "GATE_B_BOOTSTRAP_V1"


@dataclass(frozen=True)
class V1AConfig:
    # Latent representation
    latent_states: int = 8
    factor_rank: int = 2
    a_max: float = 0.98
    probability_floor: float = 1e-12
    direction_epsilon: float = 1e-12
    var_epsilon: float = 1e-6
    softmin_temperature: float = 0.02
    probe_count: int = 6
    prototype_temperature: float = 1.0

    # Forecast target / FZ0
    horizon_sessions: int = 21
    tail_alpha: float = 0.05
    es_budget: float = 0.10
    es_activation_max_ratio: float = 2.0
    es_calibration_min: float = 0.25
    es_calibration_max: float = 4.0

    # Training
    learning_rate: float = 1e-3
    weight_decay: float = 1e-4
    ridge_penalty: float = 1e-3
    anchor_penalty: float = 1e-3
    gradient_clip: float = 5.0
    max_epochs: int = 1000
    patience: int = 80
    validation_fraction: float = 0.20
    seed: int = 41

    # Canonical V1A-QRS scoring / covariance policy
    risk_budget_annual: float = 0.15
    risk_floor_ratio: float = 0.10
    covariance_additive_floor: float = 1e-8
    forecast_error_covariance_floor: float = 1e-8
    forecast_error_covariance_window: int = 60
    forecast_error_min_history: int = 12
    quantile_confidence: float = 0.90
    quantile_block_stride: int = 1

    # Exact-diagonal QAOA phase normalization
    qaoa_low_tail_quantile: float = 0.05
    qaoa_phase_global_floor_ratio: float = 0.10
    qaoa_phase_absolute_floor: float = 1e-8
    qaoa_energy_cap: float = 8.0
    qaoa_infeasible_gap: float = 1.0

    # Gate B freeze defaults (research-design constants; not live trading gates)
    gate_b_block_sessions: int = 21
    gate_b_bootstrap_replications: int = 2000
    gate_b_alpha: float = 0.10
    gate_b_effect_size: float = 0.0

    def validate(self) -> None:
        if not (2 <= int(self.latent_states) <= 16):
            raise ValueError("latent_states must be in [2,16]")
        if not (1 <= int(self.factor_rank) <= min(4, int(self.latent_states))):
            raise ValueError("factor_rank must be in [1,min(4,K)]")
        if not (0.0 < float(self.a_max) < 1.0):
            raise ValueError("a_max must be in (0,1)")
        if not (0.0 < float(self.tail_alpha) < 0.5):
            raise ValueError("tail_alpha must be in (0,0.5)")
        if int(self.horizon_sessions) <= 0:
            raise ValueError("horizon_sessions must be positive")
        if float(self.es_budget) <= 0 or float(self.risk_budget_annual) <= 0:
            raise ValueError("economic ES/risk budgets must be positive")
        if not (0.0 < float(self.risk_floor_ratio) <= 1.0):
            raise ValueError("risk_floor_ratio must be in (0,1]")
        if float(self.es_activation_max_ratio) <= 1.0:
            raise ValueError("es_activation_max_ratio must exceed 1")
        if float(self.covariance_additive_floor) <= 0 or float(self.forecast_error_covariance_floor) <= 0:
            raise ValueError("additive covariance floors must be positive")

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> "V1AConfig":
        values = dict(raw or {})
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        obj = cls(**{k: values[k] for k in values if k in allowed})
        obj.validate()
        return obj
