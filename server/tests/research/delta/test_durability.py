from pathlib import Path
from unittest.mock import patch
import sqlite3,tempfile,unittest
from backend.delta.journal.surprise_journal import SurpriseJournal

class Durability(unittest.TestCase):
    def test_failure_after_commit_reopens_durable_evidence_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'j';j=SurpriseJournal(path);event=dict(provider_execution_id='one',source='UNVERIFIED',quantity='0.1')
            with patch.object(j,'_rebuild',side_effect=RuntimeError('injected after durable commit')):
                with self.assertRaises(RuntimeError):j.receive(event)
            j.close();j=SurpriseJournal(path);self.assertEqual(j.incidents['one'],event)
            j.receive(event);self.assertEqual(j.status()['raw_events'],1);j.close()
    def test_failed_write_has_no_partial_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'j';j=SurpriseJournal(path)
            j.db.execute("create trigger reject_write before insert on events begin select raise(ABORT,'fault injection'); end")
            with self.assertRaises(sqlite3.IntegrityError):j.receive({'provider_execution_id':'one'})
            self.assertEqual(j.status()['raw_events'],0);j.close();j=SurpriseJournal(path)
            self.assertEqual(j.status()['raw_events'],0);j.close()
    def test_corrupt_persisted_evidence_blocks_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'j';j=SurpriseJournal(path);j.receive({'provider_execution_id':'one'})
            j.db.execute("update events set payload='{}'");j.db.commit();j.close()
            with self.assertRaises(ValueError):SurpriseJournal(path)
