from pathlib import Path
import sys,json,copy,unittest,hashlib
P=Path(__file__).resolve().parent;R=P.parent
from backend.delta.volume.source_reference import Engine,Group,Trade
from backend.delta.volume.projector import project,decode,MAX_BYTES
NAMES=['AAPL','AMZN','GOOGL','JPM','MSFT','WMT','XOM']
def enc(v):return json.dumps(v,sort_keys=True,separators=(',',':')).encode()
def fixture():
 mapping=dict(zip(NAMES,[38,853,7152,9155,10888,17448,17709]));source='synthetic-source:projector-fixture';session='INVENTED';snaps=[]
 for n,i in mapping.items():
  e=Engine(source,n,session,i,10,2,enabled=True);e.push(Group(source,n,session,i,1,1,1,1,1,1,128,True,False,False,(Trade(1,1,100,20,'N'),)))
  snaps.extend([e.snapshot(1),e.snapshot(2)])
 p={'schema':'INVENTED_PROFILE_V1','active':False,'full_source_23460second_allowance_active':False,'input':{'raw_sha256':'a'*64,'session':session,'mapping':mapping},'math_namespace':source,'snapshot_clocks':[1,2],'bucket_shares':10,'keep':2,'gap_ns':2_000_000_000}
 r={'schema':'INVENTED_PROJECTOR_FIXTURE_V1','profile_sha256':hashlib.sha256(enc(p)).hexdigest(),'status':'COMPLETE_WINDOW_ENGINEERING_WITH_INHERITED_ARCHIVE_EVIDENCE_REVIEW_REQUIRED','forecast_or_account_consumer':False,'source_fullsession_native_qualification':False,'window_14S_finalF_checked':True,'raw_compressed_hash_before_after':True,'actual_source':p['input'],'reversible_math_namespace':source,'native':{'finished':True,'failed':False,'failure':None,'owned_child_returncode':0,'last_attempt':{'operation':'F'},'live_memory_supervision':{'watchdog_stopped':True,'watchdog_alive':False,'failure':None}},'coverage':{'snapshots':snaps,'prefix_accounting':{'rows':1}}}
 return r,p
def call(r,p,**kw):return project(enc(r),enc(p),symbol='AAPL',decision_ns=1,enabled=True,synthetic_fixture=True,**kw)
class Tests(unittest.TestCase):
 def test_native_reference_to_feature(self):
  r,p=fixture();f=call(r,p);self.assertEqual(f.context.artifact_sha256,hashlib.sha256(enc(r)).hexdigest());self.assertEqual(f.known_side_coverage,0);self.assertEqual((f.lower,f.upper),(0,1));self.assertFalse(f.forecast_admission_influence)
  with self.assertRaises(RuntimeError):f.model_vector()
 def test_disabled_default(self):
  r,p=fixture()
  with self.assertRaisesRegex(ValueError,'DISABLED'):project(enc(r),enc(p),symbol='AAPL',decision_ns=1)
 def test_recorded_requires_exact_accepted_bytes(self):
  r,p=fixture()
  with self.assertRaisesRegex(ValueError,'UNACCEPTED'):project(enc(r),enc(p),symbol='AAPL',decision_ns=1,enabled=True)
 def test_mismatched_profile(self):
  r,p=fixture();p['bucket_shares']=11
  with self.assertRaisesRegex(ValueError,'PROFILE_BINDING'):call(r,p)
 def test_no_fallback_selection(self):
  r,p=fixture()
  for symbol,at in [('OTHER',1),('AAPL',3),('AAPL',True)]:
   with self.assertRaises(ValueError):project(enc(r),enc(p),symbol=symbol,decision_ns=at,enabled=True,synthetic_fixture=True)
 def test_duplicate_and_missing_snapshots(self):
  for kind in ['duplicate','missing']:
   r,p=fixture()
   if kind=='duplicate':r['coverage']['snapshots'][1]=copy.deepcopy(r['coverage']['snapshots'][0])
   else:r['coverage']['snapshots'].pop()
   with self.assertRaises(ValueError):call(r,p)
 def test_wrong_unselected_identity_rejects_whole_artifact(self):
  r,p=fixture();r['coverage']['snapshots'][-1]['instrument']=999
  with self.assertRaisesRegex(ValueError,'SOURCE_IDENTITY'):call(r,p)
 def test_future_unselected_completion_rejected(self):
  r,p=fixture();r['coverage']['snapshots'][-1]['buckets'][0]['available_ns']=3
  with self.assertRaises(ValueError):call(r,p)
 def test_unavailable_remains_undefined(self):
  r,p=fixture();e=Engine(p['math_namespace'],'AAPL','INVENTED',38,10,2,enabled=True);e.push(Group(p['math_namespace'],'AAPL','INVENTED',38,1,1,1,1,1,1,128,False,False,False,()));r['coverage']['snapshots'][0]=e.snapshot(1)
  f=call(r,p);self.assertIsNone(f.lower);self.assertEqual(f.native_status,'UNAVAILABLE_QUALITY')
 def test_no_authority_from_incomplete_or_scope_escalation(self):
  for field,val in [('status','FAILED'),('source_fullsession_native_qualification',True),('forecast_or_account_consumer',True)]:
   r,p=fixture();r[field]=val
   with self.assertRaises(ValueError):call(r,p)
 def test_native_failure_or_unstopped_monitor(self):
  for which in ['failed','watchdog']:
   r,p=fixture()
   if which=='failed':r['native']['failed']=True
   else:r['native']['live_memory_supervision']['watchdog_stopped']=False
   with self.assertRaises(ValueError):call(r,p)
 def test_source_frontier_cannot_be_self_attested(self):
  r,p=fixture();r['coverage']['prefix_accounting']['rows']=0
  with self.assertRaisesRegex(ValueError,'FUTURE_SOURCE_FRONTIER'):call(r,p)
 def test_duplicate_keys_nonfinite_and_size(self):
  for b in [b'{"a":1,"a":2}',b'{"a":NaN}',b' '* (MAX_BYTES+1)]:
   with self.assertRaises(ValueError):decode(b)
 def test_deterministic_and_no_input_mutation(self):
  r,p=fixture();before=enc(r);a=call(r,p).to_dict();self.assertEqual(a,call(r,p).to_dict());self.assertEqual(before,enc(r))
if __name__=='__main__':unittest.main(verbosity=2)
