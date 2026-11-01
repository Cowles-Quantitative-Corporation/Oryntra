"""One disabled child/seven oracle lanes; reuse accepted v7bounded transport."""
from pathlib import Path
import sys,time,subprocess,os,hashlib,copy,json,resource,threading,functools
from . import _transport as transport
from .state_digest import digest
from ..volume.source_reference import Engine
from .execution_profile import PROFILE as C,LANES,PROFILE_SHA
from .protocol import group
DEFAULT_LANES=LANES
def semantic_root(source,session,lanes,engines,meta):
 hashes=tuple(digest(e)for e in engines)
 p=['DELTA_VOLUME_MUX_ROOT_V1',source,session,'7',str(int(meta['have'])),str(meta['frontier']),str(meta['recv']),str(meta['completed']),str(int(meta['have_decision'])),str(meta['decision'])]
 for (i,symbol,instrument),h in zip(lanes,hashes):p.extend((str(i),symbol,str(instrument),h))
 return hashlib.sha256(('\n'.join(p)+'\n').encode('ascii')).hexdigest(),hashes
def root(source,session,lanes,engines,meta,count, runtime_manifest):
 semantic,hashes=semantic_root(source,session,lanes,engines,meta)
 return hashlib.sha256(('DELTA_VOLUME_SOURCE_EXECUTION_ROOT_V25\n'+PROFILE_SHA+'\n'+str(count)+'\n'+runtime_manifest+'\n'+semantic+'\n').encode()).hexdigest(),hashes
def profile_limits(count):
 if type(count)is not int or count!=C['group_cap']:raise ValueError('EXACT_SOURCE_GROUP_CAP')
 return {'wall':C['limits']['wall_seconds_proposed'],'ops':C['operation_cap'],'input':C['input_wire_cap'],'output':C['output_wire_cap']}
def serialized(fn):
 @functools.wraps(fn)
 def call(self,*a,**kw):
  if not self._operation.acquire(blocking=False):
   try:raise RuntimeError('CONCURRENT_OR_REENTRANT_OPERATION')
   except Exception:self._poison();raise
  try:return fn(self,*a,**kw)
  finally:self._operation.release()
 return call
