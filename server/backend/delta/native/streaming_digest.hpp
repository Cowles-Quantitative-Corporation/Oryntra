#include <cstring>
#include <type_traits>
namespace streaming_digest {
using namespace delta_volume;
class Sink {
 CC_SHA256_CTX context;std::size_t count=0;
 void bytes(const char* p,std::size_t n){if(n>65536-count)fail("STATE_VIEW_LIMIT");count+=n;CC_SHA256_Update(&context,p,static_cast<CC_LONG>(n));}
public:
 Sink(){CC_SHA256_Init(&context);}
 Sink& operator<<(const std::string& s){bytes(s.data(),s.size());return *this;}
 Sink& operator<<(const char* s){bytes(s,std::strlen(s));return *this;}
 Sink& operator<<(char c){bytes(&c,1);return *this;}
 Sink& operator<<(bool b){return *this<<(b?'1':'0');}
 template<class T,std::enable_if_t<std::is_integral_v<T>&&!std::is_same_v<T,char>&&!std::is_same_v<T,bool>,int> =0> Sink& operator<<(T n){char buffer[32];auto r=std::to_chars(buffer,buffer+sizeof(buffer),n);if(r.ec!=std::errc())fail("STATE_NUMERIC_FORMAT");bytes(buffer,static_cast<std::size_t>(r.ptr-buffer));return *this;}
 std::string finish(){unsigned char out[CC_SHA256_DIGEST_LENGTH];CC_SHA256_Final(out,&context);const char* digits="0123456789abcdef";std::string s(64,'0');for(unsigned i=0;i<32;i++){s[2*i]=digits[out[i]>>4];s[2*i+1]=digits[out[i]&15];}return s;}
};
std::string digest(const Engine& e){
#ifdef DELTA_ALLOCATION_PROBE
 alloc_probe::digest_calls.fetch_add(1,std::memory_order_relaxed);
#endif
 Sink o;
 auto val=[&](const auto& v){o<<v<<'\n';};
 val("DELTA_VOLUME_PRIMITIVE_STATE_V1");val(e.source);val(e.symbol);val(e.session);
 val(e.instrument);val(e.bucket);val(e.keep);val(e.cap);val(e.gap);val(e.piece_cap);val(e.epoch);val(e.groups);val(e.last);val(e.have);val(e.have?e.recv:0);val(e.failed);val(e.taint);val(e.correction);val(e.closed);val(e.lifetime_closed);
 for(const Vol* v:{&e.input,&e.accepted,&e.quarantine,&e.discarded,&e.epoch_v,&e.completed,&e.partial,&e.lifetime_completed,&e.lifetime_evicted,&e.discarded_completed,&e.discarded_partial})for(U x:v->v)val(x);
 val(e.have);
 if(e.have){const Group& g=e.last_group;val(g.source);val(g.symbol);val(g.session);val(g.instrument);val(g.first);val(g.last);val(g.row_count);val(g.first_recv);val(g.recv);val(g.event);val(g.flags);val(g.valid);val(g.reset);val(g.correction);val(g.trades.size());for(const auto& t:g.trades){val(t.index);val(t.recv);val(t.price);val(t.shares);val(t.side);val(t.action);}}
 auto pieces=[&](const std::vector<Piece>& xs){val(xs.size());for(const auto& p:xs){val(p.index);val(p.recv);val(p.available);val(p.price);val(p.shares);val(p.side);}};
 pieces(e.pieces);val(e.ring.size());for(const Bucket& b:e.ring){val(b.ordinal);for(U x:b.volume.v)val(x);val(b.available);val(b.frontier);pieces(b.pieces);auto lim=bounds(b.volume);val(rat(lim.first,e.bucket));val(rat(lim.second,e.bucket));val(rat(e.bucket-b.volume.v[2],e.bucket));val(srat(static_cast<std::int64_t>(b.volume.v[0])-static_cast<std::int64_t>(b.volume.v[1])-static_cast<std::int64_t>(b.volume.v[2]),e.bucket));val(srat(static_cast<std::int64_t>(b.volume.v[0])-static_cast<std::int64_t>(b.volume.v[1])+static_cast<std::int64_t>(b.volume.v[2]),e.bucket));}
 val(e.causes.size());for(const auto& s:e.causes)val(s);
 return o.finish();
}
}
