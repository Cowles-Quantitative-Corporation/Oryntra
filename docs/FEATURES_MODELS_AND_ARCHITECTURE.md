# Oryntra AI: Features, Models, and Architecture

Source basis: current Git checkout on 2026-09-05. This guide describes implemented code, not a proposed roadmap or a claim about a live deployment.

Universal V2 implementation and measured limitations are documented in [Universal V2 backend](UNIVERSAL_V2_BACKEND.md). It introduces shared scanner/portfolio signals and a separate cash-funded execution engine; it has not replaced public Official Momentum or achieved the requested consistent-alpha gate.

## 1. Product boundary

Oryntra is an educational market-analysis and historical-research product. It has a shared Python backend, a browser client, and a Flutter/iOS client. It can calculate derived indicators, detect rule-defined patterns and setups, create hypothetical research levels, save watchlists and paper trades, run historical backtests, and produce portfolio-research diagnostics.

Public access is intentionally separate from CQC internal research. Until a formation-and-IP-assignment configuration is explicitly confirmed, Oryntra publicly states that it is not operated by CQC. Subscribers never receive CQC equity, proprietary results, managed-account access, internal directives, or an authorization to trade. Portfolio Lab’s configurable directive ledger is server-authorized CQC-internal research only and disabled by default; public Quant Lab accepts only a frozen historical demonstration profile.

It cannot connect to a brokerage, submit or route orders, custody funds, trade autonomously, guarantee a fill, or provide a reliable forecast merely because a score is high. The words “signal,” “entry,” “stop,” “target,” “confidence,” and “expected” appear in legacy/internal structures and user-facing research plans; they describe deterministic or simulated output, not personalized advice.

## 2. Product surfaces and availability

| Surface | Availability | Purpose |
| --- | --- | --- |
| Public intelligence API | Always mounted | Authenticated policy/status, quota, Official Momentum scanner, and browser-upload scan paths |
| Browser workspace | Controlled by `ORYNTRA_PUBLIC_SCANNER_WEBSITE` | Six-tab user interface served by FastAPI |
| Flutter client | Separate build in `ios-app/` | Five-tab mobile interface using the same account and analysis API |
| Browser-upload backtest | Mounted outside private mode | Authenticated historical research from bars fetched directly by the client |
| Public Quant Lab upload | Authenticated route always available | Frozen historical research profile using client-supplied normalized bars; public users cannot tune model, allocation, risk, or execution settings |
| Private research API | Controlled by `ORYNTRA_PRIVATE_RESEARCH_ROUTES` | Alternate engines, server-provider scans, cache, Pattern Lab, VAI training, private backtests, pattern records, and Pro tools |
| Public Quant Lab administration | Controlled by `ORYNTRA_PUBLIC_QUANT_LAB_ENABLED` | Enables the full authenticated Quant router without enabling all private research routes |
| Internal Quant / Universal V2 research | Explicit private-research operating mode plus server-provisioned internal authorization | Configurable signals, next-open portfolio ledger, and explicit benchmark/cash alpha evaluation; never subscriber access |
| Maintenance site | Separate small application | Branded availability page independent of the main workspace |

The existence of code does not mean a feature is enabled in a deployed environment. Startup flags decide which route groups are mounted. Provider plans, browser CORS, authentication policy, mobile permissions, ad configuration, and App Store state impose additional runtime boundaries.

## 3. Model naming: what each model actually is

### 3.1 V1.0 Official Momentum — released scanner model

Public intelligence routes force `pattern_mode="official"`. The source internally calls this the V7 Official policy because the released engine evolved from earlier experiments; the product-facing label is V1.0 Official Momentum.

The model is deterministic. It starts from current and trailing OHLCV-derived evidence, runs the pattern engine, scores candidate setup families, then applies a selective bullish-momentum policy. It favors confirmed long candidates, blocks bearish trade candidates by default, requires stronger evidence around moving averages and momentum, and penalizes overextension, high ATR, wide Bollinger ranges, weak trend strength, and bearish volume divergence. If no candidate clears the policy thresholds, the correct output is no trade/hold.

