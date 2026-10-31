from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..encoding.qubo import MoveQUBO


@dataclass
class BackendResult:
    bits: list[int]
    energy: float
    backend: str
    execution_seconds: float
    metadata: dict[str, Any] = field(default_factory=dict)


class QOptimaBackend(Protocol):
    name: str

    def available(self) -> bool: ...
    def solve(self, qubo: MoveQUBO, *, shots: int = 2048, seed: int = 41) -> BackendResult: ...
