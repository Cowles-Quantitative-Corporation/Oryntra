#pragma once
#include <cstdint>
#include <stdexcept>
namespace source_profile {
constexpr std::uint64_t GROUP_CAP=13945264ULL;
constexpr std::uint64_t RAW_CAP=14868179ULL;
constexpr std::uint64_t OP_CAP=13945280ULL;
constexpr std::uint64_t INPUT_CAP=3134071984ULL;
constexpr std::uint64_t OUTPUT_CAP=4183809614ULL;
constexpr std::uint64_t OPEN=1775050200000000000ULL;
constexpr std::uint64_t CLOSE=1775050260000000000ULL;
constexpr std::uint64_t MID=1775050230000000000ULL;
inline std::uint64_t lane_cap(std::uint64_t id){switch(id){case 38:return 1991937ULL;case 853:return 3180893ULL;case 7152:return 4513251ULL;case 9155:return 358445ULL;case 10888:return 1433287ULL;case 17448:return 597398ULL;case 17709:return 1870053ULL;default:throw std::runtime_error("PROFILE_INSTRUMENT");}}
}
