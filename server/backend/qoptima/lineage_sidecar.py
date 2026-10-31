"""Lossless, bounded report references to immutable SEC diagnostic lineage.

Only runtime/report handling uses this module. Forecast generation and records
keep their existing API and values. ``artifact_root`` is a trusted server or
archive root, never a request parameter; references contain a strictly confined
relative name, not an arbitrary filesystem path. Portable results must use
``write_lineage_report_archive`` (or preserve its identical directory layout).
"""
from __future__ import annotations

from contextlib import contextmanager
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import tempfile
from typing import Any
import zipfile

REFERENCE_SCHEMA = "gamma_sec_feature_lineage_reference_v1"
JSON_ENCODING = "framed_canonical_json_utf8_gzip_v1"
_ARTIFACT_ROOT = Path(__file__).resolve().parents[2] / "data" / "control_lineage"
_SUBDIRECTORY = "sec_feature_lineage"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MAX_DOCUMENT_BYTES = 1024 * 1024 * 1024
_CHUNK = 1024 * 1024


class LineageSidecarError(ValueError):
    """Missing, corrupt or unsafe lineage is a blocking artifact error."""


def _json_encoder():
    return json.JSONEncoder(sort_keys=True, separators=(",", ":"),
                            ensure_ascii=True, allow_nan=False)