It is not a machine-learning model and does not learn from a user’s scans. Its numeric calculations come from source rules, not an LLM.

### 3.2 V8 evidence model — deterministic research candidate

V8 uses a symmetric long/short candidate foundation and scores separate evidence families: trend structure, trend strength, momentum, MACD, VWAP, relative volume, stochastic state, price levels, RSI context, pattern confirmation, and ATR risk. It requires multiple positive factor families and keeps ATR as a risk penalty rather than a directional reason.

V8 exists for comparison and research. It does not replace Official Momentum in public intelligence routes.

### 3.3 VAI 1.0 Experimental — legacy trained candidate

VAI 1.0 builds candidate observations from the Official Momentum foundation and applies a locally stored logistic model. Its training code vectorizes deterministic indicator/setup/pattern features, fits the candidate, stores JSON model/metadata artifacts, and reports a probability, threshold, and grade. When no model exists or the feature shape is incompatible, it falls back instead of pretending a prediction was produced.

This is a legacy experimental path. It is not the current public model and a trained artifact is not proof of profitable generalization.

### 3.4 VAI 2.2 Chronological PIT Experimental — newer trained candidate

VAI 2.2 is the newer research model. It uses deterministic structured inputs, excludes ticker identity, scanner score/confidence, and AI explanation text, and creates chronological train, validation, and untouched test partitions. A purge gap equal to the forward outcome horizon separates partitions. Category vocabularies and transforms are learned on training dates only. Validation selects a threshold; untouched test performance controls promotion relative to the currently promoted model.

The model fits accept/reject probability and auxiliary return/stop-risk quantities, records each run, and retains promoted JSON artifacts separately. Its output remains experimental. “Promoted” means promoted within the local research workflow, not deployed as the public scanner.

### 3.5 Historical pattern-engine modes

The pattern engine retains historical modes such as `old`, `new`, `experimental`, `risky`, `selective`, `balanced`, `official`, `v8`, `vai`, and `vai2`. The current private Pattern Lab comparison order is `official`, `v8`, `vai2`, `universal_v2`; its mode cleaner restricts that surface to these supported choices. These are research families, not simultaneous public products.

### 3.5.0 Universal V2 — shared price-evidence research engine

`universal_v2` uses identical causal trend, skip-month momentum, closing-range breakout and trend-conditioned pullback features for scanner and portfolio consumers. Its portfolio path adds target caps, covariance shrinkage/correlation stress, a long-only cash/shares ledger, next-open transactions, drift, volume-constrained fills and costs. The dedicated upload API accepts explicit daily SPY/QQQ benchmark and risk-free returns for annual CAPM alpha with HAC uncertainty. Pattern Lab and scanner backtests share its signals but retain their separate diagnostic outcome models. Macro/corporate data are not used by V2. Both tested configurations failed the acceptance criteria and remain research-only.

Universal V2 also has a disabled position-lifecycle foundation: a serializable position state, precommitted stop/limit phase, monotone trailing stop, partial-profit, spike/time/signal directive contract and audit reasons. It is CQC-internal research infrastructure, not an activated public model. Residual selection, event sleeves, sector/beta controls, regime exposure and meta-labeling each remain separately gated by point-in-time data and frozen-test requirements.

The TBA research branch now adds a shared quantitative risk supervisor after signal selection. It caps correlated clusters and marginal variance contribution, reduces exposure during high-correlation regimes or inadequate effective diversification, and uses completed portfolio returns to control realized volatility, volatility acceleration, expected shortfall, and drawdown. Recovery from defensive exposure is rate-limited. Every runnable TBA candidate enables this supervisor by default through one constructor; the frozen Minerva baseline and separately named Minerva candidates keep it disabled. The controls are causal, tunable, and audit-reported, but they have not passed the required paired native-TBA and frozen-Qlib out-of-sample Sharpe comparisons.

### 3.5.1 Shared research contract

Every scanner result now carries the same `research_governance` record: model identity/maturity, a daily-bar input fingerprint and date range, the validation requirement, and explicit portfolio/execution limits. Official, V8, VAI 1.0, and VAI 2.2 therefore expose comparable provenance without sharing scores, thresholds, or model weights. VAI 2.2 additionally states its chronological train/validation/untouched-test promotion rule. This metadata does not alter a score or make a result executable.

