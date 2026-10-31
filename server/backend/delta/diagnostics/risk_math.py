"""D01 one-session diagnostics. No policy, sizing, model fit tuning or execution."""
import hashlib
import json
import math
import numpy as np
from backend.delta.diagnostics.arithmetic_policy import controlled


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def ns(value):
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ValueError('integer int64 nonnegative nanoseconds required')
    return value


def covariance(rows, universe, evaluation_time, *, minimum_history):
    """Aligned complete SYNTHETIC/research rows, not PIT or empirical certification.

    Each row supplies session, end_ns, available_ns and returns in the exact universe.
    minimum_history is explicit; T>1 alone never supplies a sufficient-history rule.
    """
    ns(evaluation_time)
    if type(minimum_history) is not int or minimum_history < 2:
        raise ValueError('configured history rule required')
    if not universe or len(set(universe)) != len(universe) or any(type(s) is not str or not s for s in universe):
        raise ValueError('unique nonempty universe required')
    if len(rows) < minimum_history:
        raise ValueError('insufficient history')
    sessions, ends, observations = set(), [], []
    for row in rows:
        end, available = ns(row['end_ns']), ns(row['available_ns'])
        if not end <= available <= evaluation_time or end >= evaluation_time:
            raise ValueError('future or unmatured information')
        if not row['session'] or row['session'] in sessions or set(row['returns']) != set(universe):
            raise ValueError('duplicate session or incomplete universe')
        sessions.add(row['session']); ends.append(end)
        values = [row['returns'][s] for s in universe]
        if any(type(x) not in (float, int) or not math.isfinite(x) or x < -1 for x in values):
            raise ValueError('finite decimal simple returns required')
        observations.append(values)
    if ends != sorted(set(ends)):
        raise ValueError('chronological unique sessions required')
    a = np.asarray(observations, dtype=float)
    if np.any(np.ptp(a, axis=0) == 0):
        raise ValueError('constant/stale series is not qualified risk data')
    x = a - a.mean(axis=0)
    t, n = x.shape
    s0 = x.T @ x / t
    m = float(np.trace(s0) / n)
    d2 = float(np.sum((s0 - m * np.eye(n))**2) / n)
    b2 = float(sum(np.sum((np.outer(v, v) - s0)**2) for v in x) / (n * t*t))
    delta = min(b2, d2) / d2 if d2 > 0 else 0.0
    lw = (1-delta) * s0 + delta*m*np.eye(n)
    sample = s0 * t/(t-1)
    if not all(np.all(np.isfinite(v)) for v in (s0, lw, sample)):
        raise ValueError('nonfinite covariance arithmetic')
    return dict(universe=list(universe), horizon='ONE_SESSION', units='DECIMAL_RETURN_SQUARED',
                s0=s0.tolist(), lw=lw.tolist(), sample=sample.tolist(), m=m, d2=d2, b2=b2,
                shrinkage=delta, observations=t, history_hash=digest(rows),
                available_ns=max(r['available_ns'] for r in rows),
                universe_hash=digest(list(universe)), qualification='DIAGNOSTIC_ONLY')


@controlled
def envelope(cov, exposures, nav, tolerance):
    """Fixed covariance and positive post-cost NAV; explicit arithmetic tolerance."""
    if type(tolerance) not in (float, int) or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError('explicit nonnegative numerical tolerance required')
    if not math.isfinite(float(nav)) or nav <= 0:
        raise ValueError('positive NAV required')
    universe = cov['universe']
    if len(set(universe)) != len(universe) or cov['horizon'] != 'ONE_SESSION' or cov['units'] != 'DECIMAL_RETURN_SQUARED':
        raise ValueError('covariance identity/horizon')
    if set(exposures) - set(universe):
        raise ValueError('missing held or pending covariance coverage')
    w = np.array([float(exposures.get(s, 0)/nav) for s in universe])
    if not np.all(np.isfinite(w)):
        raise ValueError('finite weights required')
    results = {}
    for name in ('lw', 'sample'):
        matrix = np.asarray(cov[name], dtype=float)
        if matrix.shape != (len(universe),)*2 or not np.all(np.isfinite(matrix)):
            raise ValueError('covariance dimensions/finiteness')
        if not np.allclose(matrix, matrix.T, rtol=0, atol=tolerance):
            raise ValueError('asymmetric covariance')
        if np.min(np.linalg.eigvalsh(matrix)) < -tolerance:
            raise ValueError('non-PSD covariance')
        variance = float(w @ matrix @ w)
        if variance < -tolerance or not math.isfinite(variance):
            raise ValueError('invalid variance')
        results[name] = math.sqrt(max(0.0, variance))
    results['envelope'] = max(results.values())
    results['interpretation'] = 'TWO_MODEL_ENVELOPE_NOT_CONFIDENCE_BOUND'
    return results
