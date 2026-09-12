"""Causal, research-only Universal V2 position-management policy.

The policy is disabled by default but, when explicitly enabled, is executed by
the V2 cash/share research ledger. It does not claim profitable defaults.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Literal


PolicyAction = Literal["hold", "trim", "exit"]
ExecutionTiming = Literal["next_open", "precommitted_daily_bar"]


@dataclass(frozen=True)
class PositionPolicyConfig:
    """Bounded research parameters for a long-only position lifecycle."""

    enabled: bool = False
    initial_stop_volatility: float = 2.5
    trailing_stop_volatility: float = 3.0
    first_take_profit_r: float = 2.0
    first_take_profit_fraction: float = .50
    maximum_holding_sessions: int = 63
    signal_exit_score: float = 0.0
    spike_return_volatility: float = 3.0
    spike_relative_volume: float = 2.0
    spike_trim_fraction: float = .25
    calendar_exit_enabled: bool = False
    calendar_exit_start_month: int = 12
    calendar_exit_start_day: int = 10
    calendar_exit_rsi_threshold: float = 70.0
    calendar_exit_extension_volatility: float = 1.5
    calendar_exit_fraction: float = 1.0

    def __post_init__(self) -> None:
        values = asdict(self)
        for name, value in values.items():
            if name not in {"enabled", "calendar_exit_enabled"} and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if not (0.25 <= self.initial_stop_volatility <= 10):
            raise ValueError("initial_stop_volatility must be from 0.25 through 10")
        if not (0.25 <= self.trailing_stop_volatility <= 15):
            raise ValueError("trailing_stop_volatility must be from 0.25 through 15")
        if not (0.5 <= self.first_take_profit_r <= 10):
            raise ValueError("first_take_profit_r must be from 0.5 through 10")
        if not (0 < self.first_take_profit_fraction < 1 and 1 <= self.maximum_holding_sessions <= 756):
            raise ValueError("Invalid take-profit fraction or maximum holding period")
        if not (-1 <= self.signal_exit_score <= 1 and 1 <= self.spike_return_volatility <= 12):
            raise ValueError("Invalid signal-exit or spike-return threshold")
        if not (1 <= self.spike_relative_volume <= 20 and 0 < self.spike_trim_fraction < 1):
            raise ValueError("Invalid spike volume threshold or trim fraction")
        if not (1 <= self.calendar_exit_start_month <= 12 and 1 <= self.calendar_exit_start_day <= 31):
            raise ValueError("Calendar exit start must be a valid month and day")
        if not (50 <= self.calendar_exit_rsi_threshold <= 95 and 0 <= self.calendar_exit_extension_volatility <= 12):
            raise ValueError("Invalid calendar RSI or extension threshold")
        if not (0 < self.calendar_exit_fraction <= 1):
            raise ValueError("Calendar exit fraction must be within (0, 1]")


@dataclass(frozen=True)
class PositionState:
    """Persistable state for one simulated long position, never a broker order."""

    symbol: str
    entry_date: str
    entry_price: float
    entry_score: float
    entry_daily_volatility: float
    original_stop: float
    current_stop: float
    first_take_profit: float
    high_water_mark: float
    held_sessions: int = 0
    first_take_profit_taken: bool = False


@dataclass(frozen=True)
class PositionDirective:
    """A causal instruction for the next ledger phase, with an audit reason."""

    action: PolicyAction
    fraction: float
    reason: str
    timing: ExecutionTiming
    state: PositionState
    note: str


def open_position(symbol: str, date: str, price: float, score: float, daily_volatility: float,
                  config: PositionPolicyConfig = PositionPolicyConfig()) -> PositionState:
    """Create a position's immutable entry risk plan from information known at entry."""
    if not symbol or not math.isfinite(price) or price <= 0:
        raise ValueError("A position needs a symbol and positive finite entry price")
    if not (-1 <= score <= 1) or not math.isfinite(daily_volatility) or daily_volatility <= 0:
        raise ValueError("A position needs bounded score and positive daily volatility")
    distance = price * daily_volatility * config.initial_stop_volatility
    original_stop = max(0.0000001, price - distance)
    reward = price - original_stop
    return PositionState(
        symbol=symbol, entry_date=str(date), entry_price=float(price), entry_score=float(score),
        entry_daily_volatility=float(daily_volatility), original_stop=original_stop,
        current_stop=original_stop, first_take_profit=price + config.first_take_profit_r * reward,
        high_water_mark=float(price),
    )


def evaluate_precommitted_daily_bar(state: PositionState, bar: dict[str, float],
                                    config: PositionPolicyConfig) -> PositionDirective:
    """Evaluate an already-known stop/limit plan on the next daily bar.

    A same-day stop/target collision uses stop-first.  This does not infer an
    intraday path and therefore never claims an exit at the bar high.
    """
    opening, high, low = _bar_values(bar)
    if not config.enabled:
        return _hold(state, "policy_disabled")
    if opening <= state.current_stop:
        return _exit(state, "hard_stop_gap", "precommitted_daily_bar")
    stop_hit, target_hit = low <= state.current_stop, high >= state.first_take_profit and not state.first_take_profit_taken
    if stop_hit:
        return _exit(state, "hard_stop" if not target_hit else "hard_stop_ambiguous_with_target", "precommitted_daily_bar")
    if target_hit:
        updated = PositionState(**{**asdict(state), "first_take_profit_taken": True})
        return PositionDirective("trim", config.first_take_profit_fraction, "first_take_profit", "precommitted_daily_bar", updated,
                                 "A predeclared limit may fill at its level; the daily high itself is never used as a fill price.")
    return _hold(state, "no_precommitted_exit")


