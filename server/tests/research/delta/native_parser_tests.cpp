#include "parser_scratch.hpp"
#include <cassert>
#include <iostream>
std::vector<std::string> legacy(const std::string& s){std::istringstream in(s);std::vector<std::string> t;std::string w;while(in>>w)t.push_back(w);return t;}
int main(){ParserScratch p;
 const std::vector<std::string> cases={""," ","\t","I 1 source session 7","P\t2  0 x","001 18446744073709551616 -1 +1 nan","F 3","x",std::string(16384,'a')," \t ","S 4 0 1"};
 for(unsigned repeat=0;repeat<3;repeat++)for(const auto& s:cases){p.read(s);auto old=legacy(s);assert(p.size()==old.size());for(std::size_t i=0;i<old.size();i++)assert(p[i]==old[i]);bool threw=false;try{(void)p[p.size()];}catch(const std::out_of_range&){threw=true;}assert(threw);}
 std::string many;for(unsigned i=0;i<8192;i++)many+="x ";p.read(many);assert(p.size()==8192);p.read("F 2");assert(p.size()==2&&p[0]=="F"&&p[1]=="2");
 // Committed value owns its text despite scratch reuse; stale slots inaccessible.
 std::string owned=p[0];p.read("completely different words");assert(owned=="F");p.read("");assert(p.size()==0);
 std::cout<<"PARSER_TOKEN_PARITY_BOUNDARIES_OWNERSHIP_OK\n";
}
