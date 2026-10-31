from pathlib import Path
import sys,unittest,copy,json
from dataclasses import replace,FrozenInstanceError
from fractions import Fraction
from backend.delta.volume.source_reference import Engine,Group,Trade
from backend.delta.volume.features import Context,Scale,digest,snapshot_to_feature
SOURCE='synthetic-source:uncertainty-interface-v32';SCALE=Scale(10,2,2_000_000_000,30_000_000_000)
def engine():return Engine(SOURCE,'AAPL','SYNTHETIC',38,10,2,enabled=True)
def push(e,i,side='N',shares=10,*,valid=True,reset=False,correction=False,recv=None,trade_recv=None):
 t=i if recv is None else recv;tr=t if trade_recv is None else trade_recv
 trades=()if shares==0 else(Trade(i,tr,100_000_000_000,shares,side),)
 e.push(Group(SOURCE,'AAPL','SYNTHETIC',38,i,i,1,tr,t,tr,128,valid,reset,correction,trades))
def context(s):return Context('a'*64,digest(s),'INVENTED_FIXTURE',SOURCE,None,'AAPL','SYNTHETIC',38,s['decision_ns'],s['frontier'],SCALE,'SYNTHETIC_ONLY')
def feature(s,ctx=None,frontier=None):return snapshot_to_feature(s,ctx or context(s),decision_ns=s['decision_ns'],observed_source_frontier=s['frontier']if frontier is None else frontier,enabled=True)
def sample(side='N'):
 e=engine();push(e,1,side,20);return e.snapshot(1)
class Features(unittest.TestCase):
 def test_disabled_default_and_model_bridge_always_disabled(self):
  s=sample()
  with self.assertRaisesRegex(ValueError,'DISABLED'):snapshot_to_feature(s,context(s),decision_ns=1,observed_source_frontier=1)
  with self.assertRaisesRegex(RuntimeError,'NOT_AUTHORIZED'):feature(s).model_vector()
 def test_all_unknown_defined_full_interval_not_zero_point(self):
  f=feature(sample());self.assertEqual(f.known_side_coverage,0);self.assertEqual((f.lower,f.upper,f.absolute_width),(0,1,1));self.assertEqual((f.signed_lower,f.signed_upper,f.signed_width),(-1,1,2));self.assertEqual(f.information_absolute,'FULL_RANGE');self.assertIsNone(f.strict_reported_signed);self.assertEqual(f.retained_BAN,(0,0,20));self.assertFalse(f.forecast_admission_influence)
 def test_absolute_pressure_not_direction(self):
  e=engine();push(e,1,'B');push(e,2,'A');f=feature(e.snapshot(2));self.assertEqual((f.lower,f.upper),(1,1));self.assertEqual((f.signed_lower,f.signed_upper),(0,0));self.assertEqual(f.strict_reported_absolute,1);self.assertEqual(f.strict_reported_signed,0)
 def test_partial_unknown_preserves_bounds_and_no_midpoint(self):
  e=engine();push(e,1,'B',4);push(e,2,'N',6);push(e,3,'B',4);push(e,4,'N',6);f=feature(e.snapshot(4));self.assertEqual(f.known_side_coverage,Fraction(2,5));self.assertEqual((f.lower,f.upper),(0,1));self.assertEqual((f.signed_lower,f.signed_upper),(Fraction(-1,5),1));self.assertEqual(f.information_absolute,'FULL_RANGE');self.assertEqual(f.information_signed,'BOUNDED_RANGE');self.assertIsNone(f.strict_reported_signed)
 def test_indivisible_unknown_relaxation_and_split_lineage(self):
  # B4+one N6 per bucket has achievable absolute ratios .2 or1,
  # so reported lower0 is conservative, not necessarily attainable.
  e=engine();push(e,1,'B',4);push(e,2,'N',6);push(e,3,'B',4);push(e,4,'N',6);f=feature(e.snapshot(4));self.assertEqual(f.lower,0);self.assertIn('NOT_PRINT_LEVEL',f.bound_semantics)
  f=feature(sample());self.assertEqual(f.bucket_lineage[0][3][0][0],f.bucket_lineage[1][3][0][0]);self.assertEqual(sum(r[3]for b in f.bucket_lineage for r in b[3]),20)
 def test_warmup_and_taint_statuses_keep_undefined(self):
  e=engine();snapshots=[e.snapshot(0)];push(e,1,'N',10);snapshots.append(e.snapshot(1));push(e,2,'B',0,valid=False);snapshots.append(e.snapshot(2));push(e,3,'B',0,reset=True);snapshots.append(e.snapshot(3));push(e,4,'B',0,correction=True);snapshots.append(e.snapshot(4))
  self.assertIn('UNAVAILABLE_QUALITY',[s['status']for s in snapshots]);self.assertIn('UNAVAILABLE_CORRECTION',[s['status']for s in snapshots])
  for s in snapshots:
   f=feature(s);self.assertEqual(f.native_status,s['status']);self.assertEqual(f.information_absolute,'UNDEFINED');self.assertIsNone(f.known_side_coverage);self.assertIsNone(f.absolute_width)
 def test_stale_decision_and_stale_bucket_not_filled(self):
  e=engine();push(e,1,'N',20);s=e.snapshot(2_000_000_002);self.assertEqual(feature(s).native_status,'STALE_DECISION')
  # Keep quote/group fresh while no new bucket completes.
  for i,t in enumerate(range(1_000_000_000,32_000_000_001,1_000_000_000),2):push(e,i,shares=0,recv=t)
  s=e.snapshot(32_000_000_000);self.assertEqual(s['status'],'STALE_BUCKET_WINDOW');self.assertIsNone(feature(s).lower)
 def test_receipt_snapshot_identity_and_clock_frontier_binding(self):
  s=sample();ctx=context(s);bad=copy.deepcopy(s);bad['source']='other'
  with self.assertRaisesRegex(ValueError,'CONTENT_BINDING'):feature(bad,ctx)
  with self.assertRaisesRegex(ValueError,'FUTURE_SOURCE_FRONTIER'):feature(s,frontier=0)
  with self.assertRaisesRegex(ValueError,'DECISION_CLOCK'):snapshot_to_feature(s,ctx,decision_ns=2,observed_source_frontier=1,enabled=True)
  bad=copy.deepcopy(s);bad['available_ns']=2
  with self.assertRaisesRegex(ValueError,'FUTURE_AVAILABILITY'):feature(bad)
 def test_exact_ratio_mass_scale_and_claim_tampering_rejected(self):
  for k,v in [('lower','1/2'),('known_side_coverage',.5),('upper','1/0'),('lower','00/1'),('instrument',True),('forecast_or_admission_influence',True),('calibrated_VPIN_probability',True),('market_capture','COMPLETE')]:
   s=sample();s[k]=v
   with self.assertRaises(ValueError):feature(s)
  s=sample();s['retained_volume']=[1,0,19]
  with self.assertRaisesRegex(ValueError,'MASS'):feature(s)
  s=sample();s['buckets'][0]['volume']=[0,0,9]
  with self.assertRaises(ValueError):feature(s)
 def test_completion_clock_not_misread_as_publisher_event(self):
  e=engine();push(e,1,'N',20,recv=10,trade_recv=1);f=feature(e.snapshot(10));self.assertEqual(f.bucket_lineage[0][3][0][1:3],(1,10))
  s=e.snapshot(10);s['buckets'][0]['pieces'][0][2]=11
  with self.assertRaisesRegex(ValueError,'FUTURE_PRINT'):feature(s)
 def test_no_mutation_frozen_output_exact_json_and_scope(self):
  s=sample();before=digest(s);f=feature(s);self.assertEqual(before,digest(s));j=f.to_dict();self.assertEqual(j['lower'],'0/1');self.assertIsNone(j['strict_reported_signed']);json.dumps(j,allow_nan=False)
  with self.assertRaises(FrozenInstanceError):f.lower=Fraction(1)
  ctx=replace(context(s),source_kind='RECORDED_NORMALIZED_OCCURRENCES',raw_sha256='b'*64,scope='FULL_SESSION')
  with self.assertRaisesRegex(ValueError,'RECORDED'):feature(s,ctx)
