"""Tiny invented single-frame archives; no existing vendor archive is read."""
import unittest,importlib.util
HAS_ARCHIVE=all(importlib.util.find_spec(n)is not None for n in('pyarrow','zstandard'))
if HAS_ARCHIVE:
 from pathlib import Path
 import csv,io,hashlib,tempfile,time,json
 import zstandard as zstd
 from unittest.mock import patch
 from test_arrow_decoder import EXPECTED_COLUMNS,batch,raw
 from test_decoded_bridge import rows
 from backend.delta.decoding.archive import decode_small_archive
 from backend.delta.decoding.bridge import DecodedVolumeBridge
 from backend.delta.volume.source_reader import ResourceSample

def csvbytes(records,header=None):
 text=io.StringIO(newline='');w=csv.writer(text,lineterminator='\n');w.writerow(EXPECTED_COLUMNS if header is None else header)
 for r in records:w.writerow([r[k]for k in EXPECTED_COLUMNS])
 return text.getvalue().encode()
def probe():return ResourceSample(time.monotonic_ns(),16*1024**2,4*1024**3)

@unittest.skipUnless(HAS_ARCHIVE,'requires existing Arrow/Zstandard research environment; no install')
class SmallArchiveTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  from test_decoded_bridge import DecodedBridgeTests
  DecodedBridgeTests.setUpClass.__func__(cls)
  cls.payload=csvbytes(rows());cls.archive=zstd.ZstdCompressor().compress(cls.payload)
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()
 def make(self,out):
  from test_decoded_bridge import DecodedBridgeTests
  return DecodedBridgeTests.make(self,out)
 def run_archive(self,path,c,content=None,**overrides):
  content=self.archive if content is None else content;path.write_bytes(content)
  opts=dict(expected_sha256=hashlib.sha256(content).hexdigest(),expected_bytes=len(content),expected_rows=8,deadline_ns=time.monotonic_ns()+5_000_000_000,probe=probe,enabled=True);opts.update(overrides)
  return decode_small_archive(path,c,**opts)
 def test_complete_consumed_bytes_and_native_cleanup(self):
  with tempfile.TemporaryDirectory()as root:
   p=Path(root);out=p/'out';out.mkdir();rec,c=self.make(out)
   with c:r=self.run_archive(p/'invented.zst',c)
   self.assertTrue(r['source_bytes_authenticated']);self.assertEqual(r['archive']['sha256'],hashlib.sha256(self.archive).hexdigest());self.assertEqual(r['archive']['decoded_sha256'],hashlib.sha256(self.payload).hexdigest())
   self.assertEqual(r['archive']['rows'],8);self.assertTrue(r['archive']['single_frame_complete']);self.assertTrue(c.proc._released and c.finished)
   self.assertEqual(r['decoder']['rows'],8);self.assertEqual(len(r['snapshots']),14)
 def test_hash_size_and_source_identity_fail_before_P(self):
  for kind in('hash','size','symlink'):
   with self.subTest(kind=kind),tempfile.TemporaryDirectory()as root:
    p=Path(root);out=p/'out';out.mkdir();rec,c=self.make(out);path=p/'invented.zst';path.write_bytes(self.archive)
    kw=dict(expected_sha256=hashlib.sha256(self.archive).hexdigest(),expected_bytes=len(self.archive),expected_rows=8,deadline_ns=time.monotonic_ns()+5_000_000_000,probe=probe,enabled=True)
    if kind=='hash':kw['expected_sha256']='0'*64
    if kind=='size':kw['expected_bytes']+=1
    if kind=='symlink':target=p/'real';path.replace(target);path.symlink_to(target)
    with c:
     with self.assertRaises(Exception):decode_small_archive(path,c,**kw)
    self.assertTrue(c.failed and c.proc._released);self.assertEqual(c.telemetry_ack[1],'I')
 def test_truncated_trailing_concat_unknownsize_rejected(self):
  variants=[self.archive[:-1],self.archive+b'x',self.archive+self.archive,zstd.ZstdCompressor(write_content_size=False).compress(self.payload),b'notzstd']
  for data in variants:
   with self.subTest(size=len(data)),tempfile.TemporaryDirectory()as root:
    p=Path(root);out=p/'out';out.mkdir();rec,c=self.make(out)
    with c:
     with self.assertRaises(Exception):self.run_archive(p/'invented.zst',c,data)
    self.assertTrue(c.failed and not c.finished and c.proc._released);self.assertEqual(c.telemetry_ack[1],'I')
 def test_csv_header_rowcount_width_and_quotes_reject(self):
  wrong_header=self.payload.replace(b'ts_recv',b'wrong__',1)
  wrong_width=self.payload.replace(b'100.000000001',b'100.000000001,extra',1)
  quoted=self.payload.replace(b'100.000000001',b'"100.000000001"',1)
  for payload,expected in[(wrong_header,8),(wrong_width,8),(quoted,8),(self.payload,9),(self.payload,7)]:
   with self.subTest(expected=expected),tempfile.TemporaryDirectory()as root:
    p=Path(root);out=p/'out';out.mkdir();rec,c=self.make(out)
    with c:
     with self.assertRaises(Exception):self.run_archive(p/'invented.zst',c,zstd.ZstdCompressor().compress(payload),expected_rows=expected)
    self.assertTrue(c.failed and not c.finished and c.proc._released)
 def test_late_mutation_during_each_final_probe_invalidates_completion(self):
  for phase in(1,2):
   with self.subTest(phase=phase),tempfile.TemporaryDirectory()as root:
    p=Path(root);out=p/'out';out.mkdir();rec,c=self.make(out);path=p/'invented.zst';calls=[0];done=[False]
    def swap():
     if c.finished:
      calls[0]+=1
      if calls[0]==phase:
       done[0]=True;replacement=p/'replacement';replacement.write_bytes(self.archive);replacement.replace(path)
     return probe()
    with c:
     with self.assertRaisesRegex(ValueError,'ARCHIVE_(PATH|DESCRIPTOR)_CHANGED_FINAL'):self.run_archive(path,c,probe=swap)
    self.assertTrue(done[0]and c.failed and not c.finished and c.proc._released)
    self.assertEqual(json.loads((out/'failure.json').read_text())['status'],'FAILED_NO_COMPLETE')
 def test_final_stat_latency_rejects_without_callback_after_it(self):
  import backend.delta.decoding.archive as module
  with tempfile.TemporaryDirectory()as root:
   p=Path(root);out=p/'out';out.mkdir();rec,c=self.make(out);path=p/'invented.zst';clock=[100];calls=[0];real=Path.stat
   def stat_after_delay(obj,*a,**k):
    value=real(obj,*a,**k)
    if obj==path:
     calls[0]+=1
     if calls[0]==3:clock[0]=200
    return value
   with c,patch.object(module.time,'monotonic_ns',side_effect=lambda:clock[0]),patch.object(Path,'stat',stat_after_delay):
    with self.assertRaisesRegex(ValueError,'WALL_LIMIT'):self.run_archive(path,c,deadline_ns=200)
   self.assertEqual(calls[0],3);self.assertTrue(c.failed and not c.finished and c.proc._released)
 def test_cancellation_during_decode_cleans_owned_client(self):
  with tempfile.TemporaryDirectory()as root:
   p=Path(root);out=p/'out';out.mkdir();rec,c=self.make(out)
   with c,patch.object(DecodedVolumeBridge,'consume',side_effect=KeyboardInterrupt('INVENTED_ARCHIVE_CANCEL')):
    with self.assertRaises(KeyboardInterrupt):self.run_archive(p/'invented.zst',c)
   self.assertTrue(c.failed and c.proc._released and not c.finished)
   self.assertEqual(json.loads((out/'failure.json').read_text())['first_cause'][0],'KeyboardInterrupt')
 def test_disabled_and_small_bounds_precede_file_open(self):
  with self.assertRaisesRegex(ValueError,'DISABLED'):decode_small_archive(None,None,expected_sha256='',expected_bytes=0,expected_rows=0,deadline_ns=0,probe=None)
  with self.assertRaisesRegex(ValueError,'SMALL_ARCHIVE_BOUNDS'):decode_small_archive(None,None,expected_sha256='',expected_bytes=1024**2+1,expected_rows=8,deadline_ns=0,probe=None,enabled=True)

 def test_exact_consumed_buffer_reauthenticated_after_preliminary_receipt(self):
  import builtins
  import backend.delta.decoding.archive as module
  with tempfile.TemporaryDirectory()as root:
   p=Path(root);out=p/'out';out.mkdir();rec,c=self.make(out);path=p/'invented.zst';real_open=builtins.open;changed=[False]
   def replacing_open(name,*a,**k):
    handle=real_open(name,*a,**k)
    if Path(name)==path:
     changed[0]=True;data=bytearray(self.archive);data[len(data)//2]^=1;path.write_bytes(data)
    return handle
   with c,patch.object(module,'open',side_effect=replacing_open,create=True):
    with self.assertRaisesRegex(ValueError,'CONSUMED_ARCHIVE_HASH_OR_SIZE'):self.run_archive(path,c)
   self.assertTrue(changed[0]and c.failed and c.proc._released);self.assertEqual(c.telemetry_ack[1],'I')
 def test_read_failure_close_uncertainty_preserves_primary_and_custody(self):
  import builtins,os
  import backend.delta.decoding.archive as module
  with tempfile.TemporaryDirectory()as root:
   p=Path(root);out=p/'out';out.mkdir();rec,c=self.make(out);path=p/'invented.zst';real_open=builtins.open;fds=[]
   class Broken:
    def __init__(self,stream):self.stream=stream;fds.append(stream.fileno())
    def fileno(self):return self.stream.fileno()
    def read(self,*a):raise OSError('INVENTED_COMPRESSED_READ')
    def close(self):self.stream.close();raise OSError('INVENTED_CLOSE')
   def broken_open(*a,**k):return Broken(real_open(*a,**k))
   with c,patch.object(module,'open',side_effect=broken_open,create=True):
    with self.assertRaisesRegex(OSError,'INVENTED_COMPRESSED_READ')as caught:self.run_archive(path,c)
   self.assertIn('ARCHIVE_CLOSE_UNCERTAIN:OSError',caught.exception.__notes__)
   self.assertTrue(c.failed and c.proc._released)
   with self.assertRaises(OSError):os.fstat(fds[0])

if __name__=='__main__':unittest.main(verbosity=2)
