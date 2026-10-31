"""Disabled exact diagnostic interface. No source reader, model or order consumer."""
from dataclasses import dataclass,asdict
from fractions import Fraction
import hashlib,json,re
U64=2**64-1;I64=2**63-1
STATUSES=('UNAVAILABLE_CORRECTION','UNAVAILABLE_QUALITY','STALE_DECISION','WARMUP','STALE_BUCKET_WINDOW','AVAILABLE_REPORTED_BUCKETS')
def require(ok,reason):
 if not ok:raise ValueError(reason)
def integer(x,lo=0,hi=U64):require(type(x)is int and lo<=x<=hi,'INTEGER_DOMAIN');return x
def digest(snapshot):return hashlib.sha256(json.dumps(snapshot,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def sha(x):require(type(x)is str and re.fullmatch('[0-9a-f]{64}',x)is not None,'SHA256_DOMAIN');return x
def ratio(x):
 require(type(x)is str and re.fullmatch(r'-?[0-9]+/[1-9][0-9]*',x)is not None,'EXACT_RATIO_REQUIRED');f=Fraction(x);require(x==f'{f.numerator}/{f.denominator}','CANONICAL_RATIO_REQUIRED');return f
@dataclass(frozen=True)
class Scale:
 bucket_shares:int
 retained_buckets:int
 decision_max_age_ns:int
 bucket_max_age_ns:int
 def validate(self):
  integer(self.bucket_shares,1,2**32-1);integer(self.retained_buckets,1,128);integer(self.decision_max_age_ns,1,I64);integer(self.bucket_max_age_ns,1,I64)
@dataclass(frozen=True)
class Context:
 artifact_sha256:str
 snapshot_sha256:str
 source_kind:str
 source:str
 raw_sha256:str|None
 symbol:str
 session:str
 instrument:int
 decision_ns:int
 snapshot_frontier:int
 scale:Scale
 scope:str
 def validate(self):
  sha(self.artifact_sha256);sha(self.snapshot_sha256);integer(self.instrument,1);integer(self.decision_ns,0,I64);integer(self.snapshot_frontier);require(type(self.scale)is Scale,'TYPED_SCALE_REQUIRED');self.scale.validate()
  require(all(type(v)is str and 0<len(v)<=128 for v in(self.source,self.symbol,self.session)),'SOURCE_IDENTITY_DOMAIN')
  if self.source_kind=='INVENTED_FIXTURE':require(self.raw_sha256 is None and self.scope=='SYNTHETIC_ONLY','INVENTED_SCOPE')
  elif self.source_kind=='RECORDED_NORMALIZED_OCCURRENCES':
   sha(self.raw_sha256);require(self.source=='synthetic-source:recorded:'+self.raw_sha256 and self.scope=='OPENING_MINUTE_ENGINEERING_ONLY','RECORDED_NAMESPACE_OR_SCOPE')
  else:raise ValueError('SOURCE_KIND_UNQUALIFIED')
@dataclass(frozen=True)
class Diagnostic:
 context:Context
 native_status:str
 information_absolute:str
 information_signed:str
 lower:Fraction|None
 upper:Fraction|None
 absolute_width:Fraction|None
 signed_lower:Fraction|None
 signed_upper:Fraction|None
 signed_width:Fraction|None
 known_side_coverage:Fraction|None
 strict_reported_absolute:Fraction|None
 strict_reported_signed:Fraction|None
 retained_BAN:tuple
 available_ns:int|None
 bucket_age_ns:int|None
 bucket_span_ns:int|None
 bucket_lineage:tuple
 bound_semantics:str='CONSERVATIVE_MASS_RELAXATION_NOT_PRINT_LEVEL_ATTAINABILITY'
 side_semantics:str='REPORTED_B_A_LABELS_NOT_VERIFIED_TRUE_AGGRESSOR;N_UNASSIGNED'
 forecast_admission_influence:bool=False
 def model_vector(self):raise RuntimeError('MODEL_INTEGRATION_NOT_AUTHORIZED')
 def to_dict(self):
  def encode(v):
   if isinstance(v,Fraction):return f'{v.numerator}/{v.denominator}'
   if isinstance(v,dict):return {k:encode(x)for k,x in v.items()}
   if isinstance(v,(tuple,list)):return [encode(x)for x in v]
   return v
  return encode(asdict(self))
def information(lo,hi,full):return 'POINT'if lo==hi else'FULL_RANGE'if(lo,hi)==full else'BOUNDED_RANGE'
def snapshot_to_feature(snapshot,context,*,decision_ns,observed_source_frontier,enabled=False):
 if enabled is not True:raise ValueError('DISABLED_FEATURE_INTERFACE')
 require(type(snapshot)is dict and type(context)is Context,'TYPED_CONTEXT_REQUIRED');context.validate()
 require(digest(snapshot)==context.snapshot_sha256,'SNAPSHOT_CONTENT_BINDING')
 integer(decision_ns,0,I64);integer(observed_source_frontier)
 require(decision_ns==context.decision_ns==snapshot.get('decision_ns'),'DECISION_CLOCK_IDENTITY')
 for field,want in [('source',context.source),('symbol',context.symbol),('session',context.session),('instrument',context.instrument),('frontier',context.snapshot_frontier)]:require(snapshot.get(field)==want,'IDENTITY_OR_FRONTIER:'+field)
 integer(snapshot['instrument'],1);integer(snapshot['frontier']);require(snapshot['frontier']<=observed_source_frontier,'FUTURE_SOURCE_FRONTIER')
 require(snapshot.get('contract')=='CONDITIONAL_REPORTED_VOLUME_BUCKET_SYNTHETIC_V2'and snapshot.get('volume_unit')=='REPORTED_SHARES'and snapshot.get('clock_unit')=='NANOSECONDS'and snapshot.get('price_unit')=='USD_NANODOLLARS','NATIVE_CONTRACT_OR_UNITS')
 for field in('calibrated_VPIN_probability','independent_bucket_samples','forecast_or_admission_influence'):require(snapshot.get(field)is False,'UNSUPPORTED_CLAIM:'+field)
 require(snapshot.get('market_capture')=='UNKNOWN','MARKET_CAPTURE_NOT_QUALIFIED')
 status=snapshot.get('status');require(status in STATUSES,'UNKNOWN_NATIVE_STATUS')
 available=snapshot.get('available_ns')
 if available is not None:
  integer(available,0,I64);require(available<=decision_ns,'FUTURE_AVAILABILITY');event=snapshot.get('group_event_ns');integer(event,0,I64);require(event<=available,'FUTURE_EVENT')
 for field in('bucket_age_ns','bucket_span_ns'):
  if snapshot.get(field)is not None:integer(snapshot[field],0,I64)
 buckets=snapshot.get('buckets');require(type(buckets)is list and len(buckets)<=context.scale.retained_buckets,'BUCKET_POPULATION')
 if buckets:require(available is not None,'RETAINED_DATA_WITHOUT_SNAPSHOT_AVAILABILITY')
 volume=[0,0,0];lineage=[];absolute_lo=absolute_hi=0
 for b in buckets:
  require(type(b)is dict,'BUCKET_SCHEMA')
  v=b.get('volume');require(type(v)is list and len(v)==3,'BAN_DOMAIN')
  for j,x in enumerate(v):integer(x);volume[j]+=x
  require(sum(v)==context.scale.bucket_shares,'BUCKET_SCALE_MISMATCH');last=integer(b.get('closure_frontier'));at=integer(b.get('available_ns'),0,I64);ordinal=integer(b.get('ordinal'),1)
  require(last<=snapshot['frontier']and at<=decision_ns,'FUTURE_BUCKET_LINEAGE')
  require(at<=available,'BUCKET_COMPLETION_AFTER_SNAPSHOT_AVAILABILITY')
  pieces=b.get('pieces');require(type(pieces)is list and 0<len(pieces)<=256,'PIECE_BOUND');refs=[];piece_mass=[0,0,0]
  for piece in pieces:
   require(type(piece)is list and len(piece)==6,'PIECE_SCHEMA');ix,recv,completed,price,shares,side=piece
   integer(ix,1);integer(recv,0,I64);integer(completed,0,I64);integer(price,1,I64);integer(shares,1,2**32-1);require(side in('B','A','N'),'SIDE_DOMAIN')
   require(ix<=last and recv<=completed<=at<=available,'FUTURE_PRINT_LINEAGE');piece_mass['BAN'.index(side)]+=shares;refs.append((ix,recv,completed,shares,side))
  require(piece_mass==v,'PIECE_MASS_CONSERVATION');lineage.append((ordinal,last,at,tuple(refs)))
  delta=abs(v[0]-v[1]);absolute_lo+=max(0,delta-v[2]);absolute_hi+=min(sum(v),delta+v[2])
 require(snapshot.get('retained_volume')==volume,'RETAINED_MASS_MISMATCH')
 if buckets:
  require(snapshot.get('last_bucket_completion_ns')==buckets[-1]['available_ns'],'LAST_BUCKET_CLOCK_IDENTITY')
  require(snapshot.get('bucket_span_ns')==buckets[-1]['available_ns']-buckets[0]['pieces'][0][2],'BUCKET_SPAN_IDENTITY')
  for previous,current in zip(buckets,buckets[1:]):require(previous['ordinal']<current['ordinal']and previous['available_ns']<=current['available_ns']and previous['closure_frontier']<=current['closure_frontier'],'BUCKET_ORDER')
 fields=('lower','upper','known_side_coverage','signed_lower','signed_upper','strict_absolute_imbalance','strict_signed_imbalance')
 if status!='AVAILABLE_REPORTED_BUCKETS':
  require(all(snapshot.get(k)is None for k in fields),'UNDEFINED_NATIVE_METRIC_MUST_STAY_NONE')
  return Diagnostic(context,status,'UNDEFINED','UNDEFINED',None,None,None,None,None,None,None,None,None,tuple(volume),available,snapshot.get('bucket_age_ns'),snapshot.get('bucket_span_ns'),tuple(lineage))
 require(available is not None and decision_ns-available<=context.scale.decision_max_age_ns,'STALE_AVAILABLE_STATUS')
 require(len(buckets)==context.scale.retained_buckets and buckets,'AVAILABLE_SCALE_OR_EMPTY')
 require(snapshot.get('known_correction_notice')is False and snapshot.get('group_valid')is True,'TAINTED_AVAILABLE_STATUS')
 last_at=buckets[-1]['available_ns'];require(decision_ns-last_at<=context.scale.bucket_max_age_ns and snapshot.get('bucket_max_age_ns')==context.scale.bucket_max_age_ns,'STALE_BUCKET_OR_PROFILE_MISMATCH')
 require(snapshot.get('bucket_age_ns')==decision_ns-last_at,'BUCKET_AGE_IDENTITY')
 den=sum(volume);B,A,N=volume;expected={'lower':Fraction(absolute_lo,den),'upper':Fraction(absolute_hi,den),'known_side_coverage':Fraction(B+A,den),'signed_lower':Fraction(B-A-N,den),'signed_upper':Fraction(B-A+N,den)}
 for k,want in expected.items():require(ratio(snapshot.get(k))==want,'BOUND_OR_COVERAGE_MISMATCH:'+k)
 strict_abs=strict_signed=None
 if N==0:
  strict_abs=ratio(snapshot.get('strict_absolute_imbalance'));strict_signed=ratio(snapshot.get('strict_signed_imbalance'));require(strict_abs==expected['lower']==expected['upper']and strict_signed==expected['signed_lower']==expected['signed_upper'],'STRICT_REPORTED_POINT_MISMATCH')
 else:require(snapshot.get('strict_absolute_imbalance')is None and snapshot.get('strict_signed_imbalance')is None,'UNKNOWN_SIDE_CANNOT_CREATE_POINT')
 lo,hi=expected['lower'],expected['upper'];sl,sh=expected['signed_lower'],expected['signed_upper'];require(0<=lo<=hi<=1 and -1<=sl<=sh<=1,'BOUND_DOMAIN')
 return Diagnostic(context,status,information(lo,hi,(Fraction(0),Fraction(1))),information(sl,sh,(Fraction(-1),Fraction(1))),lo,hi,hi-lo,sl,sh,sh-sl,expected['known_side_coverage'],strict_abs,strict_signed,tuple(volume),available,snapshot['bucket_age_ns'],snapshot.get('bucket_span_ns'),tuple(lineage))
