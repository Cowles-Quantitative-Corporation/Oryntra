"""Disabled conditional reported-T volume buckets; no feed/model/account consumer."""
from dataclasses import dataclass,asdict
from collections import deque
from copy import deepcopy
from fractions import Fraction
import re
U64=2**64-1;U32=2**32-1;I64=2**63-1
LEGACY_BUCKET_MAX_AGE_NS=30_000_000_000

def uint(x,lo=0,hi=U64):
 if type(x)is not int or not lo<=x<=hi:raise ValueError('DOMAIN')
 return x

def add(a,b,cap=U64):
 uint(cap);uint(a,0,cap);uint(b,0,cap)
 if b>cap-a:raise ValueError('OVERFLOW')
 return a+b

def identity(x):
 if type(x)is not str or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',x):raise ValueError('DOMAIN')
 return x

def ratio(n,d):
 uint(d,1)
 if type(n)is not int or abs(n)>U64:raise ValueError('DOMAIN')
 f=Fraction(n,d);return f'{f.numerator}/{f.denominator}'

def bounds(v):
 b,a,n=v;d=b-a;total=b+a+n
 return (max(0,abs(d)-n),min(total,abs(d)+n))

@dataclass(frozen=True)
class Trade:
 index:int
 recv:int
 price:int
 shares:int
 side:str
 action:str='T'

@dataclass(frozen=True)
class Group:
 source:str
 symbol:str
 session:str
 instrument:int
 first:int
 last:int
 row_count:int
 first_recv:int
 recv:int
 event:int
 flags:int
 valid:bool
 reset:bool
 correction_notice:bool
 trades:tuple

from .reference import Group,Trade
from .profile import LIFETIME_CAPS

