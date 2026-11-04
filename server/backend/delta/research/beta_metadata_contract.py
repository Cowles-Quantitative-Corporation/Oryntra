"""Disabled pure, synthetic-only metadata query/inventory contract.

No database connection, CLI, file/network access, values, probabilities or Beta
consumer. A matched supplied manifest is not source custody or completeness.
Availability below is conditional declared metadata, not publication attestation
or a replacement for native feature/forecast admissibility checks.
"""
from datetime import date, datetime, timezone
import hashlib
import json
import math
import re
from zoneinfo import ZoneInfo

from .reference_ledger import stamp

SCHEMA = 'DELTA_SYNTHETIC_BETA_METADATA_V1'
SERIES = tuple('BAMLC0A0CM BAMLH0A0HYM2 CPIAUCSL CPILFESL DFII10 DFII5 DGS10 DGS2 DGS30 DGS3MO DGS5 GDPC1 ICSA INDPRO PAYEMS PCEPI PCEPILFE RSAFS T10YIE T5YIE UNRATE VIXCLS'.split())
COLUMNS = ('series_id', 'observation_date', 'realtime_start', 'realtime_end',
           'release_at', 'available_to_model_at', 'eligible_session', 'source',
           'availability_precision')
METADATA_SQL = ('SELECT rowid AS metadata_row_id, ' + ', '.join(COLUMNS) +
                ' FROM macro_revisions WHERE series_id IN (' + ','.join('?' for _ in SERIES) +
                ') ORDER BY series_id, rowid')
WINDOW_START, WINDOW_END = '2026-04-29', '2026-05-20'
MAX_ROWS, MAX_DIAGNOSTICS, MAX_STRING = 256, 128, 2048
_ALFRED = {'FRED/ALFRED-output_type=1-realtime-period-network', 'FRED/ALFRED-output_type=3-network'}
_FRED = 'FRED-output_type=1-observation-dated-network'
_PRECISIONS = {'date_only_conservative_next_session', 'exact_external_timestamp'}
_NY = ZoneInfo('America/New_York')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


class ContractError(ValueError):
    """No successful inventory; evidence is bounded and metadata-only."""
    def __init__(self, reason, evidence=()):
        super().__init__('NOT_EVALUABLE:' + reason)
        self.reason, self.evidence = reason, tuple(evidence)


def _need(ok, reason):
    if not ok:
        raise ContractError(reason)


def _sha(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _day(value):
    if type(value) is not str or re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) is None:
        raise ValueError('explicit date')
    return date.fromisoformat(value)


