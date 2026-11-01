"""Temporary invented bytes only; never opens a vendor export."""
import hashlib
import os
from pathlib import Path
import time
from unittest.mock import patch

import pytest

from backend.delta.volume import source_reader as sr


def sample():
    return sr.ResourceSample(time.monotonic_ns(), 16 * 1024**2, 4 * 1024**3)


def run(path, **overrides):
    options = dict(expected_sha256=hashlib.sha256(b"abc").hexdigest(),
                   expected_bytes=3, deadline_ns=time.monotonic_ns() + 2_000_000_000,
                   probe=sample, enabled=True, block_bytes=2)
    options.update(overrides)
    return sr.authenticate_source(path, **options)


def test_exact_eof_identity_and_no_payload_return(tmp_path):
    p = tmp_path / "invented"; p.write_bytes(b"abc")
    receipt = run(p)
    assert receipt.sha256 == hashlib.sha256(b"abc").hexdigest()
    assert receipt.bytes_read == 3 and receipt.inode == p.stat().st_ino
    assert receipt.max_sampled_combined_rss_bytes == 16 * 1024**2
    assert not hasattr(receipt, "data")


def test_disabled_precedes_open(tmp_path):
    with patch.object(sr.os, "open") as opened:
        with pytest.raises(sr.SourceReadError, match="DISABLED"):
            run(tmp_path / "absent", enabled=False)
        opened.assert_not_called()


@pytest.mark.parametrize("change", [dict(expected_bytes=True), dict(block_bytes=1048577),
                                     dict(expected_sha256="wrong"), dict(deadline_ns=True)])
def test_contract_before_open(tmp_path, change):
    with patch.object(sr.os, "open") as opened:
        with pytest.raises(sr.SourceReadError, match="CONTRACT"):
            run(tmp_path / "absent", **change)
        opened.assert_not_called()


def test_hash_and_size_fail_closed(tmp_path):
    p = tmp_path / "invented"; p.write_bytes(b"abc")
    with pytest.raises(sr.SourceReadError, match="HASH"):
        run(p, expected_sha256="0" * 64)
    with pytest.raises(sr.SourceReadError, match="SIZE"):
        run(p, expected_bytes=4)


def test_symlink_and_nonregular_rejected(tmp_path):
    p = tmp_path / "invented"; p.write_bytes(b"abc")
    link = tmp_path / "link"; link.symlink_to(p)
    with pytest.raises(OSError): run(link)
    with pytest.raises(sr.SourceReadError, match="NOT_REGULAR"):
        run(tmp_path)


@pytest.mark.parametrize("kind,reason", [("missing", "UNCERTAIN"), ("stale", "UNCERTAIN"),
                                        ("future", "UNCERTAIN"), ("bool", "UNCERTAIN"),
                                        ("rss", "MEMORY"), ("disk", "DISK")])
def test_resource_rejection_before_open(tmp_path, kind, reason):
    def probe():
        now = time.monotonic_ns()
        return {"missing": None, "stale": sr.ResourceSample(0, 0, 4*1024**3),
                "future": sr.ResourceSample(now+10**9, 0, 4*1024**3),
                "bool": sr.ResourceSample(now, True, 4*1024**3),
                "rss": sr.ResourceSample(now, 512*1024**2+1, 4*1024**3),
                "disk": sr.ResourceSample(now, 0, 2*1024**3-1)}[kind]
    with patch.object(sr.os, "open") as opened:
        with pytest.raises(sr.SourceReadError, match=reason):
            run(tmp_path / "absent", probe=probe)
        opened.assert_not_called()


def test_expired_wall_precedes_probe_and_open(tmp_path):
    with patch.object(sr.os, "open") as opened:
        with pytest.raises(sr.SourceReadError, match="WALL"):
            run(tmp_path / "absent", deadline_ns=1)
        opened.assert_not_called()


@pytest.mark.parametrize("phase", [8, 9])
def test_final_guard_failure_closes_descriptor_and_returns_no_receipt(tmp_path, phase):
    p = tmp_path / "invented"; p.write_bytes(b"abc")
    calls = 0; closed = []; real_close = os.close
    def probe():
        nonlocal calls
        calls += 1
        return sr.ResourceSample(time.monotonic_ns(), 0, 0 if calls == phase else 4*1024**3)
    def close(fd): closed.append(fd); real_close(fd)
    with patch.object(sr.os, "close", side_effect=close):
        with pytest.raises(sr.SourceReadError, match="DISK"): run(p, probe=probe)
    assert len(closed) == 1
    with pytest.raises(OSError): os.fstat(closed[0])


def test_replacement_during_read_rejects(tmp_path):
    p = tmp_path / "invented"; p.write_bytes(b"abc")
    calls = 0
    def probe():
        nonlocal calls
        calls += 1
        if calls == 3:
            other = tmp_path / "replacement"; other.write_bytes(b"abc"); other.replace(p)
        return sample()
    with pytest.raises(sr.SourceReadError, match="IDENTITY_CHANGED"): run(p, probe=probe)


