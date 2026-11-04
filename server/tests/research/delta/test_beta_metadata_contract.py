"""Pure invented headers and owned SQLite fixtures; no actual source access."""
from copy import deepcopy
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from backend.delta.research import beta_metadata_contract as b


def row(ident=1, **changes):
    result = dict(metadata_row_id=ident, series_id='CPIAUCSL', observation_date='2026-03-01',
                  realtime_start='2026-04-10', realtime_end=None,
                  release_at='2026-04-10T12:30:00Z', available_to_model_at='2026-04-10T23:59:59.999999999Z',
                  eligible_session='2026-04-13', source='FRED/ALFRED-output_type=3-network',
                  availability_precision='date_only_conservative_next_session', disposition='stored')
    result.update(changes)
    return result


def context(ident='may1', at='2026-05-01T13:30:00Z', day='2026-05-01', opened='2026-05-01T09:30:00-04:00'):
    return dict(id=ident, at=at, session_date=day, session_open=opened)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.binding = dict(source_sha256='a'*64, schema_sha256='b'*64, selector_sha256=b.digest(b.METADATA_SQL))
        self.seed = [row()]
        self.manifest = b.seed_commitment(self.seed, self.binding)
        self.pin = b.digest(self.manifest)
    def call(self, candidates=(), **kwargs):
        values = dict(source_binding=self.binding, cutoffs=[context()],
                      expected_seed_manifest_sha256=self.pin, enabled=True)
        values.update(kwargs)
        return b.inventory(self.seed, self.manifest, candidates, **values)
    def test_disabled_before_iteration(self):
        class Poison:
            def __iter__(self): raise AssertionError('iterated')
        with self.assertRaisesRegex(b.ContractError, 'DISABLED'):
            b.inventory(Poison(), {}, Poison(), source_binding={}, cutoffs=[])
    def test_carried_seed_and_new_window_pending_are_independent(self):
        # Both pending seed and pending new vintage remain present, not zero/available.
        self.seed = [row(available_to_model_at='2026-05-04T12:00:00Z', eligible_session='2026-05-04')]
        self.manifest = b.seed_commitment(self.seed, self.binding); self.pin = b.digest(self.manifest)
        new = row(2, realtime_start='2026-04-30', release_at='2026-04-30T12:30:00Z',
                  available_to_model_at='2026-05-01T14:00:00Z', eligible_session='2026-05-04')
        result = self.call([new]); a, c = result['rows']
        self.assertEqual((a['stream'], a['vintage_provenance'], a['eligibility']['may1']), ('SEED', 'PRE_WINDOW', 'PENDING'))
        self.assertEqual((c['stream'], c['vintage_provenance'], c['eligibility']['may1']), ('CANDIDATE', 'NEW_WINDOW_VINTAGE', 'PENDING'))
    def test_per_observation_versions_and_monthly_carry_forward(self):
        self.seed += [row(2, observation_date='2026-02-01'), row(3, realtime_start='2026-04-11', available_to_model_at='2026-04-11T23:59:59Z')]
        self.manifest = b.seed_commitment(self.seed, self.binding); self.pin = b.digest(self.manifest)
        result = self.call()
        self.assertEqual(len(result['rows']), 3)
        self.assertTrue(all(r['eligibility']['may1'] == 'AVAILABLE_DECLARED_METADATA' for r in result['rows']))
        self.assertEqual(result['capture_completeness'], 'UNKNOWN')
        self.assertIn('PAYEMS', result['missing_series'])
    def test_seed_pin_and_all_commitment_fields_bound(self):
        for field in self.manifest:
            with self.subTest(field=field):
                changed = deepcopy(self.manifest); changed[field] = 'tampered'
                with self.assertRaises(b.ContractError):
                    b.inventory(self.seed, changed, [], source_binding=self.binding, cutoffs=[context()],
                                expected_seed_manifest_sha256=b.digest(changed), enabled=True)
        self.seed[0]['available_to_model_at'] = '2026-04-11T00:00:00Z'
        with self.assertRaisesRegex(b.ContractError, 'SEED_CONTENT_BINDING'): self.call()
    def test_wrong_manifest_pin_source_selector_and_prefix_rejected(self):
        with self.assertRaisesRegex(b.ContractError, 'SEED_MANIFEST_PIN'): self.call(expected_seed_manifest_sha256='c'*64)
        wrong = dict(self.binding, selector_sha256='d'*64)
        with self.assertRaisesRegex(b.ContractError, 'SELECTOR_BINDING'): self.call(source_binding=wrong)
        self.seed = [row(realtime_start='2026-04-30')]
        self.manifest = b.seed_commitment(self.seed, self.binding); self.pin = b.digest(self.manifest)
        with self.assertRaisesRegex(b.ContractError, 'SEED_PREFIX_PREDICATE'): self.call()
    def test_source_distinct_from_seed_and_metadata_identity_retained(self):
        binding = dict(self.binding, source_sha256='c'*64)
        result = self.call([row(1)], source_binding=binding)
        self.assertEqual(result['rows'][0]['identity'], ['a'*64, 1])
        self.assertEqual(result['rows'][1]['identity'], ['c'*64, 1])
        self.assertEqual(result['exact_duplicate_count'], 1)
    def test_revision_and_conflicting_end_preserved_not_last_write_wins(self):
        new = row(2, realtime_start='2026-05-01', release_at='2026-05-01T11:00:00Z',
                  available_to_model_at='2026-05-01T12:00:00Z', eligible_session='2026-05-04')
        result = self.call([new])
        self.assertEqual([r['vintage_provenance'] for r in result['rows']], ['PRE_WINDOW', 'NEW_WINDOW_VINTAGE'])
        result = self.call([row(2, realtime_end='2026-04-30')])
        self.assertEqual(len(result['rows']), 2)
        self.assertTrue(all(r['eligibility']['may1'] == 'CONFLICTING_METADATA' for r in result['rows']))
        self.assertEqual([d['kind'] for d in result['diagnostics']], ['CONFLICTING_KEY']*2)
    def test_exact_duplicates_are_counted_and_rows_retained(self):
        result = self.call([row(2), row(3)])
        self.assertEqual(result['exact_duplicate_count'], 2)
        self.assertEqual(len(result['rows']), 3)
        self.assertEqual(result['rows'][2]['duplicate_of'], ['a'*64, 1])
    def test_null_bad_wrong_type_and_reverse_clocks_retained(self):
        examples = [('observation_date', None), ('realtime_start', 'bad'), ('eligible_session', 123),
                    ('release_at', None), ('available_to_model_at', 'bad'), ('realtime_end', '2026-04-09')]
        for field, value in examples:
            with self.subTest(field=field):
                result = self.call([row(2, **{field: value})])
                self.assertEqual(result['rows'][1]['metadata'][field], value)
                self.assertTrue(result['diagnostics'])
                self.assertNotEqual(result['rows'][1]['eligibility']['may1'], 'AVAILABLE_DECLARED_METADATA')
    def test_out_of_range_local_calendar_conversion_retains_bad_header(self):
        raw = row(2, observation_date='0001-01-01', realtime_start='0001-01-01',
                  release_at='0001-01-01T00:00:00Z', available_to_model_at='0001-01-01T00:00:00Z')
        result = self.call([raw])
        self.assertEqual(result['rows'][1]['metadata']['available_to_model_at'], raw['available_to_model_at'])
        self.assertIn('UNPARSEABLE_CLOCK', result['rows'][1]['clock_issues'])
    def test_open_end_is_not_null_clock(self):
        result = self.call()
        self.assertEqual(result['diagnostics'], [])
        self.assertIsNone(result['rows'][0]['metadata']['realtime_end'])
    def test_nanosecond_boundary_and_offset_equivalence(self):
        cutoff = '2026-05-01T13:29:59Z'
        for delta, expected in [('', 'AVAILABLE_DECLARED_METADATA'), ('.000000001', 'PENDING')]:
            self.seed = [row(available_to_model_at='2026-05-01T13:29:59'+delta+'Z')]
            self.manifest = b.seed_commitment(self.seed, self.binding); self.pin = b.digest(self.manifest)
            result = self.call(cutoffs=[context(at=cutoff)])
            self.assertEqual(result['rows'][0]['eligibility']['may1'], expected)
        self.assertEqual(b.stamp('2026-05-01T09:30:00-04:00'), b.stamp('2026-05-01T13:30:00Z'))
        self.assertEqual(b.stamp('2026-03-06T09:30:00-05:00'), b.stamp('2026-03-06T14:30:00Z'))
    def test_midnight_is_not_session_open(self):
        self.seed = [row(eligible_session='2026-05-01')]
        self.manifest = b.seed_commitment(self.seed, self.binding); self.pin = b.digest(self.manifest)
        result = self.call(cutoffs=[context(at='2026-05-01T00:00:00Z')])
        self.assertEqual(result['rows'][0]['eligibility']['may1'], 'PENDING')
        self.assertEqual(self.call()['rows'][0]['eligibility']['may1'], 'AVAILABLE_DECLARED_METADATA')
    def test_invalid_context_offsets_and_context_count(self):
        with self.assertRaisesRegex(b.ContractError, 'CUTOFF_CLOCK'):
            self.call(cutoffs=[context(at='2026-05-01T00:00:00+00:60')])
        with self.assertRaisesRegex(b.ContractError, 'CUTOFF_ROSTER'): self.call(cutoffs=[context()]*16)
        with self.assertRaisesRegex(b.ContractError, 'CUTOFF_ID'): self.call(cutoffs=[context()]*2)
    def test_date_only_waits_later_session_and_fred_flags_not_upgraded(self):
        result = self.call([row(2, eligible_session='2026-04-10')])
        self.assertTrue(result['diagnostics'])
        fred = row(2, source='FRED-output_type=1-observation-dated-network',
                   observation_date='2026-04-10', availability_precision='observation_date_conservative_next_session')
        result = self.call([fred])
        self.assertEqual(result['rows'][1]['metadata']['source'], fred['source'])
        self.assertFalse(result['historical_publication_attested'])
        result = self.call([row(2, source='UNKNOWN')])
        self.assertIn('SOURCE_METADATA_UNKNOWN', [d['kind'] for d in result['diagnostics']])
    def test_missing_and_nonstored_disposition_never_default_to_stored(self):
        raw = row(2, observation_date='2026-02-01'); del raw['disposition']
        result = self.call([raw, row(3, observation_date='2026-01-01', disposition='quarantined')])
        self.assertEqual(result['rows'][1]['eligibility']['may1'], 'UNKNOWN_DISPOSITION')
        self.assertEqual(result['rows'][2]['eligibility']['may1'], 'UNAVAILABLE_DISPOSITION')
    def test_future_header_not_lost(self):
        raw = row(2, realtime_start='2026-05-22', release_at='2026-05-22T12:00:00Z',
                  available_to_model_at='2026-05-22T23:00:00Z', eligible_session='2026-05-26')
        result = self.call([raw]); r = result['rows'][1]
        self.assertEqual((r['vintage_provenance'], r['eligibility']['may1']), ('FUTURE_HEADER', 'PENDING'))
    def test_row_and_diagnostic_exact_limits_fail_closed(self):
        self.assertEqual(self.call([row(2)], row_limit=2)['processed_rows'], 2)
        with self.assertRaisesRegex(b.ContractError, 'ROW_BUDGET'): self.call([row(2), row(3)], row_limit=2)
        bad = row(2, observation_date='bad')
        self.assertEqual(len(self.call([bad], diagnostic_limit=1)['diagnostics']), 1)
        with self.assertRaisesRegex(b.ContractError, 'DIAGNOSTIC_BUDGET') as caught:
            self.call([bad, row(3, observation_date=None)], diagnostic_limit=1)
        self.assertEqual(len(caught.exception.evidence), 1)
    def test_no_payload_fields_or_nonfinite_containers_accepted(self):
        for patch in [dict(value=999), dict(value_text='SECRET'), dict(probabilities=[1, 0]),
                      dict(release_at=[]), dict(release_at=float('nan'))]:
            with self.subTest(patch=tuple(patch)):
                with self.assertRaises(b.ContractError): self.call([row(2, **patch)])
    def test_physical_identity_collision_cannot_describe_two_different_keys(self):
        result = self.call([row(1, observation_date='2026-02-01')])
        self.assertTrue(all(r['eligibility']['may1'] == 'CONFLICTING_METADATA' for r in result['rows']))
        self.assertEqual([d['kind'] for d in result['diagnostics']], ['CONFLICTING_SOURCE_ROW_ID']*2)
    def test_availability_at_declared_open_is_invalid_not_promoted_later(self):
        raw = row(2, observation_date='2026-02-01', eligible_session='2026-05-01', available_to_model_at='2026-05-01T13:30:00Z')
        result = self.call([raw])
        self.assertIn('AVAILABLE_NOT_BEFORE_DECLARED_OPEN', result['rows'][1]['clock_issues'])
        self.assertEqual(result['rows'][1]['eligibility']['may1'], 'INVALID_METADATA')
    def test_fifteen_distinct_contexts_are_preserved(self):
        dates = ['01', '04', '05', '06', '07', '08', '11', '12', '13', '14', '15', '18', '19', '20', '21']
        contexts = [context(ident=d, day='2026-05-'+d, at='2026-05-'+d+'T13:30:00Z', opened='2026-05-'+d+'T09:30:00-04:00') for d in dates]
        result = self.call(cutoffs=contexts)
        self.assertEqual(result['contexts'], contexts)
        self.assertEqual(list(result['rows'][0]['eligibility']), dates)
    def test_carried_seed_can_be_unavailable_independent_of_provenance(self):
        self.seed = [row(disposition='quarantined')]
        self.manifest = b.seed_commitment(self.seed, self.binding); self.pin = b.digest(self.manifest)
        result = self.call(); r = result['rows'][0]
        self.assertEqual((r['stream'], r['vintage_provenance'], r['eligibility']['may1']),
                         ('SEED', 'PRE_WINDOW', 'UNAVAILABLE_DISPOSITION'))
    def test_ALFRED_seed_future_observation_is_retained_but_never_available(self):
        self.seed = [row(observation_date='2026-04-12', realtime_start='2026-04-10',
                         available_to_model_at='2026-04-12T23:59:59Z')]
        self.manifest = b.seed_commitment(self.seed, self.binding); self.pin = b.digest(self.manifest)
        result = self.call(); r = result['rows'][0]
        self.assertEqual((r['stream'], r['vintage_provenance']), ('SEED', 'PRE_WINDOW'))
        self.assertEqual(r['metadata']['observation_date'], '2026-04-12')
        self.assertIn('SOURCE_ORDERING_ANOMALY', r['clock_issues'])
        self.assertEqual(r['eligibility']['may1'], 'INVALID_METADATA')
        self.assertEqual(result['diagnostics'][0]['identity'], ['a'*64, 1])
    def test_ALFRED_candidate_future_observation_stays_invalid_at_later_cutoffs(self):
        raw = row(2, observation_date='2026-05-01', realtime_start='2026-04-30',
                  release_at='2026-04-30T12:00:00Z', available_to_model_at='2026-05-01T12:00:00Z',
                  eligible_session='2026-05-04')
        contexts = [context(), context('may4', at='2026-05-04T13:30:00Z', day='2026-05-04', opened='2026-05-04T13:30:00Z')]
        result = self.call([raw], cutoffs=contexts); r = result['rows'][1]
        self.assertEqual((r['stream'], r['vintage_provenance']), ('CANDIDATE', 'NEW_WINDOW_VINTAGE'))
        self.assertEqual(r['metadata'], raw)
        self.assertIn('SOURCE_ORDERING_ANOMALY', r['clock_issues'])
        self.assertEqual(set(r['eligibility'].values()), {'INVALID_METADATA'})
    def test_ALFRED_equal_and_older_observations_remain_valid(self):
        for observed in ('2026-04-10', '2026-03-01'):
            with self.subTest(observed=observed):
                self.seed = [row(observation_date=observed)]
                self.manifest = b.seed_commitment(self.seed, self.binding); self.pin = b.digest(self.manifest)
                result = self.call()
                self.assertEqual(result['diagnostics'], [])
                self.assertEqual(result['rows'][0]['eligibility']['may1'], 'AVAILABLE_DECLARED_METADATA')
    def test_observation_dated_FRED_uses_distinct_equality_contract(self):
        for observed, expected in [('2026-04-10', 'AVAILABLE_DECLARED_METADATA'), ('2026-04-12', 'INVALID_METADATA')]:
            with self.subTest(observed=observed):
                self.seed = [row(observation_date=observed, source='FRED-output_type=1-observation-dated-network',
                                 available_to_model_at='2026-04-12T23:59:59Z',
                                 availability_precision='observation_date_conservative_next_session')]
                self.manifest = b.seed_commitment(self.seed, self.binding); self.pin = b.digest(self.manifest)
                result = self.call(); r = result['rows'][0]
                self.assertEqual(r['eligibility']['may1'], expected)
                self.assertNotIn('SOURCE_ORDERING_ANOMALY', r['clock_issues'])
                if observed == '2026-04-12': self.assertIn('SOURCE_METADATA_INCONSISTENT', r['clock_issues'])
    def test_source_facts_not_verified_or_readiness(self):
        result = self.call(declared_source_facts=dict(credit_missing_observations=44, quarantined_keys=28))
        self.assertEqual(result['declared_source_facts']['quarantined_keys'], 28)
        self.assertFalse(result['source_facts_verified']); self.assertFalse(result['fresh_beta_artifact'])
        self.assertFalse(result['actual_source_authorized']); self.assertEqual(result['numeric_readiness'], 'NOT_ASSESSED')
        self.assertNotIn('probabilities', json.dumps(result)); self.assertNotIn('value_text', json.dumps(result))


