"""Synthetic Arrow→exact groups→actual owned native protocol integration."""
import unittest,importlib.util
HAS_ARROW=importlib.util.find_spec('pyarrow')is not None
if HAS_ARROW:
 from pathlib import Path
 import tempfile,subprocess,hashlib,time,json
 from unittest.mock import patch
 from test_arrow_decoder import raw,batch,C
 from backend.delta.decoding.bridge import DecodedVolumeBridge
 from backend.delta.runtime.client import SupervisedClient
 from backend.delta.runtime.failure_telemetry import FailureRecorder
 from backend.delta.runtime.supervision import OwnedReader

def rows():return [raw(1,0,action='T',flags=0,side='N',shares=3),raw(2,1,name='AMZN'),raw(3,2,action='T',side='B',shares=7),raw(4,30_000_000_000,name='JPM'),raw(5,30_000_000_001,name='XOM'),raw(6,59_999_999_999,action='T',side='A',shares=10),raw(7,60_000_000_000,name='JPM'),raw(8,60_000_000_001,name='WMT')]

@unittest.skipUnless(HAS_ARROW,'requires existing research PyArrow environment; no install')
class DecodedBridgeTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  batch([raw(1)]) # Initialize optional Arrow conversion before acquiring owned child.
  cls.tmp=tempfile.TemporaryDirectory();p=Path(cls.tmp.name);native=Path(__file__).parents[3]/'backend/delta/native/source_worker.cpp';helper=Path(__file__).parents[3]/'backend/delta/runtime/owned_rss.c'
  cls.binary=p/'worker';lib=p/'owned.dylib'
  subprocess.run(['/usr/bin/clang++','-std=c++17','-Wall','-Wextra','-Werror','-pedantic','-O0','-g','-fsanitize=address,undefined',str(native),'-o',str(cls.binary)],check=True,timeout=30)
  subprocess.run(['/usr/bin/clang','-dynamiclib','-Wall','-Wextra','-Werror',str(helper),'-o',str(lib)],check=True,timeout=20)
  cls.reader=OwnedReader(lib,expected_sha256=hashlib.sha256(lib.read_bytes()).hexdigest())
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()
 def make(self,out):
  rec=FailureRecorder(out,time.monotonic());c=SupervisedClient(enabled=True,reader=self.reader,watch=False,recorder=rec,binary=self.binary,expected_binary_sha256=hashlib.sha256(self.binary.read_bytes()).hexdigest(),runtime_manifest='c'*64,source=C['math_namespace'],session=C['input']['session'],deadline=time.monotonic()+10)
  return rec,c
 def test_exact_batch_native_parity_and_boundary_population(self):
  gold=None
  for parts in([8],[1]*8,[2,3,3]):
   with tempfile.TemporaryDirectory()as out:
    rec,c=self.make(out)
    with c:
     b=DecodedVolumeBridge(c,enabled=True);at=0
     for size in parts:b.consume(batch(rows()[at:at+size]));at+=size
     result=b.finish()
    self.assertTrue(c.proc._released);self.assertFalse(result['source_bytes_authenticated']);self.assertEqual(result['selected_groups'],5);self.assertEqual(result['excluded_groups'],2)
    self.assertEqual(result['decoder']['rows'],8);self.assertEqual(result['decoder']['observed_T_shares'],20);self.assertEqual(c.engines[0].input,[7,10,3]);self.assertEqual(len(result['snapshots']),14)
    self.assertTrue(result['native']['finished']);self.assertEqual(result['native']['last_acked_sequence'],21)
    value=(result['snapshots'],result['decoder'],result['native']['attempted_stream_sha256'],result['native']['acked_stream_sha256'])
    if gold is None:gold=value
    else:self.assertEqual(value,gold)
 def test_invalid_batch_aborts_owned_client_without_complete(self):
  with tempfile.TemporaryDirectory()as out:
   rec,c=self.make(out)
   with c:
    b=DecodedVolumeBridge(c,enabled=True);b.consume(batch([raw(1)]));bad=raw(2,1);bad['ts_event']=raw(3,2)['ts_event']
    with self.assertRaises(ValueError):b.consume(batch([bad]))
    self.assertTrue(b.failed and c.failed);self.assertFalse(c.finished);self.assertTrue(c.proc._released)
    evidence=json.loads((Path(out)/'failure.json').read_text());self.assertEqual(evidence['status'],'FAILED_NO_COMPLETE')
 def test_partial_eof_aborts_without_final_ack(self):
  with tempfile.TemporaryDirectory()as out:
   rec,c=self.make(out)
   with c:
    b=DecodedVolumeBridge(c,enabled=True);b.consume(batch([raw(1,flags=0,action='T')]))
    with self.assertRaisesRegex(ValueError,'partial EOF'):b.finish()
    self.assertTrue(c.failed and c.proc._released);self.assertEqual(c.telemetry_ack[1],'I')
 def test_decoder_cancellation_cleanup_and_propagation(self):
  with tempfile.TemporaryDirectory()as out:
   rec,c=self.make(out)
   with c:
    b=DecodedVolumeBridge(c,enabled=True)
    with patch.object(b.decoder,'consume',side_effect=KeyboardInterrupt('INVENTED_DECODE_CANCEL')):
     with self.assertRaises(KeyboardInterrupt):b.consume(batch([raw(1)]))
    self.assertTrue(b.failed and c.failed and c.proc._released);self.assertFalse(c.finished)
    evidence=json.loads((Path(out)/'failure.json').read_text());self.assertEqual(evidence['first_cause'][0],'KeyboardInterrupt')
 def test_disabled_and_batch_bound(self):
  with self.assertRaisesRegex(ValueError,'DISABLED'):DecodedVolumeBridge(None)
  with tempfile.TemporaryDirectory()as out:
   rec,c=self.make(out)
   with c:
    b=DecodedVolumeBridge(c,enabled=True)
    with self.assertRaisesRegex(ValueError,'BATCH_BOUND'):b.consume(batch([raw(i)for i in range(1,514)]))
    self.assertEqual(b.decoder.rows,0);self.assertTrue(c.failed and c.proc._released)


 def test_required_null_and_type_reject_before_any_P_adoption(self):
  import pyarrow as pa
  from backend.delta.decoding.schema import EXPECTED_COLUMNS
  cases=[('null',field)for field in('sequence','depth','ts_in_delta','bid_ct_00','ask_ct_09')]+[('type','sequence')]
  for kind,field in cases:
   with self.subTest(kind=kind,field=field),tempfile.TemporaryDirectory()as out:
    rec,c=self.make(out)
    with c:
     b=DecodedVolumeBridge(c,enabled=True);rs=[raw(1),raw(2,1),raw(3,2)]
     if kind=='null':rs[1][field]=None;values=batch(rs)
     else:
      values=batch(rs);index=EXPECTED_COLUMNS.index(field);values=values.set_column(index,field,pa.array([1,2,3]))
     with self.assertRaisesRegex(ValueError,'NULL_SOURCE_VALUE|EXACT_STRING_COLUMN'):b.consume(values)
     self.assertTrue(b.failed and b.decoder.failed and c.failed and c.proc._released)
     self.assertFalse(c.finished);self.assertEqual(c.telemetry_ack[1],'I');self.assertEqual(c.telemetry_adopted[0],0)
     self.assertEqual(b.decoder.rows,0);self.assertTrue(all(e.groups==0 for e in c.engines))
     evidence=json.loads((Path(out)/'failure.json').read_text());self.assertEqual(evidence['status'],'FAILED_NO_COMPLETE')

if __name__=='__main__':unittest.main(verbosity=2)
