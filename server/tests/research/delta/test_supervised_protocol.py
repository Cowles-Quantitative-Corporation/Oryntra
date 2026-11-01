"""Sparse invented protocol frames only; no tape/target/model values."""
from pathlib import Path
import hashlib,json,subprocess,time,sys
from unittest.mock import patch
import pytest
from test_owned_runtime import reader
from backend.delta.runtime.client import SupervisedClient
from backend.delta.runtime.failure_telemetry import FailureRecorder
from backend.delta.runtime.execution_profile import PROFILE as C,LANES
from backend.delta.runtime._multiplex import root
from backend.delta.volume.source_reference import Group,Trade,Engine
@pytest.fixture(scope='module')
def binary(tmp_path_factory):
 out=tmp_path_factory.mktemp('native-ack')/'worker';src=Path(__file__).parents[3]/'backend/delta/native/source_worker.cpp'
 subprocess.run(['/usr/bin/clang++','-std=c++17','-Wall','-Wextra','-Werror','-pedantic','-O0','-g','-fsanitize=address,undefined',str(src),'-o',str(out)],check=True,timeout=30)
 return out
def setup(tmp_path,reader,binary):
 out=tmp_path/'failure';out.mkdir();rec=FailureRecorder(out,time.monotonic())
 return rec,dict(enabled=True,reader=reader,watch=False,recorder=rec,binary=binary,expected_binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),runtime_manifest='b'*64,source=C['math_namespace'],session=C['input']['session'],deadline=time.monotonic()+10)
def group(i,at):return Group(C['math_namespace'],'AAPL',C['input']['session'],38,i,i,1,at,at,at,128,True,False,False,(Trade(i,at,100_000_000_001,20,'N','T'),))
def snapshots(c,start=0):
 for at in C['snapshot_clocks'][start:]:
  for lane in range(7):c.snapshot(lane,at)
def test_native_ack_adoption_and_final_cleanup(tmp_path,reader,binary):
 rec,kw=setup(tmp_path,reader,binary)
 with SupervisedClient(**kw)as c:
  c.push(0,group(1,C['slice_open_ns']+1))
  for lane in range(7):c.snapshot(lane,C['snapshot_clocks'][0])
  c.push(0,group(2,C['slice_close_ns']-1));snapshots(c,1);d=c.finish()
  assert d['finished']and not d['failed']and d['last_acked_sequence']==18
  assert d['last_attempt']['operation']=='F'and d['retained_history_frames']==0
  assert d['runtime_manifest_sha256']=='b'*64 and d['native_binary_sha256']==kw['expected_binary_sha256']
  assert c.proc._released and not c.monitor.report()['watchdog_alive']
  assert c.telemetry_adopted[0]==2 and c.telemetry_ack[1]=='F'
 assert rec.first is None and not(rec.out/'failure.json').exists()
def test_validation_before_spawn(tmp_path,reader,binary):
 rec,kw=setup(tmp_path,reader,binary)
 with patch('backend.delta.runtime.owned_process.OwnedProcess.__init__')as spawn:
  with pytest.raises(ValueError,match='DISABLED'):SupervisedClient(**dict(kw,enabled=False))
  with pytest.raises(ValueError,match='BINARY_PIN'):SupervisedClient(**dict(kw,expected_binary_sha256='0'*64))
  spawn.assert_not_called()
@pytest.mark.parametrize('mode',['sequence','frame','prefix','lane','root','duplicate','missing','oversize'])
def test_adversarial_ack_firstcause_persistence(tmp_path,reader,mode):
 engines=[Engine(C['math_namespace'],n,C['input']['session'],i,10,2,enabled=True)for _,n,i in LANES]
 meta=dict(have=False,frontier=0,recv=0,completed=0,have_decision=False,decision=0)
 initial=root(C['math_namespace'],C['input']['session'],LANES,engines,meta,C['group_cap'],'b'*64)[0]
 script=tmp_path/'worker';code='''import sys,hashlib
line=sys.stdin.readline();h=hashlib.sha256(line.encode()).hexdigest();mode=MODE;root=ROOT
if mode=='missing':sys.exit(0)
body='- '+h+' '+h+' - '+root
if mode=='frame':body='- '+'f'*64+' '+h+' - '+root
if mode=='prefix':body='- '+h+' '+'f'*64+' - '+root
if mode=='lane':body='0 '+h+' '+h+' - '+root
if mode=='root':body='- '+h+' '+h+' - '+'0'*64
text='A '+('2'if mode=='sequence'else'1')+' '+body+'\\n'
if mode=='duplicate':text+=text
if mode=='oversize':text='A 1 '+'x'*20000+'\\n'
print(text,end='',flush=True);sys.stdin.readline()
'''.replace('MODE',repr(mode)).replace('ROOT',repr(initial))
 script.write_text('#!'+sys.executable+'\n'+code);script.chmod(0o700)
 rec,kw=setup(tmp_path,reader,script)
 with pytest.raises(Exception):SupervisedClient(**kw)
 e=json.loads((rec.out/'failure.json').read_text());assert e['status']=='FAILED_NO_COMPLETE'
 assert (rec.out/'failure.json').stat().st_size<=4096 and e['first_context']['ack']is None
 assert e['cleanup']['final']=='OWNED_GROUP_CLEARED_AND_LEADER_REAPED'
 expected={'sequence':'ACK_SEQUENCE','frame':'LANE_FRAME_PREFIX_OR_AGGREGATE_MISMATCH','prefix':'LANE_FRAME_PREFIX_OR_AGGREGATE_MISMATCH','lane':'LANE_FRAME_PREFIX_OR_AGGREGATE_MISMATCH','root':'LANE_FRAME_PREFIX_OR_AGGREGATE_MISMATCH','duplicate':'UNSOLICITED_OUTPUT','oversize':'RESPONSE_BYTES_LIMIT'}
 if mode in expected:assert expected[mode]in e['first_cause'][1]