def _validate_json(value: Any) -> None:
    # JSON's implicit tuple/key conversion would violate a lossless round trip.
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _validate_json(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _validate_json(item)
        return
    raise LineageSidecarError("SEC lineage must contain only finite JSON values and string keys")


def _state_count(lineage: dict[str, Any]) -> int:
    if not isinstance(lineage, dict) or not isinstance(lineage.get("feature_states"), dict):
        raise LineageSidecarError("SEC lineage requires its exact feature_states object")
    return len(lineage["feature_states"])


def _root(artifact_root: Path | str | None) -> Path:
    root = Path(_ARTIFACT_ROOT if artifact_root is None else artifact_root)
    if root.is_symlink():
        raise LineageSidecarError("Lineage artifact root cannot be a symbolic link")
    return root.resolve()


def _validate_reference(reference: dict[str, Any]) -> str:
    keys = {"schema", "encoding", "sha256", "uncompressed_bytes", "feature_state_count", "relative_path"}
    if not isinstance(reference, dict) or set(reference) != keys:
        raise LineageSidecarError("Invalid SEC lineage reference fields")
    if reference["schema"] != REFERENCE_SCHEMA or reference["encoding"] != JSON_ENCODING:
        raise LineageSidecarError("Unsupported SEC lineage reference schema or encoding")
    digest = reference["sha256"]
    if not isinstance(digest, str) or not _HASH.fullmatch(digest):
        raise LineageSidecarError("Invalid SEC lineage SHA-256")
    expected = f"{_SUBDIRECTORY}/{digest}.json.gz"
    if reference["relative_path"] != expected:
        raise LineageSidecarError("SEC lineage path must be its canonical contained relative path")
    size, count = reference["uncompressed_bytes"], reference["feature_state_count"]
    if type(size) is not int or not 0 < size <= _MAX_DOCUMENT_BYTES:
        raise LineageSidecarError("Invalid SEC lineage byte count")
    if type(count) is not int or not 0 <= count <= size:
        raise LineageSidecarError("Invalid SEC lineage state count")
    return expected


def _envelope_header(count: int) -> bytes:
    # Count is inside the content hash. Validate this bounded framing before
    # exposing/exporting any pool; no giant JSON parse is needed for verification.
    return f'{{"feature_state_count":{count},"lineage":'.encode("ascii")


@contextmanager
def _open_verified_gzip(reference: dict[str, Any], artifact_root=None):
    """Verify in bounded memory, then yield a private compressed snapshot.

    An open descriptor pins an inode, not its contents. Verification and a later
    resolver/export read must therefore use a private snapshot so in-place
    changes to the published file cannot invalidate the verified bytes.
    """
    relative = _validate_reference(reference)
    header = _envelope_header(reference["feature_state_count"])
    # Reject impossible framing before opening/decompressing a sidecar. Without
    # this bound, a forged tiny size could produce read(-n), meaning read-all.
    if reference["uncompressed_bytes"] <= len(header):
        raise LineageSidecarError("SEC lineage byte count smaller than payload framing")
    root = _root(artifact_root)
    # Open directory and leaf with NOFOLLOW; keep their descriptors while reading
    # so replacement by a symlink cannot redirect a validated path.
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            directory_fd = os.open(_SUBDIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        finally:
            os.close(root_fd)
        try:
            # NONBLOCK prevents a substituted FIFO from hanging before the
            # regular-file check; it has no effect on ordinary disk files.
            fd = os.open(Path(relative).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        finally:
            os.close(directory_fd)
        with os.fdopen(fd, "rb") as source, tempfile.TemporaryFile(mode="w+b") as raw:
            source_stat = os.fstat(source.fileno())
            if not stat.S_ISREG(source_stat.st_mode):
                raise LineageSidecarError("SEC lineage sidecar must be a regular file")
            # Our canonical gzip writer has at most tiny deflate overhead. Bound
            # both the initial file size and a file growing during the copy;
            # forged references cannot make snapshot storage unbounded.
            compressed_limit = reference["uncompressed_bytes"] + max(
                _CHUNK, reference["uncompressed_bytes"] // 100)
            if source_stat.st_size > compressed_limit:
                raise LineageSidecarError("SEC lineage compressed byte count exceeds limit")
            copied = 0
            while chunk := source.read(min(_CHUNK, compressed_limit - copied + 1)):
                copied += len(chunk)
                if copied > compressed_limit:
                    raise LineageSidecarError("SEC lineage compressed byte count exceeds limit")
                raw.write(chunk)
            raw.seek(0)
            digest, size = hashlib.sha256(), 0
            with gzip.GzipFile(fileobj=raw, mode="rb") as compressed:
                observed_header = compressed.read(len(header))
                if observed_header != header:
                    raise LineageSidecarError("SEC lineage authenticated state count mismatch")
                digest.update(observed_header)
                size = len(observed_header)
                while chunk := compressed.read(min(_CHUNK, reference["uncompressed_bytes"] - size + 1)):
                    size += len(chunk)
                    if size > reference["uncompressed_bytes"]:
                        raise LineageSidecarError("SEC lineage byte count mismatch")
                    digest.update(chunk)
            if size != reference["uncompressed_bytes"] or digest.hexdigest() != reference["sha256"]:
                raise LineageSidecarError("SEC lineage hash or byte count mismatch")
            raw.seek(0)
            yield raw
    except LineageSidecarError:
        raise
    except (OSError, EOFError, ValueError) as exc:
        raise LineageSidecarError(f"SEC lineage sidecar unavailable or corrupt: {exc}") from exc


def verify_lineage_reference(reference: dict[str, Any], *, artifact_root=None) -> None:
    """Validate schema, containment, gzip integrity and canonical-document hash."""
    with _open_verified_gzip(reference, artifact_root):
        pass


def resolve_sec_lineage(reference: dict[str, Any], *, artifact_root=None) -> dict[str, Any]:
    """Explicit full lineage retrieval; only the consumer requesting it expands it."""
    with _open_verified_gzip(reference, artifact_root) as raw:
        try:
            with gzip.GzipFile(fileobj=raw, mode="rb") as document:
                envelope = json.load(document)
        except (OSError, EOFError, ValueError) as exc:
            raise LineageSidecarError("SEC lineage is not valid JSON") from exc
    if not isinstance(envelope, dict) or set(envelope) != {"feature_state_count", "lineage"}:
        raise LineageSidecarError("SEC lineage has an invalid payload envelope")
    lineage = envelope["lineage"]
    _validate_json(lineage)
    if envelope["feature_state_count"] != reference["feature_state_count"] or _state_count(lineage) != reference["feature_state_count"]:
        raise LineageSidecarError("SEC lineage state count mismatch")
    return lineage


def write_sec_lineage(lineage: dict[str, Any], *, artifact_root=None) -> dict[str, Any]:
    """Stream the exact JSON pool, fsync, and atomically publish by content hash."""
    count = _state_count(lineage)
    _validate_json(lineage)
    root = _root(artifact_root)
    temporary = None
    directory_fd = None
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            try:
                os.mkdir(_SUBDIRECTORY, mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            directory_fd = os.open(_SUBDIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        finally:
            os.close(root_fd)
        # Descriptor-relative creation/publication prevents a directory symlink
        # swap from redirecting any bytes outside the trusted artifact root.
        temporary = f".lineage-{secrets.token_hex(16)}.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory_fd)
        digest, size = hashlib.sha256(), 0
        with os.fdopen(fd, "wb") as raw:
            with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
                def chunks():
                    yield _envelope_header(count)
                    for text in _json_encoder().iterencode(lineage):
                        yield text.encode("utf-8")
                    yield b"}"
                for chunk in chunks():
                    size += len(chunk)
                    if size > _MAX_DOCUMENT_BYTES:
                        raise LineageSidecarError("SEC lineage exceeds the artifact size limit")
                    digest.update(chunk)
                    compressed.write(chunk)
            raw.flush()
            os.fsync(raw.fileno())
        reference = {"schema": REFERENCE_SCHEMA, "encoding": JSON_ENCODING,
                     "sha256": digest.hexdigest(), "uncompressed_bytes": size,
                     "feature_state_count": count,
                     "relative_path": f"{_SUBDIRECTORY}/{digest.hexdigest()}.json.gz"}
        destination = Path(reference["relative_path"]).name
        try:
            # Atomic no-clobber publication. Concurrent identical writes converge;
            # a tampered existing artifact is never silently replaced or trusted.
            os.link(temporary, destination, src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd, follow_symlinks=False)
        except FileExistsError:
            pass
        # Verify the publicly resolvable path as well as the streamed source;
        # directory replacement or pre-existing tampering must fail closed.
        verify_lineage_reference(reference, artifact_root=root)
        os.fsync(directory_fd)
        return reference
    except Exception as exc:
        # Once linked, the object is complete and another writer may already
        # have returned its verified reference. Never delete it after a later
        # durability failure; this caller fails closed, but that reference lives.
        if isinstance(exc, LineageSidecarError):
            raise
        raise LineageSidecarError(f"Could not publish SEC lineage sidecar: {exc}") from exc
    finally:
        if directory_fd is not None:
            try:
                if temporary is not None:
                    try:
                        os.unlink(temporary, dir_fd=directory_fd)
                    except FileNotFoundError:
                        pass
            finally:
                os.close(directory_fd)


def externalize_sec_lineage(diagnostics: dict[str, Any]) -> dict[str, Any]:
    """Replace only the runtime report's global pool, after durable publication."""
    if "sec_feature_lineage" not in diagnostics:
        return diagnostics
    reference = write_sec_lineage(diagnostics["sec_feature_lineage"])
    return {**diagnostics, "sec_feature_lineage": reference}


def _references(value: Any):
    if isinstance(value, dict):
        if value.get("schema") == REFERENCE_SCHEMA:
            _validate_reference(value)
            yield value
        else:
            for item in value.values():
                yield from _references(item)
    elif isinstance(value, list):
        for item in value:
            yield from _references(item)


def write_lineage_report_archive(report: dict[str, Any], destination: Path | str, *, artifact_root=None) -> Path:
    """Atomically package report + all sidecars, without giant JSON strings.

    ``destination`` is selected by trusted export code, not a reference or request
    path. Extraction to a trusted directory yields the same resolver layout.
    No archive is published if any referenced sidecar cannot be verified.
    """
    target = Path(destination)
    references = {}
    for reference in _references(report):
        relative = reference["relative_path"]
        if relative in references and references[relative] != reference:
            raise LineageSidecarError("Conflicting SEC lineage references in one report")
        references[relative] = reference
    temporary = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".lineage-report-", suffix=".tmp", dir=target.parent)
        with os.fdopen(fd, "w+b") as output:
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
                with archive.open("result.json", "w") as document:
                    for text in _json_encoder().iterencode(report):
                        document.write(text.encode("utf-8"))
                for relative, reference in sorted(references.items()):
                    with _open_verified_gzip(reference, artifact_root) as raw:
                        with archive.open(relative, "w") as document:
                            shutil.copyfileobj(raw, document, length=_CHUNK)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        temporary = None
        return target
    except LineageSidecarError:
        raise
    except Exception as exc:
        raise LineageSidecarError(f"Could not publish lineage report archive: {exc}") from exc
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
