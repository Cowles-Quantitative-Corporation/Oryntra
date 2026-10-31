"""Atomic, content-bound research replay checkpoints (no pickle deserialization).

A commit represents an entire scheduled rebalance: all paired portfolios, their
realized windows, and persistence counters advance together. A process lost
before the commit repeats only that uncommitted rebalance with the original seed.
"""
from __future__ import annotations

import base64
from datetime import date, datetime
import gzip
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import tempfile
from functools import wraps
from typing import Any

import numpy as np
import pandas as pd


SCHEMA = "qoptima_replay_checkpoint_v1"
MAX_DOCUMENT_BYTES = 256 * 1024 * 1024
_TAG = "__qoptima_checkpoint_type__"


class ReplayCheckpointError(ValueError):
    """Invalid, incompatible or incomplete checkpoint; never a cash-policy event."""


def checkpoint_run_lock(function):
    """Hold an advisory single-writer lock; a killed process releases it."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        directory = kwargs.get("replay_checkpoint_directory")
        if not directory:
            return function(*args, **kwargs)
        import fcntl
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        with (root / ".replay.lock").open("a+b") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ReplayCheckpointError("This replay checkpoint already has an active writer") from exc
            try:
                return function(*args, **kwargs)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return wrapped


def _encode(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, np.generic):
        return _encode(value.item())
    if isinstance(value, float):
        return value if np.isfinite(value) else {_TAG: "float", "v": value.hex()}
    if isinstance(value, pd.DataFrame):
        return {_TAG: "frame", "index": _encode(value.index), "columns": _encode(value.columns),
                "data": [_encode(value.iloc[:, i].to_numpy()) for i in range(len(value.columns))],
                "attrs": _encode(value.attrs)}
    if isinstance(value, pd.Series):
        return {_TAG: "series", "index": _encode(value.index), "data": _encode(value.to_numpy()),
                "name": _encode(value.name)}
    if isinstance(value, pd.DatetimeIndex):
        return {_TAG: "datetime_index", "values": _encode(value.as_unit("ns").asi8),
                "timezone": str(value.tz) if value.tz is not None else None, "name": _encode(value.name)}
    if isinstance(value, pd.Index):
        return {_TAG: "index", "values": _encode(value.tolist()), "name": _encode(value.name)}
    if isinstance(value, np.ndarray):
        if value.dtype.hasobject:
            return {_TAG: "object_array", "shape": list(value.shape), "values": _encode(value.tolist())}
        return {_TAG: "array", "dtype": value.dtype.str, "shape": list(value.shape),
                "bytes": base64.b64encode(value.tobytes(order="C")).decode("ascii")}
    if isinstance(value, dict):
        if _TAG not in value and all(isinstance(k, str) for k in value):
            return {k: _encode(v) for k, v in value.items()}
        items = [(_encode(k), _encode(v)) for k, v in value.items()]
        items.sort(key=lambda pair: _json_bytes(pair[0]))
        return {_TAG: "dict", "items": items}
    if isinstance(value, (tuple, set, frozenset)):
        items = [_encode(v) for v in value]
        if isinstance(value, (set, frozenset)):
            items.sort(key=_json_bytes)
        return {_TAG: "tuple" if isinstance(value, tuple) else "set", "items": items}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return {_TAG: "timestamp", "v": pd.Timestamp(value).isoformat()}
    if isinstance(value, Path):
        return {_TAG: "path", "v": str(value)}
    raise ReplayCheckpointError(f"Unsupported checkpoint value type: {type(value).__name__}")


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _decode(item)
        return value
    if not isinstance(value, dict):
        return value
    tag = value.get(_TAG)
    if tag is None:
        for key, item in value.items():
            value[key] = _decode(item)
        return value
    if tag == "float":
        return float.fromhex(value["v"])
    if tag == "dict":
        return {_decode(k): _decode(v) for k, v in value["items"]}
    if tag in {"tuple", "set"}:
        return (tuple if tag == "tuple" else set)(_decode(v) for v in value["items"])
    if tag == "array":
        dtype = np.dtype(value["dtype"])
        if dtype.hasobject:
            raise ReplayCheckpointError("Checkpoint binary arrays cannot contain Python objects")
        return np.frombuffer(base64.b64decode(value["bytes"], validate=True), dtype=dtype).reshape(value["shape"]).copy()
    if tag == "object_array":
        return np.asarray(_decode(value["values"]), dtype=object).reshape(value["shape"])
    if tag == "timestamp":
        return pd.Timestamp(value["v"])
    if tag == "path":
        return Path(value["v"])
    if tag == "datetime_index":
        idx = pd.to_datetime(_decode(value["values"]), unit="ns", utc=value["timezone"] is not None)
        if value["timezone"] is not None:
            idx = idx.tz_convert(value["timezone"])
        return pd.DatetimeIndex(idx, name=_decode(value["name"]))
    if tag == "index":
        return pd.Index(_decode(value["values"]), name=_decode(value["name"]))
    if tag == "series":
        return pd.Series(_decode(value["data"]), index=_decode(value["index"]), name=_decode(value["name"]))
    if tag == "frame":
        data = {i: _decode(col) for i, col in enumerate(value["data"])}
        frame = pd.DataFrame(data, index=_decode(value["index"]))
        frame.columns = _decode(value["columns"])
        frame.attrs = _decode(value["attrs"])
        return frame
    raise ReplayCheckpointError(f"Unknown checkpoint data tag: {tag}")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _json_chunks(value: Any):
    chunks, size = [], 0
    for chunk in json.JSONEncoder(sort_keys=True, separators=(",", ":"), allow_nan=False).iterencode(value):
        chunks.append(chunk)
        size += len(chunk)
        if size >= 65536:
            yield "".join(chunks).encode("utf-8")
            chunks, size = [], 0
    if chunks:
        yield "".join(chunks).encode("utf-8")


def _encoded_digest(value: Any) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    for block in _json_chunks(value):
        digest.update(block)
        size += len(block)
    return digest.hexdigest(), size


def content_digest(value: Any) -> str:
    return _encoded_digest(_encode(value))[0]


def _atomic_document(path: Path, value: Any) -> None:
    payload = _encode(value)
    digest, size = _encoded_digest(payload)
    if size > MAX_DOCUMENT_BYTES:
        raise ReplayCheckpointError("Checkpoint exceeds the bounded 256 MiB document size")
    header = _json_bytes({"schema": SCHEMA, "sha256": digest})
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed:
                compressed.write(header + b"\n")
                for block in _json_chunks(payload):
                    compressed.write(block)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_document(path: Path) -> Any:
    try:
        with gzip.open(path, "rb") as source:
            header = json.loads(source.readline(4096))
            payload = source.read(MAX_DOCUMENT_BYTES + 1)
        if len(payload) > MAX_DOCUMENT_BYTES or header.get("schema") != SCHEMA:
            raise ReplayCheckpointError("Invalid or oversized checkpoint document")
        if hashlib.sha256(payload).hexdigest() != header.get("sha256"):
            raise ReplayCheckpointError("Checkpoint content checksum mismatch")
        return _decode(json.loads(payload))
    except ReplayCheckpointError:
        raise
    except Exception as exc:
        raise ReplayCheckpointError(f"Cannot verify checkpoint {path.name}: {exc}") from exc


def _verify_document(path: Path, expected_digest: str) -> None:
    """Verify immutable input snapshots without materializing a second tree."""
    try:
        with gzip.open(path, "rb") as source:
            header = json.loads(source.readline(4096))
            digest, size = hashlib.sha256(), 0
            while block := source.read(1024 * 1024):
                digest.update(block)
                size += len(block)
                if size > MAX_DOCUMENT_BYTES:
                    raise ReplayCheckpointError("Oversized input checkpoint")
        if header.get("schema") != SCHEMA or header.get("sha256") != expected_digest or digest.hexdigest() != expected_digest:
            raise ReplayCheckpointError("Immutable replay input checksum mismatch")
    except ReplayCheckpointError:
        raise
    except Exception as exc:
        raise ReplayCheckpointError(f"Cannot verify immutable replay input {path.name}: {exc}") from exc


def code_runtime_identity() -> dict[str, Any]:
    backend = Path(__file__).resolve().parents[1]
    code = {str(path.relative_to(backend)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(backend.rglob("*.py")) if "__pycache__" not in path.parts}
    versions = {}
    for name in ("numpy", "pandas", "scipy", "torch", "qiskit", "qiskit-aer"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return {"source_files": code, "python": platform.python_version(), "packages": versions,
            "platform": platform.platform(),
            "threads": {name: os.environ.get(name) for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")}}


def artifact_identity(schedule: list[dict[str, Any]]) -> dict[str, str]:
    result = {}
    for row in schedule:
        path = Path(str(row.get("artifact_path") or ""))
        if not path.is_file():
            raise ReplayCheckpointError(f"Cannot checkpoint absent V1A artifact: {path}")
        result[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def validate_replay_state(state: dict[str, Any], *, returns: pd.DataFrame,
                          anchors: list[int], cursor: int, rebalance: int, paired: bool, qpo_only: bool = False) -> None:
    required = {"risk_state", "strategy_returns", "canonical_qoptima_returns", "classical_returns", "base_returns",
        "current_symbols", "current_q", "current_q_canonical", "current_c", "current_b", "known_q", "known_c",
        "known_q_canonical", "rebalances", "skipped_universe", "missing_future_cells", "legacy_cash_reset_count",
        "legacy_qoptima_cash_reset_count", "rejection_events", "forced_cash_sessions"}
    if not isinstance(state, dict) or set(state) != required:
        raise ReplayCheckpointError("Replay checkpoint state fields are incomplete or unknown")
    symbols = state["current_symbols"]
    if len(set(symbols)) != len(symbols) or not set(symbols) <= set(returns.columns):
        raise ReplayCheckpointError("Checkpoint portfolio symbols are not aligned to source returns")
    for name in ("current_q", "current_q_canonical", "current_c", "current_b", "known_q", "known_c", "known_q_canonical"):
        vector = state[name]
        if not isinstance(vector, np.ndarray) or vector.shape != (len(symbols),) or not np.isfinite(vector).all():
            raise ReplayCheckpointError(f"Checkpoint {name} is not a finite aligned weight vector")
    for name in ("missing_future_cells", "legacy_cash_reset_count", "legacy_qoptima_cash_reset_count", "forced_cash_sessions"):
        if isinstance(state[name], bool) or not isinstance(state[name], int) or state[name] < 0:
            raise ReplayCheckpointError(f"Checkpoint {name} is not a nonnegative count")
    rows = state["rebalances"]
    committed = {str(returns.index[anchor]): anchor for anchor in anchors[:cursor]}
    dates = [row["decision_date"] for row in rows]
    if len(set(dates)) != len(dates) or any(day not in committed for day in dates):
        raise ReplayCheckpointError("Checkpoint rebalance audit exceeds or duplicates committed anchors")
    if [committed[day] for day in dates] != sorted(committed[day] for day in dates):
        raise ReplayCheckpointError("Checkpoint rebalance audit is out of order")
    for name in ("strategy_returns", "canonical_qoptima_returns", "classical_returns", "base_returns"):
        blocks = state[name]
        expected_rows = (0 if qpo_only and name != "strategy_returns" else
                         len(rows) if name != "canonical_qoptima_returns" or paired else 0)
        if len(blocks) != expected_rows:
            raise ReplayCheckpointError(f"Checkpoint {name} has incomplete paired holding windows")
        for row, block in zip(rows, blocks):
            anchor = committed[row["decision_date"]]
            expected = returns.index[anchor:min(len(returns), anchor+rebalance)]
            if not isinstance(block, pd.Series) or not block.index.equals(expected) or not np.isfinite(block.to_numpy()).all():
                raise ReplayCheckpointError(f"Checkpoint {name} holding dates/returns do not match committed source sessions")
    if state["forced_cash_sessions"] > sum(len(block) for block in state["strategy_returns"]):
        raise ReplayCheckpointError("Checkpoint forced-cash count exceeds committed scored sessions")


class ReplayCheckpoint:
    def __init__(self, directory: str | Path, *, slot: str, inputs: dict[str, Any], resume: bool = True):
        self.directory = Path(directory) / slot
        component_hashes = {key: content_digest(value) for key, value in inputs.items()}
        self.identity = content_digest(component_hashes)
        self.manifest_path = self.directory / "inputs.json.gz"
        self.state_path = self.directory / "latest.json.gz"
        self.resumed_anchors = 0
        self.committed_anchors = 0
        self.status = "prepared"
        if self.manifest_path.exists():
            manifest = _read_document(self.manifest_path)
            if manifest.get("identity") != self.identity or manifest.get("component_sha256") != component_hashes:
                raise ReplayCheckpointError("Replay checkpoint inputs changed (data, configuration, forecasts, artifacts or code); use a distinct study directory")
            if not resume and self.state_path.exists():
                raise ReplayCheckpointError("Existing replay checkpoint requires explicit resume or a distinct study directory")
            for digest in set(component_hashes.values()):
                _verify_document(self.directory / "inputs" / f"{digest}.json.gz", digest)
        else:
            if self.state_path.exists():
                raise ReplayCheckpointError("Replay checkpoint state exists without its input manifest")
            for key, digest in component_hashes.items():
                path = self.directory / "inputs" / f"{digest}.json.gz"
                if path.exists():
                    _verify_document(path, digest)
                else:
                    _atomic_document(path, inputs[key])
            _atomic_document(self.manifest_path, {"identity": self.identity, "component_sha256": component_hashes,
                "input_storage": "immutable_content_addressed_compressed_snapshots"})

    def load(self, *, anchors: list[int]) -> dict[str, Any] | None:
        if not self.state_path.exists():
            return None
        payload = _read_document(self.state_path)
        if payload.get("identity") != self.identity or payload.get("anchors") != anchors:
            raise ReplayCheckpointError("Replay checkpoint state identity/anchor schedule mismatch")
        cursor = payload.get("next_anchor_index")
        if isinstance(cursor, bool) or not isinstance(cursor, int) or not 0 <= cursor <= len(anchors):
            raise ReplayCheckpointError("Replay checkpoint has an invalid next-anchor cursor")
        if payload.get("status") not in {"rebalance_committed", "path_complete"}:
            raise ReplayCheckpointError("Replay checkpoint has an invalid completion status")
        if payload["status"] == "path_complete" and cursor != len(anchors):
            raise ReplayCheckpointError("Replay checkpoint claims completion with missing anchors")
        self.resumed_anchors = cursor
        self.committed_anchors = cursor
        self.status = payload["status"]
        return payload

    def save(self, *, next_anchor_index: int, anchors: list[int], state: dict[str, Any], complete: bool = False) -> None:
        _atomic_document(self.state_path, {"identity": self.identity, "anchors": anchors,
            "next_anchor_index": int(next_anchor_index), "status": "path_complete" if complete else "rebalance_committed", "state": state})
        self.committed_anchors = int(next_anchor_index)
        self.status = "path_complete" if complete else "rebalance_committed"

    def audit(self, *, completed_anchors: int | None = None) -> dict[str, Any]:
        return {"enabled": True, "identity_sha256": self.identity, "resumed_anchors": self.resumed_anchors,
                "completed_anchors": self.committed_anchors, "status": self.status, "state_file": str(self.state_path),
                "commit_policy": "all_paired_portfolios_and_persistence_after_realized_window",
                "input_validation": "exact_content_code_runtime_artifact_identity"}
