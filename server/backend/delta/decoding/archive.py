"""Disabled tiny single-frame archive integration; never a real-export runner."""
from pathlib import Path
from dataclasses import asdict
import csv,hashlib,io,os,stat,time
import pyarrow as pa
import zstandard as zstd
from .schema import EXPECTED_COLUMNS
from .bridge import DecodedVolumeBridge
from ..volume.source_reader import authenticate_source,_checked_resource_sample,SourceReadError
MAX_COMPRESSED_BYTES=1024*1024
MAX_DECODED_BYTES=2*1024*1024
MAX_ROWS=4096

def decode_small_archive(path,client,*,expected_sha256,expected_bytes,expected_rows,deadline_ns,probe,enabled=False):
 if enabled is not True:raise ValueError('DISABLED_ARCHIVE_INTEGRATION')
 if type(expected_bytes)is not int or not 0<expected_bytes<=MAX_COMPRESSED_BYTES or type(expected_rows)is not int or not 0<expected_rows<=MAX_ROWS:raise ValueError('SMALL_ARCHIVE_BOUNDS')
 bridge=None;stream=None;checks=0
 def check():
  nonlocal checks
  if not client.finished:client.check_resource()
  elif client.failed or not client._completion_verified:raise RuntimeError('UNVERIFIED_CLIENT_COMPLETION')
  _checked_resource_sample(probe,deadline_ns);checks+=1
 def identity(s):return s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns
 try:
  # Reuse the accepted source contract, then independently authenticate the exact
  # immutable bytes actually consumed. Prior closed-path receipt is not a handoff.
  prior=authenticate_source(path,expected_sha256=expected_sha256,expected_bytes=expected_bytes,deadline_ns=deadline_ns,probe=probe,enabled=True,block_bytes=65536)
  check()
  def opener(name,flags):return os.open(name,flags|os.O_NOFOLLOW|os.O_NONBLOCK)
  stream=open(path,'rb',buffering=0,opener=opener);failure=None
  try:
   before=os.fstat(stream.fileno())
   if not stat.S_ISREG(before.st_mode)or(before.st_dev,before.st_ino,before.st_size)!=(prior.device,prior.inode,expected_bytes):raise SourceReadError('ARCHIVE_DESCRIPTOR_IDENTITY')
   chunks=[];count=0
   while True:
    check();chunk=stream.read(min(65536,expected_bytes-count+1));check()
    if not chunk:break
    count+=len(chunk)
    if count>expected_bytes:raise SourceReadError('ARCHIVE_GREW')
    chunks.append(chunk)
   compressed=b''.join(chunks);del chunks
   if count!=expected_bytes or hashlib.sha256(compressed).hexdigest()!=expected_sha256:raise SourceReadError('CONSUMED_ARCHIVE_HASH_OR_SIZE')
   if identity(os.fstat(stream.fileno()))!=identity(before):raise SourceReadError('ARCHIVE_CHANGED_DURING_READ')
   params=zstd.get_frame_parameters(compressed)
   if not 0<params.content_size<=MAX_DECODED_BYTES or params.window_size>8*1024**2:raise ValueError('DECLARED_FRAME_BOUND_OR_UNKNOWN_SIZE')
   check()
   decoded=zstd.ZstdDecompressor().decompress(compressed,max_output_size=MAX_DECODED_BYTES,allow_extra_data=False)
   check()
   if len(decoded)!=params.content_size or b'"'in decoded:raise ValueError('EXACT_UNQUOTED_FRAME_COMPLETION')
   # Known-content-size, exactly one frame, no trailing/concatenated frames.
   text=decoded.decode('utf-8',errors='strict');records=csv.reader(io.StringIO(text,newline=''),strict=True)
   if next(records,None)!=EXPECTED_COLUMNS:raise ValueError('EXACT_ARCHIVE_HEADER')
   bridge=DecodedVolumeBridge(client,enabled=True);pending=[];rows=0
   def emit(values):
    check()
    batch=pa.RecordBatch.from_arrays([pa.array([r[i]for r in values],type=pa.string())for i in range(74)],names=EXPECTED_COLUMNS)
    bridge.consume(batch);check()
   for record in records:
    if len(record)!=74:raise ValueError('EXACT_ARCHIVE_ROW_WIDTH')
    rows+=1
    if rows>expected_rows:raise ValueError('EXTRA_ARCHIVE_ROWS')
    pending.append(record)
    if len(pending)==512:emit(pending);pending=[]
   if rows!=expected_rows:raise ValueError('ARCHIVE_ROW_COUNT')
   if pending:emit(pending)
   check();result=bridge.finish();check()
   if identity(os.fstat(stream.fileno()))!=identity(before):raise SourceReadError('ARCHIVE_DESCRIPTOR_CHANGED_FINAL')
  except BaseException as exc:
   failure=exc;raise
  finally:
   try:stream.close()
   except BaseException as secondary:
    if failure is None:raise
    failure.add_note('ARCHIVE_CLOSE_UNCERTAIN:'+type(secondary).__name__)
  check()
  if identity(path.stat(follow_symlinks=False))!=identity(before):raise SourceReadError('ARCHIVE_PATH_CHANGED_FINAL')
  result.update(scope='SMALL_SINGLE_FRAME_PROVIDED_ARCHIVE_ENGINEERING_ONLY',source_bytes_authenticated=True,archive={'sha256':expected_sha256,'compressed_bytes':count,'decoded_bytes':len(decoded),'decoded_sha256':hashlib.sha256(decoded).hexdigest(),'rows':rows,'single_frame_complete':True,'source_receipt':asdict(prior),'additional_resource_checks':checks,'identity_guarantee':'FINAL_SNAPSHOT_NOT_PERPETUAL_PATH_AUTHORITY'})
  if time.monotonic_ns()>=deadline_ns:raise SourceReadError('WALL_LIMIT')
  return result
 except BaseException as primary:
  try:
   if bridge is not None:bridge._abort(primary)
   else:client.abort(primary)
  except BaseException as secondary:primary.add_note('ARCHIVE_CLIENT_CLEANUP_UNCERTAIN:'+type(secondary).__name__)
  raise
