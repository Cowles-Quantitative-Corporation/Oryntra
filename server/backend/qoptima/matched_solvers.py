from __future__ import annotations

from typing import Any
import time
import numpy as np


def _energy(qubo, bits: np.ndarray) -> float:
    return float(qubo.energy(np.asarray(bits, dtype=int)))


def exact_qubo(qubo, *, max_variables: int = 24) -> dict[str, Any]:
    n = int(qubo.num_variables)
    if n > int(max_variables):
        return {"available": False, "reason": f"exact solver capped at {max_variables} variables", "solver": "exact_enumeration"}
    started = time.perf_counter()
    best_e = float("inf")
    best_bits = None
    # Chunk enumeration to avoid giant bit matrices.
    total = 1 << n
    chunk = 1 << min(n, 16)
    linear = np.asarray(qubo.linear, dtype=float)
    pairs = list(qubo.quadratic.items())
    for start in range(0, total, chunk):
        end = min(total, start + chunk)
        ids = np.arange(start, end, dtype=np.uint64)
        e = np.full(len(ids), float(qubo.constant), dtype=float)
        for i, c in enumerate(linear):
            if abs(float(c)) > 1e-15:
                e += float(c) * ((ids >> np.uint64(i)) & np.uint64(1))
        for (i, j), c in pairs:
            if abs(float(c)) > 1e-15:
                bi = (ids >> np.uint64(i)) & np.uint64(1)
                bj = (ids >> np.uint64(j)) & np.uint64(1)
                e += float(c) * (bi & bj)
        k = int(np.argmin(e))
        if float(e[k]) < best_e:
            best_e = float(e[k])
            value = int(ids[k])
            best_bits = np.asarray([(value >> i) & 1 for i in range(n)], dtype=int)
    return {
        "available": True,
        "solver": "exact_enumeration",
        "bits": best_bits.tolist(),
        "energy": best_e,
        "wall_seconds": float(time.perf_counter() - started),
        "states": int(total),
    }


def milp_qubo(qubo, *, max_variables: int = 40) -> dict[str, Any]:
    """Solve the exact binary QUBO as a MILP by linearizing x_i*x_j."""
    n = int(qubo.num_variables)
    if n > int(max_variables):
        return {"available": False, "reason": f"MILP solver capped at {max_variables} QUBO variables", "solver": "scipy_milp"}
    try:
        from scipy.optimize import milp, LinearConstraint, Bounds
        from scipy.sparse import coo_matrix
    except Exception as exc:
        return {"available": False, "reason": f"scipy.milp unavailable: {exc}", "solver": "scipy_milp"}
    started = time.perf_counter()
    pairs = [(int(i), int(j), float(v)) for (i, j), v in qubo.quadratic.items() if abs(float(v)) > 1e-15]
    p = len(pairs)
    size = n + p
    c = np.zeros(size, dtype=float)
    c[:n] = np.asarray(qubo.linear, dtype=float)
    for k, (_i, _j, value) in enumerate(pairs):
        c[n + k] = value

    # y <= xi; y <= xj; y >= xi + xj - 1.
    rows, cols, data, lb, ub = [], [], [], [], []
    r = 0
    for k, (i, j, _value) in enumerate(pairs):
        y = n + k
        # y - xi <= 0
        rows += [r, r]; cols += [y, i]; data += [1.0, -1.0]; lb.append(-np.inf); ub.append(0.0); r += 1
        # y - xj <= 0
        rows += [r, r]; cols += [y, j]; data += [1.0, -1.0]; lb.append(-np.inf); ub.append(0.0); r += 1
        # y - xi - xj >= -1
        rows += [r, r, r]; cols += [y, i, j]; data += [1.0, -1.0, -1.0]; lb.append(-1.0); ub.append(np.inf); r += 1
    constraints = None
    if r:
        A = coo_matrix((data, (rows, cols)), shape=(r, size)).tocsr()
        constraints = LinearConstraint(A, np.asarray(lb), np.asarray(ub))
    try:
        res = milp(
            c=c,
            integrality=np.ones(size, dtype=int),
            bounds=Bounds(np.zeros(size), np.ones(size)),
            constraints=constraints,
            options={"time_limit": 30.0, "mip_rel_gap": 1e-9},
        )
    except Exception as exc:
        return {"available": False, "reason": f"MILP failure: {type(exc).__name__}: {exc}", "solver": "scipy_milp"}
    if res.x is None:
        return {
            "available": False,
            "reason": str(getattr(res, "message", "no MILP solution")),
            "solver": "scipy_milp",
            "wall_seconds": float(time.perf_counter() - started),
        }
    bits = np.rint(np.asarray(res.x[:n], dtype=float)).astype(int)
    energy = _energy(qubo, bits)
    return {
        "available": True,
        "solver": "scipy_milp",
        "bits": bits.tolist(),
        "energy": float(energy),
        "wall_seconds": float(time.perf_counter() - started),
        "status": int(getattr(res, "status", -1)),
        "message": str(getattr(res, "message", "")),
    }


