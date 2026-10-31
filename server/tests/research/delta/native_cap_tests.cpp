#define DELTA_NO_ENTRY 1
#include "source_worker.cpp"
#include <cassert>
int main(){
 using namespace delta_volume;
 U ids[]={38,853,7152,9155,10888,17448,17709};const char* names[]={"AAPL","AMZN","GOOGL","JPM","MSFT","WMT","XOM"};U sum=0;
 for(unsigned i=0;i<7;i++){
  U cap=source_profile::lane_cap(ids[i]);sum=add(sum,cap);Engine e("synthetic-source:cap-test",names[i],"2026-04-01",ids[i],10,2,UMAX,2000000000,256);e.groups=cap-1;
  Group g{e.source,e.symbol,e.session,e.instrument,1,1,1,100,100,100,128,true,false,false,{}};e.push(g);assert(e.groups==cap);auto before=e.input;g.first=g.last=2;g.first_recv=g.recv=g.event=101;bool failed=false;try{e.push(g);}catch(const std::runtime_error&){failed=true;}assert(failed&&e.failed&&e.groups==cap);for(unsigned j=0;j<3;j++)assert(e.input.v[j]==before.v[j]);
 }
 assert(sum==13945264&&source_profile::GROUP_CAP==sum&&source_profile::OP_CAP==sum+16&&source_profile::RAW_CAP==14868179);
 bool bad=false;try{source_profile::lane_cap(1);}catch(const std::runtime_error&){bad=true;}assert(bad);bad=false;try{add(UMAX,1);}catch(const std::runtime_error&){bad=true;}assert(bad);
 std::cout<<"SEVEN_LANE_LIMIT_EQUALITY_PLUS1_WRONG_ID_U64_OVERFLOW_OK\n";
}
