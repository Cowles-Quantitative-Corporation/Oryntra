from __future__ import annotations

import math
import time
import numpy as np

from .base import BackendResult
from ..budget import estimate_future_qpu_usage


class VirtualQPUBackend:
    """QPU-shaped local backend for architecture/timing/cost development.

    The solution search is classical stochastic local search. It intentionally
    exposes QPU-style job metadata and estimated *QPU* execution time, while
    keeping Mac wall-clock time separate. It must never be described as a
    physical quantum computation.
    """

    name = "virtual_qpu"

    def __init__(self, *, price_per_second: float = 1.60):
        self.price_per_second = float(price_per_second)

    def available(self) -> bool:
        return True

    def solve(self, qubo, *, shots: int = 2048, seed: int = 41) -> BackendResult:
        n = int(qubo.num_variables)
        if n < 1:
            return BackendResult(bits=[], energy=float(qubo.constant), backend=self.name, execution_seconds=0.0, metadata={})
        started = time.perf_counter()
        rng = np.random.default_rng(seed)

        # Multiple stochastic restarts + coordinate descent. Fast enough for a Mac,
        # but importantly does not claim to be the quantum algorithm itself.
        restarts = max(8, min(96, int(math.sqrt(max(1, shots)))))
        best = None
        best_energy = float("inf")
        seeds = [np.zeros(n, dtype=int), np.ones(n, dtype=int)]
        seeds.extend(rng.integers(0, 2, size=n, dtype=int) for _ in range(restarts))
        for x0 in seeds:
            x = np.asarray(x0, dtype=int).copy()
            energy = float(qubo.energy(x))
            improved = True
            sweeps = 0
            while improved and sweeps < max(8, min(40, n * 2)):
                improved = False
                sweeps += 1
                order = rng.permutation(n)
                for i in order:
                    x[i] ^= 1
                    candidate = float(qubo.energy(x))
                    if candidate < energy - 1e-12:
                        energy = candidate
                        improved = True
                    else:
                        x[i] ^= 1
            if energy < best_energy:
                best_energy = energy
                best = x.copy()

        wall = time.perf_counter() - started
        depth_proxy = max(8, n * 6)
        estimate = estimate_future_qpu_usage(
            variables=n,
            circuit_depth=depth_proxy,
            shots=int(shots),
            price_per_second=self.price_per_second,
        )
        return BackendResult(
            bits=[int(v) for v in (best if best is not None else np.zeros(n, dtype=int))],
            energy=float(best_energy),
            backend=self.name,
            execution_seconds=float(wall),
            metadata={
                "quantum_circuit": False,
                "qpu_interface_emulation": True,
                "solver": "stochastic_local_search_reference",
                "restarts": int(len(seeds)),
                "mac_wall_seconds": float(wall),
                "virtual_qpu_seconds": float(estimate["estimated_qpu_seconds"]),
                "virtual_qpu_cost_usd": float(estimate["estimated_cost_usd"]),
                "circuit_depth": int(depth_proxy),
                "warning": "Timing/cost emulate a QPU contract; solution search is classical and is not evidence of quantum advantage.",
            },
        )
