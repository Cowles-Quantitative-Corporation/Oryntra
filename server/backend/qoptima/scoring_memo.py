"""Bounded pure scoring preparation; no score, gate or seed policy lives here."""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import lru_cache
from threading import RLock
from typing import Any, Callable
import numpy as np

_ENABLED = ContextVar('qoptima_scoring_memo_enabled', default=True)
_MIB = 1024*1024


@contextmanager
def without_scoring_memoization():
    """Reference path for equivalence verification, local to this task/thread."""
    token = _ENABLED.set(False)
    try:
        yield
    finally:
        _ENABLED.reset(token)


class _ByteLRU:
    def __init__(self, maximum_bytes: int, maximum_entries: int):
        self.maximum_bytes = int(maximum_bytes)
        self.maximum_entries = int(maximum_entries)
        self.entries = OrderedDict()
        self.bytes = self.hits = self.misses = 0
        self.lock = RLock()

    def get_or_build(self, key, builder: Callable, size: Callable):
        with self.lock:
            if key in self.entries:
                self.hits += 1
                value, cost = self.entries.pop(key)
                self.entries[key] = (value, cost)
                return value
            self.misses += 1
        value = builder()
        cost = int(size(value))
        if cost > self.maximum_bytes:
            return value
        with self.lock:
            if key in self.entries:
                return self.entries[key][0]
            while self.entries and (self.bytes+cost > self.maximum_bytes or len(self.entries) >= self.maximum_entries):
                _, (_, removed) = self.entries.popitem(last=False)
                self.bytes -= removed
            self.entries[key] = (value, cost)
            self.bytes += cost
        return value

    def clear(self):
        with self.lock:
            self.entries.clear()
            self.bytes = self.hits = self.misses = 0

    def info(self):
        with self.lock:
            return dict(entries=len(self.entries), bytes=self.bytes, maximum_bytes=self.maximum_bytes,
                hits=self.hits, misses=self.misses)


_COVARIANCES = _ByteLRU(64*_MIB, 8)
_BOOTSTRAP = _ByteLRU(32*_MIB, 128)
_CORRELATION_STRESS = _ByteLRU(4*_MIB, 128)


def prepared_error_covariances(samples: np.ndarray, *, window: int, min_history: int,
    floor: float, builder: Callable, information_cutoffs=None, realized_at=None):
    x = np.asarray(samples, dtype=float)
    arguments = dict(window=int(window), min_history=int(min_history), floor=float(floor))
    if information_cutoffs is not None or realized_at is not None:
        arguments.update(information_cutoffs=information_cutoffs, realized_at=realized_at)
    if not _ENABLED.get():
        return builder(x, **arguments)
    n, p = x.shape
    estimate = x.nbytes+(max(0, n-int(min_history))+1)*p*p*8
    if estimate > _COVARIANCES.maximum_bytes:
        return builder(x, **arguments)
    # Content and memory order are both explicit. In-place mutation, changed
    # maturities, asset order, units, config or implementation invalidate reuse.
    content = x.tobytes(order='C')
    key = (builder, x.shape, x.strides, x.dtype.str, content, int(window), int(min_history), float(floor), information_cutoffs, realized_at)
    def build():
        historical, current = builder(x, **arguments)
        for array in [*historical, current]:
            if array is not None:
                array.setflags(write=False)
        return tuple(historical), current
    def cost(value):
        historical, current = value
        return len(content)+sum(a.nbytes for a in historical if a is not None)+(0 if current is None else current.nbytes)
    return _COVARIANCES.get_or_build(key, build, cost)


@lru_cache(maxsize=512)
def _order_rank(n: int, beta: float, confidence: float, survival: Callable):
    eligible = [k for k in range(1, n+1) if survival(n, beta, k) >= confidence]
    return max(eligible) if eligible else None


def conservative_order_rank(n: int, beta: float, confidence: float, survival: Callable):
    if _ENABLED.get():
        return _order_rank(int(n), float(beta), float(confidence), survival)
    return _order_rank.__wrapped__(int(n), float(beta), float(confidence), survival)


def bootstrap_indices(sample_count: int, replications: int, seed: int) -> np.ndarray | None:
    n, reps, seed = int(sample_count), int(replications), int(seed)
    if not _ENABLED.get() or n*reps*np.dtype(np.int64).itemsize > _BOOTSTRAP.maximum_bytes:
        return None
    def build():
        rng = np.random.default_rng(seed)
        # Keep the original per-replication RNG call order exactly.
        result = np.empty((reps, n), dtype=np.int64)
        for i in range(reps):
            result[i] = rng.integers(0, n, size=n)
        result.setflags(write=False)
        return result
    return _BOOTSTRAP.get_or_build((n,reps,seed), build, lambda a:a.nbytes)


def prepared_correlation_stress_covariance(covariance: np.ndarray, *, blend: float, builder: Callable):
    cov = np.asarray(covariance, dtype=float)
    if not _ENABLED.get() or 2*cov.nbytes > _CORRELATION_STRESS.maximum_bytes:
        return builder(cov, blend=float(blend))
    content = cov.tobytes(order='C')
    key = (builder, cov.shape, cov.strides, cov.dtype.str, content, float(blend))
    def build():
        result = builder(cov, blend=float(blend))
        result.setflags(write=False)
        return result
    return _CORRELATION_STRESS.get_or_build(key, build, lambda a:len(content)+a.nbytes)


class _MetricsProblemSnapshot:
    """Private immutable snapshot for one search; current-book metrics stay fixed."""
    def __init__(self, problem):
        self._problem = deepcopy(problem)
        for value in vars(self._problem).values():
            if isinstance(value, np.ndarray):
                value.setflags(write=False)
        self._reference_key = self._key(self._problem.current_weights)
        self._reference = None
        self._recent = OrderedDict()

    @staticmethod
    def _key(weights):
        w = np.asarray(weights, dtype=float).reshape(-1)
        return w.shape, w.strides, w.tobytes()

    def __getattr__(self, name):
        return getattr(self._problem, name)

    def metrics(self, weights):
        key = self._key(weights)
        if key == self._reference_key:
            if self._reference is None:
                self._reference = self._problem.metrics(weights)
            return deepcopy(self._reference)
        if key not in self._recent:
            self._recent[key] = self._problem.metrics(weights)
            while len(self._recent) > 8:
                self._recent.popitem(last=False)
        result = self._recent.pop(key)
        self._recent[key] = result
        return deepcopy(result)


def memoized_metrics_problem(problem):
    return _MetricsProblemSnapshot(problem) if _ENABLED.get() else problem


def clear_scoring_memos():
    _COVARIANCES.clear()
    _BOOTSTRAP.clear()
    _CORRELATION_STRESS.clear()
    _order_rank.cache_clear()


def scoring_memo_info() -> dict[str, Any]:
    return {'covariances':_COVARIANCES.info(), 'bootstrap_indices':_BOOTSTRAP.info(),
        'correlation_stress':_CORRELATION_STRESS.info(),
        'order_ranks':_order_rank.cache_info()._asdict()}