class QueryTests(unittest.TestCase):
    def fixture(self, db):
        columns = ','.join(x+' TEXT' for x in b.COLUMNS)
        db.execute('CREATE TABLE macro_revisions('+columns+', value REAL, value_text TEXT, all_source_fields_json TEXT, probabilities TEXT)')
        db.execute('CREATE TABLE unrelated_secrets(secret TEXT)')
        for ident, patch in [(1, {}), (2, dict(available_to_model_at=None)), (3, dict(realtime_start='bad'))]:
            r = row(ident, **patch)
            db.execute('INSERT INTO macro_revisions VALUES ('+','.join('?' for _ in range(len(b.COLUMNS)+4))+')',
                       [r[c] for c in b.COLUMNS]+[987654321, 'POISON', '{"price":999}', '[1,0]'])
        db.commit()
    def authorizer(self, action, table, column, *_):
        if action == sqlite3.SQLITE_READ:
            return sqlite3.SQLITE_OK if table == 'macro_revisions' and column in set(b.COLUMNS) | {'ROWID'} else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    def test_fixed_query_avoids_values_but_keeps_malformed_headers(self):
        with sqlite3.connect(':memory:') as db:
            self.fixture(db); db.row_factory = sqlite3.Row; db.set_authorizer(self.authorizer)
            rows = [dict(r) for r in db.execute(b.METADATA_SQL, b.SERIES)]
            self.assertEqual(len(rows), 3); self.assertIsNone(rows[1]['available_to_model_at'])
            self.assertEqual(rows[2]['realtime_start'], 'bad'); self.assertEqual(set(rows[0]), set(b.COLUMNS) | {'metadata_row_id'})
            for query in ['SELECT * FROM macro_revisions', 'SELECT series_id FROM macro_revisions WHERE value>0',
                          'SELECT value_text FROM macro_revisions', 'SELECT secret FROM unrelated_secrets']:
                with self.subTest(query=query):
                    with self.assertRaises(sqlite3.DatabaseError): db.execute(query).fetchall()
    def test_query_only_filters_fixed_series_not_dates(self):
        with sqlite3.connect(':memory:') as db:
            self.fixture(db)
            db.execute("UPDATE macro_revisions SET series_id='OTHER' WHERE rowid=1")
            self.assertEqual(len(db.execute(b.METADATA_SQL, b.SERIES).fetchall()), 2)
            self.assertNotIn('available_to_model_at >', b.METADATA_SQL)
            self.assertEqual(b.METADATA_SQL.count('?'), 22)
    def test_owned_wal_anchor_keeps_multiple_metadata_reads_on_one_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'invented.sqlite'
            with sqlite3.connect(path) as writer:
                self.fixture(writer); writer.execute('PRAGMA journal_mode=WAL')
                with sqlite3.connect(path) as reader:
                    reader.execute('BEGIN')
                    before = reader.execute(b.METADATA_SQL, b.SERIES).fetchall()  # First read anchors.
                    writer.execute("UPDATE macro_revisions SET available_to_model_at='2026-05-19T00:00:00Z'")
                    writer.commit()
                    self.assertEqual(reader.execute(b.METADATA_SQL, b.SERIES).fetchall(), before)
                    reader.rollback()
                    self.assertNotEqual(reader.execute(b.METADATA_SQL, b.SERIES).fetchall(), before)

if __name__ == '__main__':
    unittest.main()
