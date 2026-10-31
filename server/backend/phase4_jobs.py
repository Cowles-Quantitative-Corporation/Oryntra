"""Whitelisted local research-job runner for the private Oryntra Control app."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from typing import Any

from .database import get_connection
from .phase4_control import DEFAULT_TEST_SUITES
from .control_research import run_interactive_backtest, run_current_market_snapshot, run_beta_incremental_backtest


_ROOT = Path(__file__).resolve().parents[1]
_RUN_ROOT = _ROOT / "data" / "control_runs"
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="oryntra-control-job")


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _safe_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _process_resource_snapshot() -> dict[str, int | float]:
    """Cheap process-local observations; they do not diagnose a prior exit."""
    snapshot: dict[str, int | float] = {}
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        # Linux reports KiB; macOS reports bytes. Normalize the public field.
        snapshot["process_peak_rss_kib"] = int(usage.ru_maxrss / 1024 if sys.platform == "darwin" else usage.ru_maxrss)
        snapshot["process_user_cpu_seconds"] = round(usage.ru_utime, 6)
        snapshot["process_system_cpu_seconds"] = round(usage.ru_stime, 6)
    except (ImportError, OSError, ValueError):
        pass
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                snapshot["process_rss_kib"] = int(line.split()[1])
                break
    except (OSError, ValueError, IndexError):
        pass
    return snapshot


def _atomic_checkpoint_json(path: Path, value: Any, *, replace: bool = False) -> str:
    """Publish a complete JSON file, never overwrite an immutable row snapshot."""
    text = _safe_json(value) + "\n"
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)  # Atomic create; an existing row is never replaced.
            temporary.unlink()
        if hasattr(os, "O_DIRECTORY"):
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _ControlRowCheckpoints:
    """Append-only row results plus a recoverable manifest for one job attempt."""

    def __init__(self, job_id: int, spec: dict[str, Any]):
        self.job_id = int(job_id)
        self.spec = spec
        models = spec.get("models") or [spec.get("alpha_model") or "tba8"]
        self.requested_models = list(dict.fromkeys(str(value).strip().lower() for value in ([models] if isinstance(models, str) else models) if str(value).strip()))
        self.attempt_id = uuid.uuid4().hex
        self.directory = _RUN_ROOT / f"job_{job_id}_interactive_backtest_{self.attempt_id}.checkpoints"
        self.directory.mkdir(parents=True, exist_ok=False)
        self.manifest_path = self.directory / "manifest.json"
        self.rows: dict[int, dict[str, Any]] = {}
        self.context: dict[str, Any] = {}
        self.manifest = {"schema": "control_row_checkpoints_v1", "job_id": self.job_id,
                         "attempt_id": self.attempt_id, "created_at": _now()+"Z",
                         "requested_models": self.requested_models,
                         "spec_sha256": hashlib.sha256(_safe_json(spec).encode()).hexdigest(),
                         "matrix_complete": False, "rows": []}
        _atomic_checkpoint_json(self.manifest_path, self.manifest)

    def partial_result(self) -> dict[str, Any]:
        rows = [self.rows[index] for index in sorted(self.rows)]
        captured_ids = {row["model"]["id"] for row in rows}
        return {"status": "partial", "matrix_complete": False,
                "models": rows, "requested_model_count": len(self.requested_models),
                "pending_model_ids": [model for model in self.requested_models if model not in captured_ids],
                "completed_model_count": sum(row.get("status") == "completed" for row in rows),
                "blocked_model_count": sum(row.get("status") == "blocked" for row in rows),
                "evaluation": self.context.get("evaluation"),
                "configuration": {key: self.spec.get(key) for key in (
                    "models", "construction", "risk_model", "evaluation_period", "provider", "risk_free_mode",
                    "risk_free_annual_pct", "pre_gamma_beta_source", "pre_gamma_rejection_policy")},
                "row_checkpoints": {"manifest_path": str(self.manifest_path), "attempt_id": self.attempt_id,
                                    "persisted_row_count": len(rows)},
                "qualification": "Completed individual rows are recoverable; the requested matrix has not finished. No missing row is treated as completed."}

    def capture(self, row: dict[str, Any], context: dict[str, Any]) -> None:
        index = int(context["model_index"])
        model_id = str(row["model"]["id"])
        if index in self.rows or index < 1 or index > len(self.requested_models) or self.requested_models[index-1] != model_id:
            raise ValueError("Row checkpoint identity is duplicate or does not match the requested model order")
        if row.get("status") not in {"completed", "blocked"}:
            raise ValueError("Only a returned completed/blocked row can be checkpointed")
        safe_model = "".join(char if char.isalnum() or char in "_-" else "_" for char in model_id)
        path = self.directory / f"row_{index:03d}_{safe_model}.json"
        payload = {"schema": "control_row_checkpoint_v1", "job_id": self.job_id, "attempt_id": self.attempt_id,
                   "captured_at": _now()+"Z", "matrix_complete": False, "row": row, "context": context}
        digest = _atomic_checkpoint_json(path, payload)
        self.rows[index] = row
        self.context = context
        self.manifest["rows"].append({"model_id": model_id, "model_index": index, "status": row["status"],
                                      "path": str(path), "sha256": digest})
        self.manifest["updated_at"] = _now()+"Z"
        _atomic_checkpoint_json(self.manifest_path, self.manifest, replace=True)
        conn = get_connection()
        try:
            cursor = conn.execute("UPDATE phase4_jobs SET result_json=? WHERE id=? AND status='RUNNING'",
                                  (_safe_json(self.partial_result()), self.job_id))
            if cursor.rowcount != 1:
                raise ValueError("Job is no longer RUNNING; durable row saved without overwriting its terminal record")
            conn.commit()
        finally:
            conn.close()

    def finish(self, result: dict[str, Any]) -> None:
        final_path = self.directory / "matrix-result.json"
        result["row_checkpoints"] = {"manifest_path": str(self.manifest_path), "attempt_id": self.attempt_id,
                                     "persisted_row_count": len(self.rows), "matrix_result_path": str(final_path)}
        digest = _atomic_checkpoint_json(final_path, result)
        self.manifest.update(matrix_complete=True, finished_at=_now()+"Z",
                             matrix_result={"path": str(final_path), "sha256": digest})
        _atomic_checkpoint_json(self.manifest_path, self.manifest, replace=True)


def _insert_job(user_id: int, suite_id: str, spec: dict[str, Any]) -> int:
    suite = DEFAULT_TEST_SUITES[suite_id]
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO phase4_jobs(user_id,job_type,label,spec_json,status) VALUES (?,?,?,?, 'QUEUED')",
            (int(user_id), suite["kind"], suite["label"], _safe_json({"suite_id": suite_id, **spec})),
        )
        job_id = int(cur.lastrowid)
        conn.commit()
        return job_id
    finally:
        conn.close()


def _paths(job_id: int, suite_id: str) -> tuple[Path, Path]:
    _RUN_ROOT.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in suite_id)
    return _RUN_ROOT / f"job_{job_id}_{safe}.log", _RUN_ROOT / f"job_{job_id}_{safe}.json"


def _validate_existing_path(value: str, label: str) -> Path:
    path = Path(str(value or "")).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"{label} does not exist: {path}")
    return path


def _command(job_id: int, suite_id: str, spec: dict[str, Any]) -> tuple[list[str], Path | None]:
    suite = DEFAULT_TEST_SUITES[suite_id]
    if suite["kind"] == "pytest":
        return [sys.executable, "-m", "pytest", "-q", *suite["targets"]], None
    if suite["kind"] in {"interactive_backtest", "current_market", "beta_incremental_backtest"}:
        return [], None
    if suite["kind"] not in {"study", "custom_study"}:
        raise ValueError("Unsupported job kind")
    bars = _validate_existing_path(spec.get("bars_csv", ""), "Frozen bars CSV")
    risk_free = _validate_existing_path(spec.get("risk_free_csv", ""), "Risk-free CSV")
    _log, output = _paths(job_id, suite_id)
    command = [
        sys.executable, suite["tool"], "--bars-csv", str(bars), "--risk-free-csv", str(risk_free),
        "--output", str(output),
    ]
    if suite["kind"] == "custom_study":
        command += [
            "--alpha-model", str(spec.get("alpha_model", "tba8")),
            "--construction", str(spec.get("construction", "phase15")),
            "--risk-model", str(spec.get("risk_model", "v203")),
            "--phase3", "on" if bool(spec.get("phase3_enabled", True)) else "off",
            "--benchmark", str(spec.get("benchmark", "balanced_global_equal")),
        ]
    if spec.get("declared_years"):
        command += ["--declared-years", str(spec["declared_years"])]
    if spec.get("evidence_label") and suite_id == "phase3_branches":
        command += ["--evidence-label", str(spec["evidence_label"])]
    return command, output


def _run(job_id: int, suite_id: str, spec: dict[str, Any]) -> None:
    checkpoints = None
    log_path, _default_output = _paths(job_id, suite_id)
    conn = get_connection()
    try:
        conn.execute("UPDATE phase4_jobs SET status='RUNNING',started_at=?,log_path=? WHERE id=?", (_now(), str(log_path), int(job_id)))
        conn.commit()
    finally:
        conn.close()
    try:
        suite = DEFAULT_TEST_SUITES[suite_id]
        if suite["kind"] in {"interactive_backtest", "current_market", "beta_incremental_backtest"}:
            with log_path.open("w", encoding="utf-8") as handle:
                handle.write(f"Starting {suite['label']}\n")
                handle.flush()
                if suite["kind"] == "interactive_backtest":
                    progress_sequence = 0
                    progress_started = time.monotonic()
                    def persist_progress(event):
                        nonlocal progress_sequence
                        progress_sequence += 1
                        # Keep identifiers and scalar advancement only, not records,
                        # credentials, forecasts, candidate weights or raw provenance.
                        allowed = {"stage", "message", "status", "scope", "model_id", "model_index", "models_total",
                                   "percent", "rebalance", "rebalance_total", "rebalance_status", "portfolio_path",
                                   "records_emitted", "completed_model_count", "blocked_model_count", "error_type",
                                   "candidate", "cutoff", "cache_reused", "symbols_total",
                                   "resumed", "completed_rebalances", "next_rebalance", "checkpoint_status",
                                   "checkpoint_path", "checkpoint_sha256", "checkpoint_identity"}
                        details = {key: value for key, value in event.items()
                                   if key in allowed and isinstance(value, (str, int, float, bool, type(None)))}
                        payload = {**details, "event": "control_research_progress", "observed_at": _now()+"Z",
                                   "sequence": progress_sequence,
                                   "job_elapsed_seconds": round(time.monotonic()-progress_started, 6),
                                   **_process_resource_snapshot()}
                        handle.write(json.dumps(payload, sort_keys=True, allow_nan=False)+"\n")
                        handle.flush()
                    checkpoints = _ControlRowCheckpoints(job_id, spec)
                    def persist_row(row, context):
                        checkpoints.capture(row, context)
                        persist_progress({"stage": "row_checkpoint_saved", "message": "Complete model row saved durably",
                                          "model_id": row["model"]["id"], "model_index": context["model_index"],
                                          "models_total": context["models_total"]})
                    result = run_interactive_backtest(int(spec["user_id"]), spec, progress_callback=persist_progress,
                                                      row_checkpoint_callback=persist_row)
                    checkpoints.finish(result)
                elif suite["kind"] == "current_market":
                    result = run_current_market_snapshot(int(spec["user_id"]), spec)
                else:
                    result = run_beta_incremental_backtest(int(spec["user_id"]), spec)
                handle.write("No requested model completed; see per-model blockers\n" if isinstance(result, dict) and result.get("status") == "blocked" else "Completed with per-model errors; inspect result rows\n" if isinstance(result, dict) and result.get("status") == "completed_with_model_errors" else "Completed successfully\n")
            status = "FAILED" if isinstance(result, dict) and result.get("status") == "blocked" else "PASSED"
            output_path = None
            exit_code = -1 if status == "FAILED" else 0
        else:
            command, output_path = _command(job_id, suite_id, spec)
            env = os.environ.copy()
            env.setdefault("PYTHONPATH", str(_ROOT))
            with log_path.open("w", encoding="utf-8") as handle:
                proc = subprocess.run(command, cwd=_ROOT, stdout=handle, stderr=subprocess.STDOUT, text=True, env=env, timeout=int(spec.get("timeout_seconds", 7200)))
            result = None
            if output_path and output_path.exists():
                try:
                    result = json.loads(output_path.read_text(encoding="utf-8"))
                except Exception:
                    result = {"output_path": str(output_path)}
            status = "PASSED" if proc.returncode == 0 else "FAILED"
            exit_code = int(proc.returncode)
        conn = get_connection()
        try:
            conn.execute(
                """UPDATE phase4_jobs SET status=?,exit_code=?,output_path=?,result_json=?,finished_at=? WHERE id=?""",
                (status, exit_code, str(output_path) if output_path else None,
                 _safe_json(result) if result is not None else None, _now(), int(job_id)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\nCONTROL JOB ERROR: {type(exc).__name__}: {exc}\n")
        conn = get_connection()
        try:
            error_result = {"error": str(exc), "type": type(exc).__name__}
            if checkpoints is not None:
                error_result = {**checkpoints.partial_result(), **error_result, "status": "failed_with_partial_results"}
            conn.execute(
                "UPDATE phase4_jobs SET status='FAILED',exit_code=-1,result_json=?,finished_at=? WHERE id=?",
                (_safe_json(error_result), _now(), int(job_id)),
            )
            conn.commit()
        finally:
            conn.close()


def submit_job(user_id: int, suite_id: str, spec: dict[str, Any] | None = None) -> dict[str, Any]:
    if suite_id not in DEFAULT_TEST_SUITES:
        raise ValueError("Unknown test suite")
    payload = dict(spec or {})
    if DEFAULT_TEST_SUITES[suite_id]["kind"] in {"interactive_backtest", "current_market", "beta_incremental_backtest"}:
        payload["user_id"] = int(user_id)
    job_id = _insert_job(user_id, suite_id, payload)
    _EXECUTOR.submit(_run, job_id, suite_id, payload)
    return get_job(user_id, job_id)


def get_job(user_id: int, job_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM phase4_jobs WHERE id=? AND user_id=?", (int(job_id), int(user_id))).fetchone()
        if not row:
            raise ValueError("Job not found")
        out = dict(row)
        out["spec"] = json.loads(out.pop("spec_json") or "{}")
        out["result"] = json.loads(out.pop("result_json") or "null")
        log_path = out.get("log_path")
        if log_path and Path(log_path).is_file():
            text = Path(log_path).read_text(encoding="utf-8", errors="replace")
            out["log_tail"] = text[-16000:]
        else:
            out["log_tail"] = ""
        return out
    finally:
        conn.close()


def list_jobs(user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT id FROM phase4_jobs WHERE user_id=? ORDER BY id DESC LIMIT ?", (int(user_id), max(1, min(200, int(limit))))).fetchall()
    finally:
        conn.close()
    return [get_job(user_id, int(row["id"])) for row in rows]
