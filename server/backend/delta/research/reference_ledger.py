"""Pure reference-own-risk prerequisite; no file access, fitting or admission.

Disabled by default. Successful validation qualifies only the supplied source
contract. Availability is modeled; terminal-close labels are not fill returns.
The original full-model guards and all historical artifacts remain unchanged.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
import re

SCHEMA = 'DELTA_REFERENCE_OWN_RISK_V1'
FULL_PRODUCER = 'gamma_entry_open_target_research_v1'
FULL_VERSION = '0.4.5-entry-open-target-diagnostic'
UNIT = 'public_adjusted_next_session_open_to_adjusted_horizon_close_simple_return'
CONVENTION = 'next_session_open_to_horizon_close'
TOLERANCE = Fraction(1, 10**12)
SOURCE_KEYS = frozenset(('records', 'attempts', 'outcomes', 'errors', 'calendar'))


def require(ok, reason):
    if not ok:
        raise ValueError('NOT_EVALUABLE:' + reason)


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def number(value):
    """Preserved native decimal-rendering interpretation; not exact real prices."""
    require(type(value) in (int, float, Decimal), 'finite numeric input, no bool')
    value = Decimal(str(value))
    require(value.is_finite(), 'finite number')
    return Fraction(value)


def stamp(value):
    """Exact ISO second + up to nine fractional digits; no datetime truncation.

    Offset precision is whole minutes. Other timestamp grammars/precision are
    unsupported rather than silently rounded to a different availability time.
    """
    require(type(value) is str, 'explicit aware timestamp')
    match = re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})'
                         r'(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})', value)
    require(match is not None, 'supported exact nanosecond timestamp')
    base, fraction, offset = match.groups()
    if offset != 'Z':
        require(0 <= int(offset[1:3]) <= 23 and 0 <= int(offset[4:6]) <= 59,
                'timezone offset hour/minute range')
    dt = datetime.fromisoformat(base + ('+00:00' if offset == 'Z' else offset))
    delta = dt.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 10**9 + int((fraction or '').ljust(9, '0'))


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        require(key not in out, 'duplicate JSON key')
        out[key] = value
    return out


def _index(rows):
    require(type(rows) is list, 'row roster')
    out = {}
    for row in rows:
        ident = row['attempt_id']
        require(type(ident) is str and ident and ident not in out, 'unique attempt identity')
        out[ident] = row
    return out


@dataclass(frozen=True)
class ReferenceRow:
    identity: str
    original_attempt_id: str
    source_record_sha256: str
    symbol: str
    origin: str
    target_start: str
    target_end: str
    forecast_available_ns: int
    target_start_ns: int
    due_ns: int
    outcome_available_ns: int | None
    prediction: Fraction
    actual: Fraction | None
    residual: Fraction | None


@dataclass(frozen=True)
class ReferenceLedger:
    rows: tuple
    source_pins: tuple
    policy_identity: str
    schema: str = SCHEMA
    unit: str = UNIT
    historical_publication_attested: bool = False
    operational_risk_admitted: bool = False

    def asof(self, symbol, cutoff_ns):
        require(type(cutoff_ns) is int and cutoff_ns >= 0, 'cutoff clock')
        require(symbol in {r.symbol for r in self.rows}, 'known symbol')
        selected = sorted((r for r in self.rows if r.symbol == symbol and r.due_ns <= cutoff_ns),
                          key=lambda r: (r.due_ns, r.identity))
        residuals = tuple(r.residual if r.outcome_available_ns is not None
                          and r.outcome_available_ns <= cutoff_ns else None for r in selected)
        return dict(attempts=tuple(r.identity for r in selected), residuals=residuals,
                    attempted=len(selected), observed=sum(x is not None for x in residuals),
                    missing=sum(x is None for x in residuals), cutoff_ns=cutoff_ns,
                    operational_risk_admitted=False)


def derive_reference_ledger(sources, expected_sha256, *, origins, symbols,
                            full_policy_id, full_model_source_sha256, enabled=False):
    """Validate exact byte sources and derive immutable separately identified rows.

    This has no historical execution mode or data-loading runner. Callers must
    obtain separate authority for actual inputs; enabled=True is not acceptance.
    """
    require(enabled is True, 'component disabled')
    try:
        require(type(sources) is dict and set(sources) == SOURCE_KEYS
                and type(expected_sha256) is dict and set(expected_sha256) == SOURCE_KEYS,
                'complete explicit source closure')
        decoded = {}
        for key in sorted(SOURCE_KEYS):
            raw, pin = sources[key], expected_sha256[key]
            require(type(raw) is bytes and type(pin) is str and re.fullmatch('[a-f0-9]{64}', pin),
                    'source bytes and SHA256')
            require(hashlib.sha256(raw).hexdigest() == pin, 'source hash mismatch:' + key)
            decoded[key] = json.loads(raw, object_pairs_hook=_pairs,
                                     parse_constant=lambda _: require(False, 'nonfinite JSON'))
        require(type(origins) is tuple and origins and origins == tuple(sorted(set(origins))),
                'complete ordered origins')
        require(type(symbols) is tuple and len(symbols) == len(set(symbols)) == 7
                and all(type(s) is str and s for s in symbols), 'seven explicit symbols')
        require(all(type(x) is str and re.fullmatch('[a-f0-9]{64}', x)
                    for x in (full_policy_id, full_model_source_sha256)), 'full source identities')
        calendar = decoded['calendar']['sessions']
        require(type(calendar) is dict, 'calendar map')
        days = sorted(calendar)
        for day in days:
            require(date.fromisoformat(day).isoformat() == day, 'canonical calendar date')
            c = calendar[day]
            require(all(type(c[k]) is int and c[k] >= 0 for k in ('open_ns', 'close_ns'))
                    and c['open_ns'] < c['close_ns'], 'calendar clocks')
        require(all(calendar[a]['close_ns'] < calendar[b]['open_ns'] for a,b in zip(days, days[1:])),
                'ordered session clocks')
        records = decoded['records']['records']
        require(type(records) is list, 'record roster')
        byorigin = {}
        for rec in records:
            origin = rec['created_at'][:10]
            require(origin not in byorigin, 'duplicate record origin')
            byorigin[origin] = rec
        require(tuple(sorted(x for x in byorigin if x < '2026-03-31')) == origins,
                'complete historical origin roster')
        attempts = _index(decoded['attempts']['attempts'])
        outcomes = _index(decoded['outcomes']['outcomes'])
        errors = _index(decoded['errors']['errors'])
        require(set(outcomes) <= set(attempts) and set(errors) == set(outcomes),
                'outcome/error roster and missingness')
        policy = canonical_hash(dict(schema=SCHEMA, full_policy=full_policy_id,
                                     model_source=full_model_source_sha256,
                                     source_pins=expected_sha256, origins=origins, symbols=symbols,
                                     arithmetic='Fraction(Decimal(str(decoded_number)))',
                                     validation_tolerance=str(TOLERANCE)))
        rows, expected_ids = [], set()
        for origin in origins:
            rec = byorigin[origin]; md = rec['metadata']; j = days.index(origin)
            require(j > 0 and j + 6 < len(days), 'calendar target/maturity support')
            start, end = days[j+1], days[j+5]
            require(end <= '2026-03-31', 'historical target boundary')
            require(rec['model_id'] == FULL_PRODUCER and rec['model_version'] == FULL_VERSION
                    and type(rec['horizon_sessions']) is int and rec['horizon_sessions'] == 5
                    and rec['units'] == 'period_return' and md['config_fingerprint'] == full_policy_id
                    and md['return_convention'] == CONVENTION and type(md['feature_lag_sessions']) is int
                    and md['feature_lag_sessions'] == 1, 'full model/target identity')
            require(rec['information_cutoff'][:10] == days[j-1]
                    and md['training_information_cutoff'][:10] == days[j-1]
                    and md['reference_training_cutoff'][:10] == days[j-1]
                    and md['target_start'][:10] == start and md['target_end'][:10] == end
                    and md['target_start_price_convention'] ==
                    'reported_adjusted_next_session_open_training_proxy_not_observed_fill',
                    'reference cutoff and adjusted OPEN basis')
            require(set(rec['values']) == set(symbols), 'complete per-origin symbols')
            reference = number(md['reference_mean']); require(reference > -1, 'reference simple return')
            record_sha = canonical_hash(rec)
            due = calendar[end]['close_ns']; available = calendar[days[j+6]]['open_ns']
            for symbol in symbols:
                full = number(rec['values'][symbol]); require(full > -1, 'full simple return')
                aid = canonical_hash(dict(record_sha256=record_sha, symbol=symbol,
                                          target_start=start, target_end=end, return_convention=CONVENTION))
                expected_ids.add(aid); require(aid in attempts, 'missing formed attempt')
                a = attempts[aid]
                expected = dict(symbol=symbol, origin=origin, due_session=end, target_start=start,
                                horizon_sessions=5, record_sha256=record_sha,
                                model_source_sha256=full_model_source_sha256,
                                model_id=FULL_PRODUCER, model_version=FULL_VERSION,
                                config_fingerprint=full_policy_id)
                require(all(type(a.get(k)) is type(v) and a.get(k) == v for k,v in expected.items())
                        and number(a['forecast']) == full, 'attempt source binding')
                actual = outcome_at = residual = None
                if aid in outcomes:
                    o, e = outcomes[aid], errors[aid]
                    identity = dict(symbol=symbol, origin=origin, target_start=start, due_session=end)
                    require(all(o.get(k) == v and e.get(k) == v for k,v in identity.items())
                            and o.get('publication_attested') is False
                            and o.get('target_is_public_open_proxy') is True, 'label identity/basis')
                    op, close = number(o['source_adjusted_open']), number(o['source_adjusted_close'])
                    require(op > 0 and close > 0, 'positive adjusted prices')
                    actual = number(o['realized_simple_return'])
                    require(actual >= -1 and abs(actual - (close/op-1)) <= TOLERANCE,
                            'outcome arithmetic')
                    outcome_at = stamp(o['available_at_simulated'])
                    require(outcome_at == available and outcome_at > due
                            and stamp(e['available_at_simulated']) == available, 'modeled label maturity')
                    require(number(e['forecast']) == full and number(e['actual']) == actual
                            and e.get('operational_risk_admitted') is False, 'saved error binding')
                    saved_error = number(e['error_actual_minus_forecast'])
                    require(abs(saved_error - (actual-full)) <= TOLERANCE, 'full error arithmetic')
                    residual = actual-reference
                    require(abs(residual - (saved_error+full-reference)) <= TOLERANCE,
                            'reference derivation crosscheck')
                ident = canonical_hash(dict(schema=SCHEMA, policy=policy, original_attempt=aid,
                                            record_sha256=record_sha, reference=str(reference), symbol=symbol,
                                            origin=origin, target_start=start, target_end=end))
                require(ident != aid, 'distinct reference identity')
                rows.append(ReferenceRow(ident, aid, record_sha, symbol, origin, start, end,
                                         calendar[origin]['open_ns'], calendar[start]['open_ns'], due,
                                         outcome_at, reference, actual, residual))
        require(set(attempts) == expected_ids and len(rows) == len(expected_ids),
                'no omitted or foreign historical attempts')
        return ReferenceLedger(tuple(rows), tuple(sorted(expected_sha256.items())), policy)
    except ValueError as exc:
        if str(exc).startswith('NOT_EVALUABLE:'):
            raise
        raise ValueError('NOT_EVALUABLE:invalid source value') from exc
    except (KeyError, TypeError, IndexError, ArithmeticError, OverflowError) as exc:
        raise ValueError('NOT_EVALUABLE:invalid source structure or arithmetic') from exc