def test_ack_is_not_adoption(tmp_path,reader,binary):
 rec,kw=setup(tmp_path,reader,binary);c=SupervisedClient(**kw);original=c._confirm
 def after_ack(*a,**k):
  v=original(*a,**k)
  if a[0]=='P':raise RuntimeError('INVENTED_AFTER_ACK_BEFORE_ADOPTION')
  return v
 with patch.object(c,'_confirm',side_effect=after_ack):
  with pytest.raises(RuntimeError,match='AFTER_ACK'):c.push(0,group(1,C['slice_open_ns']+1))
 e=json.loads((rec.out/'failure.json').read_text());assert e['first_context']['ack'][2]==1 and e['first_context']['adopted'][0]==0
 assert c.failed and not c.finished and c.proc._released and c.engines[0].groups==0
def test_final_ack_mismatch_no_complete(tmp_path,reader,binary):
 rec,kw=setup(tmp_path,reader,binary);c=SupervisedClient(**kw);snapshots(c);original=c._request
 def bad_final(op,payload=''):
  parts,text=original(op,payload)
  if op=='F':parts[2]=parts[2][:-1]+('0'if parts[2][-1]!='0'else'1')
  return parts,text
 with patch.object(c,'_request',side_effect=bad_final):
  with pytest.raises(RuntimeError):c.finish()
 assert c.failed and not c.finished and c.telemetry_ack[1]=='S'and c.proc._released
 assert json.loads((rec.out/'failure.json').read_text())['first_cause'][1]
def test_persistence_failure_primary_retained(tmp_path,reader,binary):
 rec,kw=setup(tmp_path,reader,binary);c=SupervisedClient(**kw)
 with patch.object(rec,'persist',side_effect=OSError('INVENTED_DISK_FAILURE')):
  with pytest.raises(ValueError):c.push(7,group(1,C['slice_open_ns']+1))
 assert c.failed and not c.finished and c.proc._released and rec.first[1]=='LANE_MAP'
 assert rec.persistence=='UNAVAILABLE_OR_PARTIAL_WRITE'and not(rec.out/'failure.json').exists()
def test_telemetry_latch_blocks_final_success(tmp_path,reader,binary):
 rec,kw=setup(tmp_path,reader,binary);c=SupervisedClient(**kw);snapshots(c);rec.error('invented',RuntimeError('optional failure'))
 with pytest.raises(RuntimeError,match='TELEMETRY_LATCH'):c.finish()
 assert c.failed and not c.finished and c.proc._released

def test_abort_does_not_replace_first_failure_file(tmp_path,reader,binary):
 rec,kw=setup(tmp_path,reader,binary);c=SupervisedClient(**kw)
 with pytest.raises(ValueError):c.push(7,group(1,C['slice_open_ns']+1))
 path=rec.out/'failure.json';before=path.read_bytes();first=rec.first
 c.abort(RuntimeError('SECONDARY_ABANDONMENT'))
 assert path.read_bytes()==before and rec.first==first and c.failed and not c.finished

