"""Cooperative live RSS supervision of this parent and one attested owned child."""
from pathlib import Path
import ctypes,threading,time,os,subprocess,hashlib,sys,math
from contextlib import contextmanager
P=Path(__file__).resolve().parent
class Sample(ctypes.Structure):_fields_=[('parent_rss',ctypes.c_uint64),('child_rss',ctypes.c_uint64),('child_start',ctypes.c_uint64),('child_exit',ctypes.c_uint64),('error',ctypes.c_int),('descendants',ctypes.c_int)]
class OwnedReader:
 def __init__(self,library_path,*,expected_sha256):
  if sys.platform!='darwin':raise RuntimeError('OWNED_RSS_PLATFORM_UNSUPPORTED')
  path=Path(library_path)
  if type(expected_sha256)is not str or len(expected_sha256)!=64 or hashlib.sha256(path.read_bytes()).hexdigest()!=expected_sha256:raise ValueError('OWNED_RSS_LIBRARY_PIN')
  self.lib=ctypes.CDLL(str(path.resolve()));self.lib.owned_sample.argtypes=[ctypes.c_int,ctypes.c_uint64,ctypes.POINTER(Sample)];self.lib.owned_sample.restype=ctypes.c_int
  self.lib.owned_group_state.argtypes=[ctypes.c_int];self.lib.owned_group_state.restype=ctypes.c_int
 def group_state(self,pid):
  if type(pid)is not int or not 0<pid<2**31:raise ValueError('OWNED_GROUP_PID')
  return int(self.lib.owned_group_state(pid))
 def sample(self,pid,expected):
  if type(pid)is not int or not 0<=pid<2**31 or type(expected)is not int or not 0<=expected<2**64:raise ValueError('OWNED_SAMPLE_IDENTITY')
  s=Sample();code=self.lib.owned_sample(pid,expected,ctypes.byref(s));v={n:int(getattr(s,n))for n,_ in Sample._fields_}
  if code:raise RuntimeError(f'RSS_MEASUREMENT_UNCERTAIN:code={code}:errno={s.error}:unexpected_descendants={s.descendants}')
  return v