class Multiplex(transport.Incremental):
 def __init__(self,*,enabled=False,source=None,session=None,lanes=DEFAULT_LANES,binary=None,count=C['group_cap'],deadline=None,runtime_manifest=None,expected_binary_sha256=None):
  if enabled is not True:raise ValueError('DISABLED')
  if type(runtime_manifest)is not str or len(runtime_manifest)!=64 or any(c not in '0123456789abcdef' for c in runtime_manifest):raise ValueError('RUNTIME_MANIFEST_SHA256')
  if binary is None or type(expected_binary_sha256)is not str or hashlib.sha256(Path(binary).read_bytes()).hexdigest()!=expected_binary_sha256:raise ValueError('NATIVE_BINARY_PIN')
  self.runtime_manifest=runtime_manifest;self.native_binary_sha256=expected_binary_sha256
  if source!=C['math_namespace']or session!=C['input']['session']or lanes!=LANES:raise ValueError('PROFILE_IDENTITY')
  if type(lanes)is not tuple or len(lanes)!=7 or any(type(x)is not tuple or len(x)!=3 for x in lanes):raise ValueError('IMMUTABLE_SEVEN_LANE_MAP')
  if tuple(x[0]for x in lanes)!=tuple(range(7))or any(type(x[0])is not int or type(x[1])is not str or type(x[2])is not int for x in lanes)or len({x[1]for x in lanes})!=7 or len({x[2]for x in lanes})!=7:raise ValueError('UNIQUE_ORDERED_LANE_MAP')
  if type(count)is not int or count!=C['group_cap']:raise ValueError('FIXED_SOURCE_CAP')
  limits=profile_limits(count)
  remaining=limits['wall']if deadline is None else deadline-time.monotonic()
  if type(remaining)not in(int,float)or not 0<remaining<=limits['wall']:raise ValueError('WHOLE_RUN_DEADLINE')
  self.wall_limit=remaining;self.max_operations=limits['ops'];self.max_input=limits['input'];self.max_output=limits['output']
  self.fixed_count=count
  self.telemetry_ack=None;self.telemetry_adopted=(0,0,time.monotonic_ns());self.telemetry_progress=None
  self._operation=threading.Lock();self.source=source;self.session=session;self.lanes=lanes;self.engines=[Engine(source,n,session,i,10,2,enabled=True)for _,n,i in lanes]
  self.meta={'have':False,'frontier':0,'recv':0,'completed':0,'have_decision':False,'decision':0}
  self.started=time.monotonic();self.seq=0;self.input_bytes=0;self.output_bytes=0;self.snapshots=0;self.failed=False;self.finished=False;self.protocol_finished=False;self.last_attempt=None;self.attempts=hashlib.sha256();self.acks=hashlib.sha256();self.last_acked_digest=None;self.failure=None;self.ack_lane_metadata=[]
  from .owned_process import OwnedProcess
  self.proc=OwnedProcess([str(Path(binary).resolve()),'--recorded-source-only-'+PROFILE_SHA,runtime_manifest],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0,start_new_session=True,enabled=True,group_state=self.reader.group_state,on_created=self._register_owned_process);os.set_blocking(self.proc.stdin.fileno(),False)
  try:
   body=' '.join(str(v)for lane in lanes for v in lane)
   self._confirm('I',f'{source} {session} 7 {body}','-',self.engines,self.meta)
  except Exception:self._poison();raise
 def _register_owned_process(self,proc):
  self.proc=proc
 def _confirm(self,op,payload,lane,engines,meta,expected_snapshot=None):
  try:
   expected_root,hashes=root(self.source,self.session,self.lanes,engines,meta,self.fixed_count,self.runtime_manifest)
   parts,text=self._request(op,payload);body=parts[2].split(' ',5)
   expected=[str(lane),self.last_attempt['frame_sha256'],self.attempts.hexdigest(),'-'if lane=='-'else hashes[lane],expected_root]
   if parts[0]!=('S'if expected_snapshot is not None else'A')or len(body)!=(6 if expected_snapshot is not None else 5)or body[:5]!=expected:raise RuntimeError('LANE_FRAME_PREFIX_OR_AGGREGATE_MISMATCH')
   if expected_snapshot is not None and json.loads(body[5])!=expected_snapshot:raise RuntimeError('FULL_SNAPSHOT_MISMATCH')
   if self.failed:raise RuntimeError('GLOBAL_ABORT_DURING_ACK')
   self.seq+=1;self.acks.update((text+'\n'+expected_root+'\n').encode());self.last_acked_digest=expected_root
   self.ack_lane_metadata=[{'lane':i,'symbol':n,'instrument':instrument,'groups':engines[i].groups,'frontier':engines[i].last,'state_sha256':hashes[i]}for i,n,instrument in self.lanes]
   self.telemetry_ack=(self.seq,op,meta['completed'],meta['frontier'],expected_root,tuple((m['groups'],m['frontier'])for m in self.ack_lane_metadata),time.monotonic_ns())
  except Exception:self._poison();raise
 @serialized
 def push(self,lane,g):
  self._live()
  try:
   if type(lane)is not int or not 0<=lane<7:raise ValueError('LANE_MAP')
   if g.last<=self.meta['frontier']or self.meta['have']and g.recv<self.meta['recv']or self.meta['have_decision']and g.recv<self.meta['decision']:raise ValueError('GLOBAL_COMPLETION_ORDER')
   if self.meta['completed']>=self.fixed_count or self.engines[lane].groups>=C['per_lane_group_caps'][self.lanes[lane][1]]:raise ValueError('SOURCE_LIFETIME_CAP')
   if g.last>C['input']['raw_rows_exact']or g.first_recv<C['slice_open_ns']or g.recv>=C['slice_close_ns']:raise ValueError('PROFILE_TEMPORAL_POPULATION')
   pending=list(self.engines);pending[lane]=copy.deepcopy(pending[lane]);pending[lane].push(g)
   meta=dict(self.meta,have=True,frontier=g.last,recv=g.recv,completed=self.meta['completed']+1)
   self._confirm('P',str(lane)+' '+group(g)[2:],lane,pending,meta);self.engines=pending;self.meta=meta;self.telemetry_adopted=(meta['completed'],meta['frontier'],time.monotonic_ns())
  except Exception:self._poison();raise
 @serialized
 def snapshot(self,lane,at):
  self._live()
  try:
   if type(lane)is not int or not 0<=lane<7 or type(at)is not int:raise ValueError('LANE_OR_CLOCK')
   if self.snapshots>=transport.MAX_SNAPSHOTS:raise RuntimeError('SNAPSHOT_LIMIT')
   if self.meta['have']and at<self.meta['recv']or self.meta['have_decision']and at<self.meta['decision']:raise ValueError('GLOBAL_DECISION_CLOCK')
   if self.snapshots>=14 or lane!=self.snapshots%7 or at!=C['snapshot_clocks'][self.snapshots//7]:raise ValueError('EXACT_SOURCE_SNAPSHOT_SCHEDULE')
   expected=self.engines[lane].snapshot(at);meta=dict(self.meta,have_decision=True,decision=at)
   self._confirm('S',f'{lane} {at}',lane,self.engines,meta,expected);self.meta=meta;self.snapshots+=1;return expected
  except Exception:self._poison();raise
 @serialized
 def finish(self):
  self._live()
  try:
   if self.meta['completed']>self.fixed_count or self.snapshots!=14:raise ValueError('SOURCE_COMPLETION_CAP')
   self._confirm('F','','-',self.engines,self.meta);self.proc.stdin.close();self.proc.wait(timeout=max(.01,min(20,self.wall_limit-(time.monotonic()-self.started))));self._guard()
   if self.proc.returncode!=0 or self.proc.stdout.read(1)or self.proc.stderr.read(1024):raise RuntimeError('UNCLEAN_FINAL_EXIT')
   self.protocol_finished=True
   for f in(self.proc.stdout,self.proc.stderr):f.close()
   return self.diagnostic()
  except Exception:self._poison();raise
 def diagnostic(self):
  return {'scope':'DISABLED_PROFILE_BOUND_SOURCE_ONLY_SEVEN_LANE_TRANSPORT','profile_sha256':PROFILE_SHA,'runtime_manifest_sha256':self.runtime_manifest,'native_binary_sha256':self.native_binary_sha256,'failed':self.failed,'failure':self.failure,'finished':self.finished,'native_outcome':'ACKNOWLEDGED'if self.finished else'UNVERIFIED_ALL_LANES_NO_RELEASE','last_acked_sequence':self.seq,'last_acked_root_sha256':self.last_acked_digest,'last_acked_lanes':copy.deepcopy(self.ack_lane_metadata),'last_attempt':self.last_attempt,'attempted_stream_sha256':self.attempts.hexdigest(),'acked_stream_sha256':self.acks.hexdigest(),'input_bytes':self.input_bytes,'output_bytes':self.output_bytes,'snapshots_total':self.snapshots,'retained_history_frames':0,'retained_response_history':0,'max_inflight_requests':1,'owned_child_PID':self.proc.pid,'owned_child_returncode':self.proc.poll(),'elapsed_seconds':time.monotonic()-self.started,'parent_peak_RSS_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
