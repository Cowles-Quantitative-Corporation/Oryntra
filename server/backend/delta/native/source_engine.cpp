#include "source_caps.hpp"
// Synthetic-only checked-integer reference worker. No feed/file/model integration.
#include <algorithm>
#include <charconv>
#include <cstdint>
#include <deque>
#include <iostream>
#include <limits>
#include <numeric>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include <memory>
namespace delta_volume {
using U=std::uint64_t;
constexpr U BUCKET_MAX_AGE=30000000000ULL;
constexpr U UMAX=std::numeric_limits<U>::max(), U32=4294967295ULL, I64=9223372036854775807ULL;
[[noreturn]] void fail(const char* reason){throw std::runtime_error(reason);}
U add(U a,U b,U cap=UMAX){if(a>cap||b>cap||b>cap-a)fail("OVERFLOW");return a+b;}
U parse(const std::string& s){if(s.empty()||s.size()>20)fail("DOMAIN");for(char c:s)if(c<'0'||c>'9')fail("DOMAIN");U x=0;auto r=std::from_chars(s.data(),s.data()+s.size(),x);if(r.ec!=std::errc()||r.ptr!=s.data()+s.size())fail("DOMAIN");return x;}
void id(const std::string& s){if(s.empty()||s.size()>128)fail("DOMAIN");for(char c:s)if(!((c>='A'&&c<='Z')||(c>='a'&&c<='z')||(c>='0'&&c<='9')||c=='_'||c=='.'||c==':'||c=='-'))fail("DOMAIN");}
std::string rat(U n,U d){if(d==0)fail("DOMAIN");U g=std::gcd(n,d);return std::to_string(n/g)+"/"+std::to_string(d/g);}
struct Vol {U v[3]={0,0,0};U sum()const{return add(add(v[0],v[1]),v[2]);}};
int side(char s){if(s=='B')return 0;if(s=='A')return 1;if(s=='N')return 2;fail("DOMAIN");}
std::pair<U,U> bounds(const Vol& v){U d=v.v[0]>v.v[1]?v.v[0]-v.v[1]:v.v[1]-v.v[0];return {d>v.v[2]?d-v.v[2]:0,std::min(v.sum(),add(d,v.v[2]))};}
std::string srat(std::int64_t n,U d){if(n<0)return "-"+rat(static_cast<U>(-(n+1))+1,d);return rat(static_cast<U>(n),d);}
struct Trade {U index,recv,price,shares;char side,action;};
struct Piece {U index,recv,available,price,shares;char side;};
struct Group {std::string source,symbol,session;U instrument,first,last,row_count,first_recv,recv,event,flags;bool valid,reset,correction;std::vector<Trade> trades;};
struct Bucket {U ordinal;Vol volume;std::vector<Piece> pieces;U available,frontier;};
void vector_json(std::ostream& o,const Vol& v){o<<'['<<v.v[0]<<','<<v.v[1]<<','<<v.v[2]<<']';}
void pieces_json(std::ostream& o,const std::vector<Piece>& p){o<<'[';bool first=true;for(const auto& x:p){if(!first)o<<',';first=false;o<<'['<<x.index<<','<<x.recv<<','<<x.available<<','<<x.price<<','<<x.shares<<",\""<<x.side<<"\"]";}o<<']';}
void nullable(std::ostream& o,const std::string& s){if(s.empty())o<<"null";else o<<'\"'<<s<<'\"';}
class Engine {
public:
 std::string source,symbol,session;U instrument,bucket,keep,cap,gap,piece_cap;
 U epoch=0,groups=0,last=0,recv=0,closed=0;bool have=false,failed=false,taint=false,correction=false;
 Vol input,accepted,quarantine,discarded,epoch_v,completed,partial;Group last_group{};Vol lifetime_completed,lifetime_evicted,discarded_completed,discarded_partial;U lifetime_closed=0;
 std::vector<Piece> pieces;std::deque<Bucket> ring;std::vector<std::string> causes;
 Engine(std::string src,std::string sym,std::string day,U ins,U q,U k,U limit,U maxgap,U maxpieces):source(src),symbol(sym),session(day),instrument(ins),bucket(q),keep(k),cap(limit),gap(maxgap),piece_cap(maxpieces){
  id(source);id(symbol);id(session);if(source.rfind("synthetic-source:",0)!=0)fail("DISABLED");if(ins==0||ins>U32||q==0||q>U32||k==0||k>128||limit==0||q>limit||maxgap==0||maxgap>I64||maxpieces==0||maxpieces>256)fail("DOMAIN");
 }
 void inc(Vol& v,char s,U qty){int i=side(s);v.v[i]=add(v.v[i],qty,cap);if(v.sum()>cap)fail("OVERFLOW");}
 void clear(){for(int i=0;i<3;i++){discarded.v[i]=add(discarded.v[i],epoch_v.v[i],cap);discarded_completed.v[i]=add(discarded_completed.v[i],completed.v[i],cap);discarded_partial.v[i]=add(discarded_partial.v[i],partial.v[i],cap);}epoch_v=Vol{};completed=Vol{};partial=Vol{};pieces.clear();ring.clear();closed=0;epoch=add(epoch,1);}
 void piece(const Trade& t,U q,const Group& g){if(pieces.size()>=piece_cap)fail("PIECE_LIMIT");inc(partial,t.side,q);pieces.push_back({t.index,t.recv,g.recv,t.price,q,t.side});}
 void complete(const Group& g){closed=add(closed,1);lifetime_closed=add(lifetime_closed,1);for(int i=0;i<3;i++){completed.v[i]=add(completed.v[i],partial.v[i],cap);lifetime_completed.v[i]=add(lifetime_completed.v[i],partial.v[i],cap);if(ring.size()==keep)lifetime_evicted.v[i]=add(lifetime_evicted.v[i],ring.front().volume.v[i],cap);}ring.push_back({closed,partial,pieces,g.recv,g.last});if(ring.size()>keep)ring.pop_front();partial=Vol{};pieces.clear();}
 void trade(const Trade& t,const Group& g){
  inc(accepted,t.side,t.shares);inc(epoch_v,t.side,t.shares);U left=t.shares;
  if(partial.sum()){U used=std::min(left,bucket-partial.sum());piece(t,used,g);left-=used;if(partial.sum()==bucket)complete(g);}
  U full=left/bucket;left%=bucket;
  if(full){U count=std::min(full,keep),skip=full-count;closed=add(closed,skip);lifetime_closed=add(lifetime_closed,skip);inc(completed,t.side,skip*bucket);inc(lifetime_completed,t.side,skip*bucket);inc(lifetime_evicted,t.side,skip*bucket);for(U j=0;j<count;j++){piece(t,bucket,g);complete(g);}}
  if(left)piece(t,left,g);
 }
 void validate(const Group& g)const{
  if(g.source!=source||g.symbol!=symbol||g.session!=session||g.instrument!=instrument||g.first==0||g.first>I64||g.last>I64||g.first>g.last||((g.row_count==1)!=(g.first==g.last))||g.row_count==0||g.row_count>32||g.row_count>g.last-g.first+1||g.first_recv>I64||g.recv>I64||g.event>I64||g.flags>255||(g.flags&128)==0||g.first_recv>g.recv||g.event>g.recv||g.first<=last||(have&&g.first_recv<recv)||g.trades.size()>g.row_count)fail("DOMAIN");
  U index=g.first-1,at=g.first_recv;
  for(const auto& t:g.trades){if(t.index<=index||t.index<g.first||t.index>g.last||t.recv<at||t.recv>g.recv||t.price==0||t.price>=I64||t.shares==0||t.shares>U32||t.action!='T')fail("DOMAIN");side(t.side);index=t.index;at=t.recv;}
 }
 void apply(const Group& g){
  if(groups>=source_profile::lane_cap(instrument))fail("GROUP_LIMIT");bool silence=have&&g.recv-recv>gap,boundary=g.reset||(g.flags&32),bad=!g.valid||(g.flags&14);
  if(boundary||bad||g.correction||silence){causes.clear();if(boundary)causes.push_back("EXPLICIT_RESET");if(bad)causes.push_back("QUALITY");if(g.correction)causes.push_back("KNOWN_CORRECTION");if(silence)causes.push_back("GAP_POLICY");clear();}if(g.correction)correction=true;if(bad)taint=true;else if(boundary)taint=false;
  last=g.last;recv=g.recv;last_group=g;have=true;groups++;bool blocked=boundary||bad||taint||correction;
  for(const auto& t:g.trades){inc(input,t.side,t.shares);if(blocked)inc(quarantine,t.side,t.shares);else trade(t,g);}
 }
 void invariants()const{
  for(int i=0;i<3;i++)if(input.v[i]!=add(accepted.v[i],quarantine.v[i])||accepted.v[i]!=add(discarded.v[i],epoch_v.v[i])||epoch_v.v[i]!=add(completed.v[i],partial.v[i]))fail("CONSERVATION");
  for(int i=0;i<3;i++)if(lifetime_completed.v[i]!=add(discarded_completed.v[i],completed.v[i])||discarded.v[i]!=add(discarded_completed.v[i],discarded_partial.v[i])||lifetime_evicted.v[i]>lifetime_completed.v[i])fail("CONSERVATION");
  if(lifetime_closed>UMAX/bucket||lifetime_completed.sum()!=lifetime_closed*bucket)fail("CONSERVATION");
  if(partial.sum()>=bucket||closed>UMAX/bucket||completed.sum()!=closed*bucket)fail("CONSERVATION");
  for(const auto& b:ring){Vol v;for(const auto& p:b.pieces){int i=side(p.side);v.v[i]=add(v.v[i],p.shares);}if(b.volume.sum()!=bucket||v.v[0]!=b.volume.v[0]||v.v[1]!=b.volume.v[1]||v.v[2]!=b.volume.v[2])fail("CONSERVATION");}
 }
 void push(const Group& g){if(failed)fail("LATCHED");try{validate(g);Engine next=*this;next.apply(g);next.invariants();*this=std::move(next);}catch(...){failed=true;throw;}}
 std::string snapshot(U decision)const{
  if(decision>I64)fail("DOMAIN");if(failed)fail("LATCHED");if(have&&decision<recv)fail("FUTURE_PREFIX");
  std::string status=correction?"UNAVAILABLE_CORRECTION":taint?"UNAVAILABLE_QUALITY":have&&decision-recv>gap?"STALE_DECISION":ring.size()<keep?"WARMUP":decision-ring.back().available>BUCKET_MAX_AGE?"STALE_BUCKET_WINDOW":"AVAILABLE_REPORTED_BUCKETS";
  std::string lo,hi,strict,known_ratio,sl,su,ss;if(status=="AVAILABLE_REPORTED_BUCKETS"){U low=0,high=0;bool known=true;for(const auto& b:ring){auto p=bounds(b.volume);low=add(low,p.first);high=add(high,p.second);known=known&&b.volume.v[2]==0;}lo=rat(low,keep*bucket);hi=rat(high,keep*bucket);if(known)strict=lo;Vol totals;for(const auto& b:ring)for(int i=0;i<3;i++)totals.v[i]=add(totals.v[i],b.volume.v[i]);U den=keep*bucket;std::int64_t diff=static_cast<std::int64_t>(totals.v[0])-static_cast<std::int64_t>(totals.v[1]);known_ratio=rat(den-totals.v[2],den);sl=srat(diff-static_cast<std::int64_t>(totals.v[2]),den);su=srat(diff+static_cast<std::int64_t>(totals.v[2]),den);if(known)ss=srat(diff,den);}
  std::ostringstream o;o<<"{\"contract\":\"CONDITIONAL_REPORTED_VOLUME_BUCKET_SYNTHETIC_V2\",\"volume_unit\":\"REPORTED_SHARES\",\"price_unit\":\"USD_NANODOLLARS\",\"clock_unit\":\"NANOSECONDS\",\"source\":\""<<source<<"\",\"symbol\":\""<<symbol<<"\",\"session\":\""<<session<<"\",\"instrument\":"<<instrument<<",\"status\":\""<<status<<"\",\"epoch\":"<<epoch<<",\"groups\":"<<groups<<",\"frontier\":"<<last<<",\"available_ns\":";if(have)o<<recv;else o<<"null";o<<",\"decision_ns\":"<<decision;
  const U gv[]={last_group.first,last_group.first_recv,last_group.event,last_group.flags,last_group.row_count,last_group.last-last_group.first+1};const char* gn[]={"group_first_frontier","group_first_recv_ns","group_event_ns","group_flags","group_row_count","group_physical_span"};for(int i=0;i<6;i++){o<<",\""<<gn[i]<<"\":";if(have)o<<gv[i];else o<<"null";}const bool gb[]={last_group.valid,last_group.reset,last_group.correction};const char* bn[]={"group_valid","group_reset","group_correction_notice"};for(int i=0;i<3;i++){o<<",\""<<bn[i]<<"\":";if(have)o<<(gb[i]?"true":"false");else o<<"null";}

  const Vol* vv[]={&input,&accepted,&quarantine,&discarded,&epoch_v,&completed,&partial};const char* names[]={"input","accepted","quarantine","discarded","epoch_volume","completed_volume","partial_volume"};for(int i=0;i<7;i++){o<<",\""<<names[i]<<"\":";vector_json(o,*vv[i]);}
  Vol retained,evicted;for(const auto& b:ring)for(int i=0;i<3;i++)retained.v[i]=add(retained.v[i],b.volume.v[i]);for(int i=0;i<3;i++){if(retained.v[i]>completed.v[i])fail("CONSERVATION");evicted.v[i]=completed.v[i]-retained.v[i];}
  const Vol* extra[]={&lifetime_completed,&lifetime_evicted,&discarded_completed,&discarded_partial,&retained,&evicted};const char* en[]={"lifetime_completed_volume","lifetime_evicted_volume","discarded_completed_volume","discarded_partial_volume","retained_volume","evicted_current_epoch_volume"};for(int i=0;i<6;i++){o<<",\""<<en[i]<<"\":";vector_json(o,*extra[i]);}o<<",\"lifetime_completed_buckets\":"<<lifetime_closed;
  o<<",\"partial_pieces\":";pieces_json(o,pieces);o<<",\"completed_buckets\":"<<closed<<",\"buckets\":[";bool first=true;for(const auto& b:ring){if(!first)o<<',';first=false;auto bs=bounds(b.volume);o<<"{\"ordinal\":"<<b.ordinal<<",\"volume\":";vector_json(o,b.volume);o<<",\"pieces\":";pieces_json(o,b.pieces);o<<",\"available_ns\":"<<b.available<<",\"closure_frontier\":"<<b.frontier<<",\"lower\":\""<<rat(bs.first,bucket)<<"\",\"upper\":\""<<rat(bs.second,bucket)<<"\",\"known_side_coverage\":\""<<rat(bucket-b.volume.v[2],bucket)<<"\",\"signed_lower\":\""<<srat(static_cast<std::int64_t>(b.volume.v[0])-static_cast<std::int64_t>(b.volume.v[1])-static_cast<std::int64_t>(b.volume.v[2]),bucket)<<"\",\"signed_upper\":\""<<srat(static_cast<std::int64_t>(b.volume.v[0])-static_cast<std::int64_t>(b.volume.v[1])+static_cast<std::int64_t>(b.volume.v[2]),bucket)<<"\"}";}
  o<<"],\"lower\":";nullable(o,lo);o<<",\"upper\":";nullable(o,hi);o<<",\"strict_absolute_imbalance\":";nullable(o,strict);o<<",\"bucket_max_age_ns\":"<<BUCKET_MAX_AGE<<",\"last_bucket_completion_ns\":";if(ring.empty())o<<"null";else o<<ring.back().available;o<<",\"bucket_age_ns\":";if(ring.empty())o<<"null";else o<<decision-ring.back().available;o<<",\"bucket_span_ns\":";if(ring.empty())o<<"null";else o<<ring.back().available-ring.front().pieces.front().available;
  o<<",\"known_side_coverage\":";nullable(o,known_ratio);o<<",\"signed_lower\":";nullable(o,sl);o<<",\"signed_upper\":";nullable(o,su);o<<",\"strict_signed_imbalance\":";nullable(o,ss);o<<",\"last_boundary_causes\":[";for(std::size_t i=0;i<causes.size();i++){if(i)o<<',';o<<'\"'<<causes[i]<<'\"';}o<<"],\"print_size_hhi\":null,\"print_size_hhi_status\":\"NOT_IMPLEMENTED_NO_SQUARED_SIZE_ACCUMULATION\"";o<<",\"known_correction_notice\":"<<(correction?"true":"false")<<",\"market_capture\":\"UNKNOWN\",\"correction_semantics\":\"UNKNOWN_UNLESS_NOTICE_BLOCKED\",\"independent_bucket_samples\":false,\"forecast_or_admission_influence\":false,\"calibrated_VPIN_probability\":false}";return o.str();
 }
};
bool line(std::string& s){s.clear();char c;while(std::cin.get(c)){if(c=='\n')return true;if((static_cast<unsigned char>(c)<32&&c!='\t')||static_cast<unsigned char>(c)>126)fail("DOMAIN");if(s.size()>=16384)fail("INPUT_LIMIT");s.push_back(c);}return !s.empty();}
}
int main(int argc,char**argv){using namespace delta_volume;if(argc!=2||std::string(argv[1])!="--synthetic-only"){std::cerr<<"DISABLED: synthetic-only switch required\n";return 2;}std::unique_ptr<Engine> e;std::string s;
 try{while(line(s)){std::istringstream in(s);std::vector<std::string> t;std::string w;while(in>>w)t.push_back(w);if(t.empty())fail("DOMAIN");auto num=[&](std::size_t i){if(i>=t.size())fail("DOMAIN");return parse(t[i]);};auto boolean=[&](std::size_t i){U v=num(i);if(v>1)fail("DOMAIN");return v==1;};
  if(t[0]=="INIT"){if(e||t.size()!=10)fail("DOMAIN");e=std::make_unique<Engine>(t[1],t[2],t[3],num(4),num(5),num(6),num(7),num(8),num(9));std::cout<<"{\"initialized\":true}\n";}
  else if(t[0]=="ADD"){if(t.size()!=4)fail("DOMAIN");U value=add(num(1),num(2),num(3));std::cout<<"{\"sum\":"<<value<<"}\n";}
  else if(t[0]=="G"){if(!e||t.size()<16)fail("DOMAIN");U n=num(15);if(n>32||t.size()!=16+6*n)fail("DOMAIN");Group g{t[1],t[2],t[3],num(4),num(5),num(6),num(7),num(8),num(9),num(10),num(11),boolean(12),boolean(13),boolean(14),{}};for(U j=0;j<n;j++){std::size_t at=16+6*j;if(t[at+4].size()!=1||t[at+5].size()!=1)fail("DOMAIN");g.trades.push_back({num(at),num(at+1),num(at+2),num(at+3),t[at+4][0],t[at+5][0]});}e->push(g);std::cout<<e->snapshot(g.recv)<<'\n';}
  else if(t[0]=="S"){if(!e||t.size()!=2)fail("DOMAIN");std::cout<<e->snapshot(num(1))<<'\n';}
  else fail("DOMAIN");std::cout.flush();
 }}catch(const std::exception& ex){std::cout<<"{\"error\":\""<<ex.what()<<"\"}\n";return 2;}return 0;
}