@pytest.mark.parametrize('cancel',[KeyboardInterrupt,SystemExit])
@pytest.mark.parametrize('boundary',['after_F_ack','wait_after_F','post_check_1','stop','post_check_2','group_clear','telemetry_gate','release','final_diagnostic','final_report'])
def test_cancellation_during_finalization_cleans_and_propagates(tmp_path,reader,binary,cancel,boundary):
 import threading
 from contextlib import ExitStack
 rec,kw=setup(tmp_path,reader,binary);kw['watch']=True
 c=SupervisedClient(**kw);snapshots(c);fired=[False];checks=[0];diagnostics=[0]
 original_check=c.monitor.check;original_diag=c.diagnostic
 def once():
  if not fired[0] and threading.current_thread()is threading.main_thread():
   fired[0]=True;raise cancel('INVENTED_FINAL_CANCEL')
 def check(*a,**k):
  if c.protocol_finished and threading.current_thread()is threading.main_thread():
   checks[0]+=1
   if boundary=='post_check_'+str(checks[0]):once()
  return original_check(*a,**k)
 def diag(*a,**k):
  diagnostics[0]+=1
  if boundary=='final_diagnostic'and diagnostics[0]==2:once()
  return original_diag(*a,**k)
 def wrap(original,name):
  def invoke(*a,**k):
   if name=='wait_after_F':
    value=original(*a,**k)
    if c.last_attempt and c.last_attempt['operation']=='F':once()
    return value
   if name=='after_F_ack':
    value=original(*a,**k)
    if a[0]=='F':once()
    return value
   if name==boundary:
    if name in('stop','telemetry_gate','release')and c.protocol_finished:once()
    elif name=='group_clear'and checks[0]>=2:once()
    elif name=='final_report'and c.proc._released:once()
   return original(*a,**k)
  return invoke
 try:
  with ExitStack()as stack:
   stack.enter_context(patch.object(c.monitor,'check',side_effect=check));stack.enter_context(patch.object(c,'diagnostic',side_effect=diag))
   for obj,name,label in [(c,'_confirm','after_F_ack'),(c.proc,'wait','wait_after_F'),(c.monitor,'stop','stop'),(c.proc,'group_clear','group_clear'),(rec,'success_guard','telemetry_gate'),(c.proc,'release','release'),(c.monitor,'report','final_report')]:
    if boundary==label:stack.enter_context(patch.object(obj,name,side_effect=wrap(getattr(obj,name),label)))
   with pytest.raises(cancel,match='INVENTED_FINAL_CANCEL'):
    with c:c.finish()
  assert fired[0] and c.failed and not c.finished and not c._completion_verified
  assert c.proc._released and not c.monitor.report()['watchdog_alive']
  e=json.loads((rec.out/'failure.json').read_text());assert e['status']=='FAILED_NO_COMPLETE'
  assert e['first_cause'][0]==cancel.__name__ and 'INVENTED_FINAL_CANCEL'in e['first_cause'][1]
 finally:
  if not c.proc._released:c.abort(RuntimeError('TEST_TEARDOWN'))
  c.monitor.stop()

@pytest.mark.parametrize('cancel',[KeyboardInterrupt,SystemExit])
@pytest.mark.parametrize('phase',['after_valid_I','after_spawn','after_registration','after_watchdog_start'])
def test_constructor_cancellation_acquisition_cleanup(tmp_path,reader,binary,cancel,phase):
 import threading
 from contextlib import ExitStack
 from backend.delta.runtime._multiplex import Multiplex
 from backend.delta.runtime.owned_process import OwnedProcess
 rec,kw=setup(tmp_path,reader,binary);kw['watch']=True
 clients=[];children=[];fired=[False]
 original_register=Multiplex._register_owned_process;original_confirm=Multiplex._confirm
 original_popen=subprocess.Popen.__init__;original_start=threading.Thread.start
 def trigger():
  if not fired[0]:fired[0]=True;raise cancel('INVENTED_INIT_CANCEL')
 def register(self,proc):
  original_register(self,proc);clients.append(self);children.append(proc)
  if phase=='after_registration':trigger()
 def confirm(self,*a,**k):
  value=original_confirm(self,*a,**k)
  if phase=='after_valid_I'and a[0]=='I':trigger()
  return value
 def popen(self,*a,**k):
  original_popen(self,*a,**k)
  if phase=='after_spawn':children.append(self);trigger()
 def start(self,*a,**k):
  value=original_start(self,*a,**k)
  if phase=='after_watchdog_start'and self.name=='owned-rss-supervisor':trigger()
  return value
 try:
  with ExitStack()as stack:
   stack.enter_context(patch.object(Multiplex,'_register_owned_process',register))
   stack.enter_context(patch.object(Multiplex,'_confirm',confirm))
   if phase=='after_spawn':stack.enter_context(patch.object(subprocess.Popen,'__init__',popen))
   if phase=='after_watchdog_start':stack.enter_context(patch.object(threading.Thread,'start',start))
   with pytest.raises(cancel,match='INVENTED_INIT_CANCEL')as caught:SupervisedClient(**kw)
  assert caught.value.args==('INVENTED_INIT_CANCEL',)and fired[0]and len(children)==1
  assert children[0]._released
  for c in clients:
   assert c.failed and not c.finished and not c._completion_verified
   if c.monitor:assert not c.monitor.report()['watchdog_alive']
  evidence=json.loads((rec.out/'failure.json').read_text());assert evidence['status']=='FAILED_NO_COMPLETE'
  assert evidence['first_cause'][0]==cancel.__name__ and 'INVENTED_INIT_CANCEL'in evidence['first_cause'][1]
  assert(rec.out/'failure.json').stat().st_size<=4096
 finally:
  for c in clients:
   if c.monitor:c.monitor.stop()
  for child in children:
   if not child._released:child.cleanup_owned_group()
