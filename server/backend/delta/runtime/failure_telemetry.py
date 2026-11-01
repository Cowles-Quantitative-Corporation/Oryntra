"""Bounded in-memory first cause; one <=4KiB failure record, no callback I/O."""
import json,os,time,threading,sys
from pathlib import Path
class FailureRecorder:
 CAP=4096
 def __init__(self,out,start):
  self.out=Path(out);self.start=start;self.first=None;self.first_context=None;self.errors=[];self.lock=threading.Lock();self.persistence='NOT_ATTEMPTED';self.final=None;self.cleanup_seen=None
 def error(self,where,exc):
  if len(self.errors)<2:self.errors.append([str(where)[:24],type(exc).__name__[:32],str(exc)[:80]])
 def capture(self,source,reason,context=None,stamp=None):
  # No disk, resource sampling, callbacks or blocking lock in this path.
  if self.first is not None:return self.first
  if not self.lock.acquire(blocking=False):
   self.error('capture_lock',RuntimeError('UNAVAILABLE'));return None
  try:
   if self.first is None:
    self.first=(str(source)[:32],str(reason)[:160],stamp[0]if stamp else time.monotonic_ns(),stamp[1]if stamp else time.time_ns(),stamp[2]if stamp else('WATCHDOG'if threading.current_thread()is not threading.main_thread()else'CALLER'),len(str(reason)))
    self.first_context=context
   return self.first
  finally:self.lock.release()
 def success_guard(self):
  if self.first is not None or self.errors:raise RuntimeError('FAILURE_TELEMETRY_LATCH_NO_COMPLETE')
 def compact(self,value):
  try:data=(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
  except Exception as exc:
   self.error('optional_serialize',exc)
   value={'schema':'delta.failure.v18','status':value['status'],'first_cause':value['first_cause'],'wrapper':value['wrapper'],'cleanup':'UNAVAILABLE_OPTIONAL_SERIALIZATION','telemetry':'UNAVAILABLE_OPTIONAL_SERIALIZATION','errors':self.errors}
   data=(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
  if len(data)<=self.CAP:return value,data
  # Optional duplicate snapshot cannot displace mandatory primary identity.
  value=dict(value);value['before_cleanup']='UNAVAILABLE_COMPACTED_TO_4K';value['first_context']='UNAVAILABLE_COMPACTED_TO_4K'
  data=(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
  if len(data)>self.CAP:
   value={'schema':'delta.failure.v18','status':value['status'],'first_cause':value['first_cause'],'wrapper':value['wrapper'],'cleanup':value['cleanup'],'telemetry':'UNAVAILABLE_OPTIONAL_OVER_BUDGET','errors':self.errors}
   data=(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
  if len(data)>self.CAP:raise ValueError('MANDATORY_FAILURE_CAP')
  return value,data
 def persist(self,value):
  value,data=self.compact(value);self.final=value
  # Reserve is unchanged: one4096bytefile, no duplicated temp/marker files.
  used=sum(p.stat().st_size for p in self.out.iterdir()if p.is_file())
  if used+len(data)>2097152:raise ValueError('AGGREGATE_FAILURE_BUDGET')
  fd=os.open(self.out/'failure.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  try:
   at=0
   for _ in range(8):
    if at==len(data):break
    n=os.write(fd,data[at:])
    if n<=0:raise OSError('FAILURE_ZERO_WRITE')
    at+=n
   if at!=len(data):raise OSError('FAILURE_PARTIAL_WRITE_BOUND')
  finally:os.close(fd)
  self.persistence='WRITTEN_NO_FSYNC_OR_ATOMIC_FAILURE_GUARANTEE';return value
 def finish(self,wrapper,before,cleanup,primary=None):
  if self.first is None:self.capture((primary or{}).get('type','WRAPPER'),(primary or{}).get('reason',str(wrapper)),before)
  fallback=self.first
  if fallback is None:fallback=(str((primary or{}).get('type','WRAPPER'))[:32],str((primary or{}).get('reason',str(wrapper)))[:160],(primary or{}).get('mono_ns'),(primary or{}).get('utc_ns'),'STICKY_CLIENT_CAPTURE_UNAVAILABLE',len(str((primary or{}).get('reason',str(wrapper)))))
  value={'schema':'delta.failure.v18','status':'PUBLICATION_UNCERTAIN_NO_AUTHORITATIVE_FAILURE'if type(wrapper).__name__=='PublicationUncertain'else'FAILED_NO_COMPLETE','first_cause':fallback,'first_context':self.first_context,'before_cleanup':before,'cleanup':cleanup,'wrapper':[type(wrapper).__name__[:32],str(wrapper)[:160]],'errors':list(self.errors),'time':[time.monotonic_ns(),time.time_ns(),max(0.,time.monotonic()-self.start)],'start_monotonic_seconds':self.start,'views':'NON_ATOMIC_TIMESTAMPED; ACK!=ADOPTED!=DECODED; LAST_SAMPLE_NOT_PEAK','no_retry':True}
  try:return self.persist(value)
  except Exception as exc:
   self.error('persist',exc);self.persistence='UNAVAILABLE_OR_PARTIAL_WRITE';self.final=value
   # Original primary remains visible even if durable evidence cannot be made.
   try:sys.stderr.write('DELTA_PRIMARY_FAILURE '+json.dumps({'first_cause':value['first_cause'],'persistence':self.persistence,'errors':self.errors},sort_keys=True)[:1024]+'\n')
   except Exception:pass
   return value
