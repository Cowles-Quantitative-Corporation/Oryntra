# TBA 9: Research Integrity Candidate

TBA 9 is the newest private, research-only successor to TBA 8. It preserves
TBA 8's frozen residual forecast, score-persistence, confidence, liquidity,
capacity, and risk-supervisor settings. It does not claim new alpha and is not
available in the scanner, app, subscriptions, paper journal, or Portfolio Lab.

Its upgrade is the evidence contract around a run:

1. Every investable name must come from an availability-dated, point-in-time
   universe snapshot. Missing or stale membership makes the name ineligible;
   a later constituent list is never backfilled into history.
2. Development and holdout dates are declared before holdout scoring and are
   immutably recorded with the experiment. TBA 9 never changes its frozen
   configuration after seeing holdout results.
3. Named completed-return benchmarks (for example SPY and QQQ) are reported
   separately. They do not modify the model's weights.
4. The same frozen data, timing, universe mask, and holdout are used for
   ablation and one-at-a-time parameter-sensitivity diagnostics. Those tables
   are descriptive audit evidence, not a winner-selection mechanism.

The entry point is `backend.tba9_integrity.run_tba9_integrity_research`. It
requires aligned OHLCV histories, availability-dated universe snapshots, an
explicit risk-free series, an explicit benchmark-return series, and distinct
development/holdout boundaries. It writes the experiment and its two locked
partitions to the existing private research ledger.

The simulation retains the project-wide timing rule: a close-time decision is
filled at the next open. The point-in-time membership mask is applied after
signal and risk transforms, so an ineligible name has a zero target and cannot
be restored by a later stage. This protects research integrity; it does not
prove future performance, prevent all data-quality problems, or authorize a
trading claim.
