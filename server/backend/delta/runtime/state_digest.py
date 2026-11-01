"""Versioned primitive-state view; no new bucket arithmetic or market statistics."""
import hashlib
VECTORS=('input','accepted','quarantine','discarded','epoch_v','completed','partial','lifetime_completed','lifetime_evicted','discarded_completed','discarded_partial')
def state_bytes(e):
 p=['DELTA_VOLUME_PRIMITIVE_STATE_V1',e.source,e.symbol,e.session]
 def emit(*xs):p.extend(str(int(x))if type(x)is bool else str(x)for x in xs)
 emit(e.instrument,e.bucket,e.keep,e.cap,e.gap,e.piece_cap,e.epoch,e.groups,e.last,e.recv is not None,0 if e.recv is None else e.recv,e.failed,e.taint,e.correction,e.closed,e.lifetime_closed)
 for name in VECTORS:emit(*getattr(e,name))
 g=e.last_group;emit(g is not None)
 if g is not None:
  p.extend((g.source,g.symbol,g.session));emit(g.instrument,g.first,g.last,g.row_count,g.first_recv,g.recv,g.event,g.flags,g.valid,g.reset,g.correction_notice,len(g.trades))
  for t in g.trades:emit(t.index,t.recv,t.price,t.shares);p.extend((t.side,t.action))
 emit(len(e.pieces))
 for piece in e.pieces:emit(*piece)
 emit(len(e.ring))
 for b in e.ring:
  emit(b['ordinal'],*b['volume'],b['available_ns'],b['closure_frontier'],len(b['pieces']))
  for piece in b['pieces']:emit(*piece)
  p.extend(b[k]for k in('lower','upper','known_side_coverage','signed_lower','signed_upper'))
 emit(len(e.boundary_causes));p.extend(e.boundary_causes)
 return ('\n'.join(p)+'\n').encode('ascii')
def digest(e):
 data=state_bytes(e)
 if len(data)>65536:raise ValueError('STATE_VIEW_LIMIT')
 return hashlib.sha256(data).hexdigest()
