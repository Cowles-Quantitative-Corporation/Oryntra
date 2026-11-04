"""Owned tiny invented SQLite fixtures only; never opens an actual data source."""
from copy import deepcopy
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from backend.delta.research import synthetic_wal_projection as w
from backend.delta.research import qualify_reference as q


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.source=self.root/'source.sqlite';self.writer=sqlite3.connect(self.source,timeout=0)
        self.addCleanup(self.writer.close)
        self.writer.execute('CREATE TABLE ohlcv_bars(ticker TEXT,timeframe TEXT,timestamp TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,provider TEXT,adjusted INTEGER,fetched_at TEXT)')
        self.writer.execute('CREATE TABLE synthetic_fixture_metadata(nonce TEXT)')
        self.writer.execute("INSERT INTO synthetic_fixture_metadata VALUES ('invented-only')")
        self.writer.execute('CREATE TABLE unrelated_secrets(secret TEXT)');self.writer.execute("INSERT INTO unrelated_secrets VALUES ('DO_NOT_EXPORT_SYNTHETIC_SECRET')")
        self.writer.commit();self.writer.execute('PRAGMA journal_mode=WAL');self.writer.execute('PRAGMA wal_autocheckpoint=0')
        self.insert();self.writer.commit()
        stat=self.source.stat();self.config=dict(schema=w.SCHEMA,scope='INVENTED_TEMP_FIXTURE_ONLY',fixture_root=str(self.root),fixture_nonce='invented-only',source_identity=[stat.st_dev,stat.st_ino],expected_keys=[['AAPL','2026-04-01']],resources=dict(w.LIMITS))
    def insert(self,day='2026-04-01',symbol='AAPL',price=100,adjusted=1):
        self.writer.execute('INSERT INTO ohlcv_bars VALUES (?,?,?,?,?,?,?,?,?,?,?)',(symbol,'1d',day+'T04:00:00',price,price,price,price,10,'INVENTED',adjusted,'2026-10-01'))
    def call(self):return w.run_fixture(self.config,expected_config_sha256=q.digest(q.encode(self.config)),enabled=True)
    def phase(self,name,action):
        original=q.Guard.check;seen=[]
        def check(guard,phase,additional=0):
            if phase==name and not seen:seen.append(phase);action()
            return original(guard,phase,additional)
        return patch.object(q.Guard,'check',check)
    def no_complete(self):self.assertFalse((self.root/'projection/result.json').exists())
    def test_committed_WAL_row_included_main_bytes_unchanged(self):
        before=self.source.read_bytes();self.insert('2026-04-02',price=102);self.writer.commit()
        self.assertEqual(before,self.source.read_bytes());self.assertGreater(Path(str(self.source)+'-wal').stat().st_size,0)
        self.config['expected_keys'].append(['AAPL','2026-04-02']);result=self.call()
        self.assertEqual([r['close']for r in result['rows']],[100,102]);self.assertFalse(result['actual_source_authorized'])
    def test_BEGIN_is_not_snapshot_anchor(self):
        self.config['expected_keys'].append(['AAPL','2026-04-02'])
        def commit():self.insert('2026-04-02',price=102);self.writer.commit()
        with self.phase('after_begin_before_first_read',commit):result=self.call()
        self.assertEqual(len(result['rows']),2)
    def test_writer_after_anchor_does_not_change_key_value_snapshot(self):
        def commit():
            self.writer.execute("UPDATE ohlcv_bars SET close=777 WHERE ticker='AAPL'");self.insert('2026-04-02',price=102);self.writer.commit()
        with self.phase('after_anchor',commit):result=self.call()
        self.assertEqual(result['keys'],[['AAPL','2026-04-01']]);self.assertEqual(result['rows'][0]['close'],100)
        self.assertEqual(self.writer.execute('SELECT close FROM ohlcv_bars ORDER BY timestamp').fetchall(),[(777.0,),(102.0,)])
    def test_writer_between_keys_and_values_cannot_mix_versions(self):
        def commit():self.writer.execute('UPDATE ohlcv_bars SET close=888');self.writer.commit()
        with self.phase('after_keys_before_values',commit):result=self.call()
        self.assertEqual(result['rows'][0]['close'],100);self.assertEqual(self.writer.execute('SELECT close FROM ohlcv_bars').fetchone()[0],888)
    def test_uncommitted_row_excluded_and_rollback_absent(self):
        self.insert('2026-04-02',price=999);result=self.call();self.writer.rollback()
        self.assertEqual(len(result['rows']),1);self.assertEqual(self.writer.execute('SELECT count(*) FROM ohlcv_bars').fetchone()[0],1)
    def test_poison_dates_symbols_flags_and_secrets_not_exported(self):
        for day,symbol,flag in [('2026-05-01','AAPL',1),('2026-06-01','AAPL',1),('2026-04-01','OTHER',1),('2026-04-02','AAPL',0)]:self.insert(day,symbol,999999,flag)
        self.writer.commit();result=self.call();encoded=q.encode(result)
        self.assertEqual(len(result['rows']),1);self.assertNotIn(b'999999',encoded);self.assertNotIn(b'DO_NOT_EXPORT',encoded)
        self.assertTrue((self.root/'projection/result.json').exists())
    def test_NULL_is_retained_not_missing_key_or_zero(self):
        self.writer.execute('UPDATE ohlcv_bars SET open=NULL');self.writer.commit();result=self.call()
        self.assertIsNone(result['rows'][0]['open']);self.assertEqual(len(result['keys']),1)
    def test_duplicate_key_fails_before_value_read(self):
        self.insert();self.writer.commit()
        with self.phase('after_keys_before_values',lambda:self.fail('numeric stage reached')):
            with self.assertRaisesRegex(q.QualificationFailure,'KEY_ROSTER'):self.call()
        self.no_complete()
    def test_wrong_declared_schema_fails_before_values(self):
        self.writer.execute('ALTER TABLE ohlcv_bars RENAME TO original')
        self.writer.execute('CREATE TABLE ohlcv_bars(ticker TEXT,open TEXT)');self.writer.commit()
        with self.assertRaisesRegex(q.QualificationFailure,'COLUMN_TYPES'):self.call()
        self.no_complete()
    def test_disabled_authority_outside_root_existing_output_before_connect(self):
        with patch.object(w.sqlite3,'connect',side_effect=AssertionError('connect forbidden')):
            with self.assertRaisesRegex(q.QualificationFailure,'DISABLED'):w.run_fixture({})
            with self.assertRaisesRegex(q.QualificationFailure,'CONFIG_BINDING'):w.run_fixture(self.config,expected_config_sha256='0'*64,enabled=True)
            other=deepcopy(self.config);other['fixture_root']='/not-an-owned-temporary-fixture'
            with self.assertRaisesRegex(q.QualificationFailure,'TEMP_FIXTURE_ROOT'):w.run_fixture(other,expected_config_sha256=q.digest(q.encode(other)),enabled=True)
            (self.root/'projection').mkdir()
            with self.assertRaisesRegex(q.QualificationFailure,'FRESH_EXCLUSIVE'):self.call()
    def test_single_read_connection_no_mode_change_or_unrelated_query(self):
        original=sqlite3.connect;connections=[];trace=[]
        def opened(*a,**kw):
            self.assertNotIn('immutable',a[0]);self.assertTrue(a[0].endswith('?mode=ro'))
            db=original(*a,**kw);connections.append(db);db.set_trace_callback(trace.append);return db
        with patch.object(w.sqlite3,'connect',side_effect=opened):self.call()
        self.assertEqual(len(connections),1)
        self.assertEqual(sum(x.upper().startswith('BEGIN')for x in trace),1)
        self.assertEqual(sum(x.upper().startswith('ROLLBACK')for x in trace),1)
        self.assertFalse(any('JOURNAL_MODE'in x.upper()or'WAL_CHECKPOINT'in x.upper()or'UNRELATED_SECRETS'in x.upper()for x in trace))
        self.assertFalse(any(x.upper().startswith(('INSERT','UPDATE','DELETE','CREATE','ATTACH','VACUUM'))for x in trace))
    def test_wrong_identity_and_scope_fail_before_connect(self):
        with patch.object(w.sqlite3,'connect',side_effect=AssertionError('connect forbidden')):
            self.config['source_identity'][1]+=1
            with self.assertRaisesRegex(q.QualificationFailure,'SOURCE_IDENTITY'):self.call()
            self.config['scope']='ACTUAL_SOURCE'
            with self.assertRaisesRegex(q.QualificationFailure,'FIXTURE_SCOPE'):self.call()
    def test_nonfinite_number_fails_without_complete(self):
        self.writer.execute('UPDATE ohlcv_bars SET close=?',(float('inf'),));self.writer.commit()
        with self.assertRaisesRegex(q.QualificationFailure,'FINITE_OR_NULL'):self.call()
        self.no_complete()

    def test_invalid_calendar_key_rejected_before_connection(self):
        self.config['expected_keys']=[['AAPL','2026-02-30']]
        with patch.object(w.sqlite3,'connect',side_effect=AssertionError('connect forbidden')):
            with self.assertRaisesRegex(q.QualificationFailure,'KEY_DATE'):self.call()
    def test_invalid_timestamp_not_accepted_by_prefix_alone(self):
        self.writer.execute("UPDATE ohlcv_bars SET timestamp='2026-04-01NOT_A_TIMESTAMP'");self.writer.commit()
        with self.assertRaisesRegex(q.QualificationFailure,'TIMESTAMP'):self.call()
        self.no_complete()

    def test_busy_source_fails_without_retry(self):
        # Fixture owner, not method, prepares rollback-mode lock negative case.
        self.writer.execute('PRAGMA journal_mode=DELETE');self.writer.execute('BEGIN EXCLUSIVE')
        start=time.monotonic()
        with self.assertRaises(sqlite3.OperationalError):self.call()
        self.assertLess(time.monotonic()-start,2);self.writer.rollback();self.no_complete()
    def test_cancellation_at_every_boundary_releases_reader(self):
        phases=('before_connect','after_begin_before_first_read','after_anchor','after_keys_before_values','value_row','after_values','reader_closed')
        original=q.Guard.check
        for phase in phases:
            for kind in (KeyboardInterrupt,SystemExit):
                with self.subTest(phase=phase,kind=kind.__name__):
                    out=self.root/'projection'
                    if out.exists():
                        # Only previous OWNED failure evidence is removed by test owner.
                        for f in out.iterdir():f.unlink()
                        out.rmdir()
                    error=kind()
                    def check(g,p,additional=0):
                        if p==phase:raise error
                        return original(g,p,additional)
                    with patch.object(q.Guard,'check',check):
                        with self.assertRaises(kind)as caught:self.call()
                    self.assertIs(caught.exception,error);self.no_complete()
                    self.writer.execute('BEGIN IMMEDIATE');self.writer.rollback()
    def test_progress_callback_keeps_original_cause(self):
        error=q.QualificationFailure('ORIGINAL_PROGRESS_REASON');original=q.Guard.check
        def check(g,phase,additional=0):
            if phase=='sqlite_progress':raise error
            return original(g,phase,additional)
        with patch.object(q.Guard,'check',check):
            with self.assertRaises(q.QualificationFailure)as caught:self.call()
        self.assertIs(caught.exception,error);self.no_complete()
    def test_source_path_replacement_fails_before_publication(self):
        def replace():
            self.source.rename(self.root/'old-source.sqlite');self.source.write_bytes(b'not a database')
        with self.phase('after_values',replace):
            with self.assertRaisesRegex(q.QualificationFailure,'SOURCE_PATH_IDENTITY'):self.call()
        self.no_complete()
    def test_cleanup_failure_cannot_publish_or_mask_original(self):
        error=KeyboardInterrupt();release=w._release
        def fail(db):return release(db)+[('synthetic_close_uncertainty',OSError('fault'))]
        with patch.object(w,'_release',side_effect=fail),self.phase('after_anchor',lambda:(_ for _ in ()).throw(error)):
            with self.assertRaises(KeyboardInterrupt)as caught:self.call()
        self.assertIs(caught.exception,error);self.assertTrue(any('CLEANUP_UNCERTAIN'in note for note in error.__notes__));self.no_complete()
    def test_cleanup_cancellation_identity_during_rollback_and_close(self):
        original_connect=sqlite3.connect
        for boundary,timing in (('rollback','before'),('rollback','after'),('close','before'),('close','after')):
            for kind in (KeyboardInterrupt,SystemExit):
                with self.subTest(boundary=boundary,timing=timing,kind=kind.__name__):
                    out=self.root/'projection'
                    if out.exists():
                        for f in out.iterdir():f.unlink()
                        out.rmdir()
                    error=kind();owned=[]
                    class Connection:
                        def __init__(self,db):self.db=db;self.closed=False
                        def __getattr__(self,name):return getattr(self.db,name)
                        def rollback(self):
                            if boundary=='rollback'and timing=='before':raise error
                            self.db.rollback()
                            if boundary=='rollback'and timing=='after':raise error
                        def close(self):
                            if boundary=='close'and timing=='before':raise error
                            self.db.close();self.closed=True
                            if boundary=='close'and timing=='after':raise error
                    def connect(*a,**kw):
                        wrapped=Connection(original_connect(*a,**kw));owned.append(wrapped);return wrapped
                    with patch.object(w.sqlite3,'connect',side_effect=connect):
                        with self.assertRaises(kind)as caught:self.call()
                    self.assertIs(caught.exception,error);self.assertEqual(owned[0].closed,not(boundary=='close'and timing=='before'));owned[0].db.close();self.no_complete()
                    body=q.decode((out/'failure.json').read_bytes());self.assertEqual(body['reason'],kind.__name__)
    def test_existing_primary_kept_over_cleanup_cancellation_at_both_boundaries(self):
        original_connect=sqlite3.connect
        for boundary,timing in (('rollback','before'),('rollback','after'),('close','before'),('close','after')):
            for kind in (KeyboardInterrupt,SystemExit):
                with self.subTest(boundary=boundary,timing=timing,kind=kind.__name__):
                    out=self.root/'projection'
                    if out.exists():
                        for f in out.iterdir():f.unlink()
                        out.rmdir()
                    primary=q.QualificationFailure('BODY_PRIMARY');secondary=kind();owned=[]
                    class Connection:
                        def __init__(self,db):self.db=db;self.closed=False
                        def __getattr__(self,name):return getattr(self.db,name)
                        def rollback(self):
                            if boundary=='rollback'and timing=='before':raise secondary
                            self.db.rollback()
                            if boundary=='rollback'and timing=='after':raise secondary
                        def close(self):
                            if boundary=='close'and timing=='before':raise secondary
                            self.db.close();self.closed=True
                            if boundary=='close'and timing=='after':raise secondary
                    def connect(*a,**kw):
                        wrapped=Connection(original_connect(*a,**kw));owned.append(wrapped);return wrapped
                    with patch.object(w.sqlite3,'connect',side_effect=connect),self.phase('after_anchor',lambda:(_ for _ in ()).throw(primary)):
                        with self.assertRaises(q.QualificationFailure)as caught:self.call()
                    self.assertIs(caught.exception,primary);self.assertEqual(owned[0].closed,not(boundary=='close'and timing=='before'));owned[0].db.close();self.no_complete()
                    self.assertTrue(any(boundary+':'+kind.__name__ in note for note in primary.__notes__))
    def test_cleanup_cancellation_keeps_other_failures_as_notes(self):
        release=w._release;error=KeyboardInterrupt();secondary=SystemExit()
        def fail(db):return release(db)+[('rollback',OSError('secondary')),('close',error),('later',secondary)]
        with patch.object(w,'_release',side_effect=fail):
            with self.assertRaises(KeyboardInterrupt)as caught:self.call()
        self.assertIs(caught.exception,error);self.no_complete()
        self.assertTrue(any('rollback:OSError'in note for note in error.__notes__))
        self.assertTrue(any('later:SystemExit'in note for note in error.__notes__))

    def test_cleanup_failure_without_primary_is_not_complete(self):
        release=w._release
        with patch.object(w,'_release',side_effect=lambda db:release(db)+[('close',OSError('fault'))]):
            with self.assertRaisesRegex(q.QualificationFailure,'CLEANUP_UNCERTAIN'):self.call()
        self.no_complete()
    def test_resource_failures_before_publication(self):
        for reason in ('WALL_LIMIT','RSS_LIMIT','DISK_RESERVE','OUTPUT_LIMIT'):
            with self.subTest(reason=reason):
                out=self.root/'projection'
                if out.exists():
                    for f in out.iterdir():f.unlink()
                    out.rmdir()
                with self.phase('reader_closed',lambda:(_ for _ in ()).throw(q.QualificationFailure(reason))):
                    with self.assertRaisesRegex(q.QualificationFailure,reason):self.call()
                self.no_complete()
    def test_partial_and_zero_publication_writes(self):
        original=q.write_all
        def short(stream,payload,guard=None):
            class Partial:
                def write(self,b):return stream.write(b[:max(1,len(b)//2)])
            return original(Partial(),payload,guard)
        with patch.object(q,'write_all',side_effect=short):result=self.call()
        self.assertEqual(q.decode((self.root/'projection/result.json').read_bytes()),q.decode(q.encode(result)))
    def test_zero_write_no_complete(self):
        original=q.write_all
        class Zero:
            def write(self,b):return 0
        with patch.object(q,'write_all',side_effect=lambda stream,payload,guard=None:original(Zero(),payload,guard)):
            with self.assertRaisesRegex(q.QualificationFailure,'WRITE_PROGRESS'):self.call()
        self.no_complete()
    def test_uncertain_and_confirmed_publication_preserve_exception(self):
        error=q.PublicationUncertain('SYNTHETIC_UNCERTAIN')
        with patch.object(q,'publish',side_effect=error):
            with self.assertRaises(q.PublicationUncertain)as caught:self.call()
        self.assertIs(caught.exception,error);body=q.decode((self.root/'projection/failure.json').read_bytes());self.assertEqual(body['schema'],w.SCHEMA);self.assertEqual(body['status'],'PUBLICATION_UNCERTAIN')
    def test_confirmed_link_cancellation_is_not_relabelled_failed(self):
        original=q.os.link;error=KeyboardInterrupt()
        def link(*a,**kw):original(*a,**kw);raise error
        with patch.object(q.os,'link',side_effect=link):
            with self.assertRaises(KeyboardInterrupt)as caught:self.call()
        self.assertIs(caught.exception,error);self.assertTrue((self.root/'projection/result.json').exists());self.assertFalse((self.root/'projection/failure.json').exists())


if __name__=='__main__':unittest.main()
