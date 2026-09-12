# Astra handoff: Universal V2 research buildout

This is the implementation and tuning contract for future Astra work. It does not authorize a production promotion, broker functionality, or a claim that any candidate is profitable.

## What already exists

Universal V2 has one causal price-signal implementation for scanner, Pattern Lab and portfolio research. Its portfolio research path has cash-funded long-only accounting, next-open transactions, actual share drift, prior-dollar-volume participation limits, declared costs, explicit daily cash returns, beta-adjusted alpha, HAC uncertainty and retained experiment records.

The new position-policy foundation is in `server/backend/universal_position_policy.py`. It defines a serializable `PositionState`, `PositionDirective` and `PositionPolicyConfig`. It is disabled by default and is not yet applied inside the cash/share ledger. `server/backend/universal_market_context.py` adds the same kind of disabled-by-default contract for market, breadth and correlation-aware exposure scaling. `server/backend/universal_taxonomy.py` adds the source-dated multi-family graph contract. `server/backend/universal_research_blueprint.py` names the additional workstreams and their data contracts. The authenticated `GET /api/universal/blueprint` route and Quant Lab's “Next-engine research map” display the same contract.

## Cross-stock market context and family shocks

Treat a stock signal as one input, not an isolated truth. The `MarketContextConfig` accepts only completed-session market return, breadth and median pairwise correlation. Its default behavior is disabled. When enabled for a declared experiment, it needs multiple confirming stress signals before it emits `caution` or `risk_off`; a single SPY down day is never enough. The directive is an exposure multiplier and entry gate created at close for next-open execution. It must first scale **target weights** on the unchanged cash/share ledger. Do not connect it to forced exits until the target-scaling experiment is independently frozen and evaluated.

`server/data/universal_taxonomy/family_catalog.json` supplies a nested family tree: market/region, sector/industry, structural themes, commodity linkages, and rate/credit sensitivity. `FamilyExposure` makes membership genuinely many-to-many. Each source-backed edge has a `membership_weight`, a separately estimated signed `downside_sensitivity`, confidence, source URL and validity dates. A family decline can therefore contribute a documented pressure to a stock without asserting that every constituent moves identically.

`server/data/universal_taxonomy/financedatabase_seed_25000.json` is the first product-coverage seed: 25,000 active, deduplicated equities with automatic region, sector, industry, theme and macro-sensitivity mappings. It is generated through `tools/build_universal_taxonomy.py --finance-database-root …`, retains its source revision and MIT attribution, and labels thematic/name-derived memberships with lower confidence. It is useful for UI coverage and later review; it is **not** point-in-time historical membership or backtest evidence. For research, build provider-dated snapshots from a source that explicitly defines eligible common equities and market capitalization. The tool requires full membership coverage, a stable security ID, one universe as-of date and source metadata. It refuses incomplete output. Retain every historical snapshot; present-day constituents are not a valid historical backtest universe.

The first tuning sequence is deliberately narrow:

1. Freeze a 63-session market return, breadth and correlation definition; compare no overlay, 0.70 caution and 0.35 risk-off exposure multipliers on development dates only.
2. Freeze the winning overlay, test it once on later dates and a separate point-in-time universe, then inspect alpha, drawdown, turnover and missed-upside attribution.
3. Build dated taxonomy snapshots; estimate family sensitivities only on a development period and apply them out of sample.
4. Add residual strength only after subtracting market and sector/family effects with stable identifiers. Keep one strongest leaf contribution per root family branch so sectors, industries and themes do not double count.

## First implementation: activate the stateful exit engine

Do not add discretionary rules directly to `simulate_book`. First build an adapter that translates each confirmed fill into a `PositionState`, runs the precommitted daily-bar phase before each session, runs the close phase after each session, and turns a `PositionDirective` into a ledger order. Preserve these sequencing rules:

```text
t close: feature/signal and close-only position-policy update
t+1 open: execute prior target changes and prior close directives
t+1 daily bar: evaluate only precommitted stop/limit levels
t+1 close: mark shares and calculate the next directive
```

For a long position, an opening price below a stop exits at the opening price; an intraday stop exits at the known stop level. If a daily bar reaches both stop and profit target and intraday ordering is unknown, use stop-first. A take-profit limit fills only at its declared limit, never at the day high. A spike detected from close/volume data cannot execute until the next open. Keep all prices, fractions, event dates, reason codes, costs and unfilled quantities in the position-event log.

The stop invariant is non-negotiable: `next_stop >= current_stop >= original_stop`. A losing position’s maximum permitted loss may not expand due to a later pattern. Dynamic rules may tighten stops, trim shares, alter a future profit ladder only under a declared rule, or exit; they may not reclassify hindsight as an entry-time decision.

