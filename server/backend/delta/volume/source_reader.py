"""Disabled bounded source authentication; no decoder, process launcher or model path."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import time
from typing import Callable


class SourceReadError(ValueError):
    """Fail-closed reason code; no completion receipt exists on failure."""


@dataclass(frozen=True)
class ResourceSample:
    observed_ns: int
    combined_rss_bytes: int
    disk_free_bytes: int


@dataclass(frozen=True)
class SourceReceipt:
    sha256: str
    bytes_read: int
    device: int
    inode: int
    checks: int
    max_sampled_combined_rss_bytes: int
    scope: str = "BYTE_AUTHENTICATION_ONLY_NOT_DECODE_OR_CAPTURE_QUALIFICATION"


def _integer(value: int, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value <= 2**63 - 1


def _checked_resource_sample(probe, deadline_ns):
    now = time.monotonic_ns()
    if now >= deadline_ns:
        raise SourceReadError("WALL_LIMIT")
    sample = probe()
    after = time.monotonic_ns()
    if (type(sample) is not ResourceSample
            or not all(_integer(v) for v in (
                sample.observed_ns, sample.combined_rss_bytes,
                sample.disk_free_bytes))
            or not now <= sample.observed_ns <= after
            or after - now > 250_000_000):
        raise SourceReadError("RESOURCE_SAMPLE_UNCERTAIN")
    if after >= deadline_ns:
        raise SourceReadError("WALL_LIMIT")
    if sample.combined_rss_bytes > 512 * 1024 * 1024:
        raise SourceReadError("SAMPLED_MEMORY_LIMIT")
    if sample.disk_free_bytes < 2 * 1024**3:
        raise SourceReadError("DISK_RESERVE")
    return sample


def authenticate_source(
    path: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
    deadline_ns: int,
    probe: Callable[[], ResourceSample],
    enabled: bool = False,
    block_bytes: int = 1024 * 1024,
) -> SourceReceipt:
    """Stream one file with exact manifest identity and mandatory sampled guards.

    The probe must cover the owning process tree; this function cannot attest a
    caller's sampler. It never spawns children or supplies an implicit sampler.
    RSS is sampled, not a guaranteed peak. No bytes are returned to consumers.
    """
    if enabled is not True:
        raise SourceReadError("DISABLED_SOURCE_READER")
    if (not isinstance(path, Path) or type(expected_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
            or not _integer(expected_bytes, 1) or not _integer(deadline_ns, 1)
            or not _integer(block_bytes, 1) or block_bytes > 1024 * 1024
            or not callable(probe)):
        raise SourceReadError("INVALID_SOURCE_CONTRACT")
    if not hasattr(os, "O_NOFOLLOW"):
        raise SourceReadError("NO_SYMLINK_CUSTODY_UNSUPPORTED")
    if deadline_ns - time.monotonic_ns() > 600_000_000_000:
        raise SourceReadError("WALL_BUDGET_EXCEEDS_600_SECONDS")
    checks = peak = 0

    def guard() -> None:
        nonlocal checks, peak
        sample = _checked_resource_sample(probe, deadline_ns)
        checks += 1
        peak = max(peak, sample.combined_rss_bytes)

    def identity(s: os.stat_result) -> tuple[int, ...]:
        return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns

    guard()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    failure = None
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size != expected_bytes:
            raise SourceReadError("NOT_REGULAR_OR_SIZE_MISMATCH")
        digest = hashlib.sha256()
        count = 0
        while True:
            guard()
            chunk = os.read(fd, min(block_bytes, expected_bytes - count + 1))
            guard()
            if not chunk:
                break
            count += len(chunk)
            if count > expected_bytes:
                raise SourceReadError("SOURCE_GREW")
            digest.update(chunk)
        if (count != expected_bytes or identity(os.fstat(fd)) != identity(before)
                or identity(path.stat(follow_symlinks=False)) != identity(before)):
            raise SourceReadError("SOURCE_IDENTITY_CHANGED")
        observed_hash = digest.hexdigest()
        if observed_hash != expected_sha256:
            raise SourceReadError("SOURCE_HASH_MISMATCH")
        guard()  # Caller callback may mutate the source while custody exists.
        if identity(os.fstat(fd)) != identity(before):
            raise SourceReadError("SOURCE_IDENTITY_CHANGED_AFTER_FINAL_PROBE")
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            os.close(fd)
        except OSError as exc:
            if failure is None:
                raise
            failure.add_note("SOURCE_CLOSE_FAILED: " + type(exc).__name__)
    guard()  # Includes descriptor-close latency; failures still publish no receipt.
    # No caller callback follows this final pathname snapshot. This is not a
    # perpetual pathname guarantee or descriptor handoff to another consumer.
    try:
        final_path = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise SourceReadError("SOURCE_PATH_UNAVAILABLE_AFTER_FINAL_PROBE") from exc
    if identity(final_path) != identity(before):
        raise SourceReadError("SOURCE_IDENTITY_CHANGED_AFTER_FINAL_PROBE")
    if time.monotonic_ns() >= deadline_ns:
        raise SourceReadError("WALL_LIMIT")
    return SourceReceipt(observed_hash, count, before.st_dev, before.st_ino,
                         checks, peak)
