# Universal V2: shared engine and alpha research

Implemented in the working checkout on 2026-09-05. Engine identifier: `universal_v2`; version: `2.0.0-research`. This is a working research backend, **not a validated alpha improvement or a production promotion**. The public Official Momentum scanner remains unchanged.

## What is shared

The scanner, scanner backtest, Pattern Lab, and Quant Lab can now consume the same causal signal calculation. Portfolio construction and execution are separate from the signal so a scanner score cannot be confused with a portfolio return.

```text
Validated adjusted daily history
             │
             ▼
    Universal signal features + configuration fingerprint
             ├──► Scanner: strength, eligibility, components, date
             ├──► Pattern Lab / scanner backtest: signal diagnostics
             │
             ▼
    Portfolio targets: net signals → caps → stressed covariance risk
             │
             ▼
    Cash/shares ledger: next-open fills → costs → capacity → close NAV
             │
             ▼
    Daily net returns + explicit benchmark + daily cash-return series
             │
             ▼
    Annual alpha, HAC uncertainty, consistency gates, experiment record
```

The signal is universal; the older ATR stop/target diagnostic and the new portfolio ledger intentionally answer different questions. A scanner-backtest win rate is not the portfolio engine's win rate. Legacy V8/VAI/Quant profiles retain their own scoring and simulation conventions for historical comparison; they are not silently relabeled V2.

## Signal definition

All features at session `t` use only prices through that session. There are no future labels, ticker-identity features, live LLM predictions, or macro/fundamental inputs in V2. The current input scope is daily listed-equity history, not universal coverage of options, futures, intraday books, or every asset class.

| Component | Exact direction |
| --- | --- |
| Trend | Average of bounded, volatility-normalized 21-, 63-, 126- and 252-session returns |
| Momentum | Return from `t−252` to `t−21`, normalized by volatility and √231; skips the most recent month |
| Breakout | Close's position within the trailing 63-session closing-price range, mapped to −1…1 |
| Pullback | Opposes the volatility-normalized five-session move, only to the extent the trend component is positive |

Normalization uses trailing 63-session daily return standard deviation, population convention, with a 0.3% daily floor. `tanh` bounds trend, momentum and short-term reversal contributions. At least 253 bars are needed to form a ready signal; earlier signals are zero/no-trade. Portfolio evaluation requires a subsequent session for execution.

Default component weights are 40/35/15/10%. The default entry threshold is 0.15. Scores lie in −1…1 and are **not probabilities or expected alpha**. The legacy setup adapter maps strength to 0…100 solely for schema compatibility. Its `universal_evidence` field preserves the original meaning.

One configuration fingerprint identifies all parameters, including execution assumptions. Use identical configurations with the dedicated scan/run endpoints to reproduce the same signal. The ordinary private scanner and Pattern Lab adapters use the explicit engine defaults; they do not load the study-selected settings automatically.

## Portfolio construction

Eligible positive signals are divided by trailing volatility and normalized to the gross-exposure budget. An iterative cap redistributes excess only among eligible names with remaining capacity. Unallocatable capital stays in cash.

Trailing sample covariance is shrunk toward its diagonal. A second positive-semidefinite blend moves correlations toward +1 while keeping marginal volatilities fixed. This stressed covariance actually affects V2's risk scaler; unlike the legacy correlation scenario chart, it is not just a display. The scaler can only reduce exposure.

Defaults: 25% target name cap, 100% gross cap, 12% annual volatility target, 63-session covariance window, 50% diagonal shrinkage, and 25% correlation convergence. The name, gross and volatility limits apply to **targets**. Actual weights drift after fills; gap risk, liquidity limits and the no-trade band can cause realized weights/risk to differ. This is not a hard real-time risk guarantee.

Weekly targets update on the first observed session of a `W-FRI` calendar period and execute the following session's open. Monthly targets update on the first observed session of a month; daily is also supported. The first ready signal initializes the schedule. Equal successive targets do not trigger needless rebalancing.

## Execution ledger

The book starts flat at the requested evaluation boundary, uses the prior close's target, trades at the next open, and marks held shares at the close. A newly purchased asset does not earn the preceding overnight gap. Shares drift between trades; constant target weights do not imply free daily rebalancing.

Orders are netted by security. Sells happen before buys; buys are scaled together to available cash including their modeled costs. Shorting and leverage are rejected. Floating-point cash dust is clamped only within the ledger's numerical tolerance.

