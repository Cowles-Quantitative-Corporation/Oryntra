"""Accepted bounded request framing; internal to supervised client only."""
import subprocess,os,select,time,resource,hashlib
MAX_RESPONSE=16384;MAX_SNAPSHOTS=16
class Incremental:
 def _guard(self):
  if time.monotonic()-self.started>self.wall_limit:raise RuntimeError('CHILD_LIFETIME_WALL_LIMIT')
  if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss>512*1024*1024:raise RuntimeError('SELF_RSS_LIMIT')
 def _live(self):
  if self.failed or self.finished:raise RuntimeError('LATCHED_OR_FINISHED')
 def _poison(self):raise NotImplementedError('INTERNAL_TRANSPORT_REQUIRES_SUPERVISED_CLIENT')
 def _request(self,op,payload=''):
  self._live()
  try:
   self._guard()
   if self.seq>=self.max_operations:raise RuntimeError('OPERATION_LIMIT')
   request_started=time.monotonic()
   text=f'{op} {self.seq+1}'+(' '+payload if payload else'')
   if not text.isascii()or '\n'in text or '\r'in text or len(text.encode())>16384:raise ValueError('FRAME_DOMAIN')
   data=(text+'\n').encode()
   if self.input_bytes+len(data)>self.max_input:raise RuntimeError('INPUT_BYTES_LIMIT')
   self.last_attempt={'sequence':self.seq+1,'frame_sha256':hashlib.sha256(data).hexdigest(),'operation':op,'issued_monotonic_ns':time.monotonic_ns()};self.attempts.update(data);at=0
   while at<len(data):
    if time.monotonic()-request_started>20:raise RuntimeError('REQUEST_DEADLINE')
    self._guard();_,ready,_=select.select([],[self.proc.stdin],[],max(.001,min(20-(time.monotonic()-request_started),self.wall_limit-(time.monotonic()-self.started))))
    if not ready:raise RuntimeError('WRITE_TIMEOUT')
    try:at+=os.write(self.proc.stdin.fileno(),data[at:])
    except BlockingIOError:continue
   self.input_bytes+=len(data);buffer=b''
   while b'\n'not in buffer:
    if time.monotonic()-request_started>20:raise RuntimeError('REQUEST_DEADLINE')
    self._guard();ready,_,_=select.select([self.proc.stdout,self.proc.stderr],[],[],max(.001,min(20-(time.monotonic()-request_started),self.wall_limit-(time.monotonic()-self.started))))
    if not ready:raise RuntimeError('RESPONSE_TIMEOUT')
    for f in ready:
     chunk=os.read(f.fileno(),65536)
     if f is self.proc.stderr:
      if chunk:raise RuntimeError('NATIVE_DIAGNOSTIC:'+chunk[:300].decode(errors='replace'))
      continue
     if not chunk:raise RuntimeError('EOF_WITHOUT_ACK')
     buffer+=chunk;self.output_bytes+=len(chunk)
     if len(buffer)>MAX_RESPONSE or self.output_bytes>self.max_output:raise RuntimeError('RESPONSE_BYTES_LIMIT')
   line,extra=buffer.split(b'\n',1)
   if extra:raise RuntimeError('UNSOLICITED_OUTPUT')
   parts=line.decode('ascii').split(' ',2)
   if len(parts)!=3 or parts[1]!=str(self.seq+1):raise RuntimeError('ACK_SEQUENCE')
   if parts[0]=='E':raise RuntimeError('NATIVE_ERROR:'+parts[2])
   return parts,text
  except Exception:self._poison();raise