### 3.6 Oryntra V1.0 Quant Lab — portfolio research, not scanner scoring

Quant Lab is a separate daily-bar portfolio simulation. Its internal profile identifiers retain `v8_*` names, but the current UI labels them as V1.0 baselines. It does not consume a scanner result and does not ask VAI to choose trades.

| Profile | Default sleeve mix |
| --- | --- |
| Corporate quant system | 25% trend, 20% relative strength, 10% mean reversion, 10% defensive low volatility, 35% corporate quality |
| Diversified price baseline | 35% trend, 30% relative strength, 15% mean reversion, 20% defensive low volatility |
| Balanced price baseline | 45% trend, 40% relative strength, 15% mean reversion |
| Trend-first price baseline | 65% trend, 25% relative strength, 10% mean reversion |
| Relative-strength price baseline | 25% trend, 65% relative strength, 10% mean reversion |
| Equal-weight baseline | 34% trend, 33% relative strength, 33% mean reversion |

Selected positive allocations are normalized to 100% server-side.

## 4. Scanner feature

The scanner accepts a normalized US ticker and a supported historical period. In browser/mobile direct mode, the client retrieves completed daily bars from the user-selected Polygon/Massive or Twelve Data account, normalizes them, and submits only those bars and provider label to the authenticated upload route. The provider key remains in browser IndexedDB or Flutter secure storage and is sent directly to the provider. Private server mode can instead use the server-side market repository and configured provider credentials.

The scanner pipeline:

1. validates identity, access policy, quota, ticker, period, and uploaded bar shape;
2. builds an OHLCV frame without persisting browser-uploaded bars;
3. calculates deterministic indicators;
4. detects patterns and setup candidates with Official Momentum;
5. builds hypothetical research levels and a quality grade;
6. attaches available point-in-time corporate context;
7. reduces the response through the public-payload boundary so raw OHLCV arrays are not returned; and
8. records bounded usage/history state where the operating mode permits it.

### Scanner output shown by the clients

- ticker, company label when available, current price, daily change, and selected period;
- signal/hold state, setup type/direction, quality score/grade, conviction, and risk/reward reference;
- hypothetical entry zone, risk point, reference target, and distance percentages;
- trend label/strength, volume context, and support/resistance levels;
- detected pattern cards with family, direction, confidence, timestamp, trigger/zone, and contextual evidence where present;
- rules that contributed to or blocked the setup;
- TradingView chart linkage and provider/provenance labels;
- quota/search-count context; and
- explicit research-only and raw-data-boundary notices.

The web chart supports 6-month, 1-year, and 5-year views and daily/weekly display controls. The Flutter scanner embeds TradingView, can add the ticker to the watchlist, start a paper-trade record from eligible levels, subscribe the ticker to market alerts, show a local scan-result notification, and update the iOS latest-scan widget.

## 5. Deterministic indicator library

The indicator engine derives the following from supplied OHLCV history:

- price, previous close, day change, daily range, 5/20-day and 52-week highs/lows;
- SMA 20/50/200 and EMA 9/21/50, moving-average position, distance, and cross state;
- RSI 7 and RSI 14;
- MACD line, signal, histogram, and cross state;
- Bollinger upper/middle/lower bands, percentile position, and width;
- ATR 14, ATR as a percentage of price, and trailing ATR percentile;
- ADX 14, directional indicators, and trend-strength label;
- stochastic %K/%D, Williams %R, and momentum over 5/20/60 sessions;
- 20-day VWAP and above/below-VWAP state;
- volume, 20-day average volume, relative volume, volume trend, OBV, and price/volume divergence;
- Ichimoku conversion/base/span relationships and state;
- classic pivot levels, support/resistance, and percentage distances; and
- combined trend and EMA-cross labels.

Short histories can produce unavailable values. Callers and clients must preserve those null/unknown states rather than inventing a number.

## 6. Pattern and setup features

The pattern package produces deterministic observations across four families:

- candlestick relationships, including common reversal/continuation and multi-candle structures;
- chart formations and consolidations;
- fair-value-gap zones; and
- market-structure events such as break of structure and change of character.

The coordinating engine normalizes each event to a common contract: pattern name/family, direction, confidence, timestamp, candle index, optional zone, trigger price, context, warnings, and summary counts. It de-duplicates events and limits the displayed subset.

The setup detector compares breakout, pullback, trend continuation, reversal attempt, overextended, and no-trade candidates. It uses indicator and pattern evidence, applies mode-specific adjustments and minimum thresholds, then returns the strongest eligible setup or no trade. The trade-plan builder converts an eligible setup into educational entry/risk/target references, quality/conviction labels, scenario text, and 5/10/20-session descriptive projections. These projections are rule-based scenario estimates, not a calibrated forecast.

## 7. Watchlist

Watchlists are account-scoped SQLite records. Browser and mobile users can add and remove normalized ticker symbols, see empty/loading/error states, and launch a scan from a saved symbol. The browser also offers “scan all,” executing a bounded batch of uploaded-bar analyses and rendering a comparison list. Deleting an account removes its watchlist records.

## 8. Paper trades

Paper trading stores simulated hypotheses only. A record can include ticker, long/short direction, entry, stop, target, notional size, notes, linked setup/plan context, open time, close price/time, status, and calculated paper result. Users can:

- create a paper trade manually or from a scanner result;
- view open trades or complete history;
- close a record at a user-entered price;
- delete an owned record;
- inspect open/closed counts, win rate, net/average result, and plan context; and
- use cached browser display data for resilience without treating it as authoritative account state.

The server checks record ownership. It does not obtain market fills, validate live liquidity, or send an order.

## 9. Historical backtest

The lighter backtest feature evaluates setup rules for a single ticker over uploaded daily history. The browser exposes period, minimum score, and optional setup filter. The output includes summary performance, completed examples, setup breakdown, exit reasons, and a trade log. Browser-upload execution is authenticated, caps input size/history, runs the real backend pipeline, and marks raw uploaded history as not persisted.

This backtest is different from Quant Lab: it evaluates historical scanner/setup examples rather than a multi-asset sleeve portfolio.

Its result carries the selected scanner engine’s shared research contract plus the actual tested candle count, symbols, date range, and dataset fingerprint. The execution statement remains deliberately narrow: next-session daily-bar simulation with configured commission/slippage, not intraday fills or market-impact modeling.

## 10. Quant Lab

Quant Lab returns the same top-level `research_governance` structure as the scanner paths, adapted for a multi-asset run. It records its model profile, universe/configuration fingerprint, validation requirement, and the distinction between daily-bar cost/capacity proxies and a real order-book or fire-sale model. This makes cross-feature research boundaries explicit without conflating the scanner with portfolio construction.

### 10.1 Strategy sleeves

- Time-series trend: sign of each symbol’s trailing return; long-only mode clips negative signals.
- Cross-sectional momentum: long the top 30% and, in long/short mode, short the bottom 30%; requires at least four available symbols.
- Mean reversion: opposes a 5-session move whose z-score against a 63-session volatility estimate reaches ±1.5.
- Defensive low volatility: favors the lowest 35% of trailing 63-session volatility and can short the highest 35%; requires at least four symbols.
- Corporate quality and change: ranks point-in-time corporate scores and forms top/bottom baskets when at least four symbols have nonzero eligible evidence.

### 10.2 Model profiles and portfolio controls

The V1.1 long-only trend/momentum research profile is a selectable, validation-gated candidate: 60% time-series trend and 40% cross-sectional momentum. Its profile default is long-only; callers can still explicitly request long/short construction for a separate research comparison. It retains the same next-session timing, one-way volatility scaler, liquidity-cost proxy, capacity scenarios, correlation-convergence diagnostics, chronology, and experiment-recording contract as the other profiles. It is not the application default and is not a live-trading signal.