Default costs are 12 bps per one-way notional plus `18 bps × sqrt(participation)`. Participation uses notional divided by the **previous** 20-session median close×volume. The default hard fill ceiling is 2% of that prior dollar volume per asset per session. Zero/missing estimated capacity permits no fill, not an idealized execution. Missing prices or volume observations are rejected; actual zero volume is valid and blocks fills.

The default no-trade band is 1.5% of NAV: small non-exit target changes are ignored. Complete exits bypass the band. Excess size expires at that rebalance; it is not implicitly executed later. Reports include unfilled notional, costs, fills and maximum participation. The dollar-volume proxy uses adjusted prices and supplied volume, so split/volume consistency must be audited before capacity claims.

This is a conservative capacity-constrained daily simulation, not a calibrated fire-sale or order-book model. It has no endogenous price feedback, spread/depth feed, redemption queue, borrow availability, taxes, financing shocks, or optimal execution schedule. Fixed cost coefficients are declared research assumptions, not estimates of what BlackRock, Citadel or another firm would pay.

Cash earns the explicitly supplied daily risk-free return proxy. With no cash series it earns zero, and alpha is unavailable. Risk-free returns are accounting inputs, not signals. Open positions remain marked at the evaluation end and are not forced closed just to manufacture a win rate. Closed-trade win rate means net cash-flow P&L across an entire flat-to-flat position episode, including partial adjustments and all costs; open episodes are excluded.

## Alpha and promotion criteria

The benchmark is the daily arithmetic average of adjusted SPY and QQQ returns: a daily rebalanced 50/50 reference before its own trading costs. QQQ is a Nasdaq-100 proxy, not the Nasdaq Composite. Missing benchmark/risk-free sessions are never silently filled.

For each calendar year, regress `strategy − cash` on an intercept and `benchmark − cash`. Annual alpha is **252 × the daily intercept**, expressed in percentage points. It is neither CAGR nor unadjusted return minus benchmark return. Mean annual alpha and the pooled-period regression are reported separately; they can differ.

Uncertainty uses Newey–West HAC covariance, Bartlett weights, automatic lag `floor(4 × (n/100)^(2/9))`, finite-sample correction `n/(n−2)`, and approximate 95% intervals. These intervals allow serial dependence but **do not correct for searching many configurations**.

The current scorecard requires exactly ten complete calendar years, mean annual alpha ≥5%, at most one negative year, at most two years total below 1%, all remaining years ≥2.5%, and closed-episode win rate ≥55%. The negative-year allowance is included in the two-below-1% allowance. A complete year needs both calendar boundaries and at least 240 sessions; a partial year cannot qualify. These are requested acceptance criteria, not promised achievable outcomes or automatic deployment authority.

## Minerva V1 candidate

**Minerva** is the name for the next Universal generation's first research candidate. It is not an app-store/public-model release label and does not replace Official Momentum or any existing selectable default.

Its frozen price-only baseline is a walk-forward ridge model with 756 prior sessions, 21-session retraining, a five-session label, ridge penalty 1.0, weekly rebalancing, 18% volatility target, 24 maximum holdings, 12 bps one-way cost, `18 bps × sqrt(participation)` impact, and a 2% prior-dollar-volume fill ceiling. The exact research record, including failed breadth variants, is in [MINERVA_V1_RESEARCH_RECORD.md](MINERVA_V1_RESEARCH_RECORD.md).

Two strictly opt-in corporate sleeves are available for a later preregistered test:

| Sleeve | What it uses | Guardrail |
| --- | --- | --- |
| Fundamental quality | An availability-dated, cross-sectional public corporate-quality panel | Weight defaults to zero; a missing or non-causal panel rejects the run. |
| Filing acceleration | The change from an issuer's previously public filing-derived growth score, decayed after the filing | This is not an analyst earnings-surprise proxy. The first filing is neutral, the source timestamp controls eligibility, and the weight defaults to zero. |

Neither sleeve has historical data coverage in the Minerva price study, so neither was included in its alpha result. A nonzero weight requires a frozen development manifest, a later point-in-time confirmation universe, and the same alpha/promotion gates above.

## Interfaces and files

