"""Invented workers only; no raw files or global process inventory."""
from pathlib import Path
import hashlib,os,subprocess,sys,time
from unittest.mock import patch
import pytest
from backend.delta.runtime.owned_process import OwnedProcess
from backend.delta.runtime.supervision import Monitor,OwnedReader

@pytest.fixture(scope='module')
def reader(tmp_path_factory):
 if sys.platform!='darwin':pytest.skip('accepted helper is macOS-specific')
 out=tmp_path_factory.mktemp('owned-runtime')/'owned_rss.dylib'
 src=Path(__file__).parents[3]/'backend/delta/runtime/owned_rss.c'
 subprocess.run(['/usr/bin/clang','-dynamiclib','-Wall','-Wextra','-Werror',str(src),'-o',str(out)],check=True,timeout=20)
 return OwnedReader(out,expected_sha256=hashlib.sha256(out.read_bytes()).hexdigest())
def worker(r,code):return OwnedProcess([sys.executable,'-u','-c',code],enabled=True,group_state=r.group_state,start_new_session=True,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
def pipes(p):
 for s in(p.stdin,p.stdout,p.stderr):
  if s:s.close()
class Proc:
 pid=12345;code=None;clear=True
 def poll(self):return self.code
 def group_clear(self):return self.clear
 def wait(self,timeout=None):
  if self.code is None:raise subprocess.TimeoutExpired('invented',timeout)
  return self.code
class Reader:
 def __init__(self):self.value=dict(parent_rss=10,child_rss=20,child_start=42,child_exit=0,descendants=0)
 def sample(self,pid,expected):return dict(self.value)if pid else dict(self.value,child_rss=0,child_start=0)
def monitor(p=None,r=None,**kw):return Monitor(p or Proc(),reader=r or Reader(),on_failure=kw.pop('on_failure',lambda _:None),final_pending=kw.pop('final_pending',lambda:False),watch=False,**kw)
def test_disabled_before_spawn():
 with patch('subprocess.Popen.__init__')as spawn:
  with pytest.raises(ValueError,match='DISABLED'):OwnedProcess(['unused'])
  with pytest.raises(ValueError,match='INSPECTOR'):OwnedProcess(['unused'],enabled=True,start_new_session=True)
  spawn.assert_not_called()
def test_normal_completion(reader):
 p=worker(reader,"import sys;print('READY');sys.stdin.readline()");m=None
 try:
  assert p.stdout.readline()==b'READY\n';pending=[False]
  m=monitor(p,reader,final_pending=lambda:pending[0],on_failure=lambda _:p.cleanup_owned_group())
  pending[0]=True;p.stdin.write(b'F\n');p.stdin.flush();assert p.wait(timeout=2)==0
  m.check(force=True);m.stop();assert p.group_clear();p.release();assert p._released
  assert m.report()['confirmed_final_exit_samples']>=1
 finally:
  if m:m.stop()
  p.cleanup_owned_group();pipes(p)
def test_failure_cleanup_firstcause(reader):
 p=worker(reader,"import sys;print('READY');sys.stdin.readline();sys.exit(7)");m=None;failures=[]
 try:
  assert p.stdout.readline()==b'READY\n'
  def fail(reason):failures.append(reason);p.cleanup_owned_group()
  m=monitor(p,reader,on_failure=fail);p.stdin.write(b'exit\n');p.stdin.flush();assert p.wait(timeout=2)==7
  with pytest.raises(RuntimeError,match='UNEXPECTED_OWNED_CHILD_EXIT'):m.check(force=True)
  assert p._released and failures==['UNEXPECTED_OWNED_CHILD_EXIT']
  with pytest.raises(RuntimeError,match='UNEXPECTED_OWNED_CHILD_EXIT'):m.check(force=True)
  assert len(failures)==1
 finally:
  if m:m.stop()
  p.cleanup_owned_group();pipes(p)
def test_descendant_on_success_exit_rejects_and_cleans(reader):
 p=worker(reader,"import os,time\nchild=os.fork()\nif child==0: time.sleep(30);os._exit(0)\nprint(child,flush=True);os._exit(0)");failures=[]
 try:
  assert int(p.stdout.readline())>0;assert p.wait(timeout=2)==0 and not p.group_clear()
  def fail(reason):failures.append(reason);p.cleanup_owned_group()
  with pytest.raises(RuntimeError,match='OWNED_DESCENDANT_SURVIVES_EXIT'):monitor(p,reader,on_failure=fail,final_pending=lambda:True)
  assert failures==['OWNED_DESCENDANT_SURVIVES_EXIT']and p._released
  with pytest.raises(ProcessLookupError):os.killpg(p.pid,0)
 finally:p.cleanup_owned_group();pipes(p)
def test_external_reap_never_signals(reader):
 p=worker(reader,'pass')
 try:
  p.wait(timeout=2);os.waitpid(p.pid,0)
  with patch('os.killpg')as signal:
   with pytest.raises(RuntimeError,match='REAPED_EXTERNALLY_UNCERTAIN'):p.cleanup_owned_group()
   signal.assert_not_called()
 finally:p._released=True;pipes(p)
@pytest.mark.parametrize('mode,reason',[('uncertain','INVENTED_UNCERTAIN'),('identity','CHILD_IDENTITY_CHANGED'),('zero','INVALID_LIVE_CHILD_SAMPLE'),('descendant','INVALID_OR_EXTRA_PROCESS_SAMPLE')])
def test_invalid_sample_is_not_zero(mode,reason):
 r=Reader();failures=[];m=monitor(r=r,on_failure=failures.append)
 if mode=='uncertain':r.sample=lambda *_:(_ for _ in()).throw(RuntimeError('INVENTED_UNCERTAIN'))
 if mode=='identity':r.value['child_start']=43
 if mode=='zero':r.value['child_rss']=0
 if mode=='descendant':r.value['descendants']=1
 with pytest.raises(RuntimeError,match=reason):m.check(force=True)
 assert failures==[reason];m.stop()
def test_exact_limit_and_sticky_failure():
 r=Reader();m=monitor(r=r,limit=30);r.value['child_rss']=21
 with pytest.raises(RuntimeError,match='COMBINED_RSS_LIMIT'):m.check(force=True)
 r.value['child_rss']=1
 with pytest.raises(RuntimeError,match='COMBINED_RSS_LIMIT'):m.check(force=True)
 assert m.report()['max_sampled_sum_RSS']==31;m.stop()
def test_gap_and_span():
 clock=[0.];m=monitor(clock=lambda:clock[0]);clock[0]=.251
 with pytest.raises(RuntimeError,match='MEASUREMENT_GAP'):m.check(force=True)
 m.stop();clock[0]=0
 class Slow(Reader):
  def sample(self,*_):clock[0]+=.251;return super().sample(1,0)
 with pytest.raises(RuntimeError,match='READ_INTERVAL'):monitor(r=Slow(),clock=lambda:clock[0])
def test_cleanup_error_preserves_cause():
 r=Reader()
 def fail(_):raise RuntimeError('INVENTED_CLEANUP_FAILURE')
 m=monitor(r=r,on_failure=fail,limit=30);r.value['child_rss']=21
 with pytest.raises(RuntimeError,match='CLEANUP_FAILURE'):m.check(force=True)
 assert m.failure=='COMBINED_RSS_LIMIT';m.stop()
@pytest.mark.parametrize('kw',[dict(limit=512*1024**2+1),dict(limit=True),dict(max_gap=.251),dict(interval=.026),dict(interval=float('nan'))])
def test_no_guard_increase(kw):
 with pytest.raises(ValueError,match='MONITOR_LIMITS'):monitor(**kw)
def test_watchdog_failure_stop():
 r=Reader();failures=[];m=Monitor(Proc(),reader=r,on_failure=failures.append,final_pending=lambda:False,watch=True)
 try:
  r.value['child_rss']=512*1024**2;deadline=time.monotonic()+1
  while not failures and time.monotonic()<deadline:time.sleep(.005)
  assert failures==['COMBINED_RSS_LIMIT']
 finally:m.stop()
 assert not m.report()['watchdog_alive']
def test_zero_exit_requires_final_pending():
 p=Proc();p.code=0
 with pytest.raises(RuntimeError,match='UNEXPECTED_OWNED_CHILD_EXIT'):monitor(p)
def test_uncertain_group_blocks_release_without_signal(reader):
 p=worker(reader,'pass');original=p._group_state
 try:
  p.wait(timeout=2);p._group_state=lambda _: -2
  with patch('os.killpg')as signal:
   with pytest.raises(RuntimeError,match='LIVENESS_UNCERTAIN'):p.release()
   signal.assert_not_called();assert not p._released
 finally:p._group_state=original;p.cleanup_owned_group();pipes(p)
def test_helper_pin_before_loading(reader):
 with pytest.raises(ValueError,match='LIBRARY_PIN'):OwnedReader(Path(reader.lib._name),expected_sha256='0'*64)
def test_new_session_required_before_spawn():
 with patch('subprocess.Popen.__init__')as spawn:
  with pytest.raises(ValueError,match='NEW_SESSION_REQUIRED'):OwnedProcess(['unused'],enabled=True,group_state=lambda _:0)
  spawn.assert_not_called()
