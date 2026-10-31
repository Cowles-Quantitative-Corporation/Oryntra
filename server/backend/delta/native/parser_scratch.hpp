#pragma once
#include <sstream>
#include <string>
#include <vector>
#include <stdexcept>
// Private token storage only. No views escape into committed Group/Engine state.
class ParserScratch {
 std::istringstream input;
 std::vector<std::string> slots;
 std::size_t count=0;
public:
 void read(const std::string& line){
  count=0;input.clear();input.str(line);
  while(true){
   if(count==slots.size())slots.emplace_back();
   if(!(input>>slots[count]))break;
   ++count;
  }
 }
 std::size_t size()const noexcept{return count;}
 const std::string& operator[](std::size_t i)const{if(i>=count)throw std::out_of_range("parser token");return slots[i];}
};