| File | Responsibility |
| --- | --- |
| `server/backend/universal_engine.py` | Validated frozen configuration, fingerprints, causal features, scanner adapters, portfolio targets |
| `server/backend/portfolio_execution.py` | Cash/shares ledger, next-open timing, volume limits, costs, drift, position episodes |
| `server/backend/alpha_evaluation.py` | Excess-return regression, HAC uncertainty, annual completeness and acceptance gates |
| `server/backend/universal_research.py` | Shared report adapter, evaluation boundaries, data/config/source fingerprints, diagnostics |
| `server/backend/universal_position_policy.py` | Disabled-by-default state contract for adaptive stops, partial profit-taking, spike/time/signal directives and audit reasons |
| `server/backend/universal_market_context.py` | Disabled-by-default completed-market context classifier for next-open target scaling, entry gating and audit reasons |
| `server/backend/universal_taxonomy.py` | Versioned multi-family security exposure graph, leaf/root validation and non-double-counted family-shock diagnostic |
| `server/backend/universal_research_blueprint.py` | UI/API-visible readiness map for future data/signal/portfolio workstreams and their non-negotiable controls |
| `server/backend/routes/universal.py` | Authenticated bounded scan/run uploads and derived experiment recording |
| `server/tools/build_universal_taxonomy.py` | Refusing-by-default builder for a source-dated top-25,000 eligible-equity taxonomy snapshot |
| `server/tools/run_universal_study.py` | Bounded development selection followed by frozen temporal and separate-stock evaluations |
| `server/tests/test_universal_engine.py` | Causality, accounting, risk, alpha, HTTP, scanner, Quant and Pattern Lab regressions |
| `docs/UNIVERSAL_V2_VALIDATION.json` | Derived results, every tested configuration, selection manifests and failures; no raw vendor bars |

New `POST /api/universal/scan-upload` and `/api/universal/run-upload` routes are mounted only with `ORYNTRA_PRIVATE_RESEARCH_ROUTES=true` or `ORYNTRA_PUBLIC_QUANT_LAB_ENABLED=true`. Both require an authenticated session. They do not accept provider keys. Run uploads allow 1–24 assets, 254–4000 daily bars per history, SPY and QQQ benchmark histories, explicit daily cash returns, configuration and optional evaluation dates. Dates must represent midnight-UTC daily sessions. Raw uploaded bars are neither persisted nor echoed.

`scan-upload` accepts `history: {ticker, bars}` and `configuration`. `run-upload` accepts `histories: [{ticker, bars}]`, `benchmarks`, `risk_free: [{date, return_daily}]`, `configuration`, `evaluation_start` and `evaluation_end`. Each bar uses `timestamp`, `open`, `high`, `low`, `close`, `volume`. The complete request schema is in the server's OpenAPI document when private-research documentation is enabled; the public-Quant flag alone does not expose API documentation.

The existing Quant API also accepts `model="universal_v2"`. It maps its risk/cost controls to V2, preserves the actual parameters in `engine_configuration`, and skips unused corporate/macro queries. Its older request shape does not carry the explicit benchmark/cash series, so it cannot issue an alpha scorecard. Legacy sleeve weights/lookbacks are not V2 feature controls; use the dedicated endpoint for full V2 configuration. Missing assets abort V2 evaluation rather than quietly changing its universe.

Private scanner/backtest requests use `pattern_mode="universal_v2"` / `engine_mode="universal_v2"`; Pattern Lab uses `engine_modes: ["universal_v2"]`. These adapters receive enough trailing history for the same signal. No browser/iOS default, account policy, production deployment or broker functionality was changed.

## Position-policy foundation

The backend now has a separate disabled-by-default position-policy state machine. It stores entry price/date/score/volatility, original and current stops, first profit target, high-water mark, holding sessions and partial-take-profit status. It can express precommitted next-daily-bar stop/limit outcomes and close-only next-open directives for time exits, signal decay and spike exhaustion. Long stops can tighten but cannot widen; ambiguous daily bars are stop-first and spike detection never receives the bar high as an exit price.

It is deliberately **not yet wired into the V2 execution ledger**. That boundary keeps current results unchanged while Astra implements a fully tested lifecycle adapter with partial fills and capacity-constrained exits. `GET /api/universal/blueprint` exposes the same foundation in the signed-in Quant Lab UI. See [the Astra tuning playbook](ASTRA_UNIVERSAL_TUNING_PLAYBOOK.md) for the permitted tuning surface, data contracts and frozen-evaluation protocol.

## Market context and multi-family taxonomy foundation

Universal V2 now has a separate market-context state contract. It consumes only completed, as-of market return, breadth and cross-stock correlation observations, requires more than one confirming stress signal by default, then produces a next-open exposure multiplier and entry gate. It is not connected to targets or exits yet, so the existing V2 simulation remains unchanged. A down broad-market day does not by itself cause a liquidation.