- signal lookback and selected sleeve allocations;
- long-only or long/short construction;
- maximum absolute single-name weight;
- maximum gross exposure;
- daily, every-fifth-session weekly, or calendar-month rebalancing;
- 21-session realized-volatility targeting that can scale exposure down but never above the unscaled target;
- next-session holdings so close-of-day information is not applied to the same session’s return;
- base trading cost in basis points;
- annualized short-borrow cost;
- optional regime-conditioned sleeve weights;
- assumed portfolio value, market-impact coefficient, and maximum ADV participation; and
- configurable chronological walk-forward fold count.

### 10.3 Liquidity model

When enabled, target-weight changes are converted to assumed trade notional from portfolio value. The engine estimates 20-session median daily dollar volume from price times volume, calculates participation, applies base turnover cost plus a square-root participation impact term, and counts observations above the configured ADV participation limit.

This is a one-day historical cost proxy. It does not schedule orders across days, model order-book depth, widen spreads under stress, enforce hard rejection/capping, model funding/redemptions, or propagate a sale’s price impact into other holdings. It should not be called a full fire-sale engine.

### 10.4 Regimes, structured inputs, and attribution

The regime layer turns prior benchmark trend, volatility, drawdown, yield-curve, credit-spread, and inflation context into probabilities for persistent trend, stressed, reversal risk, and normal conditions. If enabled, those probabilities adjust visible sleeve weights. The regime output is descriptive conditioning, not a macro forecast.

The corporate repository accepts public documents, corporate facts, and macro observations. Every record carries source and timing fields, especially `available_at`; the simulation only uses a fact after that timestamp. Supported corporate metrics cover revenue growth, operating and free-cash-flow margins, earnings surprise, guidance and estimate revisions, insider net buying, share-count growth, and net debt/EBITDA. Supported macro metrics are policy rate, 2-year yield, 10-year yield, credit spread, and inflation.

Factor/relative-value attribution reports recent market beta, residual contribution, long/short contribution, and sleeve returns. Strategy health compares the most recent 63 sessions with earlier simulated sleeve history and labels deterioration for investigation. Neither report establishes causality.

### 10.5 Report outputs

Each run returns configuration, universe dates/symbols, source metadata, a dataset fingerprint, sleeve and ensemble metrics, and:

- total and annualized return, annualized volatility, zero-cash-rate Sharpe, max drawdown, Calmar, worst day, historical 95% VaR/expected shortfall, turnover, exposure, and longest drawdown;
- development and untouched chronological holdout summaries plus walk-forward development slices;
- trend/volatility regime breakdown and time-sampled regime probabilities;
- latest gross/net exposure, largest name, effective position count, positions, and average absolute trailing correlation;
- execution cost, maximum ADV participation, participation-limit breach count, and missing-liquidity observations;
- corporate/macro coverage status;
- factor/relative-value attribution and sleeve health;
- an assumption ledger that records timing, portfolio limits, cost/liquidity inputs, structured-evidence coverage, stress construction, and material omissions for the exact run;
- daily-ADV capacity sensitivity at the selected, 2×, and 5× portfolio-value assumptions, with explicit limits that it is not an order-book or fire-sale simulation;
- an equal-weight buy-and-hold reference over the selected symbols, explicitly labeled as a no-cost comparison rather than an executable benchmark;
- an experiment identifier for completed runs, recording configuration, dataset fingerprint, history range, universe, primary/reference summaries, and validation metadata without persisting browser-uploaded raw bars;
- 126-session pairwise return-correlation matrix;
- moderate and severe correlation-convergence scenarios over 21 sessions, preserving marginal volatility and shifting correlations 50% or 85% toward +1;
- net monthly-return matrix; and
- equity, drawdown, and rolling 63-session volatility series.

The correlation stress is a diversification-breakdown sensitivity test. It is not a price shock, P&L forecast, allocation optimizer, or automatic risk reduction.

### 10.6 Client differences

The browser exposes all seven model profiles, editable sleeve sliders, lookback choices, cost/borrow/portfolio/impact/ADV controls, risk presets, and the full visual report. Mobile shows the corporate, diversified, trend-first, relative-strength, and V1.1 long-only trend/momentum profiles, up to eight symbols per run, fixed 126-session lookback and three folds, user-selectable sleeves and core risk/cost controls, background execution time, and automatic local storage of completed reports. Selecting the V1.1 profile applies its 60/40 weights and turns off the long/short switch; a user can deliberately re-enable it for a separate comparison. Polygon/Massive Basic mobile runs are capped at four symbols by the client; a larger mobile universe requires the other provider path and its applicable plan limits.

