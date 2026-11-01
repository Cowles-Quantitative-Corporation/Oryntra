"""Same protocol + cooperative combined RSS; no source/guard-capacity expansion."""
import os,signal,sys,threading,time
from ._multiplex import Multiplex
from .supervision import Monitor
from .failure_telemetry import FailureRecorder
class SupervisedClient(Multiplex):
 def __init__(self,*,reader=None,watch=True,recorder=None,**kw):
  if kw.get('enabled')is not True:raise ValueError('DISABLED')
  if reader is None or not callable(getattr(reader,'sample',None))or not callable(getattr(reader,'group_state',None))or type(recorder)is not FailureRecorder:raise ValueError('EXPLICIT_READER_AND_FAILURE_RECORDER_REQUIRED')
  self._failure_persisted=False;self._completion_verified=False
  self.recorder=recorder;self.cleanup_first_uncertain=None;self.cleanup_attempts=0;self.capture_error=None
  self.monitor=None;self.reader=reader;self._watch=watch;self._cleanup_lock=threading.Lock();self.group_cleanup=None
  try:super().__init__(**kw)
  except BaseException as exc:self._abort_preserving(exc);raise
 def _memory_abort(self,reason):
  if self.failure is None:self.failure={'type':'MEMORY_SUPERVISION','reason':reason,'mono_ns':time.monotonic_ns(),'utc_ns':time.time_ns(),'role':'WATCHDOG'if threading.current_thread()is not threading.main_thread()else'CALLER'}
  self._capture_first(self.failure['type'],self.failure['reason'])
  self._poison()
 def _guard(self):
  super()._guard()
  if self.monitor is None:self.monitor=Monitor(self.proc,reader=self.reader,on_failure=self._memory_abort,final_pending=lambda:bool(self.last_attempt and self.last_attempt['operation']=='F'),watch=self._watch,on_created=lambda m:setattr(self,'monitor',m))
  self.monitor.check()
 def check_resource(self):
  self._live();self.monitor.check(force=True)
 def failure_snapshot(self):
  ack=getattr(self,'telemetry_ack',None);attempt=getattr(self,'last_attempt',None)
  return {'runtime_manifest_sha256':getattr(self,'runtime_manifest',None),'native_binary_sha256':getattr(self,'native_binary_sha256',None),'ack':ack,'attempt':None if attempt is None else(attempt.get('sequence'),attempt.get('operation'),attempt.get('frame_sha256'),attempt.get('issued_monotonic_ns')),'adopted':getattr(self,'telemetry_adopted',None),'progress':getattr(self,'telemetry_progress',None),'monitor':None if self.monitor is None else self.monitor.cache,'flags':[getattr(self,'failed',None),getattr(self,'finished',None)],'capture_error':self.capture_error,'ownership':getattr(self,'ownership',None).cached()if getattr(self,'ownership',None)is not None else('UNAVAILABLE_NOT_ATTACHED',),'at_ns':time.monotonic_ns()}
 def _capture_first(self,kind,reason):
  if self.recorder is not None:
   try:self.recorder.capture(kind,reason,self.failure_snapshot(),(self.failure.get('mono_ns'),self.failure.get('utc_ns'),self.failure.get('role')))
   except Exception as exc:self.capture_error=str(exc)[:80]
 def cleanup_snapshot(self):
  return {'attempts':self.cleanup_attempts,'first_uncertain':self.cleanup_first_uncertain,'final':None if self.group_cleanup is None else self.group_cleanup[:160],'native_exit':None if not hasattr(self,'proc')else self.proc.returncode,'watchdog_alive':bool(self.monitor and self.monitor.thread and self.monitor.thread.is_alive()),'watchdog_stopped':bool(self.monitor and self.monitor.stop_event.is_set()),'at_ns':time.monotonic_ns()}
 def _poison(self):
  if getattr(self,'failure',None)is None:
   exc=sys.exception();self.failure={'type':'FOREGROUND','reason':str(exc)if exc else'ABANDONED','mono_ns':time.monotonic_ns(),'utc_ns':time.time_ns(),'role':'CALLER'}
  self._capture_first(self.failure['type'],self.failure['reason'])
  self.cleanup_attempts+=1
  if hasattr(self,'monitor')and self.monitor:self.monitor.stop_event.set()
  self.failed=True;self.finished=False;self._completion_verified=False
  if not self._cleanup_lock.acquire(timeout=2):
   self.group_cleanup='UNCERTAIN_CLEANUP_LOCK_TIMEOUT';self.cleanup_first_uncertain=self.cleanup_first_uncertain or self.group_cleanup;self._note_cleanup();return
  try:
   proc=getattr(self,'proc',None)
   if proc is not None:
    try:proc.cleanup_owned_group();self.group_cleanup='OWNED_GROUP_CLEARED_AND_LEADER_REAPED'
    except Exception as exc:
     self.group_cleanup='UNCERTAIN_CLEANUP:'+str(exc)
     self.cleanup_first_uncertain=self.cleanup_first_uncertain or self.group_cleanup[:160]
    for stream in (proc.stdin,proc.stdout,proc.stderr):
     try:stream.close()
     except Exception:pass
  finally:self._cleanup_lock.release();self._note_cleanup()
 def _note_cleanup(self):
  if self.recorder is not None:
   try:self.recorder.cleanup_seen=self.cleanup_snapshot()
   except Exception:pass
 def finish(self):
  try:
   super().finish();self.monitor.check(force=True);self.monitor.stop();self.monitor.check(force=True)
   if self.failed or not self.protocol_finished or self.monitor.failure:raise RuntimeError('FINAL_SUPERVISION_FAILED_NO_RELEASE')
   if not self.proc.group_clear():raise RuntimeError('FINAL_OWNED_GROUP_NOT_CLEAR')
   self.recorder.success_guard();self.proc.release()
   result=self.diagnostic();result['live_memory_supervision']=self.monitor.report();result['owned_group_cleanup']=self.group_cleanup
   self.finished=True;self._completion_verified=True
   result['finished']=True;result['native_outcome']='ACKNOWLEDGED';return result
  except BaseException as exc:self._abort_preserving(exc);raise
 def _abort_preserving(self,exc):
  try:self.abort(exc)
  except BaseException as secondary:
   self.failed=True;self.finished=False;self._completion_verified=False
   self.recorder.error('abort_interrupted',secondary)
 def diagnostic(self):
  d=super().diagnostic();d['live_memory_supervision']=None if self.monitor is None else self.monitor.report();d['owned_group_cleanup']=self.group_cleanup;return d

 def abort(self,exc):
  before=self.failure_snapshot()
  if getattr(self,'failure',None)is None:self.failure={'type':type(exc).__name__,'reason':str(exc)or type(exc).__name__,'mono_ns':time.monotonic_ns(),'utc_ns':time.time_ns(),'role':'CALLER'}
  self._poison()
  if self.monitor:
   try:self.monitor.stop()
   except Exception as secondary:self.recorder.error('monitor_stop',secondary)
  if not self._failure_persisted:
   self._failure_persisted=True
   try:self.recorder.finish(exc,before,self.cleanup_snapshot(),self.failure)
   except Exception as secondary:self.recorder.error('finish',secondary)
 def push(self,*a,**kw):
  try:return super().push(*a,**kw)
  except Exception as exc:self.abort(exc);raise
 def snapshot(self,*a,**kw):
  try:return super().snapshot(*a,**kw)
  except Exception as exc:self.abort(exc);raise
 def __enter__(self):return self
 def __exit__(self,kind,value,tb):
  if not(self.finished and self._completion_verified and self.proc._released):
   try:self.abort(value if value is not None else RuntimeError('EXPLICIT_UNVERIFIED_ABANDONMENT'))
   except BaseException as secondary:
    if value is None:raise
    self.recorder.error('context_cleanup',secondary)
  return False
