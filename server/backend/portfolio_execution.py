"""Cash-funded long-only book with next-open fills, drift, costs and capacity."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .universal_engine import UniversalConfig
from .universal_position_policy import (
    PositionDirective,
    PositionState,
    evaluate_close,
    evaluate_precommitted_daily_bar,
    open_position,
)


def simulate_book(histories: dict[str, pd.DataFrame], targets: pd.DataFrame,
                  config: UniversalConfig, cash_returns: pd.Series | None = None,
                  entry_allowed: pd.Series | None = None,
                  policy_scores: pd.DataFrame | None = None,
                  policy_volatility: pd.DataFrame | None = None) -> dict:
    names, dates = list(targets.columns), targets.index
    gates = pd.Series(True, index=dates) if entry_allowed is None else entry_allowed.reindex(dates)
    if gates.isna().any() or gates.dtype != bool:
        raise ValueError("Entry gates must cover all sessions with booleans")
    if targets.empty or dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError("Book requires unique increasing dates")
    values = targets.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < 0).any() or (values.sum(axis=1) > 1 + 1e-10).any():
        raise ValueError("Targets must be finite, long-only and cash funded")
    policy_enabled = config.position_policy.enabled
    if policy_enabled:
        if policy_scores is None or policy_volatility is None:
            raise ValueError("Position lifecycle requires causal score and volatility panels")
        if not (policy_scores.reindex(index=dates, columns=names).equals(policy_scores)
                and policy_volatility.reindex(index=dates, columns=names).equals(policy_volatility)):
            raise ValueError("Position lifecycle panels must exactly cover the execution calendar")
    fields = {}
    for field in (("Open", "High", "Low", "Close", "Volume") if policy_enabled else ("Open", "Close", "Volume")):
        try:
            frame = pd.DataFrame({name: histories[name][field].reindex(dates) for name in names})
        except KeyError as exc:
            raise ValueError("V2 execution requires Open, Close and Volume for every asset") from exc
        if not np.isfinite(frame.to_numpy()).all() or (frame < (0 if field == "Volume" else 1e-12)).any().any():
            raise ValueError(f"Invalid or missing {field}; no implicit fill of market bars")
        fields[field] = frame
    opening, closing = fields["Open"].to_numpy(), fields["Close"].to_numpy()
    highs = fields.get("High", fields["Close"]).to_numpy()
    lows = fields.get("Low", fields["Close"]).to_numpy()
    adv = fields["Close"].mul(fields["Volume"]).rolling(20, min_periods=20).median().shift(1).fillna(0).to_numpy()
    # This completed-session volume baseline is invariant for the whole book
    # calendar.  Computing it once avoids repeating an identical rolling
    # calculation for every active position on every day.
    average_volume = fields["Volume"].rolling(20, min_periods=20).median().shift(1) if policy_enabled else None
    rf = pd.Series(0.0, index=dates) if cash_returns is None else cash_returns.reindex(dates)
    if not np.isfinite(rf.to_numpy()).all() or (rf <= -1).any():
        raise ValueError("Cash returns must cover every session with finite returns greater than -1")
    shares = np.zeros(len(names))
    cash = nav = config.initial_equity
    equity, costs, turnovers, exposures, net = [], [], [], [], []
    trades, episodes, closed = [], {}, []
    policy_states: dict[str, PositionState] = {}
    pending: dict[str, PositionDirective] = {}
    policy_events = []
    held_rows = []
    skipped_notional = 0.0
    calendar_rsi = None
    calendar_extension = None
    if policy_enabled and config.position_policy.calendar_exit_enabled:
        close_frame = fields["Close"]
        change = close_frame.diff()
        gain, loss = change.clip(lower=0), -change.clip(upper=0)
        period = 14
        mean_gain = gain.ewm(com=period - 1, adjust=False, min_periods=period).mean()
        mean_loss = loss.ewm(com=period - 1, adjust=False, min_periods=period).mean()
        calendar_rsi = 100 - 100 / (1 + mean_gain.div(mean_loss.replace(0, np.nan)))
        daily_volatility = policy_volatility.reindex(index=dates, columns=names).replace(0, np.nan)
        calendar_extension = close_frame.div(close_frame.rolling(20, min_periods=20).mean()).sub(1).div(daily_volatility)

    def transact(j: int, notional: float, price: float, *, day, signal_date, reason: str) -> tuple[float, float]:
        """Cash/share ledger transaction; returns filled notional and fee."""
        nonlocal cash, skipped_notional
        if abs(notional) <= 1e-8:
            return 0.0, 0.0
        symbol = names[j]
        current = shares[j] * price
        maximum = adv[i, j] * config.participation
        actual = float(np.clip(notional, -maximum, maximum))
        actual = max(actual, -current)
        skipped_notional += abs(notional - actual)
        participation = float(abs(actual) / adv[i, j]) if adv[i, j] > 0 else 0.0
        rate = float((config.cost_bps + config.impact_bps * np.sqrt(participation)) / 10000)
        fee = float(abs(actual) * rate)
        if actual > 0:
            affordable = cash / (1 + rate)
            if actual > affordable:
                skipped_notional += actual - affordable
                actual = max(0.0, affordable)
                fee = actual * rate
        if abs(actual) <= 1e-8:
            return 0.0, 0.0
        if shares[j] < 1e-10 and actual > 0:
            episodes[symbol] = {"symbol": symbol, "entry_date": str(day.date()), "net_cashflow": 0.0, "purchased_notional": 0.0}
        episode = episodes.get(symbol)
        if episode is None:
            raise ArithmeticError("A sell requires an open position episode")
        episode["net_cashflow"] -= actual + fee
        episode["purchased_notional"] += max(0.0, actual)
        shares[j] += actual / price
        cash -= actual + fee
        trades.append({"date": str(day.date()), "signal_date": str(signal_date.date()), "symbol": symbol,
                       "notional": actual, "fees": fee, "participation": participation, "reason": reason,
                       "price": float(price)})
        if shares[j] < 1e-10:
            shares[j] = 0.0
            closed.append({**episode, "exit_date": str(day.date()), "pnl": float(episode["net_cashflow"]), "winner": bool(episode["net_cashflow"] > 0)})
            del episodes[symbol]
            policy_states.pop(symbol, None)
            pending.pop(symbol, None)
        return actual, fee

    for i, day in enumerate(dates):
        previous_nav = nav
        cash *= 1 + float(rf.iloc[i])
        start_equity = cash + shares @ opening[i]
        desired = values[i - 1] if i else np.zeros(len(names))
        # Only a changed signal target initiates a rebalance. Shares otherwise drift.
        rebalance = i > 0 and (i == 1 or not np.array_equal(values[i - 1], values[i - 2]) or gates.iloc[i - 1] != gates.iloc[i - 2])
        day_cost = day_turnover = 0.0
        policy_block_buy = np.zeros(len(names), dtype=bool)
        if policy_enabled and i:
            # Dynamic instructions were created only after yesterday's close and therefore fill at this open.
            for symbol, directive in list(pending.items()):
                j = names.index(symbol)
                if shares[j] <= 1e-10:
                    pending.pop(symbol, None)
                    continue
                fraction = directive.fraction
                actual, fee = transact(j, -shares[j] * opening[i, j] * fraction, opening[i, j], day=day,
                                       signal_date=dates[i - 1], reason=directive.reason)
                day_cost += fee
                day_turnover += abs(actual) / max(start_equity, 1e-12)
                policy_events.append({"date": str(day.date()), "symbol": symbol, "reason": directive.reason,
                                      "action": directive.action, "timing": "next_open", "price": float(opening[i, j])})
                policy_block_buy[j] = directive.action == "exit"
                pending.pop(symbol, None)
            # Existing stop/limit plans are evaluated against this completed daily bar. A collision remains stop-first.
            for symbol, state in list(policy_states.items()):
                j = names.index(symbol)
                if shares[j] <= 1e-10:
                    continue
                directive = evaluate_precommitted_daily_bar(state, {"Open": opening[i, j], "High": highs[i, j], "Low": lows[i, j]}, config.position_policy)
                if directive.action == "hold":
                    continue
                if directive.reason == "hard_stop_gap":
                    price = opening[i, j]
                elif directive.action == "exit":
                    price = state.current_stop
                else:
                    price = state.first_take_profit
                actual, fee = transact(j, -shares[j] * price * directive.fraction, price, day=day,
                                       signal_date=dates[i - 1], reason=directive.reason)
                day_cost += fee
                day_turnover += abs(actual) / max(start_equity, 1e-12)
                policy_events.append({"date": str(day.date()), "symbol": symbol, "reason": directive.reason,
                                      "action": directive.action, "timing": "precommitted_daily_bar", "price": float(price)})
                policy_block_buy[j] = directive.action == "exit"
                if directive.action == "trim" and symbol in policy_states:
                    policy_states[symbol] = directive.state
        if rebalance:
            current = shares * opening[i]
            delta = desired * start_equity - current
            if not gates.iloc[i - 1]:
                delta = np.minimum(delta, 0.0)
            delta[policy_block_buy] = np.minimum(delta[policy_block_buy], 0.0)
            below_buffer = np.abs(delta) < config.trade_buffer * start_equity
            # Exits are never suppressed by the no-trade band.
            delta[below_buffer & (desired > 0)] = 0
            limit = adv[i] * config.participation
            actual = np.clip(delta, -limit, limit)
            actual = np.maximum(actual, -current)
            skipped_notional += float(np.abs(delta - actual).sum())
            # Sell before buying to preserve a cash-funded book; policy has already consumed any earlier intraday capacity.
            for j in np.flatnonzero(actual < -1e-8):
                filled, fee = transact(j, float(actual[j]), opening[i, j], day=day, signal_date=dates[i - 1], reason="signal_rebalance")
                day_cost += fee
                day_turnover += abs(filled) / max(start_equity, 1e-12)
            for j in np.flatnonzero(actual > 1e-8):
                before = shares[j]
                filled, fee = transact(j, float(actual[j]), opening[i, j], day=day, signal_date=dates[i - 1], reason="signal_rebalance")
                day_cost += fee
                day_turnover += abs(filled) / max(start_equity, 1e-12)
                if policy_enabled and before < 1e-10 and shares[j] > 1e-10:
                    score = float(policy_scores.iloc[i - 1, j])
                    vol = float(policy_volatility.iloc[i - 1, j])
                    policy_states[names[j]] = open_position(names[j], str(day.date()), opening[i, j], score, vol, config.position_policy)
            if cash < -1e-6:
                raise ArithmeticError("Cash-funded book borrowed unexpectedly")
            cash = max(0.0, cash)
        nav = float(cash + shares @ closing[i])
        if nav <= 0 or not np.isfinite(nav):
            raise ArithmeticError("Book NAV is invalid")
        equity.append(nav)
        net.append(nav / previous_nav - 1)
        costs.append(day_cost / previous_nav)
        turnovers.append(day_turnover)
        weights = shares * closing[i] / nav
        held_rows.append(weights.copy())
        exposures.append(float(weights.sum()))
        if policy_enabled:
            if i:
                for symbol, state in list(policy_states.items()):
                    j = names.index(symbol)
                    if shares[j] <= 1e-10:
                        continue
                    directive = evaluate_close(
                        state,
                        {"Open": opening[i, j], "High": highs[i, j], "Low": lows[i, j], "Close": closing[i, j], "Volume": fields["Volume"].iloc[i, j]},
                        prior_close=closing[i - 1, j], signal_score=float(policy_scores.iloc[i, j]),
                        daily_volatility=float(policy_volatility.iloc[i, j]), average_volume=float(average_volume.iloc[i, j]) if pd.notna(average_volume.iloc[i, j]) else 0.0,
                        config=config.position_policy, calendar_date=str(day.date()),
                        calendar_rsi=float(calendar_rsi.iloc[i, j]) if calendar_rsi is not None and pd.notna(calendar_rsi.iloc[i, j]) else None,
                        calendar_extension=float(calendar_extension.iloc[i, j]) if calendar_extension is not None and pd.notna(calendar_extension.iloc[i, j]) else None,
                    )
                    policy_states[symbol] = directive.state
                    if directive.action != "hold":
                        pending[symbol] = directive
    return {"net": pd.Series(net, index=dates), "equity": pd.Series(equity, index=dates),
            "costs": pd.Series(costs, index=dates), "turnover": pd.Series(turnovers, index=dates),
            "held": pd.DataFrame(held_rows, index=dates, columns=names), "fills": trades,
            "closed_episodes": closed, "open_episodes": list(episodes.values()),
            "cash": cash, "unfilled_notional": skipped_notional,
            "win_rate_pct": 100 * sum(row["winner"] for row in closed) / len(closed) if closed else None,
            "position_policy_events": policy_events,
            "position_policy_open_states": [{"symbol": symbol, "held_sessions": state.held_sessions,
                                               "current_stop": state.current_stop, "first_take_profit_taken": state.first_take_profit_taken}
                                              for symbol, state in policy_states.items()]}
