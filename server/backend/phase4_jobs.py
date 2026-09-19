"""Whitelisted local research-job runner for the private Oryntra Control app."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from .database import get_connection
from .phase4_control import DEFAULT_TEST_SUITES


_ROOT = Path(__file__).resolve().parents[1]
_RUN_ROOT = _ROOT / "data" / "control_runs"
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="oryntra-control-job")


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _safe_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, allow_nan=False)


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
            "--benchmark", str(spec.get("benchmark", "spy_qqq_equal")),
        ]
    if spec.get("declared_years"):
        command += ["--declared-years", str(spec["declared_years"])]
    if spec.get("evidence_label") and suite_id == "phase3_branches":
        command += ["--evidence-label", str(spec["evidence_label"])]
    return command, output


def _run(job_id: int, suite_id: str, spec: dict[str, Any]) -> None:
    log_path, _default_output = _paths(job_id, suite_id)
    conn = get_connection()
    try:
        conn.execute("UPDATE phase4_jobs SET status='RUNNING',started_at=?,log_path=? WHERE id=?", (_now(), str(log_path), int(job_id)))
        conn.commit()
    finally:
        conn.close()
    try:
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
        conn = get_connection()
        try:
            conn.execute(
                """UPDATE phase4_jobs SET status=?,exit_code=?,output_path=?,result_json=?,finished_at=? WHERE id=?""",
                (status, int(proc.returncode), str(output_path) if output_path else None,
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
            conn.execute(
                "UPDATE phase4_jobs SET status='FAILED',exit_code=-1,result_json=?,finished_at=? WHERE id=?",
                (_safe_json({"error": str(exc), "type": type(exc).__name__}), _now(), int(job_id)),
            )
            conn.commit()
        finally:
            conn.close()


def submit_job(user_id: int, suite_id: str, spec: dict[str, Any] | None = None) -> dict[str, Any]:
    if suite_id not in DEFAULT_TEST_SUITES:
        raise ValueError("Unknown test suite")
    payload = dict(spec or {})
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