def _ny_day(ns):
    # Whole seconds suffice only for calendar-date conversion; comparisons retain ns.
    return datetime.fromtimestamp(ns // 10**9, timezone.utc).astimezone(_NY).date()


def _binding(binding):
    _need(type(binding) is dict and set(binding) == {'source_sha256', 'schema_sha256', 'selector_sha256'}, 'SOURCE_BINDING')
    _need(all(_sha(v) for v in binding.values()), 'SOURCE_BINDING')
    _need(binding['selector_sha256'] == digest(METADATA_SQL), 'SELECTOR_BINDING')
    return dict(binding)


def _rows(rows, remaining):
    out = []
    for row in rows:
        _need(len(out) < remaining, 'ROW_BUDGET')
        _need(type(row) is dict and set(COLUMNS) | {'metadata_row_id'} <= set(row)
              and set(row) <= set(COLUMNS) | {'metadata_row_id', 'disposition'}, 'METADATA_FIELDS_ONLY')
        _need(type(row['metadata_row_id']) is int and row['metadata_row_id'] > 0, 'ROW_ID')
        _need(row['series_id'] in SERIES, 'SERIES')
        # Scalar metadata only; don't copy/serialize an arbitrary object or payload.
        for value in row.values():
            _need(value is None or type(value) in (str, int, float, bool), 'METADATA_SCALAR')
            _need(type(value) is not str or len(value) <= MAX_STRING, 'METADATA_STRING_BUDGET')
            _need(type(value) is not float or math.isfinite(value), 'METADATA_FINITE')
            _need(type(value) is not int or -(2**63) <= value < 2**63, 'METADATA_INTEGER_BUDGET')
        out.append(dict(row))
    return out


def seed_commitment(rows, binding):
    """Pure commitment construction, NOT evidence that a seed is complete.

    The caller must obtain/pin its expected manifest digest independently. This
    helper grants no extraction authority and performs no source authentication.
    """
    binding = _binding(binding)
    rows = _rows(rows, MAX_ROWS)
    return dict(schema=SCHEMA, scope='INVENTED_PREFIX_METADATA_ONLY', source=binding,
                vintage_before=WINDOW_START, row_count=len(rows), rows_sha256=digest(rows),
                keyset_sha256=digest(sorted([[r['metadata_row_id'], r['series_id'],
                                             r['observation_date'], r['realtime_start']]
                                            for r in rows], key=lambda k: k[0])))


def inventory(seed_rows, seed_manifest, candidate_rows, *, source_binding, cutoffs,
              expected_seed_manifest_sha256=None, enabled=False,
              row_limit=MAX_ROWS, diagnostic_limit=MAX_DIAGNOSTICS,
              declared_source_facts=None):
    """Bounded supplied-header inventory; no source read or readiness decision.

    Contexts explicitly supply session_date/session_open, rather than treating
    midnight as an exchange open. They are invented contracts, not a calendar
    qualification. Provenance and eligibility are independent output dimensions.
    """
    _need(enabled is True, 'DISABLED')
    _need(type(row_limit) is int and 0 < row_limit <= MAX_ROWS, 'ROW_LIMIT')
    _need(type(diagnostic_limit) is int and 0 <= diagnostic_limit <= MAX_DIAGNOSTICS, 'DIAGNOSTIC_LIMIT')
    source_binding = _binding(source_binding)
    seeds = _rows(seed_rows, row_limit)
    _need(type(seed_manifest) is dict and set(seed_manifest) == {'schema', 'scope', 'source', 'vintage_before', 'row_count', 'rows_sha256', 'keyset_sha256'}
          and type(seed_manifest.get('source')) is dict, 'SEED_MANIFEST')
    seed_binding = _binding(seed_manifest['source'])
    _need(seed_manifest['schema'] == SCHEMA and seed_manifest['scope'] == 'INVENTED_PREFIX_METADATA_ONLY'
          and seed_manifest['vintage_before'] == WINDOW_START
          and type(seed_manifest['row_count']) is int and 0 <= seed_manifest['row_count'] <= MAX_ROWS
          and _sha(seed_manifest['rows_sha256']) and _sha(seed_manifest['keyset_sha256']), 'SEED_MANIFEST')
    _need(_sha(expected_seed_manifest_sha256) and digest(seed_manifest) == expected_seed_manifest_sha256, 'SEED_MANIFEST_PIN')
    _need(seed_manifest == seed_commitment(seeds, seed_binding), 'SEED_CONTENT_BINDING')
    candidates = _rows(candidate_rows, row_limit - len(seeds))
    _need(type(cutoffs) in (list, tuple) and 0 < len(cutoffs) <= 15, 'CUTOFF_ROSTER')
    contexts = []
    for c in cutoffs:
        _need(type(c) is dict and set(c) == {'id', 'at', 'session_date', 'session_open'}, 'CUTOFF_CONTEXT')
        _need(type(c['id']) is str and 0 < len(c['id']) <= 64, 'CUTOFF_ID')
        try:
            ns, opened, day = stamp(c['at']), stamp(c['session_open']), _day(c['session_date'])
            _need(_ny_day(opened) == day, 'SESSION_CONTEXT')
        except (ValueError, OverflowError, OSError) as exc:
            raise ContractError('CUTOFF_CLOCK') from exc
        contexts.append((dict(c), ns, opened, day))
    _need(len({c[0]['id'] for c in contexts}) == len(contexts), 'CUTOFF_ID')
    _need([c[1] for c in contexts] == sorted(c[1] for c in contexts), 'CUTOFF_ORDER')
    facts = {} if declared_source_facts is None else declared_source_facts
    _need(type(facts) is dict and set(facts) <= {'credit_missing_observations', 'quarantined_keys'}
          and all(type(v) is int and 0 <= v <= 10**6 for v in facts.values()), 'DECLARED_SOURCE_FACTS')
    diagnostics, result_rows, groups, identities = [], [], {}, {}

    def note(kind, record):
        if len(diagnostics) >= diagnostic_limit:
            raise ContractError('DIAGNOSTIC_BUDGET', diagnostics)
        diagnostics.append(dict(kind=kind, identity=record['identity'], metadata=dict(record['metadata'])))

    for stream, rows, binding in (('SEED', seeds, seed_binding), ('CANDIDATE', candidates, source_binding)):
        for raw in rows:
            record = dict(identity=[binding['source_sha256'], raw['metadata_row_id']],
                          stream=stream, metadata=raw, vintage_provenance='UNRESOLVED',
                          eligibility={}, clock_issues=[])
            result_rows.append(record)
            parsed = {}
            for field in ('observation_date', 'realtime_start', 'eligible_session', 'realtime_end'):
                if field == 'realtime_end' and raw[field] is None:
                    parsed[field] = None  # Explicit open/censored vintage interval.
                    continue
                try:
                    parsed[field] = _day(raw[field])
                except (ValueError, TypeError):
                    record['clock_issues'].append('NULL_CLOCK' if raw[field] is None else 'UNPARSEABLE_CLOCK')
            for field in ('release_at', 'available_to_model_at'):
                try:
                    parsed[field] = stamp(raw[field])
                except (ValueError, TypeError, OverflowError):
                    record['clock_issues'].append('NULL_CLOCK' if raw[field] is None else 'UNPARSEABLE_CLOCK')
            vintage = parsed.get('realtime_start')
            if vintage is not None:
                record['vintage_provenance'] = ('PRE_WINDOW' if vintage < _day(WINDOW_START) else
                    'NEW_WINDOW_VINTAGE' if vintage <= _day(WINDOW_END) else 'FUTURE_HEADER')
                _need(stream != 'SEED' or vintage < _day(WINDOW_START), 'SEED_PREFIX_PREDICATE')
            # An ALFRED vintage cannot establish a future observation. Later
            # availability must not make this source-ordering anomaly eligible.
            # Observation-dated FRED has its separate equality/precision contract.
            observation = parsed.get('observation_date')
            if raw['source'] in _ALFRED and observation is not None and vintage is not None and observation > vintage:
                record['clock_issues'].append('SOURCE_ORDERING_ANOMALY')
            if not record['clock_issues']:
                available = parsed['available_to_model_at']
                try:
                    available_day = _ny_day(available)
                except (ValueError, OverflowError, OSError):
                    record['clock_issues'].append('UNPARSEABLE_CLOCK')
                else:
                    if ((parsed['realtime_end'] is not None and parsed['realtime_end'] < vintage)
                        or parsed['release_at'] > available
                        or max(parsed['observation_date'], vintage) > available_day
                        or (raw['availability_precision'] != 'exact_external_timestamp' and parsed['eligible_session'] <= vintage)):
                        record['clock_issues'].append('INCONSISTENT_CLOCK')
                if raw['source'] == _FRED:
                    if raw['availability_precision'] != 'observation_date_conservative_next_session' or parsed['observation_date'] != vintage:
                        record['clock_issues'].append('SOURCE_METADATA_INCONSISTENT')
                elif raw['source'] not in _ALFRED or raw['availability_precision'] not in _PRECISIONS:
                    record['clock_issues'].append('SOURCE_METADATA_UNKNOWN')
            if not record['clock_issues'] and any(parsed['eligible_session'] == day and
                    parsed['available_to_model_at'] >= opened for _, _, opened, day in contexts):
                record['clock_issues'].append('AVAILABLE_NOT_BEFORE_DECLARED_OPEN')
            for issue in sorted(set(record['clock_issues'])):
                note(issue, record)
            for c, cutoff_ns, opened, session_day in contexts:
                if record['clock_issues']:
                    eligibility = 'INVALID_METADATA'
                elif raw.get('disposition') != 'stored':
                    eligibility = 'UNKNOWN_DISPOSITION' if raw.get('disposition') is None else 'UNAVAILABLE_DISPOSITION'
                elif (parsed['available_to_model_at'] > cutoff_ns or parsed['eligible_session'] > session_day
                      or (parsed['eligible_session'] == session_day and cutoff_ns < opened)):
                    eligibility = 'PENDING'
                elif parsed['eligible_session'] == session_day and parsed['available_to_model_at'] >= opened:
                    eligibility = 'INVALID_METADATA'
                else:
                    eligibility = 'AVAILABLE_DECLARED_METADATA'
                record['eligibility'][c['id']] = eligibility
            key = (raw['series_id'], raw['observation_date'], raw['realtime_start'])
            groups.setdefault(key, []).append(record)
            identities.setdefault(tuple(record['identity']), []).append(record)
    for group in identities.values():
        if len({digest(r['metadata']) for r in group}) > 1:
            for r in group:
                note('CONFLICTING_SOURCE_ROW_ID', r)
                r['eligibility'] = {c[0]['id']: 'CONFLICTING_METADATA' for c in contexts}
    duplicate_count = 0
    for group in groups.values():
        contents = {digest({k: v for k, v in r['metadata'].items() if k != 'metadata_row_id'}) for r in group}
        if len(contents) > 1:
            for r in group:
                note('CONFLICTING_KEY', r)
                r['eligibility'] = {c[0]['id']: 'CONFLICTING_METADATA' for c in contexts}
        elif len(group) > 1:
            duplicate_count += len(group) - 1
            for r in group[1:]:
                r['duplicate_of'] = list(group[0]['identity'])
    return dict(schema=SCHEMA, scope='SYNTHETIC_ONLY', status='METADATA_EVIDENCE_ONLY',
                source_binding=source_binding, seed_manifest_sha256=expected_seed_manifest_sha256,
                rows=result_rows, rows_sha256=digest(result_rows), diagnostics=diagnostics,
                exact_duplicate_count=duplicate_count, processed_rows=len(result_rows),
                observed_series=sorted({r['metadata']['series_id'] for r in result_rows}),
                missing_series=sorted(set(SERIES) - {r['metadata']['series_id'] for r in result_rows}),
                contexts=[c[0] for c in contexts], declared_source_facts=dict(facts),
                source_facts_verified=False, capture_completeness='UNKNOWN',
                historical_publication_attested=False, numeric_readiness='NOT_ASSESSED',
                fresh_beta_artifact=False, actual_source_authorized=False)