def evaluate_close(state: PositionState, bar: dict[str, float], *, prior_close: float,
                   signal_score: float, daily_volatility: float, average_volume: float,
                   config: PositionPolicyConfig, calendar_date: str | None = None,
                   calendar_rsi: float | None = None, calendar_extension: float | None = None) -> PositionDirective:
    """Update stops at the close and create a next-open-only dynamic directive."""
    _, high, _, close, volume = _bar_values(bar, include_close_volume=True)
    if not (math.isfinite(prior_close) and prior_close > 0 and -1 <= signal_score <= 1 and math.isfinite(daily_volatility) and daily_volatility > 0 and math.isfinite(average_volume) and average_volume >= 0):
        raise ValueError("Close evaluation needs finite completed-session inputs")
    high_water = max(state.high_water_mark, high, close)
    # This monotone max is an invariant: risk is never widened after entry.
    trailing = max(0.0000001, high_water - close * daily_volatility * config.trailing_stop_volatility)
    updated = PositionState(**{**asdict(state), "high_water_mark": high_water,
                               "current_stop": max(state.current_stop, trailing),
                               "held_sessions": state.held_sessions + 1})
    if not config.enabled:
        return _hold(updated, "policy_disabled")
    if updated.held_sessions >= config.maximum_holding_sessions:
        return _exit(updated, "time_exit", "next_open")
    if signal_score <= config.signal_exit_score:
        return _exit(updated, "signal_decay", "next_open")
    if config.calendar_exit_enabled:
        if calendar_date is None or calendar_rsi is None or calendar_extension is None:
            raise ValueError("Calendar exit requires completed date, RSI and extension inputs")
        try:
            month, day = int(str(calendar_date)[5:7]), int(str(calendar_date)[8:10])
        except (TypeError, ValueError, IndexError) as exc:
            raise ValueError("Calendar exit requires an ISO calendar date") from exc
        if not (math.isfinite(calendar_rsi) and math.isfinite(calendar_extension)):
            raise ValueError("Calendar exit requires finite RSI and extension inputs")
        in_window = (month, day) >= (config.calendar_exit_start_month, config.calendar_exit_start_day)
        overbought = calendar_rsi >= config.calendar_exit_rsi_threshold
        extended = calendar_extension >= config.calendar_exit_extension_volatility
        if in_window and overbought and extended:
            if config.calendar_exit_fraction >= 1:
                return _exit(updated, "calendar_overbought_year_end", "next_open")
            return PositionDirective("trim", config.calendar_exit_fraction, "calendar_overbought_year_end", "next_open", updated,
                                     "A calendar date, completed RSI and completed extension rule jointly requested a next-open de-risking trim.")
    standardized_return = (close / prior_close - 1.0) / daily_volatility
    relative_volume = volume / average_volume if average_volume > 0 else 0.0
    if standardized_return >= config.spike_return_volatility and relative_volume >= config.spike_relative_volume:
        return PositionDirective("trim", config.spike_trim_fraction, "spike_exhaustion", "next_open", updated,
                                 "Detected only after the completed close; the trim must be modeled at the next open.")
    return _hold(updated, "trailing_stop_updated")


def policy_contract(config: PositionPolicyConfig = PositionPolicyConfig()) -> dict[str, Any]:
    """Machine-readable readiness contract for UI, reports and future tuning."""
    return {
        "id": "universal_position_policy_v1",
        "status": "disabled_by_default" if not config.enabled else "active_research_ledger",
        "configuration": asdict(config),
        "state_fields": list(PositionState.__dataclass_fields__),
        "event_order": ["precommitted stop/target on next daily bar", "completed-close state update", "next-open dynamic directive"],
        "invariants": [
            "No position begins without an entry risk plan.",
            "A long stop can tighten but never widen after entry.",
            "Daily-bar ambiguity is stop-first; no exit is credited at the bar high.",
            "Spike, time and signal-decay directives are created after close and execute no earlier than next open.",
            "Calendar de-risking uses a fixed known calendar date plus completed RSI and price-extension inputs; it never uses the next year's return.",
            "The policy is research-only and does not submit broker orders.",
        ],
        "integration_status": "Enabled policies are executed by the research ledger with explicit stop, target, next-open, cost and capacity treatment. They never send broker orders.",
    }


def _bar_values(bar: dict[str, float], include_close_volume: bool = False):
    names = ("Open", "High", "Low", "Close", "Volume") if include_close_volume else ("Open", "High", "Low")
    try:
        values = tuple(float(bar[name]) for name in names)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Position policy needs finite OHLCV fields") from exc
    if not all(math.isfinite(value) for value in values) or values[0] <= 0 or values[1] <= 0 or values[2] <= 0:
        raise ValueError("Position policy needs positive finite prices")
    ohlc = values[:4] if include_close_volume else values[:3]
    # Provider-adjusted OHLC fields can differ by a few floating-point ulps when,
    # for example, Close is exactly the daily Low.  Preserve the range invariant
    # while avoiding a false rejection from representational noise.
    tolerance = 1e-12 * max(1.0, *(abs(value) for value in ohlc))
    if values[2] > min(ohlc) + tolerance or values[1] < max(ohlc) - tolerance or (include_close_volume and values[4] < 0):
        raise ValueError("Position policy received invalid OHLCV bounds")
    return values


def _hold(state: PositionState, reason: str) -> PositionDirective:
    return PositionDirective("hold", 0.0, reason, "next_open", state, "No position-size change is requested.")


def _exit(state: PositionState, reason: str, timing: ExecutionTiming) -> PositionDirective:
    return PositionDirective("exit", 1.0, reason, timing, state, "Exit price is resolved by the ledger's explicit timing and fill rules.")