## Tunable versus fixed

Allowed only after being declared in a manifest:

| Area | Candidate knobs |
| --- | --- |
| Exit lifecycle | Initial/trailing volatility distance, first take-profit R multiple, trim fraction, time horizon, signal-decay level, spike threshold/trim fraction |
| Selection | Lookback, residual-return construction, rank breadth, entry threshold, holding count, sector cap |
| Portfolio | Risk target, risk contribution limit, correlation stress, cash reserve, rebalance cadence, no-trade band |
| Regime overlay | Declared state inputs, exposure multiplier, state threshold, de-risk duration |

Never tune or relax these to improve an outcome:

- signal-to-execution timing, cash funding, hard participation fills, data completeness, source availability timestamps, benchmark definition, daily risk-free series, calendar completeness, or the same-ledger cost model;
- point-in-time membership/delisting treatment, ticker/sector identity controls, feature-schema fingerprints, labels/purge periods, and separation of development from confirmation data;
- no look-ahead daily-bar behavior, stop-first ambiguity policy, or the rule that open positions do not inflate closed-trade win rate.

## Build order

1. Integrate the position state machine with no new predictive features. Add ledger-level tests for partial exits, multiple-day capacity-constrained exits, stop/target ambiguity, gaps, spike directives, time exits, signal-decay exits, stop monotonicity, cash conservation and future-bar invariance.
2. Add report-level position lifecycle attribution: entry reason, exit reason, holding period, realized R multiple, gross/net P&L, costs, maximum adverse/favorable excursion, sector, liquidity, beta and regime at entry/exit. This is descriptive, not a training label by itself.
3. Add point-in-time universe/sector and residual-return data contracts. Do not fake missing classifications with ticker names or current index membership.
4. Build residual cross-sectional selection as a separately testable sleeve. Compare it against raw momentum on the same universe, ledger, costs and dates.
5. Add dated earnings/corporate-event ingestion only after the source/availability/revision contract is complete. Keep it as a separate sleeve at first.
6. Add regime exposure only from information known before allocation. Reuse the existing regime report as a diagnostic, not as retroactive labels.
7. Consider an out-of-fold meta-label only after feature lineage and chronological purging are enforced. It filters existing setups; it does not create a free-form prediction engine.

## Research protocol

For each workstream, write a small immutable manifest before running it:

```json
{
  "hypothesis": "A volatility-aware trailing exit improves net alpha without increasing drawdown or capacity breaches.",
  "workstream": "position_lifecycle",
  "development_dates": ["YYYY-MM-DD", "YYYY-MM-DD"],
  "confirmation_dates": ["YYYY-MM-DD", "YYYY-MM-DD"],
  "development_universe_rule": "point-in-time, documented rule",
  "confirmation_universe_rule": "predeclared and separate",
  "candidates": ["bounded, explicit configurations"],
  "selection_metric": "mean annual beta-adjusted alpha minus fixed consistency penalty"
}
```

Select only on development data. Record every candidate, including failures. Freeze the selected configuration and evaluate it once on later dates and a predeclared confirmation universe. Switching tickers in the same historic market eras is cross-sectional evidence, not independent prospective confirmation. Do not turn a zero-cost result into an implementation claim.

The primary scorecard is annual beta-adjusted alpha against the explicit 50/50 SPY/QQQ benchmark and supplied daily cash returns. It must be accompanied by HAC uncertainty, complete calendar-year counts, net costs, turnover, fill participation, sector concentration, correlation stress, maximum drawdown, closed-episode payoff/win rate and a regime-by-regime breakdown. Alpha alone is insufficient; more trades are acceptable only if net alpha and capacity survive.

## Data requirements before each advanced sleeve

| Workstream | Required data that does not currently exist in V2 |
| --- | --- |
| Residual momentum / sector controls | Point-in-time sector classifications, membership and delisting history, reliable benchmark/sector histories |
| Earnings/events | Source URL, event and availability timestamps, revision lineage, historical corporate-action/ticker identity mapping |
| Regime exposure | Availability-dated macro/rates/credit observations and stable universe history |
| Meta-label | Chronological labels, purge gap, out-of-fold upstream signals, feature schema and code fingerprints |
| Intraday spike exits | Timestamped intraday OHLCV or trade/quote data; daily bars cannot claim intraday high execution |

## Definition of done for an Astra iteration

The code must expose the exact config/data/code fingerprints, contain unit tests for causal timing and accounting, add its candidate/failure record to the validation log, regenerate the master documentation, and leave public Official Momentum unchanged unless the owner explicitly approves a promotion after a separate review.
