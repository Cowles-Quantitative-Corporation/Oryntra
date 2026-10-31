#define main delta_volume_v3_unused_main
#include "source_engine.cpp"
#undef main
#include <CommonCrypto/CommonDigest.h>
#include <iomanip>
namespace incremental {
using namespace delta_volume;
std::string state_bytes(const Engine& e){
 std::ostringstream o;
 auto val=[&](const auto& v){o<<v<<'\n';};
 val("DELTA_VOLUME_PRIMITIVE_STATE_V1");val(e.source);val(e.symbol);val(e.session);
 val(e.instrument);val(e.bucket);val(e.keep);val(e.cap);val(e.gap);val(e.piece_cap);val(e.epoch);val(e.groups);val(e.last);val(e.have);val(e.have?e.recv:0);val(e.failed);val(e.taint);val(e.correction);val(e.closed);val(e.lifetime_closed);
 for(const Vol* v:{&e.input,&e.accepted,&e.quarantine,&e.discarded,&e.epoch_v,&e.completed,&e.partial,&e.lifetime_completed,&e.lifetime_evicted,&e.discarded_completed,&e.discarded_partial})for(U x:v->v)val(x);
 val(e.have);
 if(e.have){const Group& g=e.last_group;val(g.source);val(g.symbol);val(g.session);val(g.instrument);val(g.first);val(g.last);val(g.row_count);val(g.first_recv);val(g.recv);val(g.event);val(g.flags);val(g.valid);val(g.reset);val(g.correction);val(g.trades.size());for(const auto& t:g.trades){val(t.index);val(t.recv);val(t.price);val(t.shares);val(t.side);val(t.action);}}
 auto pieces=[&](const std::vector<Piece>& xs){val(xs.size());for(const auto& p:xs){val(p.index);val(p.recv);val(p.available);val(p.price);val(p.shares);val(p.side);}};
 pieces(e.pieces);val(e.ring.size());for(const Bucket& b:e.ring){val(b.ordinal);for(U x:b.volume.v)val(x);val(b.available);val(b.frontier);pieces(b.pieces);auto lim=bounds(b.volume);val(rat(lim.first,e.bucket));val(rat(lim.second,e.bucket));val(rat(e.bucket-b.volume.v[2],e.bucket));val(srat(static_cast<std::int64_t>(b.volume.v[0])-static_cast<std::int64_t>(b.volume.v[1])-static_cast<std::int64_t>(b.volume.v[2]),e.bucket));val(srat(static_cast<std::int64_t>(b.volume.v[0])-static_cast<std::int64_t>(b.volume.v[1])+static_cast<std::int64_t>(b.volume.v[2]),e.bucket));}
 val(e.causes.size());for(const auto& s:e.causes)val(s);
 return o.str();
}
std::string digest(const Engine& e){const auto s=state_bytes(e);if(s.size()>65536)fail("STATE_VIEW_LIMIT");unsigned char out[CC_SHA256_DIGEST_LENGTH];CC_SHA256(s.data(),static_cast<CC_LONG>(s.size()),out);std::ostringstream o;o<<std::hex<<std::setfill('0');for(unsigned char c:out)o<<std::setw(2)<<static_cast<unsigned>(c);return o.str();}
}