class ClockRepairTests(unittest.TestCase):
 def test_exact_independent_counterexample_rejected(self):
  s=sample();s['available_ns']=0;s['group_event_ns']=0
  # Fresh matching context intentionally removes mere content-hash rejection.
  with self.assertRaisesRegex(ValueError,'BUCKET_COMPLETION_AFTER_SNAPSHOT_AVAILABILITY'):feature(s)
 def test_equal_bucket_print_and_snapshot_boundary_allowed(self):
  s=sample();f=feature(s);self.assertEqual(f.available_ns,1);self.assertTrue(all(b[2]==f.available_ns for b in f.bucket_lineage));self.assertTrue(all(p[2]==f.available_ns for b in f.bucket_lineage for p in b[3]));self.assertEqual(f.known_side_coverage,0)
  e=engine();push(e,1,'N',20);push(e,2,shares=0,recv=10);self.assertEqual(feature(e.snapshot(10)).available_ns,10)
 def test_unavailable_retained_clocks_checked_before_status_branch(self):
  e=engine();push(e,1,'N',10);warm=e.snapshot(1)
  e=engine();push(e,1,'N',20);stale=e.snapshot(2_000_000_002)
  for s in(warm,stale):
   f=feature(s);self.assertEqual(f.information_absolute,'UNDEFINED');self.assertIsNone(f.lower)
   bad=copy.deepcopy(s);bad['available_ns']=0;bad['group_event_ns']=0
   with self.assertRaisesRegex(ValueError,'BUCKET_COMPLETION_AFTER_SNAPSHOT_AVAILABILITY'):feature(bad)
   bad=copy.deepcopy(s);bad['available_ns']=None;bad['group_event_ns']=None
   with self.assertRaisesRegex(ValueError,'RETAINED_DATA_WITHOUT_SNAPSHOT_AVAILABILITY'):feature(bad)
 def test_malformed_nested_completion_and_bucket_types_failclosed(self):
  for change in('piece_after_bucket','piece_before_receive','missing_completion','bad_bucket'):
   s=sample()
   if change=='piece_after_bucket':s['buckets'][0]['pieces'][0][2]=2
   elif change=='piece_before_receive':s['buckets'][0]['pieces'][0][2]=0
   elif change=='missing_completion':s['buckets'][0]['pieces'][0][2]=None
   else:s['buckets'][0]=None
   with self.assertRaises(ValueError):feature(s)
 def test_disabled_invalid_input_stays_disabled_no_partial_feature(self):
  s=sample();s['available_ns']=None;ctx=context(s)
  with self.assertRaisesRegex(ValueError,'DISABLED_FEATURE_INTERFACE'):snapshot_to_feature(s,ctx,decision_ns=1,observed_source_frontier=1,enabled=False)
  with self.assertRaisesRegex(ValueError,'RETAINED_DATA_WITHOUT_SNAPSHOT_AVAILABILITY'):feature(s,ctx)
if __name__=='__main__':unittest.main(verbosity=2)
