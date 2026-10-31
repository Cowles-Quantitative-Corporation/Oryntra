from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from .config import V1A_FREEZE_VERSION, V1A_SPEC_VERSION


def freeze_spec(spec: dict[str, Any], path: str | Path, *, code_hash: str | None = None) -> dict[str, Any]:
    payload = {
        "freeze_version": V1A_FREEZE_VERSION,
        "spec_version": V1A_SPEC_VERSION,
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "spec": spec,
        "code_hash": code_hash,
        "holdout_policy": "once opened, the confirmatory period is spent for all revisions of this frozen spec",
        "gate_evaluation_order": "Gate A and Gate B are both evaluated regardless of the result of either gate",
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    payload["freeze_sha256"] = hashlib.sha256(raw).hexdigest()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def spend_holdout(ledger_path: str | Path, *, freeze_sha256: str, period_id: str, notes: str = "") -> dict[str, Any]:
    path = Path(ledger_path)
    if path.exists():
        ledger = json.loads(path.read_text(encoding="utf-8"))
    else:
        ledger = {"schema": "cqc.qoptima.v1a_holdout_ledger.v1", "entries": []}
    entry = {
        "period_id": str(period_id),
        "freeze_sha256": str(freeze_sha256),
        "spent_at_utc": datetime.now(timezone.utc).isoformat(),
        "notes": str(notes),
    }
    ledger.setdefault("entries", []).append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")
    return entry
