from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional


class ResearchLedger:
    """Append-only hash-chained JSONL evidence ledger for Beta studies."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _last_hash(self) -> str:
        if not self.path.exists():
            return "0" * 64
        last = ""
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    last = line
        if not last:
            return "0" * 64
        return json.loads(last)["entry_hash"]

    def append(self, event: str, payload: Mapping[str, Any]) -> dict:
        previous = self._last_hash()
        core = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "payload": payload,
            "previous_hash": previous,
        }
        encoded = json.dumps(core, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        entry_hash = hashlib.sha256(encoded).hexdigest()
        entry = {**core, "entry_hash": entry_hash}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
        return entry

    def verify(self) -> dict:
        previous = "0" * 64
        count = 0
        if not self.path.exists():
            return {"valid": True, "entries": 0}
        with self.path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                entry = json.loads(line)
                if entry.get("previous_hash") != previous:
                    return {"valid": False, "entries": count, "line": line_no, "reason": "previous_hash mismatch"}
                core = {k: entry[k] for k in ("timestamp", "event", "payload", "previous_hash")}
                encoded = json.dumps(core, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
                expected = hashlib.sha256(encoded).hexdigest()
                if entry.get("entry_hash") != expected:
                    return {"valid": False, "entries": count, "line": line_no, "reason": "entry_hash mismatch"}
                previous = expected
                count += 1
        return {"valid": True, "entries": count, "last_hash": previous}