class Engine:
 def __init__(self,source,symbol,session,instrument,bucket,keep,cap=U64,gap=2_000_000_000,piece_cap=256,*,enabled=False):
  if enabled is not True or not identity(source).startswith('synthetic-source:'):raise ValueError('DISABLED')
  self.source=source;self.symbol=identity(symbol);self.session=identity(session);self.instrument=uint(instrument,1,U32)
  self.bucket=uint(bucket,1,U32);self.keep=uint(keep,1,128);self.cap=uint(cap,1);self.gap=uint(gap,1,I64);self.piece_cap=uint(piece_cap,1,256)
  if bucket>cap:raise ValueError('DOMAIN')
  self.boundary_causes=[];self.last_group=None;self.epoch=0;self.groups=0;self.last=0;self.recv=None;self.failed=False;self.taint=False;self.correction=False;self.closed=0
  self.input=[0]*3;self.accepted=[0]*3;self.quarantine=[0]*3;self.discarded=[0]*3;self.epoch_v=[0]*3;self.completed=[0]*3;self.partial=[0]*3
  self.lifetime_completed=[0]*3;self.lifetime_evicted=[0]*3;self.discarded_completed=[0]*3;self.discarded_partial=[0]*3;self.lifetime_closed=0
  self.pieces=[];self.ring=deque(maxlen=keep)
 def _vecadd(self,v,side,qty):
  i='BAN'.index(side);v[i]=add(v[i],qty,self.cap)
  if sum(v)>self.cap:raise ValueError('OVERFLOW')
 def _clear(self):
  for i in range(3):
   self.discarded[i]=add(self.discarded[i],self.epoch_v[i],self.cap);self.discarded_completed[i]=add(self.discarded_completed[i],self.completed[i],self.cap);self.discarded_partial[i]=add(self.discarded_partial[i],self.partial[i],self.cap)
  self.epoch_v=[0]*3;self.completed=[0]*3;self.partial=[0]*3;self.pieces=[];self.ring.clear();self.closed=0;self.epoch=add(self.epoch,1)
 def _piece(self,t,q,g):
  if len(self.pieces)>=self.piece_cap:raise ValueError('PIECE_LIMIT')
  self._vecadd(self.partial,t.side,q);self.pieces.append([t.index,t.recv,g.recv,t.price,q,t.side])
 def _complete(self,g):
  self.closed=add(self.closed,1);self.lifetime_closed=add(self.lifetime_closed,1)
  for i in range(3):
   self.completed[i]=add(self.completed[i],self.partial[i],self.cap);self.lifetime_completed[i]=add(self.lifetime_completed[i],self.partial[i],self.cap)
   if len(self.ring)==self.keep:self.lifetime_evicted[i]=add(self.lifetime_evicted[i],self.ring[0]['volume'][i],self.cap)
  lo,hi=bounds(self.partial)
  self.ring.append({'ordinal':self.closed,'volume':list(self.partial),'pieces':deepcopy(self.pieces),'available_ns':g.recv,'closure_frontier':g.last,'lower':ratio(lo,self.bucket),'upper':ratio(hi,self.bucket),'known_side_coverage':ratio(self.bucket-self.partial[2],self.bucket),'signed_lower':ratio(self.partial[0]-self.partial[1]-self.partial[2],self.bucket),'signed_upper':ratio(self.partial[0]-self.partial[1]+self.partial[2],self.bucket)})
  self.partial=[0]*3;self.pieces=[]
 def _trade(self,t,g):
  self._vecadd(self.accepted,t.side,t.shares);self._vecadd(self.epoch_v,t.side,t.shares)
  left=t.shares
  if sum(self.partial):
   used=min(left,self.bucket-sum(self.partial));self._piece(t,used,g);left-=used
   if sum(self.partial)==self.bucket:self._complete(g)
  full,left=divmod(left,self.bucket)
  if full:
   # Fast-forward old full buckets; every retained piece still names its print.
   count=min(full,self.keep);skip=full-count;self.closed=add(self.closed,skip);self.lifetime_closed=add(self.lifetime_closed,skip)
   self._vecadd(self.completed,t.side,skip*self.bucket);self._vecadd(self.lifetime_completed,t.side,skip*self.bucket);self._vecadd(self.lifetime_evicted,t.side,skip*self.bucket)
   for _ in range(count):self._piece(t,self.bucket,g);self._complete(g)
  if left:self._piece(t,left,g)
 def _validate(self,g):
  if type(g)is not Group:raise ValueError('DOMAIN')
  identity(g.source);identity(g.symbol);identity(g.session);uint(g.instrument,1,U32)
  if(g.source,g.symbol,g.session,g.instrument)!=(self.source,self.symbol,self.session,self.instrument):raise ValueError('DOMAIN')
  for v in(g.first,g.last):uint(v,1,I64)
  for v in(g.first_recv,g.recv,g.event):uint(v,0,I64)
  uint(g.flags,0,255);uint(g.row_count,1,32)
  if any(type(v)is not bool for v in(g.valid,g.reset,g.correction_notice))or type(g.trades)is not tuple or len(g.trades)>g.row_count:raise ValueError('DOMAIN')
  if (g.row_count==1)!=(g.first==g.last) or not g.first<=g.last or g.row_count>g.last-g.first+1 or not g.flags&128 or not g.first_recv<=g.recv or g.event>g.recv or g.first<=self.last or self.recv is not None and g.first_recv<self.recv:raise ValueError('DOMAIN')
  last=g.first-1;recv=g.first_recv
  for t in g.trades:
   if type(t)is not Trade:raise ValueError('DOMAIN')
   for v in(t.index,t.recv,t.price,t.shares):uint(v)
   if not last<t.index<=g.last or t.index<g.first or not recv<=t.recv<=g.recv or not 0<t.price<I64 or not 0<t.shares<=U32 or t.side not in('B','A','N')or t.action!='T':raise ValueError('DOMAIN')
   last=t.index;recv=t.recv
 def push(self,g):
  if self.failed:raise ValueError('LATCHED')
  try:
   self._validate(g);x=deepcopy(self);x._apply(g);x._invariants()
  except ValueError:
   self.failed=True;raise
  self.__dict__.update(x.__dict__)
 def _apply(self,g):
  if self.groups>=LIFETIME_CAPS[self.instrument]:raise ValueError('GROUP_LIMIT')
  gap=self.recv is not None and g.recv-self.recv>self.gap
  boundary=g.reset or bool(g.flags&32);bad=not g.valid or bool(g.flags&14)
  if boundary or bad or g.correction_notice or gap:
   self.boundary_causes=[name for name,test in(('EXPLICIT_RESET',boundary),('QUALITY',bad),('KNOWN_CORRECTION',g.correction_notice),('GAP_POLICY',gap))if test];self._clear()
  if g.correction_notice:self.correction=True
  if bad:self.taint=True
  elif boundary:self.taint=False # explicit clean reset; correction latch remains
  self.last=g.last;self.recv=g.recv;self.last_group=g;self.groups+=1
  blocked=boundary or bad or self.taint or self.correction
  for t in g.trades:
   self._vecadd(self.input,t.side,t.shares)
   if blocked:self._vecadd(self.quarantine,t.side,t.shares)
   else:self._trade(t,g)
 def _invariants(self):
  for i in range(3):
   if self.input[i]!=self.accepted[i]+self.quarantine[i]or self.accepted[i]!=self.discarded[i]+self.epoch_v[i]or self.epoch_v[i]!=self.completed[i]+self.partial[i]:raise ValueError('CONSERVATION')
  if any(self.lifetime_completed[i]!=self.discarded_completed[i]+self.completed[i]or self.discarded[i]!=self.discarded_completed[i]+self.discarded_partial[i]or self.lifetime_evicted[i]>self.lifetime_completed[i]for i in range(3))or sum(self.lifetime_completed)!=self.lifetime_closed*self.bucket:raise ValueError('CONSERVATION')
  if sum(self.partial)>=self.bucket or sum(self.completed)!=self.closed*self.bucket:raise ValueError('CONSERVATION')
  for b in self.ring:
   if sum(b['volume'])!=self.bucket or [sum(p[4]for p in b['pieces']if p[5]==s)for s in'BAN']!=b['volume']:raise ValueError('CONSERVATION')
 def snapshot(self,decision):
  uint(decision,0,I64)
  if self.failed:raise ValueError('LATCHED')
  if self.recv is not None and decision<self.recv:raise ValueError('FUTURE_PREFIX')
  status='UNAVAILABLE_CORRECTION'if self.correction else'UNAVAILABLE_QUALITY'if self.taint else'STALE_DECISION'if self.recv is not None and decision-self.recv>self.gap else'WARMUP'if len(self.ring)<self.keep else'STALE_BUCKET_WINDOW'if decision-self.ring[-1]['available_ns']>LEGACY_BUCKET_MAX_AGE_NS else'AVAILABLE_REPORTED_BUCKETS'
  lower=upper=strict=known=signed_lower=signed_upper=strict_signed=None
  if status=='AVAILABLE_REPORTED_BUCKETS':
   lower=ratio(sum(bounds(b['volume'])[0]for b in self.ring),self.keep*self.bucket);upper=ratio(sum(bounds(b['volume'])[1]for b in self.ring),self.keep*self.bucket)
   vol=[sum(b['volume'][i]for b in self.ring)for i in range(3)];den=self.keep*self.bucket;known=ratio(den-vol[2],den);signed_lower=ratio(vol[0]-vol[1]-vol[2],den);signed_upper=ratio(vol[0]-vol[1]+vol[2],den)
   if all(b['volume'][2]==0 for b in self.ring):strict=lower;strict_signed=ratio(vol[0]-vol[1],den)
  return {'contract':'CONDITIONAL_REPORTED_VOLUME_BUCKET_SYNTHETIC_V2','volume_unit':'REPORTED_SHARES','price_unit':'USD_NANODOLLARS','clock_unit':'NANOSECONDS','source':self.source,'symbol':self.symbol,'session':self.session,'instrument':self.instrument,'status':status,'epoch':self.epoch,'groups':self.groups,'frontier':self.last,'available_ns':self.recv,'decision_ns':decision,'group_row_count':None if self.last_group is None else self.last_group.row_count,'group_physical_span':None if self.last_group is None else self.last_group.last-self.last_group.first+1,'group_first_frontier':None if self.last_group is None else self.last_group.first,'group_first_recv_ns':None if self.last_group is None else self.last_group.first_recv,'group_event_ns':None if self.last_group is None else self.last_group.event,'group_flags':None if self.last_group is None else self.last_group.flags,'group_valid':None if self.last_group is None else self.last_group.valid,'group_reset':None if self.last_group is None else self.last_group.reset,'group_correction_notice':None if self.last_group is None else self.last_group.correction_notice,'input':list(self.input),'accepted':list(self.accepted),'quarantine':list(self.quarantine),'discarded':list(self.discarded),'epoch_volume':list(self.epoch_v),'completed_volume':list(self.completed),'partial_volume':list(self.partial),'partial_pieces':deepcopy(self.pieces),'completed_buckets':self.closed,'lifetime_completed_buckets':self.lifetime_closed,'lifetime_completed_volume':list(self.lifetime_completed),'lifetime_evicted_volume':list(self.lifetime_evicted),'discarded_completed_volume':list(self.discarded_completed),'discarded_partial_volume':list(self.discarded_partial),'retained_volume':[sum(b['volume'][i]for b in self.ring)for i in range(3)],'evicted_current_epoch_volume':[self.completed[i]-sum(b['volume'][i]for b in self.ring)for i in range(3)],'buckets':list(deepcopy(self.ring)),'lower':lower,'upper':upper,'strict_absolute_imbalance':strict,'known_side_coverage':known,'signed_lower':signed_lower,'signed_upper':signed_upper,'strict_signed_imbalance':strict_signed,'last_boundary_causes':list(self.boundary_causes),'bucket_max_age_ns':LEGACY_BUCKET_MAX_AGE_NS,'last_bucket_completion_ns':None if not self.ring else self.ring[-1]['available_ns'],'bucket_age_ns':None if not self.ring else decision-self.ring[-1]['available_ns'],'bucket_span_ns':None if not self.ring else self.ring[-1]['available_ns']-self.ring[0]['pieces'][0][2],'print_size_hhi':None,'print_size_hhi_status':'NOT_IMPLEMENTED_NO_SQUARED_SIZE_ACCUMULATION','known_correction_notice':self.correction,'market_capture':'UNKNOWN','correction_semantics':'UNKNOWN_UNLESS_NOTICE_BLOCKED','independent_bucket_samples':False,'forecast_or_admission_influence':False,'calibrated_VPIN_probability':False}