class Monitor:
 def __init__(self,proc,*,reader,on_failure,final_pending,limit=512*1024*1024,interval=.025,max_gap=.250,watch=True,clock=time.monotonic,on_created=None):
  if type(limit)is not int or not 0<limit<=512*1024*1024 or any(type(v)not in(int,float)or not math.isfinite(v)or v<=0 for v in(interval,max_gap))or interval>.025 or max_gap>.250:raise ValueError('OWNED_MONITOR_LIMITS')
  self.proc=proc;self.reader=reader;self.on_failure=on_failure;self.final_pending=final_pending;self.limit=limit;self.interval=interval;self.max_gap=max_gap;self.clock=clock;self.lock=threading.Lock();self.stop_event=threading.Event();self.thread=None;self.failure=None;self.token=0;self.last=None;self.count=0;self.max_parent=0;self.max_child=0;self.max_sum=0;self.max_sample_gap=0.;self.max_read_span=0.;self.final_exit_samples=0;self.confirmed_exit_races=0
  self.last_valid=None;self.read_status='NOT_MEASURED';self.last_exit=None;self.cache={}
  self._cache()
  if on_created:on_created(self)
  self.check(force=True)
  if watch:self.thread=threading.Thread(target=self._loop,name='owned-rss-supervisor',daemon=True);self.thread.start()
 def _loop(self):
  while not self.stop_event.wait(self.interval):
   try:self.check(force=True)
   except Exception:return
 @contextmanager
 def _timed_lock(self):
  if not self.lock.acquire(timeout=self.max_gap):
   self.failure=self.failure or 'RSS_SUPERVISOR_LOCK_TIMEOUT_UNCERTAIN';self.stop_event.set();self.cache={**self.cache,'failure':self.failure,'view':'LOCK_UNAVAILABLE_CACHED_VALUES','read_status':'UNKNOWN_LOCK_HELD'};self.on_failure(self.failure);raise RuntimeError(self.failure)
  try:yield
  finally:self.lock.release()
 def check(self,force=False):
  reason=None
  with self._timed_lock():
   if self.failure:raise RuntimeError(self.failure)
   before=self.clock()
   if self.last is not None:
    gap=before-self.last;self.max_sample_gap=max(self.max_sample_gap,gap)
    if gap>self.max_gap:reason='RSS_MEASUREMENT_GAP_UNCERTAIN';self.read_status='NO_READ_GAP_FAILURE'
    elif not force and gap<self.interval:return
   if reason is None:
    try:
     self.read_status='READ_PENDING';code=self.proc.poll();self.last_exit=code;phase='LIVE_PARENT_AND_OWNED_CHILD'
     if code is not None:
      if code!=0 or not self.final_pending():raise RuntimeError('UNEXPECTED_OWNED_CHILD_EXIT')
      
      if hasattr(self.proc,'group_clear') and not self.proc.group_clear():raise RuntimeError('OWNED_DESCENDANT_SURVIVES_EXIT')
      sample=self.reader.sample(0,0);phase='CONFIRMED_OWNED_EXIT_F_PENDING_OR_VERIFIED'
     else:
      try:sample=self.reader.sample(self.proc.pid,self.token)
      except Exception:
       if self.final_pending():
        try:code=self.proc.wait(timeout=.05)
        except subprocess.TimeoutExpired:raise RuntimeError('RSS_UNCERTAIN_CHILD_NOT_CONFIRMED_EXITED')
        if code!=0:raise RuntimeError('UNEXPECTED_OWNED_CHILD_EXIT')
        if hasattr(self.proc,'group_clear') and not self.proc.group_clear():raise RuntimeError('OWNED_DESCENDANT_SURVIVES_EXIT')
        sample=self.reader.sample(0,0);phase='CONFIRMED_OWNED_EXIT_F_PENDING_OR_VERIFIED';self.confirmed_exit_races+=1
       else:raise
     self.read_status='RECEIVED_UNVALIDATED'
     if type(sample['parent_rss'])is not int or sample['parent_rss']<=0 or sample.get('descendants',0):raise RuntimeError('INVALID_OR_EXTRA_PROCESS_SAMPLE')
     if phase=='LIVE_PARENT_AND_OWNED_CHILD':
      if type(sample['child_rss'])is not int or sample['child_rss']<=0 or sample['child_exit'] or not sample['child_start']:raise RuntimeError('INVALID_LIVE_CHILD_SAMPLE')
      if self.token and sample['child_start']!=self.token:raise RuntimeError('CHILD_IDENTITY_CHANGED')
      self.token=sample['child_start']
     elif sample['child_rss']!=0:raise RuntimeError('INVALID_EXIT_PHASE_SAMPLE')
     after=self.clock();span=after-before;self.max_read_span=max(self.max_read_span,span)
     if span>self.max_gap:raise RuntimeError('RSS_READ_INTERVAL_UNCERTAIN')
     self.read_status='VALID_ATTESTED';self.last_valid=(sample['parent_rss'],sample['child_rss'],sample['parent_rss']+sample['child_rss'],phase,after)
     combined=sample['parent_rss']+sample['child_rss'];self.max_parent=max(self.max_parent,sample['parent_rss']);self.max_child=max(self.max_child,sample['child_rss']);self.max_sum=max(self.max_sum,combined);self.count+=1;self.last=after
     if phase!='LIVE_PARENT_AND_OWNED_CHILD':self.final_exit_samples+=1
     if combined>self.limit:raise RuntimeError('COMBINED_RSS_LIMIT')
    except Exception as e:
     reason=str(e)
     if self.read_status=='READ_PENDING':self.read_status='UNAVAILABLE_READ_ERROR'
   if reason:self.failure=self.failure or reason;reason=self.failure;self.stop_event.set()
   self._cache()
  if reason:self.on_failure(reason);raise RuntimeError(reason)
 def _cache(self):
  self.cache={'failure':self.failure,'samples':self.count,'last':self.last_valid,'max':[self.max_parent,self.max_child,self.max_sum]if self.count else None,'limit':self.limit,'gap':self.max_sample_gap,'read_span':self.max_read_span,'token':self.token or None,'read_status':self.read_status,'last_exit':self.last_exit,'view':'CACHED_ALREADY_HELD_NOT_RESAMPLED','at_ns':time.monotonic_ns()}
 def stop(self):
  self.stop_event.set()
  if self.thread and self.thread is not threading.current_thread():
   self.thread.join(timeout=1)
   if self.thread.is_alive():raise RuntimeError('SUPERVISOR_THREAD_EXIT_UNCERTAIN')
 def report(self):
  if not self.lock.acquire(timeout=self.max_gap):return {'failure':self.failure or'RSS_REPORT_LOCK_UNCERTAIN','telemetry':'UNAVAILABLE_LOCK_HELD','watchdog_stopped':self.stop_event.is_set(),'watchdog_alive':bool(self.thread and self.thread.is_alive())}
  try:return {'scope':'EXPECTED_TWO_PROCESS_TREE_NO_UNOBSERVED_DESCENDANTS','failure':self.failure,'samples':self.count,'interval_seconds':self.interval,'maximum_allowed_gap_seconds':self.max_gap,'max_observed_gap_seconds':self.max_sample_gap,'max_read_interval_seconds':self.max_read_span,'max_sampled_parent_RSS':self.max_parent,'max_sampled_child_RSS':self.max_child,'max_sampled_sum_RSS':self.max_sum,'sum_of_individual_sample_peaks':self.max_parent+self.max_child,'limit_RSS':self.limit,'child_start_identity':self.token,'confirmed_final_exit_samples':self.final_exit_samples,'confirmed_exit_races':self.confirmed_exit_races,'watchdog_stopped':self.stop_event.is_set(),'watchdog_alive':bool(self.thread and self.thread.is_alive()),'OS_hard_continuous_limit':False}
  finally:self.lock.release()
