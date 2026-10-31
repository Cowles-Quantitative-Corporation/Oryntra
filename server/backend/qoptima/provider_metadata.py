from __future__ import annotations

"""Provider metadata for QOptima planning.

A shipped snapshot is only a dated planning fallback.  When IBM Runtime is
available, backend sizes/configuration are refreshed from the provider without
executing a quantum job.  Paid execution remains outside this module.
"""

from pathlib import Path
from typing import Any
import json
import os

_DATA = Path(__file__).resolve().parent / "provider_snapshot.json"


def load_ibm_snapshot() -> dict[str, Any]:
    data = json.loads(_DATA.read_text())
    data["metadata_source"] = "verified_public_snapshot"
    return data


def _runtime_service():
    try:
        from qiskit_ibm_runtime import QiskitRuntimeService
    except Exception:
        return None
    token = os.getenv("IBM_QUANTUM_API_KEY") or os.getenv("QISKIT_IBM_TOKEN")
    instance = os.getenv("IBM_QUANTUM_CRN") or os.getenv("QISKIT_IBM_INSTANCE")
    if not token:
        return None
    kwargs = {"channel": "ibm_quantum_platform", "token": token}
    if instance:
        kwargs["instance"] = instance
    try:
        return QiskitRuntimeService(**kwargs)
    except Exception:
        return None


def current_ibm_metadata(*, refresh_runtime: bool = True) -> dict[str, Any]:
    data = load_ibm_snapshot()
    warnings: list[str] = []
    refreshed: dict[str, Any] = {}
    if refresh_runtime:
        service = _runtime_service()
        if service is None:
            warnings.append("IBM Runtime credentials/package unavailable; using the dated official public snapshot for backend sizes.")
        else:
            try:
                for backend in service.backends(simulator=False, operational=True):
                    name = str(getattr(backend, "name", "") or "")
                    nq = getattr(backend, "num_qubits", None)
                    if nq is None:
                        try:
                            nq = backend.configuration().num_qubits
                        except Exception:
                            continue
                    refreshed[name] = {"programmable_qubits": int(nq)}
            except Exception as exc:
                warnings.append(f"IBM Runtime metadata refresh failed: {exc}; using the verified snapshot.")
    if refreshed:
        data["runtime_backends"] = refreshed
        data["metadata_source"] = "ibm_runtime_plus_verified_public_pricing_snapshot"
    data["warnings"] = warnings
    return data


def planning_prices(metadata: dict[str, Any] | None = None) -> dict[str, float]:
    md = metadata or current_ibm_metadata(refresh_runtime=False)
    raw = md.get("pricing_usd_per_second") or {}
    return {k: float(v) for k, v in raw.items()}


def processor_capacity(metadata: dict[str, Any] | None = None) -> dict[str, int]:
    md = metadata or current_ibm_metadata(refresh_runtime=False)
    out: dict[str, int] = {}
    for key, row in (md.get("backends") or {}).items():
        try:
            out[key] = int(row["programmable_qubits"])
        except Exception:
            continue
    for key, row in (md.get("runtime_backends") or {}).items():
        try:
            out[key] = int(row["programmable_qubits"])
        except Exception:
            continue
    return out


def get_runtime_backend(name: str):
    """Return a live IBM backend object for offline transpilation only, never execution."""
    service = _runtime_service()
    if service is None:
        return None
    try:
        return service.backend(str(name))
    except Exception:
        return None
