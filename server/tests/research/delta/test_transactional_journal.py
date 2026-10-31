"""F-DISABLED-01 adversarial author regressions. No external acceptance claim."""
from pathlib import Path
from unittest.mock import patch
import multiprocessing,sqlite3,tempfile,unittest,os
from backend.delta.journal.surprise_journal import SurpriseJournal,canonical,signature


def event(ident='external:1'):
    return dict(provider_execution_id=ident,quantity='0.1',price='50',fee='0.05',source='SYNTHETIC_ONLY')
def review(remain='0.9',reserve='45.45'):
    return dict(scope='SYNTHETIC_REVIEW_EVIDENCE',review_id='review:'+remain,identity_verified=True,cash_fees_verified=True,order_remainder_verified=True,remaining_quantity=remain,remaining_reserve=reserve)
def worker(path,start,out):
    try:
        j=SurpriseJournal(path);start.wait(5);j.receive(event());j.reconcile_synthetic('external:1',review());out.put(('ok',j.status()));j.close()
    except BaseException as exc:out.put(('error',repr(exc)))


def crash_worker(path,phase):
    j=SurpriseJournal(path)
    if phase=='after_commit':j._rebuild=lambda:os._exit(73)
    else:
        original=j._insert
        def interrupted(kind,value):
            original(kind,value);os._exit(74)
        j._insert=interrupted
    j.reconcile_synthetic('external:1',review())
    os._exit(99)