## 11. AI explanation

The explanation endpoints accept structured scan/indicator context and produce plain-language educational text through the configured provider or a deterministic fallback. The explanation layer is downstream: it does not calculate OHLCV indicators, alter the scanner score, train VAI, or become the numeric source of truth. Explanations should be checked against the structured fields they summarize.

## 12. Accounts, sessions, access, and subscriptions

Account features include signup with legal acceptance, password hashing, login, logout, current-session lookup, subscription record handling, analysis policy/quota status, and password-confirmed permanent deletion. Session tokens are server-validated and stored securely by the mobile client. Browser state uses the application’s session mechanism and account-aware local caches.

The analysis-access layer distinguishes owner/personal, explicitly enabled public/business, and browser-direct modes. It reserves and refunds daily quota around analyses. Client-side visibility is not authorization; protected routes call server-side identity/policy helpers.

Legacy provider-credential HTTP endpoints remain in the route module for controlled modes, but browser-direct policy retires server storage of browser keys. Current web/mobile direct clients store keys locally and never include them in Oryntra API requests.

## 13. Market data, cache, and provenance

Private server mode’s market repository normalizes tickers and periods, checks the SQLite OHLCV cache, evaluates exchange-session freshness, selects Polygon or Twelve Data when needed, normalizes response columns/timestamps, validates numerical integrity, atomically persists usable bars, and returns source/freshness/fallback metadata. Cache-only mode never calls a provider.

The cache worker and tools support status, warm start, date-aware backfill, idempotent grouped storage, ingest-run records, known-symbol coverage, safe backup/restore checks, and maintenance workflows. A dataset fingerprint binds a Quant Lab report to its selected histories and configuration. `server/tools/run_quant_experiment_cli.py` adds a local, declarative CSV/JSON-manifest workflow for reproducible Quant Lab experiments; it records metadata and derived results only, never executes arbitrary strategy code or broker actions.

Browser/mobile direct mode is intentionally different: provider keys remain on the device, normalized completed bars are uploaded for the requested calculation, and public upload routes do not persist those raw bars. Public payload filtering removes internal market-history arrays from scanner responses.

## 14. Mobile and iOS-only features

### Authentication and provider setup

The app displays a startup screen, requires account authentication, then requires a successful provider-key check before entering the workspace. Provider keys are stored with Flutter secure storage and can be changed from Account. Session restore and logout are handled separately from the provider connection.

### Notifications

The Flutter service and native AppDelegate bridge support:

- permission/status checks;
- local scan-result notifications;
- an optional weekday 9:30 AM ET daily market reminder;
- up to the configured tracked-symbol limit;
- local scheduled alert reminders;
- APNs registration state; and
- server synchronization of push-device and stock-alert subscriptions.

The Account screen describes 9:00 AM ET premarket and noon/4:00 PM ET daily-move checks for tracked stocks. These depend on server alert evaluation and push infrastructure actually being configured; source code alone does not prove notifications will arrive.

### Background Quant Lab

A Flutter method channel asks iOS for background execution time while a Quant Lab request is active and ends it afterward. This reduces interruption risk when the app is backgrounded; iOS still controls how much time is granted.

### Latest-scan widget

After a scan, Flutter passes ticker, signal, price, and quality to the native app group. The WidgetKit extension reads those values, shows a small or medium “Latest Oryntra AI Scan” card, refreshes on a 30-minute timeline, and links back to the scanner URL scheme. It is a latest-result display, not a live market-data widget.

### Advertising and consent

The mobile app gathers consent through Google’s UMP flow before initializing ads. Adaptive banners and an interstitial-after-successful-scans service are implemented with web no-op fallbacks. Native builds use bundled production unit IDs unless build-time overrides are supplied; `ADMOB_TEST_MODE=true` switches both iOS banner and interstitial placements to Google test units. Privacy choices are accessible from Account.

### Stored Quant Lab reports

