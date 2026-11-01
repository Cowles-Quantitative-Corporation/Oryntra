"""Keep owned session leader unreaped until group custody is settled."""
import subprocess,os,time,signal,errno,threading
class OwnedProcess(subprocess.Popen):
 def __init__(self,*a,enabled=False,group_state=None,on_created=None,**kw):
  if enabled is not True:raise ValueError("DISABLED_OWNED_PROCESS")
  if not callable(group_state):raise ValueError("OWNED_GROUP_INSPECTOR_REQUIRED")
  if not all(hasattr(os,n)for n in("waitid","WNOWAIT","P_PID")):raise ValueError("OWNED_CUSTODY_PLATFORM_UNSUPPORTED")
  if on_created is not None and not callable(on_created):raise ValueError("OWNED_REGISTRATION_CALLBACK")
  self._group_state=group_state
  if kw.get('start_new_session') is not True:raise ValueError('OWNED_NEW_SESSION_REQUIRED')
  self._custody_lock=threading.RLock();self._released=False
  try:
   super().__init__(*a,**kw)
   if on_created is not None:on_created(self)
  except BaseException as primary:
   if getattr(self,'_child_created',False):
    try:self.cleanup_owned_group()
    except BaseException as secondary:primary.add_note('OWNED_CONSTRUCTOR_CLEANUP_UNCERTAIN:'+type(secondary).__name__)
   for name in('stdin','stdout','stderr'):
    stream=getattr(self,name,None)
    if stream is not None:
     try:stream.close()
     except BaseException as secondary:primary.add_note('OWNED_CONSTRUCTOR_STREAM_CLOSE_UNCERTAIN:'+type(secondary).__name__)
   raise
 def poll(self):
  with self._custody_lock:
   if self._released:return self.returncode
   try:r=os.waitid(os.P_PID,self.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
   except ChildProcessError:raise RuntimeError('OWNED_LEADER_REAPED_EXTERNALLY_UNCERTAIN')
   if r is not None:self.returncode=r.si_status if r.si_code==os.CLD_EXITED else -r.si_status
   return self.returncode
 def wait(self,timeout=None):
  start=time.monotonic()
  while self.poll() is None:
   if timeout is not None and time.monotonic()-start>=timeout:raise subprocess.TimeoutExpired(self.args,timeout)
   time.sleep(.002)
  return self.returncode
 def group_clear(self):
  # Leader remains our unreaped child: its PID/PGID cannot be reused.
  with self._custody_lock:
   if self._released:raise RuntimeError('GROUP_CUSTODY_ALREADY_RELEASED')
   self.poll() # Reconfirm this is still our unreaped child before scoped group lookup.
   code=self._group_state(self.pid)
   if type(code)is not int or code not in(-2,-1,0,1):raise RuntimeError('OWNED_GROUP_LIVENESS_UNCERTAIN:INVALID_CODE')
   if code<0:raise RuntimeError('OWNED_GROUP_LIVENESS_UNCERTAIN:'+str(code))
   return code==0
 def release(self):
  with self._custody_lock:
   if self._released:return
   if self.poll() is None or not self.group_clear():raise RuntimeError('OWNED_GROUP_NOT_CLEAR_NO_RELEASE')
   pid,status=os.waitpid(self.pid,0)
   if pid!=self.pid:raise RuntimeError('OWNED_REAP_IDENTITY_UNCERTAIN')
   self._released=True
 def cleanup_owned_group(self):
  with self._custody_lock:
   if self._released:return
   # waitid establishes custody even if direct worker has exited; never signal
   # an externally reaped/reused PID. Unreaped leader reserves group identity.
   self.poll()
   if self.returncode is not None and self.group_clear():
    self.release();return
   try:os.killpg(self.pid,signal.SIGKILL)
   except ProcessLookupError:pass
   except PermissionError:
    try:self.wait(timeout=.05)
    except subprocess.TimeoutExpired:raise RuntimeError('OWNED_GROUP_CLEANUP_DENIED')
    if not self.group_clear():raise RuntimeError('OWNED_GROUP_CLEANUP_DENIED')
    self.release();return
   self.wait(timeout=2)
   deadline=time.monotonic()+2
   while not self.group_clear():
    if time.monotonic()>=deadline:raise RuntimeError('OWNED_DESCENDANT_CLEANUP_UNCERTAIN')
    time.sleep(.005)
   self.release()
