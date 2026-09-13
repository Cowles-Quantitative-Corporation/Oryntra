# TBA quantitative risk supervisor

Status: research-only candidate, enabled for the named TBA configurations and
disabled for the frozen Minerva baseline.

## Why this exists

The earlier Universal risk layer capped name weight and total volatility, but
it could still allow one correlated group or one marginal risk contributor to
dominate the portfolio. It also did not change its portfolio exposure in
response to completed realized portfolio volatility or drawdown.

The TBA supervisor separates forecasting from risk. A TBA signal proposes a
portfolio. The supervisor may only reduce risk and leave the difference in
cash; it never turns a weak signal into a buy and never uses future returns.

## Controls

1. Correlation clusters are connected components formed from the trailing,
   shrunk and correlation-stressed covariance estimate. A cluster above its
   weight ceiling is scaled down, leaving the difference in cash.
2. Positive marginal contributions to portfolio variance are iteratively
   capped. This prevents a volatile name from dominating merely because its
   signal rank is high.
3. Average positive correlation across active positions imposes a portfolio-
   wide ceiling during correlation shocks, even when no individual cluster
   breaches its own cap.
4. Effective-bet concentration reduces gross exposure when the active book is
   too concentrated to meet the declared diversification floor.
5. Realized-volatility targeting observes completed portfolio returns over a
   fixed window and scales only downward when realized risk exceeds target.
6. A short-versus-long volatility acceleration check reacts when risk is
   increasing faster than the ordinary volatility target can reflect.
7. Rolling expected shortfall measures the worst completed-return tail and
   reduces exposure when its average loss exceeds a target-relative budget.
8. A rolling drawdown circuit breaker smoothly reduces exposure between soft
   and hard thresholds. It uses the prior close and therefore cannot react at
   an impossible earlier price.
9. Exposure changes are rebalanced only after a declared scale step, while
   recovery is rate-limited so the book cannot snap immediately from defensive
   exposure to full risk.

All thresholds are immutable dataclass configuration values and appear in the
research report. This makes each control tunable without hiding the tested
configuration.

The current shared TBA candidate enables only the positive marginal variance
contribution cap, fixed at 10%. The other controls remain implemented,
independently switchable research mechanisms but are disabled in named TBA
defaults. The initial full bundle reduced Sharpe, and the development ablation
identified strategy-drawdown scaling as the main source of harm. Keeping code
available is not the same as promoting a failed control.

## Model scope

The supervisor is enabled by default in TBA 1, TBA 2 portfolio construction,
TBA 3, TBA 4, TBA 5, TBA 6, and the new TBA 8 risk candidate. It is disabled
in `minerva_baseline()` and in separately named Minerva feature candidates.

TBA 7 exists only as a rejected historical XGBoost study in the current
repository; there is no runnable TBA 7 configuration to modify. Any future TBA
builder must use the shared `_tba_candidate` constructor so it receives the
supervisor by default.

## Out-of-sample gate

`server/tools/run_tba2_risk_ablation.py` holds Qlib prediction scores fixed and
compares three portfolio constructions on identical years and symbols:

- native Qlib top-24 equal weight;
- the legacy Oryntra risk layer;
- the new TBA supervisor.

The primary measure is daily excess-return Sharpe. Alpha remains reported but
is secondary for this gate. The supervisor advances only if mean out-of-sample
Sharpe exceeds both comparators without worsening the minimum annual Sharpe.
Missing years are not filled or silently dropped from the declared protocol.

`server/tools/run_tba_paired_risk_study.py` performs the complementary native
TBA comparison. For TBA 1, 3, 4, 5, 6, and 8 it runs the same declared years,
symbols, signal model, costs, and execution twice. The sole paired change is
whether `RiskSupervisorConfig.enabled` is false or true. It verifies the input
fingerprints match before calculating the Sharpe and alpha deltas. TBA 2 stays
in the Qlib runner because its forecast scores are external to the native
walk-forward ridge path.

A newly frozen current-survivor OHLCV panel supported native paired testing;
the recorded results are in `docs/TBA_RISK_PAIRED_RESULTS_2026-09-13.json`.
The 10% contribution cap improved mean excess Sharpe for every tested native
TBA on eight confirmation years, but worsened minimum annual Sharpe for five
of six models. It therefore remains a private candidate. No cached external
Qlib scores were available, so no Qlib comparison or open-source victory is
claimed.

## Public research basis

- Bridgewater: balance risk across different economic environments rather
  than concentrate on one forecast.
- Citadel: separate portfolio-risk oversight, continuous monitoring and
  updated stress scenarios.
- Jane Street: enforce risk rules and allocate scarce limits dynamically under
  an overall bound.
- Two Sigma: combine forecasts with risk and trading costs, use factor/regime
  scenarios, and test changes scientifically.

These public principles informed the architecture. They do not reveal or
replicate any firm's proprietary model.
