"""Disabled in-memory decoder→supervised native bridge. No raw-byte reader."""
from .decoder import ArrowDecoder
from .adapter import project
from ..runtime.execution_profile import PROFILE as C,LANES

class DecodedVolumeBridge:
 def __init__(self,client,*,enabled=False):
  if enabled is not True:raise ValueError('DISABLED_DECODER_BRIDGE')
  if(client.source,client.session,client.lanes)!=(C['math_namespace'],C['input']['session'],LANES)or client.failed or client.finished:raise ValueError('LIVE_FIXED_PROFILE_CLIENT_REQUIRED')
  self.client=client;self.failed=False;self.finished=False;self.next_clock=0;self.snapshots=[];self.selected=0;self.excluded=0
  self.names=tuple(n for _,n,_ in LANES)
  self.decoder=ArrowDecoder(session=C['input']['session'],opened=C['slice_open_ns'],mapping={n:i for _,n,i in LANES},source_id=C['math_namespace'],enabled=True)
 def _abort(self,exc):
  self.failed=True
  try:self.client.abort(exc)
  except BaseException as secondary:
   exc.add_note('DECODER_BRIDGE_CLEANUP_UNCERTAIN:'+type(secondary).__name__)
 def _advance(self,clock):
  # Include all equal-clock groups before the first strictly later completion.
  while self.next_clock<2 and clock>C['snapshot_clocks'][self.next_clock]:
   at=C['snapshot_clocks'][self.next_clock]
   for lane in range(7):self.snapshots.append(self.client.snapshot(lane,at))
   self.next_clock+=1
 def consume(self,batch):
  if self.failed or self.finished:raise ValueError('BRIDGE_LATCHED')
  try:
   if not 0<batch.num_rows<=512:raise ValueError('BRIDGE_BATCH_BOUND')
   if self.decoder.rows+batch.num_rows>C['input']['raw_rows_exact']:raise ValueError('BRIDGE_RAW_ROW_CAP')
   self.client.check_resource();groups=self.decoder.consume(batch);self.client.check_resource()
   for g in groups:
    b=g.book;self._advance(b.available_ns)
    if C['slice_open_ns']<=g.first_recv and b.available_ns<C['slice_close_ns']:
     self.client.push(self.names.index(b.symbol),project(g,enabled=True));self.selected+=1
    else:self.excluded+=1
  except BaseException as exc:self._abort(exc);raise
 def finish(self):
  if self.failed or self.finished:raise ValueError('BRIDGE_LATCHED')
  try:
   accounting=self.decoder.finish()
   if self.decoder.last_recv is None or self.decoder.last_recv<C['slice_close_ns']:raise ValueError('BATCHES_NOT_THROUGH_SLICE')
   self._advance(self.decoder.last_recv+1)
   if len(self.snapshots)!=14:raise ValueError('EXACT_SNAPSHOT_COUNT')
   native=self.client.finish();self.finished=True
   return {'scope':'COMPLETED_PROVIDED_BATCHES_ENGINEERING_ONLY','source_bytes_authenticated':False,'capture_corrections_PIT':'UNKNOWN','model_or_trade_consumer':False,'decoder':accounting,'selected_groups':self.selected,'excluded_groups':self.excluded,'first_bad_raw_index':dict(self.decoder.first_bad_raw_index),'snapshots':self.snapshots,'native':native}
  except BaseException as exc:self._abort(exc);raise