### Point-in-time coarse-to-fine universe contract

`server/backend/universe_selection.py` adds a research-only, LEAN-inspired two-stage selector. A coarse stage applies declared asset-type, country, price and dollar-volume eligibility; a fine stage may require point-in-time fundamentals and market-cap bounds. Every accepted record must have both an `as_of_date` and an `available_at` no later than the decision date. The result retains rejected-record counts, selected-row provenance and a reproducible fingerprint.

This closes a data-governance gap in the taxonomy foundation: the 25,000-security seed is useful for taxonomy lookup but is **not** a historical, dynamically selected trading universe. The selector is not wired into Minerva, Universal V2 targets, scanner results or execution. A dated vendor snapshot and a predeclared research manifest are required before it can be used in an evaluation.

### Optional OHLCV-structure candidate

`minerva_ohlcv_structure_candidate()` is an off-by-default candidate inspired by the compact public OHLCV feature families used in Qlib's published examples. It adds six completed-session inputs to the existing causal ridge learner: overnight gap, intraday return, intraday range, close location within the day, 5/21-session volume trend and a 21-session Amihud-style daily liquidity proxy. It requires aligned `High` and `Low` data that encloses each day's open and close, and cannot run from close/open/volume alone.

This is a named feature-set candidate, not a promoted Minerva change. The frozen price-only Minerva baseline remains unchanged. The candidate needs a predeclared development/holdout comparison with the same universe, costs, next-open timing and promotion gates before its feature switch can be nonzero outside research.

The taxonomy foundation models a stock as potentially belonging to several independent roots: market/region, sector/industry, structural theme, commodity linkage and rate/credit sensitivity. A membership has distinct `membership_weight`, signed `downside_sensitivity`, confidence, source, source URL and valid dates. Parent and child classifications are never added together within one root branch. The committed catalog is a reusable hierarchy, **not** a claimed list of current stocks.

The repository now includes `server/data/universal_taxonomy/financedatabase_seed_25000.json`: 25,000 active, deduplicated FinanceDatabase equities and 100,489 automatic family memberships. It gives the site a large real seed list immediately, with source revision, MIT attribution and confidence labels. The authenticated `GET /api/universal/taxonomy/{symbol}` route lazily returns one seed member's complete region/sector/industry/theme/macro family edges without sending the entire 53 MB file to the browser. FinanceDatabase supplies a categorical market-cap tier rather than a precise ranked market-cap feed, so this is explicitly a coverage seed—not an exact “global top 25,000” claim and never historical backtest membership. For research, obtain a provider export that defines eligible common equities by market capitalization at one as-of time, then run `tools/build_universal_taxonomy.py`; it refuses a partial set, duplicate edge, unknown family and overwrite. Retain dated snapshots for every historical test. Official exchange directories and SEC ticker data are useful U.S. identifier sources, but neither is a complete, point-in-time global 25,000-equity market-cap/classification history; use a licensed/global source for that role.

## Reproducible study runner

From `server/`, run with the repository environment:

```bash
.venv/bin/python tools/run_universal_study.py \
  --bars-csv /absolute/path/bars.csv \
  --risk-free-csv /absolute/path/risk_free.csv \
  --manifest /absolute/path/manifest.json \
  --output /absolute/path/new-study.json
```

Bars CSV columns: `date,ticker,open,close,volume`, including SPY and QQQ. Cash CSV: `date,return_daily`, with decimal returns, not annual percentage yields. Supply consistently adjusted OHLC and a common complete calendar. The runner does not download data or assume that a provider license permits redistribution.

The manifest declares disjoint development/confirmation symbols, development/forward/confirmation start and end dates, and 1–24 `UniversalConfig` candidates. A complete example is `server/examples/universal_study_manifest.json`. At least three complete development years are required. The fixed selection objective is mean annual alpha minus the average shortfall below 2.5%; **no confirmation returns enter selection**. Every trial is retained, including failures. An existing output path is refused. Changing the candidate list after seeing a test is another research round and must be recorded as such.

API runs retain derived metrics, exact parameters and fingerprints in the existing experiment database. Offline runs produce explicit JSON artifacts rather than silently mutating that database. Current reports fingerprint the five core calculation/report source modules as well as parameters and input data. The initial study below preceded that source-fingerprint addition; its code-version precision is therefore limited to this uncommitted V2 working version. Raw working CSVs and full interim reports from this run are in `/private/tmp/oryntra_universal_v2_study/` and are ephemeral, not repository assets.

