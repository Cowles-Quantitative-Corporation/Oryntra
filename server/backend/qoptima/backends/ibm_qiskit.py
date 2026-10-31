from __future__ import annotations

import os
from typing import Any


def ibm_runtime_status() -> dict[str, Any]:
    try:
        import qiskit_ibm_runtime  # noqa: F401
        package = True
    except Exception:
        package = False
    api_key = bool(os.getenv("QOPTIMA_IBM_API_KEY", "").strip() or os.getenv("IBM_QUANTUM_API_KEY", "").strip())
    instance = bool(os.getenv("QOPTIMA_IBM_INSTANCE", "").strip() or os.getenv("IBM_QUANTUM_CRN", "").strip())
    allow_real = os.getenv("QOPTIMA_ALLOW_REAL_QPU", "false").strip().lower() in {"1", "true", "yes", "on"}
    return {
        "runtime_package_installed": package,
        "api_key_configured": api_key,
        "instance_configured": instance,
        "real_qpu_enabled": allow_real,
        "credentials_ready_for_future_hardware": bool(package and api_key and instance),
        "ready": False,
        "execution_supported_in_v02": False,
        "note": "v0.2 intentionally does not submit paid QPU jobs; this adapter reserves the hardware boundary for a later gated release.",
    }
