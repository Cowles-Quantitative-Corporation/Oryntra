// One child, seven preserved Engine objects; disabled synthetic protocol only.
#include "accepted_state_view.hpp"
#include "streaming_digest.hpp"
#include <set>
#include <array>
#include "parser_scratch.hpp"
constexpr const char* PROFILE_HASH="2c4b1ccbbe796e60115122a90184faa47de39e8804290a6d02e51707251d51fe";
#ifdef DELTA_TX_FAULT_TEST
namespace mux {inline void(*transaction_test_hook)(const char*,delta_volume::Engine&)=nullptr;}
#define DELTA_TX_POINT(stage,working) do{if(mux::transaction_test_hook)mux::transaction_test_hook(stage,working);}while(false)
#else
#define DELTA_TX_POINT(stage,working) ((void)0)
#endif
namespace mux {
using namespace delta_volume;
std::string hex(const unsigned char* bytes){const char* digits="0123456789abcdef";std::string out(2*CC_SHA256_DIGEST_LENGTH,'0');for(unsigned i=0;i<CC_SHA256_DIGEST_LENGTH;i++){out[2*i]=digits[bytes[i]>>4];out[2*i+1]=digits[bytes[i]&15];}return out;}
std::string sha(const std::string& s){unsigned char b[CC_SHA256_DIGEST_LENGTH];CC_SHA256(s.data(),static_cast<CC_LONG>(s.size()),b);return hex(b);}
std::string prefix_sha(const CC_SHA256_CTX& source){auto c=source;unsigned char b[CC_SHA256_DIGEST_LENGTH];CC_SHA256_Final(b,&c);return hex(b);}
struct State {
 std::string source,session,runtime_manifest;U fixed_count=0;std::vector<std::unique_ptr<Engine>> lanes;U frontier=0,recv=0,completed=0,decision=0;bool have=false,have_decision=false;
 std::array<std::unique_ptr<Engine>,7> workspace;
 std::array<std::string,7> cached;std::array<bool,7> valid{};
 void initialize_cache(){try{if(lanes.size()!=7)fail("LANE_MAP");for(unsigned i=0;i<7;i++){if(workspace[i])fail("WORKSPACE_ALREADY_INITIALIZED");workspace[i]=std::make_unique<Engine>(*lanes[i]);}for(unsigned i=0;i<7;i++)refresh(i);}catch(...){valid.fill(false);throw;}}
 void refresh(U i){if(i>=7||lanes.size()!=7)fail("LANE_MAP");valid[i]=false;if(lanes[i]->failed)fail("DIGEST_CACHE_FAILED_ENGINE");std::string h=streaming_digest::digest(*lanes[i]);cached[i]=std::move(h);valid[i]=true;}
 void push_lane(U i,const Group& g){
  bool transaction_started=false;
  try{
   if(i>=7||lanes.size()!=7)fail("LANE_MAP");valid[i]=false;
   if(!workspace[i]||workspace[i].get()==lanes[i].get())fail("WORKSPACE_IDENTITY");
   if(lanes[i]->failed)fail("LATCHED");transaction_started=true;
   lanes[i]->validate(g);
   DELTA_TX_POINT("before_copy",*workspace[i]);*workspace[i]=*lanes[i];DELTA_TX_POINT("after_copy",*workspace[i]);
   DELTA_TX_POINT("before_apply",*workspace[i]);workspace[i]->apply(g);DELTA_TX_POINT("after_apply",*workspace[i]);
   workspace[i]->invariants();DELTA_TX_POINT("after_invariants",*workspace[i]);
   DELTA_TX_POINT("before_digest",*workspace[i]);std::string next_hash=streaming_digest::digest(*workspace[i]);DELTA_TX_POINT("after_digest",*workspace[i]);
   DELTA_TX_POINT("before_commit",*workspace[i]);
   static_assert(noexcept(lanes[i].swap(workspace[i])),"atomic pointer commit must not throw");
   static_assert(noexcept(cached[i].swap(next_hash)),"atomic digest commit must not throw");
   lanes[i].swap(workspace[i]);cached[i].swap(next_hash);valid[i]=true;
  }catch(...){if(transaction_started)lanes[i]->failed=true;valid.fill(false);throw;}
 }
 const std::array<std::string,7>& digests()const{for(bool ok:valid)if(!ok)fail("DIGEST_CACHE_INVALID");return cached;}
 void verify_cache()const{digests();for(unsigned i=0;i<7;i++)if(cached[i]!=streaming_digest::digest(*lanes[i]))fail("DIGEST_CACHE_MISMATCH");}
 std::string root(const std::array<std::string,7>& hashes)const{
  if(lanes.size()!=7)fail("LANE_MAP");digests();streaming_digest::Sink inner;
  auto emit=[&](const auto& value){inner<<value<<'\n';};
  emit("DELTA_VOLUME_MUX_ROOT_V1");emit(source);emit(session);emit(7);emit(have);emit(frontier);emit(recv);emit(completed);emit(have_decision);emit(decision);
  for(unsigned i=0;i<7;i++){emit(i);emit(lanes[i]->symbol);emit(lanes[i]->instrument);emit(hashes[i]);}
  const auto semantic=inner.finish();streaming_digest::Sink outer;
  outer<<"DELTA_VOLUME_SOURCE_EXECUTION_ROOT_V25\n"<<PROFILE_HASH<<'\n'<<fixed_count<<'\n'<<runtime_manifest<<'\n'<<semantic<<'\n';return outer.finish();
 }

};
}
#ifndef DELTA_NO_ENTRY
int main(int argc,char** argv){
 using namespace delta_volume;
 U operation_limit=source_profile::OP_CAP;
 if(argc!=3||std::string(argv[1])!="--recorded-source-only-2c4b1ccbbe796e60115122a90184faa47de39e8804290a6d02e51707251d51fe"||std::string(argv[2]).size()!=64){std::cerr<<"DISABLED_OR_PROFILE_MISMATCH\n";return 2;}
 for(char c:std::string(argv[2]))if(!((c>='0'&&c<='9')||(c>='a'&&c<='f')))return 2;
 mux::State state;state.fixed_count=source_profile::GROUP_CAP;state.runtime_manifest=argv[2];CC_SHA256_CTX prefix;CC_SHA256_Init(&prefix);U seq=0;unsigned snapshots=0;std::string s;
 ParserScratch t;
 try{while(line(s)){
  t.read(s);
  if(t.size()<2||seq>=operation_limit||parse(t[1])!=seq+1)fail("SEQUENCE_OR_OPERATION_LIMIT");U request=parse(t[1]);
  auto num=[&](std::size_t i){if(i>=t.size())fail("DOMAIN");return parse(t[i]);};auto boolean=[&](std::size_t i){U n=num(i);if(n>1)fail("DOMAIN");return n==1;};
  std::string lane="-",snapshot;bool finish=false;
  if(t[0]=="I"){
   if(!state.lanes.empty()||t.size()!=26||num(4)!=7)fail("LANE_MAP");state.source=t[2];state.session=t[3];if(state.source!="synthetic-source:recorded:3f6c7dbb85f25964842f8957d675ce35c59c0375c19b845df632b92b4e9beecb"||state.session!="2026-04-01")fail("CONFIG_SOURCE_IDENTITY");std::set<std::string> names;std::set<U> ids;
   for(U i=0;i<7;i++){U at=5+3*i;if(num(at)!=i||!names.insert(t[at+1]).second||!ids.insert(num(at+2)).second)fail("LANE_MAP");const char* fixed_names[]={"AAPL","AMZN","GOOGL","JPM","MSFT","WMT","XOM"};const U fixed_ids[]={38,853,7152,9155,10888,17448,17709};if(t[at+1]!=fixed_names[i]||num(at+2)!=fixed_ids[i])fail("FIXED_MAPPING");state.lanes.push_back(std::make_unique<Engine>(state.source,t[at+1],state.session,num(at+2),10,2,UMAX,2000000000ULL,256));}
  }else if(t[0]=="P"){
   if(state.lanes.size()!=7||t.size()<18||num(2)>=7)fail("LANE_MAP");U n=num(17);if(n>32||t.size()!=18+6*n)fail("DOMAIN");U atlane=num(2);lane=std::to_string(atlane);
   Group g{t[3],t[4],t[5],num(6),num(7),num(8),num(9),num(10),num(11),num(12),num(13),boolean(14),boolean(15),boolean(16),{}};
   for(U j=0;j<n;j++){U at=18+6*j;if(t[at+4].size()!=1||t[at+5].size()!=1)fail("DOMAIN");g.trades.push_back({num(at),num(at+1),num(at+2),num(at+3),t[at+4][0],t[at+5][0]});}
   if(state.completed>=source_profile::GROUP_CAP||g.last>source_profile::RAW_CAP||g.first_recv<source_profile::OPEN||g.recv>=source_profile::CLOSE)fail("PROFILE_POPULATION_OR_CAP");
   // Global completion advances; first index may precede another lane's completion.
   if(g.last<=state.frontier||(state.have&&g.recv<state.recv)||(state.have_decision&&g.recv<state.decision))fail("GLOBAL_COMPLETION_ORDER");
   state.push_lane(atlane,g);state.frontier=g.last;state.recv=g.recv;state.have=true;state.completed=add(state.completed,1);
  }else if(t[0]=="S"){
   if(state.lanes.size()!=7||t.size()!=4||num(2)>=7||snapshots>=16)fail("SNAPSHOT_LIMIT_OR_DOMAIN");U at=num(3);lane=std::to_string(num(2));
   if((state.have&&at<state.recv)||(state.have_decision&&at<state.decision))fail("GLOBAL_DECISION_CLOCK");U expected_clock=snapshots<7?source_profile::MID:source_profile::CLOSE;if(snapshots>=14||num(2)!=snapshots%7||at!=expected_clock)fail("PROFILE_SNAPSHOT_SCHEDULE");snapshot=state.lanes[num(2)]->snapshot(at);state.decision=at;state.have_decision=true;snapshots++;
  }else if(t[0]=="F"){if(state.completed>source_profile::GROUP_CAP||snapshots!=14)fail("PROFILE_COMPLETION");
   if(state.lanes.size()!=7||t.size()!=2)fail("DOMAIN");finish=true;
  }else fail("DOMAIN");
  std::string frame=s+'\n';CC_SHA256_Update(&prefix,frame.data(),static_cast<CC_LONG>(frame.size()));if(t[0]=="I")state.initialize_cache();if(finish)state.verify_cache();const auto& hashes=state.digests();
  std::string body=lane+' '+mux::sha(frame)+' '+mux::prefix_sha(prefix)+' '+(lane=="-"?"-":hashes[parse(lane)])+' '+state.root(hashes);
  seq=request;std::string response=(snapshot.empty()?"A ":"S ")+std::to_string(seq)+' '+body+(snapshot.empty()?"":" "+snapshot);
  if(response.size()>16384)fail("RESPONSE_BYTES_LIMIT");std::cout<<response<<'\n';std::cout.flush();if(finish)return 0;
 }}catch(const std::exception& e){std::cout<<"E "<<seq+1<<' '<<e.what()<<'\n';std::cout.flush();return 2;}
 std::cout<<"E "<<seq+1<<" MISSING_FINAL_HANDSHAKE\n";return 2;
}

#endif