## Actual study outcome — target not achieved

All data baskets were predeclared current-survivor US stocks, not point-in-time index membership. Initial selection used 2016–2020 on 12 development stocks; forward testing used 2021–2025, and separate-stock confirmation used 2016–2025. Reusing historical market eras is not an independent prospective test simply because tickers differ.

| Stage | Mean annual alpha | Interpretation |
| --- | ---: | --- |
| Initial selected configuration, development | +3.56% | Selected from 12 signal/threshold/risk candidates |
| Initial selection, later-period development stocks | −2.10% | Failed forward test |
| Initial selection, 12 separate stocks, ten years | +1.01% | Four negative years; 30.94% closed-episode win rate; failed |
| Cost-aware follow-up, development only | +3.97% | Selected from 12 rebalance/band/cap candidates; not confirmation evidence |
| Cost-aware follow-up, another 12 stocks, ten years | −5.12% | Six negative years; 30.94% closed-episode win rate; failed |

The initial winner used 60/20/10/10 signal weights, 0.25 threshold and 18% risk target. The follow-up changed the band from 1.5% to 8%; weekly scheduling and the 25% name cap remained selected. A zero-cost diagnostic on the initial development configuration gave +5.02% mean alpha versus +3.56% after assumed costs, motivating that follow-up. Zero-cost output was **not** treated as implementable performance. Both candidates remain research-only and neither replaced the defaults.

The durable JSON records all 24 candidate evaluations, calendars, stock baskets, annual alpha, uncertainty on confirmation reports, parameters and data fingerprints. The third basket was KO, PEP, PG, UNP, UPS, CAT, HON, AXP, MET, AMGN, GILD and TXN, declared before its download/test. Its failure is retained, not explained away by dropping stocks or bad years.

## Correctness changes and verification

The older scanner backtest constructed stop/target exit results but failed to return them. Stops/targets therefore did not close positions normally. It now returns the result, checks the final bar, handles gaps through a stop at the open, and charges both sides' commission. Prior reports from that implementation need rerunning; they are not interchangeable with corrected results.

The legacy portfolio simulator still uses shifted target weights and target-change costs rather than a share ledger. Its named historical profiles remain reproducible, but implicit daily rebalancing is a known limitation. V2 avoids that path. Shared report charts now retain the beginning and ending session, and drawdown includes loss from starting capital. These display/statistical fixes do not alter strategy daily returns.

At this checkpoint: **80 backend tests passed, one optional statsmodels comparison skipped** because that library is absent. Tests include future-data perturbations, scanner/portfolio signal parity, actual authenticated HTTP uploads and experiment persistence, default-public route exclusion, full Pattern Lab history, explicit Quant configuration, cash conservation, drift, gap timing, volume limits, two-sided costs, open-episode exclusion, known synthetic alpha, ten-versus-eleven-year gates, invalid input and chart boundaries. The suite has one existing `datetime.utcnow()` deprecation warning. These are local backend checks, not mobile, production deployment or live execution validation.

## Research basis and next work

[AQR's time-series momentum paper](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum) motivates investigating own-price persistence; it does not validate this equity-specific blend. [AQR's trading-cost research](https://www.aqr.com/Insights/Research/Working-Paper/Trading-Costs-of-Asset-Pricing-Anomalies) motivates evaluating turnover and implementation jointly with signals, not copying institutional coefficients. [Harvey and Liu's backtesting paper](https://people.duke.edu/~charvey/Research/Published_Papers/P120_Backtesting.PDF) motivates retaining the full search record and separating selection from tests. [The French data library](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library/f-f_factors.html) supplied the daily risk-free return proxy; [statsmodels' HAC reference](https://www.statsmodels.org/stable/generated/statsmodels.stats.sandwich_covariance.cov_hac.html) supplies an optional independent covariance comparison.

The next alpha work should change the economic evidence, not repeatedly mine these spent baskets. First obtain point-in-time membership/delisting and corporate-action verification, then compare against simple cash-funded baselines on the **same** ledger. Develop any new predictive signal or learned combination using nested chronological selection and label-horizon purging; freeze it before a genuinely unused test. Test whether costs, sector exposure, unstable beta, or signal decay explain any apparent gain. Point-in-time fundamental/earnings or residual-return features require real audited inputs and an explicit as-of contract before integration; no macro sleeve or additional data feed was invented here. No claimed alpha can be rescued by loosening costs, deleting failures, changing the benchmark after results, or presenting model-selection returns as independent evidence.
