"""Reproducible frozen-policy diagnostics; no optimizer, network or model refit.

Clock: first_return_date is t; entry is close t-1; feature cutoff close t-2.
Every strategy owns its drift, costs and cash. Original target policy is frozen,
so these are not fully reoptimized model backtests.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import platform
import sys
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
RF_ANNUAL = 0.04
RF_DAILY = (1 + RF_ANNUAL) ** (1 / 252) - 1
VARIANTS = ("base", "fixed75", "cap20", "vol15", "cap20_vol15")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_checkpoint_reader():
    path = ROOT / "work/qpo/QPO_Zero_Cash_1000/source_changes/replay_checkpoint.py"
    spec = importlib.util.spec_from_file_location("preserved_checkpoint", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_inputs():
    td = ROOT / "work/ten-year"
    prices = pd.read_csv(td / "inputs/final_prices.csv.gz", index_col=0, parse_dates=True)
    spy = pd.read_csv(td / "inputs/final_spy.csv.gz", index_col=0, parse_dates=True)["close"]
    returns = prices.pct_change(fill_method=None)
    idx = returns.loc["2016-09-09":"2026-09-18"].index
    assert len(idx) == 2520 and len(prices.columns) == 183
    assert returns.loc[idx].notna().all().all()
    reader = load_checkpoint_reader()
    qr = reader._read_document(ROOT / "work/qpo/QPO_Zero_Cash_1000/evaluation/result.json.gz")
    all_targets = {}
    all_clocks = {}
    for name in ("qpo_corrected", "pregamma_budget", "pregamma_expanded"):
        if name == "qpo_corrected":
            rows = qr["rebalances"]
            holding_key = "qoptima_target_weights"
        else:
            arm = name.split("_", 1)[1]
            rows = pd.read_csv(td / f"evaluations/{arm}/decisions_verified.csv").to_dict("records")
            holding_key = "holdings_json"
        targets, clocks = [], []
        for row in rows:
            date = pd.Timestamp(row["decision_date"])
            loc = prices.index.get_loc(date)
            cutoff, entry = prices.index[loc - 2], prices.index[loc - 1]
            if name == "qpo_corrected":
                assert cutoff == pd.Timestamp(row["feature_cutoff"])
                assert entry == pd.Timestamp(row["information_cutoff"])
                assert int(row["decision_lag_sessions"]) == 1
                holdings = row[holding_key]
            else:
                holdings = json.loads(row[holding_key])
            target = pd.Series(0.0, index=prices.columns)
            seen = set()
            for holding in holdings:
                symbol, value = holding["symbol"], float(holding["weight"])
                assert symbol not in seen and symbol in prices.columns
                seen.add(symbol)
                target[symbol] = value
            assert np.isfinite(target).all() and (target >= 0).all()
            assert target.sum() <= 1 + 1e-9
            targets.append(target.to_numpy())
            clocks.append({"first_return_date": date, "entry_close": entry, "feature_cutoff": cutoff})
        dates = pd.DatetimeIndex([r["first_return_date"] for r in clocks])
        assert dates.equals(idx[::21]) and len(dates) == 120
        all_targets[name] = pd.DataFrame(targets, index=dates, columns=prices.columns)
        all_clocks[name] = pd.DataFrame(clocks).set_index("first_return_date")
    return returns, spy.pct_change(fill_method=None), idx, all_targets, all_clocks


def projected_volatility(history: pd.DataFrame, weights: np.ndarray, window: int) -> float:
    weights = np.asarray(weights, dtype=float)
    if not np.isfinite(weights).all() or np.any(weights < 0):
        raise ValueError("Weights must be finite and nonnegative")
    active = weights > 0
    if not active.any():
        return 0.0
    panel = history.iloc[-window:, active]
    if len(panel) != window or not np.isfinite(panel.to_numpy()).all():
        raise ValueError("Insufficient finite point-in-time history")
    projected = panel.to_numpy() @ weights[active]
    return float(np.std(projected, ddof=1) * np.sqrt(252))


def transform_target(original: np.ndarray, history: pd.DataFrame, variant: str):
    if variant not in VARIANTS:
        raise ValueError("Unknown preregistered variant")
    target = np.asarray(original, dtype=float).copy()
    if np.any(target < 0) or not np.isfinite(target).all():
        raise ValueError("Invalid target")
    if variant == "fixed75":
        target *= 0.75
    if variant in ("cap20", "cap20_vol15"):
        target = np.minimum(target, 0.20)
    # Diagnostic volatility is computed for every variant with the same strict
    # support rule; the combined policy explicitly recomputes after clipping.
    v63 = projected_volatility(history, target, 63)
    v252 = projected_volatility(history, target, 252)
    scale = 1.0
    if variant in ("vol15", "cap20_vol15"):
        vol = max(v63, v252)
        scale = min(1.0, 0.15 / vol) if vol > 0 else 1.0
        target *= scale
    if np.any(target > np.asarray(original) + 1e-12):
        raise AssertionError("Overlay increased an original risky weight")
    return target, {"sigma63_before_scale": v63, "sigma252_before_scale": v252,
                    "uniform_vol_scale": scale, "target_gross": float(target.sum()),
                    "target_max_name": float(target.max(initial=0.0))}


def create_variant_targets(original, clocks, returns, variant):
    out, audit = [], []
    for date, row in original.iterrows():
        cutoff = clocks.loc[date, "feature_cutoff"]
        if cutoff >= clocks.loc[date, "entry_close"] or clocks.loc[date, "entry_close"] >= date:
            raise ValueError("Invalid decision/entry clock")
        history = returns.loc[:cutoff].tail(252)
        target, diagnostics = transform_target(row.to_numpy(), history, variant)
        out.append(target)
        audit.append({"first_return_date": date, "feature_cutoff": cutoff,
                      "entry_close": clocks.loc[date, "entry_close"],
                      "history_last_date": history.index[-1], "history_rows": len(history),
                      "original_target_gross": float(row.sum()), **diagnostics})
    return pd.DataFrame(out, index=original.index, columns=original.columns), pd.DataFrame(audit)


@dataclass
class Replay:
    ledger: pd.DataFrame
    holdings: pd.DataFrame
    contributions: pd.Series
    decisions: pd.DataFrame


def exact_dollar_fee_fraction(target, prior, rate):
    """Solve c=k*sum(abs((1-c)*target-prior)) without changing target weights.

    Both gross bounds are sufficient for a unique solvent root on[0,1).
    No fees, spreads or order-size terms beyond this proportional model exist.
    """
    w,p=np.asarray(target,float),np.asarray(prior,float)
    k=float(rate)
    if w.ndim!=1 or w.shape!=p.shape or not np.isfinite(w).all() or not np.isfinite(p).all():
        raise ValueError('Finite aligned1D weights required for exact-dollar fees')
    if np.any(w<0) or np.any(p<0) or not np.isfinite(k) or k<0:
        raise ValueError('Nonnegative finite weights and fee rate required')
    if k*float(w.sum())>=1 or k*float(p.sum())>=1:
        raise ValueError('Unsupported potentially insolvent fee equation')
    if k==0 or np.array_equal(w,p):
        return 0.0
    low,high=0.0,1.0
    for _ in range(80):
        c=(low+high)/2
        residual=c-k*float(np.abs((1-c)*w-p).sum())
        if residual>0:
            high=c
        else:
            low=c
    c=(low+high)/2
    if c<0 or c>=1 or abs(c-k*float(np.abs((1-c)*w-p).sum()))>1e-13:
        raise ArithmeticError('Exact-dollar fee root failed verification')
    return c


def replay(targets: pd.DataFrame, daily_returns: pd.DataFrame, *, cost_bps=5.0,
           lending_annual=0.0, borrowing_annual=0.04, initial=1000.0,
           fee_convention='legacy_pre_cost_weight_L1') -> Replay:
    """Self-financed recurrence with an explicit proportional-fee convention.

    Target weights are post-cost weights. The cost scales the entire book,
    including signed cash, and is charged once before that entry's first return.
    The primary legacy convention charges k*sum(abs(target-prior))*pretradeNAV.
    That is not generally k times the actual post-fee dollar trade; the separate
    exact_dollar_L1 sensitivity solves its self-consistent fee equation.
    """
    if not targets.columns.equals(daily_returns.columns):
        raise ValueError("Column identity/order mismatch")
    if not daily_returns.index.is_monotonic_increasing or not daily_returns.index.is_unique:
        raise ValueError("Return dates must be unique and increasing")
    if not targets.index.is_monotonic_increasing or not targets.index.is_unique:
        raise ValueError("Target dates must be unique and increasing")
    if daily_returns.index[0] != targets.index[0] or not targets.index.isin(daily_returns.index).all():
        raise ValueError("Targets must begin at first return and lie in return calendar")
    x = daily_returns.to_numpy(dtype=float)
    if not np.isfinite(x).all() or np.any(x < -1):
        raise ValueError("Missing or impossible returns; no zero filling")
    if not np.isfinite(cost_bps) or cost_bps < 0:
        raise ValueError("Invalid trading cost")
    if fee_convention not in ('legacy_pre_cost_weight_L1','exact_dollar_L1'):
        raise ValueError('Unknown fee convention')
    if min(lending_annual, borrowing_annual) <= -1:
        raise ValueError("Invalid financing rate")
    lend, borrow = (1+lending_annual)**(1/252)-1, (1+borrowing_annual)**(1/252)-1
    target_lookup = {d: row.to_numpy(dtype=float) for d, row in targets.iterrows()}
    equity, cash = float(initial), float(initial)
    assets = np.zeros(len(targets.columns))
    contribution = np.zeros(len(targets.columns))
    records, holding_rows, decision_rows = [], [], []
    total_cost = total_interest = 0.0
    for day, ret in zip(daily_returns.index, x):
        begin = equity
        cost = turnover = 0.0
        if day in target_lookup:
            target = target_lookup[day]
            if not np.isfinite(target).all() or np.any(target < 0):
                raise ValueError("Invalid target weights")
            prior = assets / equity
            turnover = float(np.abs(target - prior).sum())
            cost_fraction = (cost_bps * 1e-4 * turnover if fee_convention=='legacy_pre_cost_weight_L1'
                             else exact_dollar_fee_fraction(target,prior,cost_bps*1e-4))
            if cost_fraction >= 1:
                raise ValueError("Trading cost exhausted equity")
            cost = equity * cost_fraction
            total_cost += cost
            after_cost = equity - cost
            assets = target * after_cost
            cash = (1 - float(target.sum())) * after_cost
            decision_rows.append({"first_return_date": day, "gross_target": float(target.sum()),
                                  "max_name_target": float(target.max(initial=0)),
                                  "turnover_L1": turnover, "cost_usd": cost,
                                  "actual_dollar_trade_L1": float(np.abs(target*after_cost-prior*equity).sum()),
                                  "fee_convention": fee_convention,
                                  "equity_before_cost_usd": equity})
        start_gross = float(assets.sum()/(equity-cost))
        start_max_name = float(assets.max(initial=0)/(equity-cost))
        gain = assets * ret
        contribution += gain
        assets += gain
        interest = cash * (lend if cash >= 0 else borrow)
        cash += interest
        total_interest += interest
        equity = float(assets.sum()+cash)
        if not np.isfinite(equity) or equity <= 0:
            raise ValueError("Nonpositive or invalid book equity")
        records.append({"date": day, "net_return": equity/begin-1, "equity_usd": equity,
                        "cash_usd": cash, "stock_value_usd": float(assets.sum()),
                        "cash_interest_usd": interest, "cost_usd": cost, "turnover_L1": turnover,
                        "start_gross": start_gross, "end_gross": float(assets.sum()/equity),
                        "start_max_name": start_max_name, "end_max_name": float(assets.max(initial=0)/equity)})
        holding_rows.append(assets/equity)
    ledger = pd.DataFrame(records).set_index("date")
    contributions = pd.Series(contribution, index=targets.columns)
    contributions.loc["CASH_INTEREST"] = total_interest
    contributions.loc["TRANSACTION_COST"] = -total_cost
    if abs(float(contributions.sum()) - (equity-initial)) > 1e-8:
        raise AssertionError("Dollar attribution does not reconcile")
    return Replay(ledger, pd.DataFrame(holding_rows, index=ledger.index, columns=targets.columns),
                  contributions, pd.DataFrame(decision_rows))


def metrics(series, spy=None):
    x = np.asarray(series, dtype=float)
    if not len(x) or not np.isfinite(x).all() or np.any(x <= -1):
        raise ValueError("Invalid return series")
    wealth = np.r_[1.0, np.cumprod(1+x)]
    excess = x - RF_DAILY
    volatility = float(np.std(x, ddof=0))
    result = {"sessions": len(x), "ending_usd": float(1000*wealth[-1]),
              "CAGR_pct": float(100*((wealth[-1])**(252/len(x))-1)),
              "Sharpe": float(excess.mean()/volatility*np.sqrt(252)) if volatility > 1e-12 else None,
              "MDD_pct": float(100*np.min(wealth/np.maximum.accumulate(wealth)-1)),
              "ann_vol_pct": float(100*volatility*np.sqrt(252))}
    if spy is not None:
        market = np.asarray(spy, dtype=float)-RF_DAILY
        if len(market) != len(x) or not np.isfinite(market).all():
            raise ValueError("Unmatched benchmark")
        centered = market-market.mean()
        beta = float(np.dot(centered, excess-excess.mean()) / np.dot(centered, centered))
        result.update({"SPY_beta": beta, "SPY_alpha_pct": float(25200*(excess.mean()-beta*market.mean()))})
    return result


def annual_values(ledger):
    start = ledger.index[0]
    output = [{"period": "Start", "date": str(start.date()), "timing": "before_first_return", "value_usd": 1000.0,
               "period_gain_usd": 0.0, "period_return_pct": 0.0}]
    previous = 1000.0
    last = None
    for year in range(1, 100):
        eligible = ledger.index[ledger.index >= start+pd.DateOffset(years=year)]
        if not len(eligible):
            break
        date = eligible[0]
        value = float(ledger.loc[date, "equity_usd"])
        output.append({"period": f"Year {year}", "date": str(date.date()), "timing": "session_close",
                       "value_usd": value, "period_gain_usd": value-previous, "period_return_pct": 100*(value/previous-1)})
        previous, last = value, date
    if last != ledger.index[-1]:
        value = float(ledger.equity_usd.iloc[-1])
        output.append({"period": "Final partial year", "date": str(ledger.index[-1].date()), "timing": "session_close",
                       "value_usd": value, "period_gain_usd": value-previous, "period_return_pct": 100*(value/previous-1)})
    return pd.DataFrame(output)


def baseline_verification(returns, idx, targets, clocks):
    output = []
    for model, target in targets.items():
        cash = 0.0 if model == "qpo_corrected" else 0.04
        book = replay(target, returns.loc[idx], lending_annual=cash)
        if model == "qpo_corrected":
            path = ROOT / "work/qpo/QPO_Zero_Cash_1000/evaluation/daily_returns.csv"
        else:
            path = ROOT / f"work/ten-year/evaluations/{model.split('_',1)[1]}/daily_returns.csv"
        saved = pd.read_csv(path, index_col=0, parse_dates=True).iloc[:,0]
        assert saved.index.equals(idx)
        error = float(np.max(np.abs(saved-book.ledger.net_return)))
        if error > 1e-12:
            raise AssertionError((model, "Original baseline does not reconcile", error))
        output.append({"model": model, "original_cash_yield": cash, "return_max_absolute_error": error,
                       "verified": True, **metrics(book.ledger.net_return)})
    return output


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    returns, spy, idx, targets, clocks = load_inputs()
    verified = baseline_verification(returns, idx, targets, clocks)
    (out/"BASELINE_RECONCILIATION.json").write_text(json.dumps(verified, indent=2))
    print(json.dumps(verified, indent=2), flush=True)
    if args.verify_only:
        return
    all_metrics = []
    for model, original in targets.items():
        for variant in VARIANTS:
            target, audit = create_variant_targets(original, clocks[model], returns, variant)
            audit.to_csv(out/f"{model}__{variant}__target_audit.csv", index=False)
            target.to_csv(out/f"{model}__{variant}__targets.csv.gz", compression="gzip")
            for cost in (5, 10, 25):
                prefix = f"{model}__{variant}__cost{cost}"
                book = replay(target, returns.loc[idx], cost_bps=cost)
                book.ledger.to_csv(out/f"{prefix}__ledger.csv.gz", compression="gzip")
                book.decisions.to_csv(out/f"{prefix}__decisions.csv", index=False)
                book.contributions.sort_values(ascending=False).to_csv(out/f"{prefix}__contributions.csv")
                annual_values(book.ledger).to_csv(out/f"{prefix}__annual.csv", index=False)
                if cost == 5:
                    book.holdings.to_csv(out/f"{prefix}__holdings.csv.gz", compression="gzip")
                result = {"model": model, "variant": variant, "cost_bps": cost,
                          **metrics(book.ledger.net_return, spy.loc[idx]),
                          "mean_start_gross": float(book.ledger.start_gross.mean()),
                          "max_drifted_name": float(book.ledger.end_max_name.max()),
                          "total_cost_usd": float(book.ledger.cost_usd.sum()),
                          "cash_interest_usd": float(book.ledger.cash_interest_usd.sum()),
                          "turnover_L1_total": float(book.decisions.turnover_L1.sum())}
                all_metrics.append(result)
                # A causal same-gross SPY control. This is an attribution
                # comparison, not a factor-neutral measure of timing alpha.
                gross_target = target.sum(axis=1).to_frame("SPY")
                control = replay(gross_target, spy.loc[idx].to_frame("SPY"), cost_bps=cost)
                control.ledger.to_csv(out/f"{prefix}__samegross_spy.csv.gz", compression="gzip")
                all_metrics.append({"model": model, "variant": variant+"_samegross_SPY", "cost_bps": cost,
                                    **metrics(control.ledger.net_return, spy.loc[idx]),
                                    "mean_start_gross": float(control.ledger.start_gross.mean()),
                                    "total_cost_usd": float(control.ledger.cost_usd.sum()),
                                    "cash_interest_usd": float(control.ledger.cash_interest_usd.sum()),
                                    "turnover_L1_total": float(control.decisions.turnover_L1.sum())})
                print(json.dumps(result), flush=True)
    for cost in (5, 10, 25):
        target = pd.DataFrame({"SPY": [1.0]}, index=idx[:1])
        control = replay(target, spy.loc[idx].to_frame("SPY"), cost_bps=cost)
        control.ledger.to_csv(out/f"SPY_buyhold__cost{cost}__ledger.csv.gz", compression="gzip")
        all_metrics.append({"model": "SPY", "variant": "buy_hold", "cost_bps": cost,
                            **metrics(control.ledger.net_return, spy.loc[idx])})
    pd.DataFrame(all_metrics).to_csv(out/"ALL_METRICS.csv", index=False)
    (out/"RUN_METADATA.json").write_text(json.dumps({"protocol_sha256": sha256(ROOT/"PROTOCOL.json"),
        "source_sha256": sha256(__file__), "python": sys.version, "platform": platform.platform(),
        "numpy": np.__version__, "pandas": pd.__version__, "variants": list(VARIANTS),
        "exact_reoptimized_models": False, "all_original_baselines_reconciled": True,
        "cash_zero_variants": True, "no_original_risk_gate_certification": True}, indent=2))


if __name__ == "__main__":
    run()
