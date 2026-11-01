"""Exact accepted group wire serializer; no source decoder."""
def group(g):
 return ' '.join(map(str,('G',g.source,g.symbol,g.session,g.instrument,g.first,g.last,g.row_count,g.first_recv,g.recv,g.event,g.flags,int(g.valid),int(g.reset),int(g.correction_notice),len(g.trades))+tuple(v for t in g.trades for v in(t.index,t.recv,t.price,t.shares,t.side,t.action))))