class TransactionalJournalTests(unittest.TestCase):
    def assert_single(self,j):
        s=j.status();self.assertEqual(s['synthetic_view'],dict(cash='1899/20',owned='1/10',reserve='909/20'))
        self.assertEqual(s['available_synthetic'],'99/2');self.assertEqual(s['accounted'],['external:1'])
        self.assertEqual((s['raw_events'],s['disputes'],s['unresolved']),(2,0,[]))
        self.assertEqual(s['admission'],'NOT_EVALUABLE');self.assertFalse(s['rearm_authorized'])
    def test_exact_external_two_handle_reproduction(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'j';a=SurpriseJournal(p);b=SurpriseJournal(p)
            a.receive(event());b.receive(event());a.reconcile_synthetic('external:1',review());b.reconcile_synthetic('external:1',review())
            self.assert_single(a);self.assert_single(b);a.close();b.close();a=SurpriseJournal(p);self.assert_single(a);a.close()
    def test_exact_external_same_handle_postcommit_retry_without_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'j';a=SurpriseJournal(p);a.receive(event())
            with patch.object(a,'_rebuild',side_effect=RuntimeError('postcommit rebuild failure')):
                with self.assertRaises(RuntimeError):a.reconcile_synthetic('external:1',review())
            a.reconcile_synthetic('external:1',review());self.assert_single(a);a.close()
            a=SurpriseJournal(p);self.assert_single(a);a.close()
    def test_two_simultaneous_processes_have_one_economic_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=str(Path(tmp)/'j');j=SurpriseJournal(p);j.close()
            ctx=multiprocessing.get_context('fork');start=ctx.Event();out=ctx.Queue()
            processes=[ctx.Process(target=worker,args=(p,start,out)) for _ in range(2)]
            for process in processes:process.start()
            start.set()
            try:
                results=[out.get(timeout=10) for _ in processes]
                for result in results:self.assertEqual(result[0],'ok',result)
                for process in processes:process.join(10);self.assertEqual(process.exitcode,0)
            finally:
                for process in processes:
                    if process.is_alive():process.terminate();process.join()
                out.close()
            j=SurpriseJournal(p);self.assert_single(j);j.close()
    def test_changed_review_after_other_handle_commit_retained_as_dispute(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'j';a=SurpriseJournal(p);b=SurpriseJournal(p);a.receive(event());a.reconcile_synthetic('external:1',review())
            changed=dict(review(),review_id='conflicting-review')
            with self.assertRaises(ValueError):b.reconcile_synthetic('external:1',changed)
            s=b.status();self.assertEqual(s['disputes'],1);self.assertEqual(s['synthetic_view']['owned'],'1/10')
            with self.assertRaises(ValueError):b.reconcile_synthetic('external:1',changed)
            self.assertEqual(b.status(),s);a.close();b.close();a=SurpriseJournal(p);self.assertEqual(a.status(),s);a.close()
    def test_two_distinct_fills_validate_against_current_durable_cumulative(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'j';a=SurpriseJournal(p);b=SurpriseJournal(p)
            a.receive(event());a.reconcile_synthetic('external:1',review());b.receive(event('external:2'))
            before=b.status()
            with self.assertRaises(ValueError):b.reconcile_synthetic('external:2',review())
            self.assertEqual(b.status(),before)
            b.reconcile_synthetic('external:2',review('0.8','40.40'))
            s=a.status();self.assertEqual(s['synthetic_view'],dict(cash='899/10',owned='1/5',reserve='202/5'))
            self.assertEqual(s['available_synthetic'],'99/2');self.assertEqual(s['disputes'],0)
            self.assertEqual(len(s['accounted']),2);a.close();b.close();a=SurpriseJournal(p);self.assertEqual(a.status(),s);a.close()
    def test_conflicting_execution_delivered_through_stale_handle_blocks_accounting(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'j';a=SurpriseJournal(p);b=SurpriseJournal(p);a.receive(event());b.receive(dict(event(),fee='0.06'))
            with self.assertRaises(ValueError):a.reconcile_synthetic('external:1',review())
            self.assertEqual(a.status()['disputes'],1);self.assertEqual(a.status()['synthetic_view']['cash'],'100');a.close();b.close()
    def test_failed_review_write_rolls_back_and_same_handle_retry_is_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'j';a=SurpriseJournal(p);a.receive(event());before=a.status()
            a.db.execute("create trigger fail_review before insert on events when new.kind='SYNTHETIC_REVIEW' begin select raise(ABORT,'injected write failure'); end")
            with self.assertRaises(sqlite3.IntegrityError):a.reconcile_synthetic('external:1',review())
            self.assertEqual(a.status(),before);a.db.execute('drop trigger fail_review')
            a.reconcile_synthetic('external:1',review());a.reconcile_synthetic('external:1',review());self.assert_single(a);a.close()
    def test_database_semantic_unique_constraint_rejects_second_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=SurpriseJournal(Path(tmp)/'j');a.receive(event());a.reconcile_synthetic('external:1',review())
            with self.assertRaises(sqlite3.IntegrityError):
                a.db.execute("insert into events(kind,semantic_key,payload,sha) select kind,semantic_key,payload,sha from events where kind='SYNTHETIC_REVIEW'")
            self.assert_single(a);a.close()
    def test_replay_rejects_duplicate_review_even_if_unique_constraint_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'j';a=SurpriseJournal(p);a.receive(event());a.reconcile_synthetic('external:1',review())
            a.db.execute('alter table events rename to original_events')
            a.db.execute('create table events (n integer primary key,kind text,semantic_key text,payload text,sha text)')
            a.db.execute('insert into events select * from original_events')
            a.db.execute("insert into events(kind,semantic_key,payload,sha) select kind,semantic_key,payload,sha from original_events where kind='SYNTHETIC_REVIEW'")
            a.close()
            with self.assertRaises(ValueError):SurpriseJournal(p)
    def test_legacy_v1_file_refused_without_migration_or_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'legacy';db=sqlite3.connect(p);db.execute('create table events (n integer primary key,kind text,payload text,sha text)');db.commit();db.close()
            before=p.read_bytes()
            with self.assertRaises(ValueError):SurpriseJournal(p)
            self.assertEqual(p.read_bytes(),before)
    def test_process_death_after_commit_recovers_single_effect(self):
        self._process_death('after_commit',73,True)
    def test_process_death_before_commit_rolls_back_then_retry_accounts_once(self):
        self._process_death('before_commit',74,False)
    def _process_death(self,phase,code,committed):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'j';a=SurpriseJournal(p);a.receive(event());a.close()
            process=multiprocessing.get_context('fork').Process(target=crash_worker,args=(str(p),phase));process.start();process.join(10)
            if process.is_alive():process.terminate();process.join();self.fail('crash child timeout')
            self.assertEqual(process.exitcode,code)
            a=SurpriseJournal(p)
            self.assertEqual(a.status()['accounted'],['external:1'] if committed else [])
            a.reconcile_synthetic('external:1',review());self.assert_single(a);a.close()
    def test_changed_postcommit_retry_is_dispute_not_second_fill(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'j';a=SurpriseJournal(p);a.receive(event())
            with patch.object(a,'_rebuild',side_effect=RuntimeError('postcommit')):
                with self.assertRaises(RuntimeError):a.reconcile_synthetic('external:1',review())
            with self.assertRaises(ValueError):a.reconcile_synthetic('external:1',dict(review(),review_id='changed'))
            self.assertEqual(a.status()['synthetic_view']['owned'],'1/10');self.assertEqual(a.status()['disputes'],1);a.close()

if __name__=='__main__':unittest.main()