Completed mobile reports are saved to device preferences with timestamp, universe, fingerprint, and report payload. Account can reopen or delete them. This device-local store is separate from the server account database.

## 15. Browser-only and operator features

### Browser settings

Settings controls research defaults, browser-local Polygon/Massive and Twelve Data keys, provider verification/removal, theme selection, account access, and permanent account deletion. Provider keys are scoped to the signed-in browser user in IndexedDB, not local-storage plaintext and not server account fields.

### Optional web advertising and discoverability

The server publishes ad placement metadata and diagnostics, conditionally injects AdSense verification/script markup, and can serve `ads.txt`. It also serves `robots.txt` and a sitemap when the public base URL is configured. Ads are blank/off by default until explicit flags and placement IDs are provided.

### Private Pattern Lab

The hidden developer panel and private routes can report engine modes, select a reproducible universe, run immediate or persistent Pattern Lab jobs, list status/history, stop/resume jobs, warm the cache, inspect cache status, inspect VAI/VAI2 status, and start training. Persistent workers separate long research from the main request loop.

Pattern Lab records causal candidate dates, forward outcomes, bootstrap intervals, prior-date threshold selection, engine comparisons on identical observations, grading, and training rows. It is a research environment, not a public stock picker.

### Private Pro routes

The private Pro router contains snapshot/live/chart/cache research responses, alert definitions and evaluation, alert-event history, and a parallel paper-trade surface. These routes are mounted only in private research mode. “Live” refers to the route’s market-monitoring presentation; it does not create brokerage execution.

### Maintenance mode

The independent maintenance application serves a branded status page, static assets, theme behavior, and methodology text without importing the main scanner/database application. macOS launcher scripts start the normal server, maintenance server, cache checks, and market-data backfill workflows.

## 16. Durable storage

The main SQLite schema includes users, sessions, subscriptions, analysis usage, legacy and account-scoped watchlists, analysis cache, paper trades, scan history, app counters, OHLCV bars, ingest runs, market symbols, cache metadata, pattern events, pattern outcomes, pattern statistics, and VAI training runs. A separate research database holds corporate documents, corporate facts, and macro observations with point-in-time indexes.

These databases, caches, and generated model artifacts are operational state and intentionally excluded from Git. Deployments must preserve them separately from source replacement.

## 17. Legal and support surfaces

FastAPI serves an allowlisted set of Terms, Privacy, Refund, Risk Disclaimer, Contact, and Methodology pages. Browser signup links Terms, Privacy, and the research-risk disclosure. Flutter Account links Privacy choices, Privacy Policy, Terms, Risk Disclaimer, Methodology, and Contact in an external browser.

The pages document product behavior but are not a substitute for professional legal or market-data licensing review. Their claims must stay synchronized with the actual account, provider-key, advertising, and data-retention implementation.

## 18. Explicitly not implemented

The current repository does not implement:

- a market breadth/sector heatmap product (the Quant Lab monthly-return matrix and correlation matrix are different features);
- a broad user-facing macro dashboard beyond five structured Quant inputs;
- real-time order-book or tick-level execution simulation;
- multi-day liquidation schedules or a fire-sale feedback model;
- a non-convex ML portfolio optimizer;
- security-level style/factor neutralization across overlapping sleeves;
- broker connectivity, live positions, order creation, or autonomous trading;
- guaranteed push delivery, provider entitlement, or market-data redistribution rights; or
- proof that the current source is deployed or approved by Apple.

## 19. Where every file is documented

`docs/Oryntra_AI_Master_Technical_Documentation.txt` is generated from the exact output of `git ls-files`. It contains a coverage count, complete tracked-file list, safe environment-variable-name index, and one narrative purpose entry for every source file, test, configuration, legal page, script, Xcode/Flutter declaration, image, icon, and generated documentation artifact.

Regenerate it from the repository root with:

```bash
server/.venv/bin/python3 server/tools/generate_master_docs.py
```

Untracked files are intentionally excluded. At the time of this documentation pass, Flutter-generated local configuration files and the user’s unrelated edit to `server/frontend/index.html` were preserved and not absorbed into the documentation commit.
