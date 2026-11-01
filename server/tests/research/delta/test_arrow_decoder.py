"""Invented all-string RecordBatches only; runnable with unittest in Arrow env."""
import importlib.util,unittest
HAS_ARROW=importlib.util.find_spec('pyarrow')is not None
if HAS_ARROW:
 import pyarrow as pa
 from datetime import datetime,timezone
 from dataclasses import asdict,replace
 from fractions import Fraction
 from backend.delta.decoding.schema import EXPECTED_COLUMNS
 from backend.delta.decoding.decoder import ArrowDecoder
 from backend.delta.decoding.adapter import project
 from backend.delta.runtime.execution_profile import PROFILE as C,LANES
 from backend.delta.volume.source_reference import Engine


def raw(i,offset=0,*,name='AAPL',action='M',flags=128,side='N',shares=10):
 t=C['slice_open_ns']+offset;stamp=datetime.fromtimestamp(t//10**9,timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')+f'.{t%10**9:09}Z'
 row=dict(ts_recv=stamp,ts_event=stamp,rtype='10',publisher_id='2',instrument_id=str(dict((n,v)for _,n,v in LANES)[name]),action=action,side=side,depth='0',price='100.000000001',size=str(shares),flags=str(flags),ts_in_delta='0',sequence=str(i),symbol=name)
 for level in range(10):
  for s in('bid','ask'):
   row[f'{s}_px_{level:02}']=str(99-level if s=='bid' else 101+level)+'.000000001'
   row[f'{s}_sz_{level:02}']='10';row[f'{s}_ct_{level:02}']='1'
 return row

def batch(rows):return pa.RecordBatch.from_arrays([pa.array([r[k]for r in rows],type=pa.string())for k in EXPECTED_COLUMNS],names=EXPECTED_COLUMNS)
def decoder():return ArrowDecoder(session=C['input']['session'],opened=C['slice_open_ns'],mapping={n:i for _,n,i in LANES},source_id=C['math_namespace'],enabled=True)
def decode(rows,parts):
 d=decoder();out=[];at=0
 for size in parts:
  if at>=len(rows):break
  out.extend(d.consume(batch(rows[at:at+size])));at+=size
 if at<len(rows):out.extend(d.consume(batch(rows[at:])))
 return d,out

@unittest.skipUnless(HAS_ARROW,'requires existing research environment PyArrow; no install')
class ArrowDecoderTests(unittest.TestCase):
 def test_disabled_and_exact_price_unknown_mass(self):
  with self.assertRaisesRegex(ValueError,'DISABLED'):ArrowDecoder(session='',opened=0,mapping={},source_id='')
  d,gs=decode([raw(1,action='T',side='N',shares=20)],[1]);self.assertEqual(gs[0].trades[0].price,100_000_000_001)
  g=project(gs[0],enabled=True);e=Engine(g.source,g.symbol,g.session,g.instrument,10,2,enabled=True);e.push(g)
  self.assertEqual(e.input,[0,0,20]);self.assertEqual(e.snapshot(g.recv)['lower'],'0/1');self.assertEqual(d.finish()['observed_T_shares'],20)
 def test_interleaving_count_physical_lineage_and_partition(self):
  rows=[raw(1,action='T',flags=0,shares=3)]+[raw(i,name='JPM',action='M')for i in range(2,35)]+[raw(35,action='T',shares=7,side='B')]
  gold=None
  for parts in([35],[1]*35,[2,7,26]):
   d,gs=decode(rows,parts);g=next(g for g in gs if g.book.symbol=='AAPL');v=project(g,enabled=True)
   self.assertEqual((v.first,v.last,v.row_count),(1,35,2));self.assertEqual([t.index for t in v.trades],[1,35]);d.finish()
   serial=[asdict(x)for x in gs]
   if gold is None:gold=serial
   else:self.assertEqual(serial,gold)
 def test_quality_latch_late_valid_reset_does_not_rearm(self):
  rows=[raw(1),raw(2,1,flags=130),raw(3,2,action='R'),raw(4,3,flags=160),raw(5,4)]
  for parts in([5],[1]*5):
   d,gs=decode(rows,parts);self.assertTrue(gs[0].book.valid);self.assertTrue(all(not g.book.valid for g in gs[1:]));self.assertEqual(d.first_bad_raw_index['AAPL'],2);d.finish()
 def test_group_reset_spanning_batches_and_zero_trade(self):
  d,gs=decode([raw(1,flags=0,action='R'),raw(2,1)],[1,1]);g=project(gs[0],enabled=True)
  self.assertEqual(g.row_count,2);self.assertTrue(g.reset);self.assertEqual(g.trades,());d.finish()
 def test_partial_eof_and_failed_decoder_are_latched(self):
  d,gs=decode([raw(1,flags=0,action='T')],[1]);self.assertEqual(gs,[])
  with self.assertRaises(ValueError):d.finish()
  self.assertTrue(d.failed)
  with self.assertRaisesRegex(ValueError,'LATCHED'):d.consume(batch([raw(2)]))
 def test_global_clock_symbol_schema_and_trade_faults(self):
  variants=[]
  r=raw(1);r['symbol']='OTHER';variants.append(r)
  r=raw(1);r['publisher_id']='1';variants.append(r)
  r=raw(1,action='T');r['price']='100.0000000001';variants.append(r)
  r=raw(1,action='T',shares=0);variants.append(r)
  r=raw(1);r['ts_event']=raw(2,1)['ts_event'];variants.append(r)
  for r in variants:
   d=decoder()
   with self.assertRaises(Exception):d.consume(batch([r]))
   self.assertTrue(d.failed)
  d=decoder();b=batch([raw(1)]).remove_column(0)
  with self.assertRaises(ValueError):d.consume(b)
 def test_whole_book_depth_quality_and_sequence_repeats(self):
  r=raw(1);r['bid_px_01']='101.000000001';d,gs=decode([r,raw(1,1)],[2]);self.assertFalse(gs[0].book.valid);self.assertEqual(d.repeats,1);d.finish()
 def test_adapter_count_and_namespace_guards(self):
  d,gs=decode([raw(1)],[1]);g=gs[0]
  with self.assertRaises(ValueError):project(g)
  with self.assertRaises(ValueError):project(replace(g,count=2),enabled=True)
  with self.assertRaises(ValueError):project(replace(g,count=True),enabled=True)
  d.finish()


 def test_every_required_column_middle_null_rejected_before_mutation(self):
  for field in EXPECTED_COLUMNS:
   with self.subTest(field=field):
    rows=[raw(1),raw(2,1),raw(3,2)];rows[1][field]=None;d=decoder()
    with self.assertRaisesRegex(ValueError,'NULL_SOURCE_VALUE:'+field):d.consume(batch(rows))
    self.assertTrue(d.failed);self.assertEqual(d.rows,0);self.assertIsNone(d.last_recv)
    self.assertEqual(d.T_rows,0);self.assertTrue(all(c['count']==0 for c in d.groups.carry.values()))
    with self.assertRaisesRegex(ValueError,'LATCHED'):d.finish()
 def test_every_column_wrong_arrow_type_rejected_before_mutation(self):
  base=batch([raw(1),raw(2,1),raw(3,2)])
  for index,field in enumerate(EXPECTED_COLUMNS):
   with self.subTest(field=field):
    wrong=base.set_column(index,field,pa.array(base.column(index).to_pylist(),type=pa.large_string()));d=decoder()
    with self.assertRaisesRegex(ValueError,'EXACT_STRING_COLUMN_REQUIRED:'+field):d.consume(wrong)
    self.assertTrue(d.failed);self.assertEqual(d.rows,0);self.assertIsNone(d.last_recv)
 def test_representative_nonstring_types_are_not_coerced(self):
  base=batch([raw(1),raw(2,1),raw(3,2)]);index=EXPECTED_COLUMNS.index('sequence')
  for values in(pa.array([1,2,3]),pa.array([True,False,True]),pa.nulls(3)):
   d=decoder();wrong=base.set_column(index,'sequence',values)
   with self.assertRaisesRegex(ValueError,'EXACT_STRING_COLUMN'):d.consume(wrong)
   self.assertEqual(d.rows,0)
 def test_bad_batch_preserves_existing_partial_state_but_latches(self):
  d=decoder();self.assertEqual(d.consume(batch([raw(1,flags=0,action='T')])),())
  before=(d.rows,d.last_recv,d.T_rows,len(d.pending['AAPL']),d.groups.carry[0]['count'])
  rows=[raw(2,1),raw(3,2),raw(4,3)];rows[1]['sequence']=None
  with self.assertRaisesRegex(ValueError,'NULL_SOURCE_VALUE'):d.consume(batch(rows))
  self.assertEqual((d.rows,d.last_recv,d.T_rows,len(d.pending['AAPL']),d.groups.carry[0]['count']),before)
  with self.assertRaisesRegex(ValueError,'LATCHED'):d.consume(batch([raw(5,4)]))
 def test_empty_and_sentinel_price_encodings_preserved(self):
  for absent in('', '9223372036854775807'):
   row=raw(1,action='M');row['price']=absent
   for side in('bid','ask'):
    row[side+'_px_09']=absent;row[side+'_sz_09']='0';row[side+'_ct_09']='0'
   d,gs=decode([row],[1]);self.assertTrue(gs[0].book.valid);self.assertEqual(gs[0].trades,());d.finish()
   row['action']='T';d=decoder()
   with self.assertRaisesRegex(ValueError,'invalid observed trade'):d.consume(batch([row]))

if __name__=='__main__':unittest.main(verbosity=2)