def test_read_failure_closes_only_owned_descriptor(tmp_path):
    p = tmp_path / "invented"; p.write_bytes(b"abc")
    closed = []; real_close = os.close
    def close(fd): closed.append(fd); real_close(fd)
    with patch.object(sr.os, "read", side_effect=OSError("invented I/O fault")), \
            patch.object(sr.os, "close", side_effect=close):
        with pytest.raises(OSError, match="invented"): run(p)
    assert len(closed) == 1


def test_exact_resource_boundaries_allowed(tmp_path):
    p = tmp_path / "invented"; p.write_bytes(b"abc")
    def boundary(): return sr.ResourceSample(time.monotonic_ns(), 512*1024**2, 2*1024**3)
    assert run(p, probe=boundary).bytes_read == 3


def test_close_phase_sample_included_in_receipt(tmp_path):
    p = tmp_path / "invented"; p.write_bytes(b"abc")
    count = 0
    def probe():
        nonlocal count
        count += 1
        return sr.ResourceSample(time.monotonic_ns(), count, 4*1024**3)
    receipt = run(p, probe=probe)
    assert receipt.checks == count == 9
    assert receipt.max_sampled_combined_rss_bytes == 9


def test_no_larger_wall_budget(tmp_path):
    with patch.object(sr.os, "open") as opened:
        with pytest.raises(sr.SourceReadError, match="600_SECONDS"):
            run(tmp_path / "absent", deadline_ns=time.monotonic_ns()+601_000_000_000)
        opened.assert_not_called()


@pytest.mark.parametrize("phase", [8, 9])
@pytest.mark.parametrize("mutation", ["replace", "inplace", "truncate", "unlink", "symlink"])
def test_late_mutation_after_each_final_probe_rejects_and_closes(tmp_path, phase, mutation):
    p = tmp_path / "invented"; p.write_bytes(b"abc")
    calls = 0; closed = []; real_close = os.close
    def probe():
        nonlocal calls
        calls += 1
        if calls == phase:
            if mutation == "replace":
                other = tmp_path / "replacement"; other.write_bytes(b"xyz"); other.replace(p)
            elif mutation == "inplace":
                with p.open("r+b") as stream: stream.write(b"xyz")
            elif mutation == "truncate":
                with p.open("r+b") as stream: stream.truncate(1)
            elif mutation == "unlink": p.unlink()
            else:
                other = tmp_path / "replacement"; other.write_bytes(b"abc")
                p.unlink(); p.symlink_to(other)
        return sample()
    def close(fd): closed.append(fd); real_close(fd)
    with patch.object(sr.os, "close", side_effect=close):
        with pytest.raises(sr.SourceReadError, match="SOURCE_(IDENTITY_CHANGED|PATH_UNAVAILABLE)"):
            run(p, probe=probe)
    assert len(closed) == 1
    with pytest.raises(OSError): os.fstat(closed[0])


def test_primary_failure_preserves_secondary_close_evidence(tmp_path):
    p = tmp_path / "invented"; p.write_bytes(b"abc")
    real_close = os.close
    def close(fd): real_close(fd); raise OSError("invented close failure")
    with patch.object(sr.os, "read", side_effect=OSError("primary read failure")), \
            patch.object(sr.os, "close", side_effect=close):
        with pytest.raises(OSError, match="primary read") as caught: run(p)
    assert caught.value.__notes__ == ["SOURCE_CLOSE_FAILED: OSError"]


def test_close_failure_after_success_cannot_return_receipt(tmp_path):
    p = tmp_path / "invented"; p.write_bytes(b"abc")
    real_close = os.close
    def close(fd): real_close(fd); raise OSError("invented close failure")
    with patch.object(sr.os, "close", side_effect=close):
        with pytest.raises(OSError, match="invented close failure"): run(p)


@pytest.mark.parametrize("late", [False, True])
def test_final_path_stat_deadline_is_checked_without_another_probe(tmp_path, late):
    p = tmp_path / "invented"; p.write_bytes(b"abc")
    clock = 100; probes = 0; path_stats = 0
    real_stat = Path.stat
    def probe():
        nonlocal probes
        probes += 1
        return sr.ResourceSample(clock, 0, 4*1024**3)
    def stat_after_delay(path, *args, **kwargs):
        nonlocal clock, path_stats
        result = real_stat(path, *args, **kwargs)
        if path == p:
            path_stats += 1
            if path_stats == 2 and late:
                clock = 200  # Filesystem returns at the exact deadline.
        return result
    with patch.object(sr.time, "monotonic_ns", side_effect=lambda: clock), \
            patch.object(Path, "stat", stat_after_delay):
        if late:
            with pytest.raises(sr.SourceReadError, match="WALL_LIMIT"):
                run(p, probe=probe, deadline_ns=200)
        else:
            assert run(p, probe=probe, deadline_ns=200).bytes_read == 3
    assert path_stats == 2 and probes == 9
