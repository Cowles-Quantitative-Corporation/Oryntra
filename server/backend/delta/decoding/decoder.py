"""Arrow adapter reusing accepted exact decimal/full-book/group validators."""
from pathlib import Path
import sys
import numpy as np,pyarrow as pa,pyarrow.compute as pc
from .book_arrays import Groups,book_quality,price_nanos,unsigned
from .schema import times,EXPECTED_COLUMNS
from .types import CompactBook,Trade,Group,NANO
from datetime import datetime,timezone
class ArrowDecoder:
 def __init__(self,*,session,opened,mapping,source_id,enabled=False):
  if enabled is not True:raise ValueError('DISABLED_ARROW_DECODER')
  if type(session)is not str or type(opened)is not int or not 0<=opened<=2**63-1 or type(source_id)is not str or not 0<len(source_id)<=128:raise ValueError('DECODER_SOURCE_IDENTITY')
  if datetime.fromtimestamp(opened//NANO,timezone.utc).date().isoformat()!=session:raise ValueError('SESSION_OPEN_IDENTITY')
  if type(mapping)is not dict or set(mapping)!={'AAPL','AMZN','GOOGL','JPM','MSFT','WMT','XOM'}or any(type(v)is not int or not 0<v<2**32 for v in mapping.values())or len(set(mapping.values()))!=7:raise ValueError('EXACT_TYPED_SEVEN_NAME_MAP')
  mapping=dict(mapping);self.failed=False;self.finished=False
  self.day=session;self.open=opened;self.mapping=mapping;self.source=source_id;self.names=tuple(mapping);self.groups=Groups(self.names);self.pending={n:[]for n in self.names};self.rows=0;self.last_recv=None;self.last_seq={n:None for n in self.names};self.first_raw={n:None for n in self.names};self.first_bad_raw_index={n:None for n in self.names};self.repeats=0;self.T_rows=0;self.T_shares=0;self.emitted_T=0;self.completed_raw=0
 def _consume(self,batch):
  if batch.schema.names!=EXPECTED_COLUMNS or not 0<batch.num_rows<=32768 or self.rows+batch.num_rows>20000000:raise ValueError('full schema/batch/file bound')
  # Source CSV uses explicit empty/sentinel strings, never Arrow nulls. Check
  # every physical column before casts or any decoder/group-state mutation.
  for field,column in zip(batch.schema,batch.columns):
   if field.type!=pa.string():raise ValueError('EXACT_STRING_COLUMN_REQUIRED:'+field.name)
   if column.null_count!=0:raise ValueError('NULL_SOURCE_VALUE:'+field.name)
  def col(k):return batch.column(batch.schema.get_field_index(k))
  recv=times(col('ts_recv'));event=times(col('ts_event'));n=len(recv);indices=np.arange(self.rows+1,self.rows+n+1,dtype=np.int64);day=self.open//(86400*NANO)*(86400*NANO)
  # Full physical batch, including OTHER instruments, validated before processing.
  if np.any(event>recv)or np.any(event<day)or np.any(recv>=day+86400*NANO)or np.any(recv[1:]<recv[:-1])or self.last_recv is not None and recv[0]<self.last_recv:raise ValueError('global source clock/order')
  self.last_recv=int(recv[-1]);flags=unsigned(col('flags'),8);iid=unsigned(col('instrument_id'));seq=unsigned(col('sequence'));size=unsigned(col('size'));unsigned(col('depth'),8)
  if np.any(unsigned(col('publisher_id'))!=2)or np.any(unsigned(col('rtype'),8)!=10):raise ValueError('XNAS MBP10 identity')
  if not pc.all(pc.match_substring_regex(col('ts_in_delta'),r'^-?[0-9]+$')).as_py():raise ValueError('delta syntax')
  pc.cast(col('ts_in_delta'),pa.int32(),safe=True)
  for k,values in(('action',list('AMCRTFN')),('side',['A','B','N'])):
   if not pc.all(pc.is_in(col(k),value_set=pa.array(values))).as_py():raise ValueError('source enum')
  codearr=pc.index_in(col('symbol'),value_set=pa.array(self.names))
  if codearr.null_count:raise ValueError('unknown symbol in admitted physical source')
  code=codearr.to_numpy(zero_copy_only=False);action=np.asarray(col('action').to_pylist());side=np.asarray(col('side').to_pylist());price,present=price_nanos(col('price'));mask,tops,counts=book_quality(batch)
  depth={s:sum(unsigned(col(f'{s}_sz_{i:02}')).astype(np.uint64)for i in range(5))for s in('bid','ask')}
  reset=(action=='R')|((flags&32)!=0)
  result=self.groups.consume(code,flags,reset,indices,recv,event)
  events=[]
  if result is not None:
   ends,firsts,nrows,gflags,gresets,firstrecv,firstevent=result
   if np.any(nrows>32):raise ValueError('completed group over32')
  for ni,name in enumerate(self.names):
   lane=np.flatnonzero(code==ni)
   if not len(lane):continue
   if np.any(iid[lane]!=self.mapping[name]):raise ValueError('dated instrument map mismatch')
   if self.first_raw[name]is None:self.first_raw[name]=int(recv[lane[0]])
   # Native parser latches ANY selected raw flags14 fault for this source replay.
   # Store the physical index so earlier same-batch groups remain causal/valid.
   bad=lane[(flags[lane]&14)!=0]
   if self.first_bad_raw_index[name]is None and len(bad):self.first_bad_raw_index[name]=int(indices[bad[0]])
   q=seq[lane];previous=self.last_seq[name]
   if np.any(q[1:]<q[:-1])or previous is not None and q[0]<previous:raise ValueError('unexplained sequence regression')
   self.repeats+=int(np.sum(q[1:]==q[:-1]))+int(previous is not None and q[0]==previous);self.last_seq[name]=int(q[-1])
   trade=lane[action[lane]=='T']
   if np.any(~present[trade])or np.any(price[trade]<=0)or np.any(size[trade]==0):raise ValueError('invalid observed trade blocks file')
   self.T_rows+=len(trade);self.T_shares+=int(np.sum(size[trade],dtype=np.uint64))
   newtrades=[Trade(int(indices[i]),int(recv[i]),int(price[i]),int(size[i]),str(side[i]))for i in trade]
   pending=self.pending[name]+newtrades;used=0
   positions=np.flatnonzero(code[ends]==ni)if result is not None else[]
   for j in positions:
    e=int(ends[j]);frontier=int(indices[e]);ts=[]
    while used<len(pending)and pending[used].index<=frontier:ts.append(pending[used]);used+=1
    self.emitted_T+=len(ts);self.completed_raw+=int(nrows[j])
    gf=int(gflags[j]);first_bad=self.first_bad_raw_index[name];valid=bool(mask[e]==0 and(first_bad is None or frontier<first_bad)and not gf&14 and recv[e]-event[e]<=2*NANO)
    # R/snapshot do NOT clear the native per-source quality latch.
    b=CompactBook(self.source+':'+str(frontier),name,self.source,self.day,int(recv[e]),int(event[e]),frontier,self.mapping[name],valid,bool(gresets[j]),*( (int(tops['bid'][0][e]),int(tops['ask'][0][e]),int(tops['bid'][2][e]),int(tops['ask'][2][e]),int(depth['bid'][e]),int(depth['ask'][e]),int(counts['bid'][e]),int(counts['ask'][e]))if valid else(0,0,0,0,0,0,0,0)))
    events.append(Group(b,int(firsts[j]),int(firstrecv[j]),gf,int(nrows[j]),tuple(ts)))
   self.pending[name]=pending[used:]
   if len(self.pending[name])>32 or self.groups.carry[ni]['count']>32:raise ValueError('incomplete group bound')
  self.rows+=n
  return tuple(sorted(events,key=lambda g:g.book.source_index))
 def _finish(self):
  if any(c['count']for c in self.groups.carry.values()):raise ValueError('partial EOF group; whole file quarantined')
  if any(self.pending.values()):raise ValueError('unmatched trade occurrence')
  if self.emitted_T!=self.T_rows or self.completed_raw!=self.rows:raise ValueError('observed occurrence/group row conservation')
  return {'observed_T_rows':self.T_rows,'observed_T_shares':self.T_shares,'emitted_T_occurrences':self.emitted_T,'completed_group_raw_rows':self.completed_raw,'rows':self.rows,'sequence_repeats':self.repeats,'sequence_role':'OBSERVATIONS_NOT_DEDUP_OR_COMPLETENESS','physical_row_enumeration':'IMPLICIT_ONCE_BY_STREAM_INDEX'}

 def consume(self,batch):
  if self.failed or self.finished:raise ValueError('DECODER_LATCHED')
  try:
   if not isinstance(batch,pa.RecordBatch):raise ValueError('RECORD_BATCH_REQUIRED')
   return self._consume(batch)
  except BaseException:self.failed=True;raise
 def finish(self):
  if self.failed or self.finished:raise ValueError('DECODER_LATCHED')
  try:
   result=self._finish();self.finished=True;return result
  except BaseException:self.failed=True;raise