def heuristic_qubo(
    qubo,
    *,
    seed: int = 41,
    restarts: int = 32,
    sweeps: int = 80,
) -> dict[str, Any]:
    """Multi-start one-bit local search with annealed random perturbations."""
    n = int(qubo.num_variables)
    started = time.perf_counter()
    rng = np.random.default_rng(int(seed))
    best_bits = np.zeros(n, dtype=int)
    best_e = _energy(qubo, best_bits)
    evaluations = 1
    for restart in range(max(1, int(restarts))):
        x = rng.integers(0, 2, size=n, dtype=int)
        e = _energy(qubo, x); evaluations += 1
        temperature = max(1e-6, abs(e) * 0.02 + 0.01)
        for sweep in range(max(1, int(sweeps))):
            improved = False
            for i in rng.permutation(n):
                y = x.copy(); y[i] ^= 1
                ey = _energy(qubo, y); evaluations += 1
                delta = ey - e
                if delta < 0 or rng.random() < np.exp(-max(delta, 0.0) / max(temperature, 1e-9)):
                    x, e = y, ey
                    improved = improved or delta < 0
            temperature *= 0.92
            if not improved and temperature < 1e-4:
                break
        if e < best_e:
            best_bits, best_e = x.copy(), float(e)
    return {
        "available": True,
        "solver": "multistart_local_anneal",
        "bits": best_bits.tolist(),
        "energy": float(best_e),
        "wall_seconds": float(time.perf_counter() - started),
        "evaluations": int(evaluations),
        "restarts": int(restarts),
    }


def compare_matched_solvers(qubo, qaoa_result: dict[str, Any] | None = None, *, seed: int = 41, exact_max_variables: int = 18, milp_max_variables: int = 40) -> dict[str, Any]:
    exact = exact_qubo(qubo, max_variables=exact_max_variables)
    milp = milp_qubo(qubo, max_variables=milp_max_variables)
    heuristic = heuristic_qubo(qubo, seed=seed)
    baselines = [x for x in (exact, milp, heuristic) if x.get("available")]
    best = min(baselines, key=lambda x: float(x["energy"])) if baselines else None
    out = {
        "exact": exact,
        "mixed_integer": milp,
        "heuristic": heuristic,
        "best_classical_energy": float(best["energy"]) if best else None,
        "best_classical_solver": best.get("solver") if best else None,
    }
    if qaoa_result is not None and qaoa_result.get("energy") is not None:
        qe = float(qaoa_result["energy"])
        be = out["best_classical_energy"]
        out["qaoa"] = dict(qaoa_result)
        out["qaoa_objective_gap"] = None if be is None else float(qe - be)
        out["qaoa_matches_best_within_1e8"] = None if be is None else bool(abs(qe - be) <= 1e-8)
    return out
