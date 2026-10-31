# Deferred Research Ideas

This file preserves discussed research directions without representing them as
implemented features, roadmap commitments, or evidence of predictive value.

## Fundamental- and event-aware systematic equity sleeve

**Status:** Deferred. Do not implement, tune, market, or expose in the product
until the current Minerva price/volume model has first passed its own frozen,
out-of-sample consistency process.

**Origin of the idea:** combine Oryntra's technical/risk model with two distinct
additional evidence layers:

1. **Availability-dated fundamentals** — cross-sectional measures such as
   profitability, balance-sheet quality, valuation, and change in reported
   business performance.
2. **News and event interpretation** — a structured assessment of a dated item
   of public information: its issuer/sector/market scope, novelty, financial
   materiality, direction, likely time horizon, and confidence.

The intended design is a **systematic multi-sleeve model**, not an unbounded
headline chatbot. The price/volume sleeve, fundamental sleeve, and event sleeve
must each produce separately inspectable scores. Event information may also
produce a small, explicitly mapped peer/family exposure effect rather than
assuming every company reacts the same way.

### Conditions before any build

- Freeze and validate the existing Minerva baseline on genuinely unused data;
  do not use this idea to rescue a failed price-only result.
- Obtain a licensed, timestamped news source and retain original publication
  time, source, entity mapping, corrections, and data-availability records.
- Use point-in-time corporate data: a filing or reported value cannot appear in
  a historical feature before it was publicly available.
- Define the target and holding horizon before model selection. Test the news
  sleeve, fundamental sleeve, and their combination separately with the same
  execution assumptions and benchmark.
- Require independent universe/time-period confirmation and report null or
  negative results. Do not make predictive or investment-performance claims
  from a development result.

### Naming

If all scores and portfolio rules are automated and reproducible, this remains
**systematic quantitative equity** or a **systematic multi-factor model**.
If human analysts regularly interpret events and override the system, the usual
label is **quantamental**.

## Institutional techniques to investigate next

These are brainstorm notes for the next research cycle. They are intentionally
not enabled in Oryntra and do not change any model or public route.

### 1. Risk-budgeted signal blending

**Hypothesis:** Allocate risk budgets to signal sleeves rather than letting a
strongly ranked sleeve consume the whole portfolio's variance budget. Preserve
the existing signal forecasts, but report sleeve-level marginal contribution,
turnover, and crowding.

**Oryntra gap:** Current TBA supervision caps name-level contribution; it does
not yet attribute risk to trend, residual momentum, OHLCV structure, or future
fundamental/event sleeves separately.

**Required test:** Freeze sleeve definitions and compare equal-risk, signal-
weighted, and capped-risk constructions on identical forecasts. Promote only if
mean excess Sharpe and worst-period behavior improve after costs.

### 2. Hierarchical risk parity as a construction comparator

**Hypothesis:** Correlation clusters and hierarchical allocations may be more
stable than a single covariance inverse when correlations are noisy.

**Oryntra gap:** The current covariance layer shrinks and stresses the matrix,
but its supervisor does not compare a cluster-tree allocation against the
existing signal-weighted book.

**Required test:** Use a frozen distance/linkage convention, no future cluster
information, and compare cluster caps, hierarchical risk parity, and the
current 10% contribution cap across separate development and confirmation
years. Record cluster stability and turnover, not just Sharpe.

### 3. Factor-neutral residual construction

**Hypothesis:** Removing completed market, sector, size, and volatility-factor
exposure before ranking may make alpha more independent of broad beta.

**Oryntra gap:** Oryntra has market-residual experiments, but not a complete
point-in-time factor panel with loading stability and residual diagnostics.

**Required test:** Version factor definitions, rolling regression windows,
coverage, condition numbers, residual variance, and factor exposure before and
after trading. Never call a residual signal alpha merely because its label says
"neutral."

### 4. Forecast-combination weights with uncertainty penalties

**Hypothesis:** Combining forecasts by out-of-fold information quality and
uncertainty may outperform fixed sleeve weights while avoiding winner-takes-all
selection.

**Oryntra gap:** Current ridge and handcrafted signals are combined inside model
profiles, but forecast uncertainty and degradation are not first-class weights.

**Required test:** Use only completed rolling validation scores; shrink weights
toward equal weight; cap turnover and sleeve concentration; compare against
equal-weight and the strongest single sleeve on untouched periods.

### 5. Liquidity-aware alpha decay

**Hypothesis:** A signal with faster expected decay should receive less weight
when capacity is low, while a slower signal can tolerate larger participation.

**Oryntra gap:** The execution ledger has ADV participation and unfilled-order
accounting, but signal horizon and expected decay are not connected to sizing.

**Required test:** Define decay from a predeclared forecast horizon, estimate
impact from the existing conservative proxy, and measure net Sharpe, fill rate,
opportunity cost, and turnover. Do not infer market depth from daily OHLCV.

### 6. Scenario-conditioned risk limits

**Hypothesis:** A single unconditional risk limit is too blunt; limits should
respond to completed inflation, growth, liquidity, and correlation scenarios.

**Oryntra gap:** Market context can classify SPY/QQQ, breadth, and correlation,
but it is not yet a documented multi-driver scenario engine.

**Required test:** Treat scenarios as stress diagnostics first. Require dated
inputs, availability timestamps, regime persistence rules, and a no-lookahead
mapping. Only a confirmed improvement on unseen periods could justify making a
scenario multiplier active.

### 7. Tail dependence and conditional correlation

**Hypothesis:** Pearson correlation understates joint downside movement during
stress; downside or rank-based dependence may identify concentration earlier.

**Oryntra gap:** The supervisor currently uses covariance and positive
correlation clusters, not lower-tail dependence or regime-conditioned estimates.

**Required test:** Compare downside correlation, rank correlation, and the
current stressed covariance with minimum-sample and shrinkage safeguards. Track
false de-risking, recovery lag, turnover, and worst-period Sharpe.

### 8. Capacity-aware portfolio turnover budgets

**Hypothesis:** A portfolio with good signal Sharpe can lose its advantage when
rebalance turnover and impact cluster in the same sessions.

**Oryntra gap:** Trade-level costs exist, but there is no portfolio turnover
budget that prioritizes the highest expected net contribution when capacity is
scarce.

**Required test:** Define a fixed daily turnover budget, rank candidate trades by
expected net benefit using only completed information, and compare skipped-order
opportunity cost against unrestricted and current participation-capped books.

### 9. Online drift and model retirement triggers

**Hypothesis:** A model should lose allocation when its live research diagnostics
degrade, but the trigger must not become a hindsight optimizer.

**Oryntra gap:** Research reports record IC and consistency, but there is no
precommitted retirement or quarantine state shared by every TBA.

**Required test:** Freeze alert thresholds and minimum observation windows before
the evaluation period. Compare quarantine/re-entry rules against a fixed model,
including missed upside, transaction cost, and recovery lag.

### 10. Hidden-state/regime models with stability checks

**Hypothesis:** A small, regularized regime model may identify different
signal behavior without requiring a large neural predictor.

**Oryntra gap:** Regime ideas exist as research foundations, but no promotion
gate requires state persistence, transition stability, or out-of-sample state
coverage.

**Required test:** Fit states only on the training window, freeze transition and
feature definitions, report state occupancy and transition drift, and reject
models whose improvement comes from a rare state or a single crisis year.

### 11. Dynamic risk-limit allocation across TBA sleeves

**Institutional lead:** Jane Street's public Limshare description treats risk
limits as scarce resources that can be allocated dynamically while preserving a
global bound.

**Oryntra gap:** The current 10% contribution cap is a static name-level rule;
it does not decide which TBA sleeve should receive a scarce portfolio risk
budget when multiple sleeves compete.

**Required test:** Define sleeve identities, a global risk budget, allocation
priority, minimum/maximum sleeve limits, and a replayable rejection reason.
Compare static caps with dynamic allocation using identical signals and costs;
do not let recent returns alone award more risk without a precommitted rule.

### 12. Time-travel risk decision replay

**Institutional lead:** Jane Street publicly describes replaying historical
state to understand why a risk request was accepted or rejected.

**Oryntra gap:** Reports contain outcomes and audits, but not yet a complete
replay artifact for every target, limit, covariance input, and rejected request.

**Required test:** Persist the exact prior-close state, configuration hash,
requested weight, approved weight, binding constraint, and next-open action.
Replay must reproduce the original decision byte-for-byte before it is used for
model tuning.

### 13. Central risk-and-PnL data model

**Institutional lead:** Citadel describes independent portfolio-risk oversight,
constant monitoring, automated testing, and shared risk/PnL analytics.

**Oryntra gap:** TBA reports are model-specific; cross-model common exposures,
stress results, and PnL attribution are not yet joined in one ledger.

**Required test:** Build report-only joins for all TBA runs: position, sleeve,
factor/correlation exposure, limit status, realized PnL, stress PnL, and data
fingerprint. Validate that aggregation preserves strategy-level totals and does
not create a new execution path.

### 14. Robust optimizer anchored to model views

**Institutional lead:** AQR publicly describes anchoring a constrained optimizer
to unconstrained predictive views while accounting for covariance, constraints,
transaction costs, and estimation error.

**Oryntra gap:** The current supervisor projects risky weights downward but does
not compare that projection with a robust view-preserving optimizer.

**Required test:** Freeze the signal-implied portfolio first, then compare the
10% cap, a constrained view-anchored optimizer, and equal-risk construction.
Record every objective, penalty, constraint, solver status, turnover, and
capacity assumption. Reject any optimizer that improves only in-sample or
cannot reproduce its decision from the manifest.

### 15. Blind-factor risk discovery

**Institutional lead:** Man Numeric describes a statistical factor risk model
that discovers portfolios explaining current cross-sectional risks, then
combines those findings with explicitly defined forward-looking factors.

**Oryntra gap:** The current covariance and family diagnostics can show
correlation, but they do not identify and track latent return directions as
named risk factors.

**Required test:** Build a report-only rolling factor decomposition with fixed
normalization, shrinkage, rotation, explained-variance, composition-stability,
and missing-data rules. Compare discovered factors with existing families and
reject any factor that is unstable or is quietly used as an alpha forecast.

### 16. Conviction-aware risk budgets

**Institutional lead:** Man AHL describes using expected returns, correlation,
uncertainty, and conviction to modify portfolio allocations and net overlapping
strategy exposures.

**Oryntra gap:** TBA sleeves currently receive fixed construction rules; their
recent validation quality is not a bounded, auditable input to a shared risk
budget.

**Required test:** Define confidence from completed validation observations,
shrink it toward neutral, cap its effect, and compare it with equal sleeve
weights. Store the confidence distribution and overlap netting. A human label
such as “high conviction” cannot enter the calculation.

### 17. Constraint-unwind cascade simulation

**Institutional lead:** Two Sigma describes how shared loss limits, correlated
positions, and sequential unwinds can create a weakness cascade that sells even
otherwise strong or liquid positions.

**Oryntra gap:** Capacity and risk controls are tested individually; there is no
scenario showing what happens when several TBA sleeves hit constraints at once.

**Required test:** Add a scenario-only ledger with simultaneous loss limits,
correlation convergence, liquidity caps, and explicit unwind ordering. Report
forced-sale overlap, remaining risk, skipped orders, and recovery. Keep it
separate from live signal decisions until independent evidence exists.

### 18. Risk-forecast quality gate

**Institutional lead:** Man Group’s dynamic-risk discussion treats risk
forecasting as a separate research problem from return forecasting.

**Oryntra gap:** A dynamic risk control can sound plausible while being a poor
forecast. The recent drawdown bundle showed that a risk mechanism can lower
Sharpe even when its intention is sensible.

**Required test:** Score volatility, tail-loss, correlation, and capacity
forecasts independently with calibration, rank, and error metrics before any
forecast may scale exposure. Require an improvement over naive estimates and a
separate untouched-period portfolio gate.

## Research intake rule

For every manager, paper, or open-source framework idea added here, preserve the
primary source, the exact disclosed mechanism, Oryntra's missing capability,
the data contract, the tunable parameters, the likely failure mode, and the
predeclared promotion gate. A compelling institutional name is not evidence
that a technique is transferable or profitable.

## Suggested research order

1. **Decision replay and centralized risk/PnL ledger** — immediately testable
   with existing Oryntra outputs; improves auditability without changing alpha.
2. **Risk-forecast quality gate** — test whether volatility, tail, correlation,
   and capacity estimates are actually informative before using them to scale.
3. **Dynamic risk-limit allocation** — apply to the existing TBA sleeves after
   their contributions and overlap are observable.
4. **Conviction-aware sleeve budgets** — requires stable out-of-fold quality
   statistics and explicit uncertainty shrinkage.
5. **Blind-factor discovery** — useful once the ledger can show factor stability
   and composition drift.
6. **Robust view-anchored optimization** — only after the current cap and risk
   forecasts have a complete comparison ledger.
7. **Constraint-unwind cascade scenarios** — valuable for stress testing, but
   keep it report-only until its assumptions are independently defensible.

8. **Simulation-to-paper-trading promotion gate** — Man AHL publicly describes
   a two-stage process: long-history simulation followed by a live paper-trading
   period before capital is committed. Oryntra currently compares historical
   periods but does not have a time-based paper ledger that freezes the model
   while new observations arrive. Add a research-only promotion state with a
   frozen code/config hash, timestamped predictions, missed fills, data-quality
   events, and a predeclared pass/fail rule. This is a validation control, not
   an alpha feature.
9. **Independent replication gate** — AQR describes having a second researcher
   independently reproduce an internal result. For Oryntra, require a separate
   runner/configuration to regenerate a candidate's signals and metrics from the
   same frozen data contract, then compare artifacts before any tuning round.
   This targets implementation errors and accidental leakage that ordinary
   unit tests may miss.
10. **Similarity-based regime analogs** — Man Group describes measuring current
    conditions against historical multivariate analogs and evaluating subsequent
    factor returns. A report-only version could rank analog windows from
    macro/market features, expose the analog set and distance metric, and use it
    only to condition validation slices. It must not silently become a timing
    signal; require minimum analog count, distance stability, and untouched-year
    tests.
11. **Turnover-saving short-horizon overlay** — AQR describes using a short-term
    view to inform trades in a slower portfolio without trading the short-term
    view independently. Oryntra could test a bounded “do-not-trade-against”
    overlay that delays or accelerates existing TBA rebalance orders only when
    expected benefit exceeds estimated costs. Compare against a no-overlay
    control with turnover, slippage, holding-time, and alpha-decay reporting.
12. **Correlated-order-flow impact** — Man Group’s market-impact research argues
    that an order’s cost can affect later correlated orders, not just the current
    fill. Add a portfolio-level impact ledger that groups same-direction orders
    by asset, family, and time window, then charges a decaying future-flow cost.
    Test it against the current per-trade cost model using turnover, fill rate,
    net alpha, and capacity curves. Keep the impact estimate conservative and
    report-only until it is calibrated on real execution data.
13. **Alpha-decay and crowding monitor** — Man’s systematic-investing material
    emphasizes tracking decay after launch, sensitivity to data release, and
    crowded reversals. Add a rolling vintage panel that compares each model’s
    forecast rank, realized spread, turnover, cross-model overlap, and net alpha
    from its first observation onward. Trigger investigation—not automatic
    retuning—when decay or crowding thresholds are breached.
14. **Adaptive execution-speed sleeve** — Man AHL’s trend research separates
    signal speed from execution speed and describes passive execution when the
    trend horizon allows it. Test a bounded execution scheduler that chooses
    immediate, staged, or delayed fills based on signal half-life, spread,
    volatility, and expected impact. It must preserve the target position and
    compare net results against the current next-open assumption.
15. **Periodic refit with a frozen challenger** — Man’s ML discussion notes that
    models may be refreshed periodically while warning about capacity, crowding,
    and shortened half-lives. Create champion/challenger state: the champion
    remains live for the full evaluation window while a refit challenger is
    trained only on data available at that date. Promotion requires an untouched
    comparison and a stability/turnover budget, not just a better recent score.
16. **Effective breadth instead of position count** — BlackRock’s concentration
    work and HHI framing distinguish the number of holdings from the number of
    independent risk contributors. Add an effective-breadth report at the
    position, signal, family, and latent-factor levels. Use it as a diagnostic
    and stress-test stratifier first; do not reward a model for adding names that
    all load on the same driver.
17. **Orthogonal factor lens** — Two Sigma describes building a parsimonious,
    actionable factor lens by regressing complex asset returns on liquid core
    factors and retaining the residual as a marginal risk factor. Extend the
    existing factor diagnostic with rolling residualization, factor-selection
    stability, and explicit separation of common beta from residual return.
    Promotion requires stable exposures and no look-ahead in factor construction.
18. **Driver-based stress scenarios** — Bridgewater’s All Weather framework
    organizes stress around growth and inflation surprises rather than isolated
    historical dates. Add scenario templates that shock named drivers and map
    each TBA position/sleeve to those drivers, then report losses, concentration,
    and recovery without feeding the scenario directly into trading decisions.
19. **Alpha/beta separation ledger** — Bridgewater and Two Sigma both emphasize
    distinguishing strategic beta from active alpha. Require every study to
    report gross return, benchmark beta, beta-adjusted alpha, residual risk, and
    factor attribution from the same frozen period. Reject “improvement” claims
    when the return gain is only larger market exposure.
20. **Point-in-time data snapshots** — A research result should use the data
    that would actually have been available on each historical date. Add an
    immutable snapshot identifier, publication/availability timestamp, revision
    timestamp, and universe-membership record to every feature and bar bundle.
    A backtest should fail closed when a feature’s availability date is after its
    simulated decision time.
21. **Multiple-testing-aware scorecards** — AQR’s multiple-testing research and
    Man’s backtesting discussion both warn that repeated searches inflate the
    apparent Sharpe. Track the number and relationship of candidate trials,
    report raw and trial-adjusted statistics, and apply a predeclared evidence
    haircut before comparing a newly tuned TBA against an old champion.
22. **Data-vendor reconciliation and quality contracts** — Man describes bespoke
    cleansing/quality pipelines and comparing automated outputs with manual views;
    AQR documents return discrepancies between data sources. Add per-field
    validation (range, split/dividend continuity, missingness, duplicate dates,
    cross-vendor tolerance) and preserve an exception ledger rather than silently
    repairing values.
23. **Feature availability and staleness risk** — Alternative and fundamental
    datasets can arrive late or retain stale values. Store first-available time,
    update cadence, age at decision, coverage, and missingness for each feature;
    test performance after realistic publication lags and with stale values held
    constant. Never fill future information backward.
24. **Model explanation as a research artifact** — Two Sigma’s interpretability
    discussion suggests using model-agnostic explanation methods. For each ML
    candidate, save feature importance/partial-dependence summaries and their
    stability across folds and time. Treat explanations as diagnostics for drift
    and leakage, not as proof of causality or permission to add features.
25. **Portability and breadth-of-evidence gate** — Man’s factor research asks
    whether an effect survives across geographies, assets, and bad periods. Add
    a predeclared matrix of markets, sectors, liquidity tiers, and crisis windows;
    evaluate direction, beta-adjusted alpha, costs, and hit-rate in each cell.
    A candidate may be useful even if it is specialized, but its specialization
    must be explicit rather than mistaken for universal alpha.
26. **Specification-perturbation robustness** — Two Sigma’s factor-design work
    shows that universe, signal definition, weighting, neutrality, and sector
    treatment can materially change outcomes. For each candidate, run a small
    predeclared neighborhood of economically reasonable specifications and report
    the distribution, not the best version. Do not tune inside the holdout.
27. **Bad-period and liquidity-tail ledger** — Require every study to include
    severe drawdowns, volatility spikes, trading halts/large gaps, and the least
    liquid eligible names. Report whether the model’s claimed alpha survives
    realistic costs and whether exits remain executable.
28. **Crowding proxy without false precision** — Public manager holdings and
    shared factor exposures can provide a rough crowding indicator. Add a
    low-confidence diagnostic that reports overlap and concentration with clear
    coverage dates, but never treats incomplete holdings data as a complete map
    of institutional positioning.
29. **Research evidence graph** — Link every idea from source → disclosed
    mechanism → Oryntra gap → data contract → test specification → result →
    decision. A candidate should not be promoted from a note directly into code;
    the graph should show which evidence supports each transition and where an
    inference begins.
30. **Model cards for every TBA vintage** — Store objective, universe, feature
    availability, training window, decision cadence, costs, risk controls,
    exclusions, known failure modes, and intended use in a versioned card. This
    makes old and new models comparable and prevents a backtest configuration
    from being mistaken for a production-ready model.
31. **Experiment budget and stopping rules** — Assign each research question a
    finite trial budget, a primary metric, secondary diagnostics, and a stopping
    rule before running variants. Spending the budget without a stable out-of-
    sample improvement should close the question rather than invite endless
    tuning.
32. **Predictive versus causal separation** — Require research reports to label
    a feature as predictive, explanatory, or causal. A feature can improve
    forecasts without explaining why returns occur; causal language should be
    prohibited unless supported by a separate identification design.
33. **Model quarantine and remediation states** — Institutional governance
    treats performance exceptions as something to investigate and remediate, not
    simply tune away. Add explicit states such as candidate, shadow, active,
    watch, quarantined, retired, and restored, with a reason, approver, and
    immutable evidence bundle for every transition.
34. **Independent safeguard layer** — AHL’s public risk disclosures describe
    independent safeguards around models and data. Add a model-agnostic safety
    layer that can reject malformed signals, stale data, impossible prices,
    oversized orders, or breached risk limits without changing the model’s score.
    Measure rejects and false positives separately.
35. **Black-box plus governed forward risks** — Man Numeric describes combining
    statistically discovered blind factors with explicitly defined forward risks.
    Extend the factor-risk report so a proposed forward risk has an owner,
    rationale, proxy, effective date, sunset date, and review history; it may
    constrain risk but cannot silently become an alpha feature.
36. **Risk-model challenger and remediation backtest** — Treat covariance,
    factor, and tail-risk estimators as models that can drift. Run a challenger
    risk estimator in shadow mode, score forecast errors, and require evidence
    before replacing the incumbent. Preserve the old estimator for replay so a
    remediation does not rewrite history.
37. **Graceful degradation under data failure** — Define a per-feature and
    per-asset fallback policy before testing: hold last valid value only for a
    bounded age, reduce confidence, skip the trade, or flatten exposure. Compare
    each policy under missing, stale, conflicting, and delayed data; never let a
    silent imputation create a favorable historical result.
38. **Deterministic decision and order idempotence** — Give every model decision,
    rebalance, and simulated order a stable run/date/version identifier. Replays
    should produce the same signals and must not duplicate orders when a job is
    retried. Measure how partial fills and retries change exposure and costs.
39. **Incident replay and postmortem ledger** — Record data failures, rejected
    orders, risk overrides, and unexpected fills as immutable incidents linked to
    the affected model version. Re-run the exact state before and after a fix;
    require a regression test or documented non-fix for every incident.
40. **Resilience and recovery scenarios** — Stress the research and execution
    path with delayed feeds, missing benchmark data, process restarts, and
    partial service loss. Report stale exposure, unhedged time, recovery latency,
    and duplicate/omitted decisions separately from market PnL.

This order is a research priority, not a product roadmap or a promise that any
item will improve returns.

## Falsifiable alpha hypotheses (not approved features)

These are deliberately separate from the institutional-process controls above.
Each requires a frozen hypothesis, a cost-aware implementation, and untouched
confirmation before it can influence a TBA model.

1. **Residual signal after common-risk removal** — Remove rolling market,
   sector, and discovered-factor exposures from each candidate signal, then test
   whether the residual rank predicts next-period returns. Compare gross signal,
   residual signal, and a shuffled-label control; report whether the result is
   merely beta in disguise.
2. **Forecast calibration instead of rank-only selection** — Convert model
   scores into out-of-fold probability or expected-return bins and test whether
   realized returns are monotonic and calibrated. Use calibration to size risk
   only if it improves net performance without increasing turnover or tail loss.
3. **Meta-labeling for trade acceptance** — Keep the base TBA direction fixed,
   but train a separate, strictly out-of-fold classifier to decide whether a
   proposed trade should be accepted. Compare accepted, rejected, and all-trade
   cohorts; require a cost-aware benefit and guard against selection leakage.
4. **Signal interaction stability** — Test whether pairs or small groups of
   existing signals add information beyond their individual ranks. Predeclare
   the interaction family, cap the search space, and require the relationship to
   survive time, sector, and liquidity slices.
5. **Holding-period adaptation** — Estimate each signal’s realized decay curve
   and test whether a bounded holding-period choice improves net alpha after
   costs. The decay estimate must use only information available before the
   decision and be compared with a fixed-horizon control.
6. **Cross-sectional breadth versus conviction** — Test whether spreading risk
   across more independent names improves consistency at the same forecast
   exposure, or merely dilutes the strongest signal. Hold expected gross exposure
   constant and measure effective breadth, costs, alpha, and drawdown together.
7. **Two-stage alpha and construction architecture** — Keep return forecasting
   separate from risk-aware portfolio construction. The alpha stage emits
   forecasts with uncertainty and provenance; the construction stage applies
   costs, exposures, liquidity, and risk budgets. Compare this separation with a
   monolithic score-to-weight path so improvements cannot hide inside the
   optimizer.
8. **Shared signal library with mandate adapters** — Maintain one versioned,
   point-in-time signal library and let different TBA mandates choose bounded
   subsets, horizons, and risk targets. Measure whether signals remain useful
   after reuse and whether adapters create accidental correlated bets.
9. **Explanation drift monitoring** — Treat feature-contribution summaries as a
   live diagnostic. Alert when a model’s dominant features, sign, or marginal
   contribution shifts materially from its validated distribution, then route the
   model to review rather than silently refitting it.
10. **Forecast-to-order attribution** — Preserve the full chain from forecast to
    target weight, constrained weight, order, fill, and realized outcome. Attribute
    lost alpha separately to forecast error, risk constraints, liquidity, timing,
    and execution so tuning targets the actual bottleneck.
11. **Ensemble diversity objective** — Allocate across signals using both forecast
    quality and incremental residual return, not standalone Sharpe. A model that
    is weaker alone may be valuable if it fills a different drawdown or factor
    exposure; test this against equal-weight and best-single-model controls.
12. **Bayesian forecast shrinkage** — Shrink noisy expected-return estimates
    toward a neutral prior according to sample size, forecast error, and regime
    stability. Compare fixed shrinkage, time-varying shrinkage, and no shrinkage
    without allowing the holdout to choose the prior.
13. **Uncertainty-aware robust allocation** — Treat covariance, expected returns,
    and costs as ranges rather than point estimates. Optimize a bounded worst-case
    or distributionally robust objective and report the return paid for robustness
    against a standard optimizer.
14. **Turnover deadband and rebalance urgency** — Instead of trading every small
    target change, create a predeclared deadband based on expected alpha decay,
    spread, impact, and current deviation. Test whether waiting improves net
    alpha without allowing risk drift to accumulate.
15. **Asymmetric loss and drawdown utility** — Compare mean/variance objectives
    with explicit penalties for left-tail loss, recovery time, and consecutive
    negative periods. Require the penalty parameters to be fixed before the
    untouched test and report the return sacrificed for each protection level.
16. **Dependence-preserving resampling** — Use block or stationary bootstrap
    procedures to estimate uncertainty while preserving serial correlation,
    cross-asset dependence, volatility clustering, and turnover bursts. Report
    confidence intervals and downside probabilities, never just a resampled
    average return.
17. **Cost and fill uncertainty simulation** — Treat spread, impact, delay, and
    fill probability as distributions calibrated from observed data. Stress the
    same signal path across cost draws to learn whether the edge survives normal
    execution uncertainty rather than one optimistic cost assumption.
18. **Adversarial feature perturbation** — Perturb input features within realistic
    data-error, lag, rounding, and revision bounds. A candidate that changes
    direction under tiny plausible perturbations should be flagged as fragile,
    even if its historical score is high.
19. **Synthetic regime stress with held-out structure** — Generate scenario paths
    from fitted volatility/correlation regimes only for stress analysis, keeping
    the generator trained strictly before the test period. Use them to expose
    concentration and failure modes, not to claim extra historical alpha.
20. **Pathwise risk-of-ruin review** — Evaluate the distribution of drawdown depth,
    recovery time, capital usage, and consecutive losses across historical,
    resampled, and stress paths. Compare risk of ruin under fixed exposure,
    capped exposure, and proposed dynamic controls.
21. **Event-time integrity layer** — For news, filings, earnings, and macro
    releases, store event time, first-available time, provider receipt time,
    revision history, and market-time alignment. Test signals using the earliest
    legally/operationally available timestamp, not the article’s eventual
    corrected timestamp.
22. **News novelty and duplicate control** — Cluster near-duplicate headlines,
    syndicated articles, and repeated filings before calculating sentiment or
    surprise. Measure whether the signal adds information beyond price/volume and
    prevent multiple copies of one event from multiplying conviction.
23. **Reaction-horizon decomposition** — Separate immediate, short-horizon, and
    persistent responses to an event. A signal should declare its expected decay
    horizon and be evaluated with horizon-matched costs and holding periods.
24. **Text-model abstention and coverage** — Give a text model an explicit
    abstain state for ambiguous, low-coverage, conflicting, or out-of-domain
    documents. Test abstention rate, missed opportunities, and error cost instead
    of forcing every document into a directional score.
25. **Event-signal orthogonalization** — Residualize event features against
    existing momentum, volatility, gap, and volume signals to measure incremental
    information. If the event signal adds nothing after residualization, keep it
    as context rather than calling it a new alpha source.
26. **Borrow and short-availability realism** — For any short or market-neutral
    experiment, model borrow availability, borrow fee, recalls, locate failure,
    and forced buy-ins as time-varying inputs. A short signal that cannot be
    borrowed should become no-trade or reduced exposure, not an idealized fill.
27. **Delisting and corporate-action continuity** — Preserve delisted names,
    mergers, spin-offs, splits, special dividends, ticker changes, and bankruptcies
    in the historical universe. Test whether returns and exits remain correct when
    an asset disappears rather than silently dropping it.
28. **Trading-session and halt constraints** — Align each decision to the asset’s
    actual exchange calendar, timezone, auction, holiday, limit-up/down, and halt
    state. Report stale exposure and unfilled risk during halts separately from
    ordinary slippage.
29. **Financing and margin budget** — Include cash financing, margin requirements,
    collateral haircuts, and interest on idle or borrowed cash in portfolio tests.
    Compare gross alpha, net alpha, capital usage, and margin-call paths under
    conservative financing assumptions.
30. **Capacity by liquidity regime** — Estimate capacity separately in normal,
    stressed, and outage conditions using dollar volume, spread, depth proxies,
    and order participation. Do not summarize capacity with one average daily
    volume multiple.
31. **Multi-horizon forecast reconciliation** — Keep fast, medium, and slow
    forecasts separate, measure each horizon’s decay and overlap, and reconcile
    them with explicit uncertainty rather than letting the fastest signal dominate
    by frequency. Test conflict cases and regime transitions.
32. **Position-level order netting** — Net opposing orders across TBA sleeves
    before execution while preserving each sleeve’s intended contribution and
    attribution. Compare gross sleeve turnover with net portfolio turnover,
    slippage, and any loss of diversification.
33. **Shared capital allocator** — Allocate capital across models using common
    risk, liquidity, margin, and correlation budgets. A sleeve should be able to
    explain why it received capital and what opportunity cost it imposed on other
    sleeves.
34. **Asynchronous-information conflict policy** — When price, macro, and event
    data update at different times, store each signal’s age and define a policy
    for stale conflicts, abstention, or reduced confidence. Test the policy during
    news gaps and market closures.
35. **Portfolio-level versus sleeve-level controls** — Evaluate whether a limit
    should apply to a single strategy, a family, or the whole portfolio. Report
    cases where sleeve-level compliance still creates a portfolio-level breach.
36. **Time-varying beta estimation** — Estimate benchmark beta with a declared
    rolling window, shrinkage rule, and minimum observations; report how alpha
    changes under reasonable beta windows instead of choosing the most favorable
    one after the fact.
37. **Benchmark ensemble and attribution** — Compare a declared S&P/QQQ blend,
    each component, and relevant factor benchmarks. Attribute return to market,
    style, sector, and residual components so “alpha” is not just an omitted
    benchmark exposure.
38. **Benchmark contamination check** — Detect when a benchmark or risk factor
    contains the same signal, universe filter, or portfolio positions as the
    strategy. Flag contaminated alpha estimates and require a clean alternative
    benchmark before comparing candidates.
39. **Alpha uncertainty intervals** — Pair beta-adjusted alpha with block-aware
    confidence intervals, bootstrap uncertainty, and sensitivity to costs and
    start/end dates. Report the probability that alpha is positive rather than
    treating a point estimate as fact.
40. **Evaluation lockfile** — Freeze benchmark symbols, weights, risk-free source,
    beta method, annualization, costs, and missing-data policy in a versioned
    evaluation lockfile. A result is not comparable if the lockfile changed.
41. **Crisis-protection sleeve budget** — Test whether a small, explicitly
     budgeted defensive sleeve—such as faster trend, cash, or a defined hedge
     proxy—improves portfolio drawdown and recovery without hiding its carry cost.
     Compare the combined portfolio with each sleeve alone and a no-hedge
     control.
42. **Hedge-trigger robustness** — Evaluate defensive activation using several
     predeclared triggers (volatility, trend break, correlation convergence,
     drawdown) and measure trigger delay, whipsaw, turnover, and missed rebounds.
     A trigger cannot be selected by the crisis it was designed to explain.
43. **Tail-hedge overlap accounting** — Attribute whether a hedge protects the
    portfolio’s actual residual risks or simply duplicates an existing short or
    low-beta exposure. Report hedge cost, marginal expected shortfall reduction,
    and correlation in both calm and stressed periods.
44. **Prediction intervals instead of point forecasts** — Produce calibrated
     uncertainty bands or quantile forecasts for expected return, volatility, and
     drawdown. Test whether interval width predicts realized error and whether
     sizing by uncertainty improves net consistency without suppressing useful
     tail opportunities.
45. **Online change-point detection** — Detect structural breaks in return,
     volatility, correlation, liquidity, or feature distributions using only past
     data. Treat a detected break as a review or confidence adjustment first;
     compare false alarms, detection delay, and missed breaks before allowing any
     automatic response.
46. **Constrained online adaptation** — If a model is refit or updated over time,
     bound parameter movement, turnover, exposure changes, and update frequency.
     Keep a frozen champion and require the adaptive challenger to beat it on
     untouched rolling windows.
47. **Monotonic and sign-stability constraints** — Where an economic relationship
     is intentionally directional, test monotonic or sign-stable model constraints
     against unconstrained fits. Report whether constraints reduce overfit or
     merely remove useful nonlinear behavior.
48. **Uncertainty-aware abstention** — Permit the forecaster to decline a trade
    when prediction intervals overlap neutral after costs or when feature drift
    makes calibration unreliable. Measure abstention, opportunity cost, and tail
    protection against an always-trade control.
49. **Sealed holdout service** — Keep a final evaluation period and its data hash
    inaccessible to tuning code and reveal only a fixed result report. A model
    may be evaluated once or under a predeclared protocol, preventing repeated
    manual peeking from turning the holdout into development data.
50. **Negative-control features and labels** — Add variables that should have no
    economic relationship to the target, plus deliberately shifted labels. Any
    meaningful performance on these controls signals leakage, timestamp errors,
    or a broken evaluation path.
51. **Permutation and placebo tests** — Shuffle labels, shift event timestamps,
    or replace signals with matched-noise controls while preserving the pipeline.
    A legitimate result should disappear under a valid placebo; if it does not,
    investigate the implementation before tuning further.
52. **Adversarial research review** — Before promotion, assign a reviewer or
    automated checklist to search specifically for leakage, survivorship,
    benchmark contamination, favorable parameter choices, and omitted costs.
    Record objections and resolutions rather than only the final score.
53. **Cross-run reproducibility envelope** — Re-run the same frozen experiment
    with independent seeds, hardware/runtime versions, and a second implementation
    where practical. Report metric and order-level differences within a fixed
    tolerance; unexplained variance blocks promotion.
54. **Counterfactual execution baseline** — For each proposed order, preserve
     credible alternatives such as no trade, prior target, delayed execution,
     and a benchmark schedule. Attribute realized outcome relative to each
     baseline rather than only measuring the portfolio’s absolute return.
55. **Event-study causal hygiene** — For event features, predefine event windows,
     controls, clustering, and contamination exclusions. Separate market-wide
     reaction from incremental security-level response and avoid overlapping event
     windows that inflate sample size.
56. **Decision-level treatment effects** — Estimate whether accepting a trade
     changes outcomes relative to comparable rejected opportunities, using only
     information available at the decision time. Treat this as observational
     evidence requiring strong caveats, not proof of causality.
57. **Synthetic-control comparisons** — For concentrated positions or sleeves,
     construct a transparent matched basket or factor proxy as a counterfactual.
     Test whether the strategy adds value beyond what the matched exposure would
     have delivered, including costs and beta.
58. **Marginal decision value** — Measure the incremental contribution of each
    signal, constraint, hedge, and execution choice by removing one component at
    a time and using a predeclared order of operations. Avoid claiming value from
    a component whose benefit disappears when another dependent component is
    removed.
59. **Distribution-shift dashboard** — Compare live feature, return, liquidity,
    correlation, and universe distributions with the validated training and test
    envelopes. Track population-stability measures, tail coverage, missingness,
    and cross-sectional composition drift.
60. **Invariant signal search** — Search for relationships that remain directionally
    stable across declared market environments rather than selecting a feature
    that wins in one regime. Require enough independent environments and penalize
    features whose sign or rank ordering flips without explanation.
61. **Unlabeled-domain adaptation gate** — Before applying a model to a new
    universe or period, test only the input distribution and data contract. If
    the domain is materially different, route to shadow evaluation or abstention
    instead of silently assuming transferability.
62. **Calibration transfer check** — When a model or feature set is moved across
    sectors, liquidity tiers, or instruments, measure whether forecast calibration
    transfers. Recalibration must use a separate window and never the final
    performance holdout.
63. **Universe churn sensitivity** — Re-run studies under point-in-time universe
    membership, delayed additions/removals, and realistic listing survival. Report
    whether the model depends on today’s survivors or stable coverage that would
    not have existed historically.
64. **Optimizer feasibility ledger** — Record every constraint, its input values,
    whether the problem was feasible, any relaxation, and the final distance from
    the requested target. Never silently clip infeasible weights and report a
    portfolio as if the original objective was solved.
65. **Solution stability under perturbation** — Perturb expected returns, costs,
    covariance, and constraints within realistic estimation error and measure
    weight turnover and objective degradation. Prefer stable solutions when two
    optimizers have similar expected outcomes.
66. **Dual-value and binding-constraint diagnostics** — Save which constraints
    bind and their marginal objective cost or shadow price. This identifies whether
    a disappointing result comes from weak forecasts, capacity, risk limits, or
    an unnecessarily tight rule.
67. **Convex baseline before non-convex search** — Establish a transparent,
    reproducible convex or heuristic baseline before introducing a non-convex
    optimizer. A complex solver must beat the baseline under the same data,
    constraints, restarts, and cost assumptions—not only find a higher in-sample
    objective.
68. **Solver seed and restart envelope** — Run non-convex candidates across fixed
    seeds and restart budgets, reporting solution dispersion, failure rate, runtime,
    and out-of-sample results. A fragile optimizer should not become a production
    dependency merely because one seed found a favorable solution.
69. **Account-implementability constraints** — Model minimum order sizes, share
    rounding, fractional-share eligibility, shortability, cash buffers, settlement
    timing, and broker-specific restrictions. Report target-to-implementable
    deviation and the resulting residual risk.
70. **Order-routing uncertainty** — Treat venue, broker, order type, queue, and
    latency assumptions as explicit execution scenarios. Compare conservative,
    central, and favorable fills rather than embedding one ideal route in every
    backtest.
71. **Settlement and cash ledger** — Track unsettled proceeds, dividends,
    financing, margin availability, and cash reserved for pending orders. A trade
    that is profitable at mark-to-market can still be infeasible when settlement
    and cash timing are modeled.
72. **Broker reconciliation loop** — Reconcile intended orders, broker acknowledgments,
    fills, cancellations, positions, cash, and fees into one immutable ledger.
    Flag orphan fills and state mismatches before they can affect the next model
    decision.
73. **Bitemporal feature store** — Store both the observation timestamp and the
    time the value became available, with revisions preserved rather than
    overwritten. A query should be able to reconstruct exactly what the model
    could have known on any historical date.
74. **Incremental feature recomputation** — Recompute only affected dates/assets
    when a source or transformation changes, while recording dependency hashes.
    Compare incremental outputs with a clean full rebuild before accepting the
    result.
75. **Cache and artifact lineage** — Every cached bar, feature, signal, optimizer
    output, and report should point to its source hashes, code/config version, and
    evaluation lockfile. Invalidate caches explicitly when any dependency changes.
76. **Research compute and latency budgets** — Record runtime, memory, storage,
    and data-transfer costs alongside performance. For live-like simulations,
    enforce declared decision and execution latency budgets and report degraded
    behavior when they are exceeded.
77. **Reference implementation checksums** — Maintain a small, transparent
    reference implementation for critical indicators and portfolio math. Compare
    optimized/vectorized paths against it on randomized and edge-case fixtures so
    speed improvements cannot silently change semantics.
78. **Cross-sectional information diagnostics** — For ranking signals, report
     rank information coefficient, spread return, monotonicity, turnover-adjusted
     IC, and capacity by date—not only portfolio Sharpe. Test whether signal
     quality is broad or driven by a few extreme observations.
79. **Conditional performance matrix** — Break results down by market direction,
     volatility quintile, correlation regime, liquidity tier, sector, and holding
     horizon. Require enough observations and show uncertainty so conditional
     slices do not become another multiple-testing trap.
80. **Tail-shape and path metrics** — Report skew, kurtosis, worst gap, expected
     shortfall, maximum duration underwater, recovery ratio, and loss clustering
     alongside return and Sharpe. Compare the entire path, not just endpoint
     wealth.
81. **Turnover-adjusted information ratio** — Track information ratio per unit of
     turnover, dollar impact, and capital usage. A small gross edge that requires
     excessive trading should be visibly distinguished from a scalable one.
82. **Metric independence map** — Label metrics that share the same underlying
    return series or assumptions. Avoid presenting correlated metrics as
    independent evidence and predeclare which one is primary for promotion.
83. **Daily P&L explain** — Decompose every period’s result into price return,
    dividends/corporate actions, forecast change, sizing, benchmark beta, factor
    exposure, financing, fees, spread, impact, timing, and residual/unexplained
    P&L. Require the components to reconcile to total return within a tolerance.
84. **Expected-versus-realized execution explain** — Compare expected spread,
    impact, fill, and delay with realized values by asset, venue, order type, and
    liquidity regime. Update cost research only through a versioned process, not
    by silently changing historical assumptions.
85. **Risk-budget utilization ledger** — Track planned and realized use of
    volatility, expected shortfall, correlation, capacity, margin, and turnover
    budgets. Flag persistent underuse, sudden overuse, and limits that bind only
    after losses have occurred.
86. **Unexplained-P&L quarantine** — Define a threshold for residual P&L and
    require investigation when it is persistently positive, negative, or
    concentrated in a small number of assets/dates. A candidate with unexplained
    performance should not be promoted until the source is identified or bounded.
87. **Asset-class adapter contracts** — Define separate data, calendar, pricing,
    cost, financing, and risk contracts for equities, ETFs, futures, options,
    bonds, FX, and crypto rather than reusing stock assumptions. A shared signal
    may be tested across adapters, but its features and execution semantics must
    remain explicit.
88. **Cross-asset unit and currency integrity** — Track currency, contract size,
    multiplier, tick value, expiry, roll, and conversion timestamp for every
    instrument. Reconcile returns in native and reporting currencies and separate
    asset alpha from FX and roll effects.
89. **Instrument-specific liquidity and cost models** — Calibrate spread, impact,
    depth, fill, borrow, roll, and financing assumptions by asset class and
    venue. Do not let a liquid equity cost model silently price a future, option,
    or bond.
90. **Cross-asset transfer gate** — Before promoting a signal outside its home
    asset class, require an untouched transfer study with matched horizons,
    appropriate benchmarks, and an explicit failure explanation if the effect does
    not transfer.
91. **Roll and expiry event ledger** — For futures and options, preserve contract
    selection, roll dates, expiry, exercise/assignment, implied-volatility inputs,
    and settlement rules so historical trades are not modeled as continuous
    frictionless prices.
92. **Post-trade TCA panel** — Attribute implementation shortfall by spread,
     market impact, timing, opportunity cost, fees, venue, order type, and trader
     or scheduler decision. Compare realized costs with the model’s pre-trade
     estimate and monitor drift by liquidity regime.
93. **Logged-policy execution evaluation** — Evaluate a new execution policy only
     against historical logs or paper trades with adequate overlap and coverage.
     Use off-policy estimates with uncertainty and reject policies that require
     unsupported extrapolation beyond observed actions.
94. **Safe execution exploration** — If an execution policy is ever allowed to
     explore, cap its participation, price deviation, notional, and duration;
     require a kill switch and compare it with a conservative baseline. Exploration
     cannot change the investment signal.
95. **Opportunity-cost ledger** — Record missed fills, delayed orders, and trades
    rejected by constraints, then measure subsequent price movement and risk. This
    distinguishes useful abstention from hidden implementation drag.
96. **Frozen model tournament** — Run multiple candidates concurrently on identical
    point-in-time data, execution assumptions, risk budgets, and paper-trading
    rules. Freeze their configurations for the tournament window and rank them
    using predeclared primary and secondary metrics.
97. **Selection-blind promotion review** — Hide candidate names or prior rankings
    from the first metrics review where practical, and review the evidence bundle
    before choosing a champion. This reduces narrative preference for a familiar
    model or a single spectacular period.
98. **Tournament survival analysis** — Track candidate retirement, drawdown,
    decay, and correlation over time rather than choosing only the final return
    leader. A strategy that survives diverse environments may be preferable to a
    higher but fragile endpoint.
99. **No-retune tournament rule** — Prohibit mid-window parameter changes except
    for predeclared safety actions. Any change starts a new vintage and cannot
    inherit the old tournament’s evidence.
100. **Rebalance-calendar sensitivity** — Repeat studies across nearby rebalance
     dates, frequencies, weekday conventions, and holiday handling. A strategy
     whose result depends on one arbitrary calendar should be labeled timing-
     sensitive rather than broadly robust.
101. **Open/close execution convention** — Compare next-open, same-close,
     volume-weighted, delayed, and staged execution only when the required data
     would have been available. Report the cost and signal-decay tradeoff for each
     convention.
102. **Timestamp jitter test** — Shift signal availability and execution timestamps
     by small realistic amounts to detect look-ahead, clock alignment, or stale
     price assumptions. Material sign changes should trigger investigation.
103. **Rebalance clustering control** — Detect when many holdings or sleeves trade
    on the same dates and model correlated impact, capacity, and market exposure
    rather than charging independent average costs.
104. **Risk-observability severity model** — Classify alerts as informational,
     warning, action-required, or emergency using predeclared thresholds and
     affected capital. Keep signal-quality, data-quality, execution, and
     operational alerts distinct.
105. **Alert deduplication and correlation** — Group repeated alerts from the same
     root cause and show affected models, sleeves, assets, and time windows. Do
     not let a flood of correlated messages hide the first actionable failure.
106. **Acknowledgment and escalation ledger** — Record who/what acknowledged an
     alert, the decision taken, evidence reviewed, and whether the issue recurred.
     An unacknowledged risk breach should remain visible until resolved or
     explicitly accepted with an expiry.
107. **Silent-failure sentinels** — Monitor expected event counts, heartbeat
     freshness, position/order reconciliation, benchmark availability, and report
     generation. Alert when expected outputs disappear, not only when values look
     extreme.
108. **Monitoring backtest** — Replay historical incidents and synthetic failures
     through the alert system to measure detection delay, false positives,
     deduplication, escalation, and recovery. Monitoring quality should itself be
     tested rather than assumed.
109. **Data-source incremental value** — Add alternative datasets one at a time
     to a fixed baseline and measure incremental net alpha, coverage, turnover,
     latency, compute, and licensing cost. A correlated data source should not
     receive credit merely for restating an existing feature.
110. **Vendor redundancy and disagreement** — Compare independent providers or
     proxies for the same concept and quantify disagreement, missingness, revision
     lag, and historical availability. Use disagreement as a confidence signal or
     abstention trigger only after validation.
111. **Alternative-data half-life** — Track signal efficacy from first availability
     through decay, crowding, and changes in collection methodology. Separate
     genuine market decay from provider schema or coverage changes.
112. **Acquisition-cost break-even** — Compute the minimum net contribution needed
     to cover data fees, engineering, storage, compliance, and monitoring. Compare
     that break-even with uncertainty bands rather than using gross backtest alpha.
113. **Coverage and missingness selection bias** — Test whether data availability
     itself predicts size, sector, geography, liquidity, or future survival. Report
     results with coverage masks and a no-data control to expose selection effects.
114. **Research/production environment separation** — Keep research engines,
     TBA candidates, credentials, data stores, and public routes in explicit
     separate environments. A research result should be exportable as an artifact
     without making the candidate selectable or executable in production.
115. **Promotion artifact and permission gate** — Require a signed/versioned
     promotion artifact containing the model card, evaluation lockfile, tests,
     risk limits, and approval state before a model can cross environments.
116. **Feature-flag fail-closed behavior** — Unknown, missing, or contradictory
     feature flags should disable the candidate or return a safe no-op, not fall
     through to an older or broader model. Test startup, rollback, and partial
     configuration states.
117. **Deployment reproducibility envelope** — Pin runtime, dependency, model,
     data, and configuration versions; rebuild from a clean environment and
     compare outputs before accepting a deployment.
118. **Access and secret-scope audit** — Separate read-only research access from
     broker execution, production data, and administrative credentials. Record
     access attempts and test that a research process cannot submit orders.
119. **Explicit override policy** — Define which human or external-risk overrides
     are allowed, their scope, duration, evidence requirement, and expiration.
     An override must be a separate decision layer, never an unlogged mutation of
     the model’s forecast or weights.
120. **Overlay shadow comparison** — Run any proposed discretionary or macro
     overlay in shadow alongside the untouched systematic model. Attribute its
     incremental return, risk, turnover, missed opportunities, and correlation
     before allowing it to constrain live decisions.
121. **Override attribution and counterfactual** — For each intervention, preserve
     the systematic action, the overridden action, rationale, timestamp, and later
     outcome under both paths. Do not credit an override without showing what the
     unchanged system would have done.
122. **Override fatigue and consistency** — Monitor frequency, concentration,
     reversals, and time-to-expiry of interventions. Repeated overrides of the
     same model failure should become a research hypothesis or model change, not
     permanent discretionary patching.
123. **Decision explanation contract** — For every forecast or trade, expose the
     model vintage, data freshness, dominant signals, uncertainty, benchmark beta,
     risk constraints, expected costs, and abstention reasons in a stable schema.
     Explanations must describe contribution, not claim unsupported causality.
124. **Human-readable risk narrative** — Translate technical risk outputs into a
     consistent narrative covering exposure, concentration, liquidity, tail risk,
     and scenario sensitivity. The narrative must be generated from the same
     ledger as the numerical report and preserve links to evidence.
125. **Explanation consistency tests** — Verify that a displayed explanation
     changes when the underlying decision inputs change and remains identical on a
     deterministic replay. Test for stale, contradictory, or omitted fields.
126. **Uncertainty and limitation disclosure** — Show coverage, missingness,
     validation domain, confidence intervals, and known failure modes alongside
     any score. Do not render a precise-looking number without its uncertainty or
     valid-domain context.
127. **Multi-objective promotion frontier** — Evaluate candidates on beta-adjusted
     alpha, consistency, drawdown, expected shortfall, turnover, capacity,
     implementation complexity, and data cost. Show the Pareto frontier instead
     of collapsing every tradeoff into one tunable score.
128. **Minimum-evidence constraints** — Require hard floors for data coverage,
     out-of-sample observations, reproducibility, and execution realism before a
     candidate may compete on return. A high-return model with insufficient
     evidence should be ineligible, not merely penalized.
129. **Complexity-adjusted selection** — Charge explicit research and operational
     costs for additional features, dependencies, parameters, latency, and
     monitoring burden. Compare a simpler model’s net evidence with a complex
     model’s incremental benefit.
130. **Stability-weighted scorecard** — Weight evidence by the number of independent
     environments, trials, and observations supporting it rather than treating
     every annual slice as equally informative. Make the weighting rule fixed
     before candidate selection.
131. **Leverage hysteresis** — Test separate thresholds for reducing and restoring
     exposure so a model does not churn around one volatility or drawdown boundary.
     Measure lag, whipsaw, turnover, recovery, and residual risk against a
     single-threshold control.
132. **Procyclicality stress test** — Simulate rising volatility, widening spreads,
     falling collateral values, and shrinking capacity together. Determine whether
     risk controls force sales into the worst liquidity or create a feedback loop.
133. **Funding-liquidity waterfall** — Define the order in which cash, liquid
     assets, hedges, and positions would be used under margin or financing stress.
     Report forced-sale overlap, residual exposures, and time to restore required
     buffers.
134. **Leverage attribution** — Separate return caused by signal skill from return
     caused by changing gross/net exposure, financing, and volatility targeting.
     Compare fixed-risk, fixed-dollar, and adaptive-leverage controls under the
     same benchmark and cost assumptions.
135. **Margin-model uncertainty** — Stress broker/clearing haircuts, maintenance
     requirements, concentration add-ons, and intraday calls as ranges. A strategy
     should not pass on a single optimistic margin schedule.
136. **Mandate identity contract** — Treat benchmark, target volatility, leverage,
     liquidity, turnover, drawdown, holding period, universe, currency, and capital
     as a versioned mandate. Changing a material objective starts a new study and
     invalidates direct comparisons with the old mandate.
137. **Objective-change attribution** — When a model improves after a target,
     benchmark, or constraint change, separate the benefit from the objective
     change itself, the signal, and the optimizer. Preserve a matched old-mandate
     replay.
138. **Risk-appetite scenarios** — Evaluate the same forecasts under conservative,
     central, and aggressive risk appetites with fixed signal inputs. Report the
     performance/risk frontier rather than selecting the appetite that produces
     the best return.
139. **Capital-scale sensitivity** — Re-run candidates across account sizes and
     capital allocations, including fixed costs and capacity. A strategy should
     disclose the scale at which its behavior or net alpha changes materially.
140. **Mandate conflict detection** — Detect incompatible settings such as a high
     turnover target with illiquid assets, tight drawdown limits with slow exits,
     or high leverage with insufficient margin. Fail clearly before optimization.
141. **Covariance validity and repair ledger** — Record whether covariance and
     correlation matrices are positive semidefinite, how missing data and thin
     samples were handled, and any eigenvalue shrinkage or projection applied.
     A repair should be visible and versioned, not an invisible optimizer fix.
142. **Dependence-estimator ensemble** — Compare rolling sample covariance,
     shrinkage, factor, robust, and regime-conditioned estimators on forecast
     error and portfolio stability. Do not select the estimator solely by the
     backtest return it enabled.
143. **Tail-dependence validation** — Evaluate whether the dependence model
     captures co-movement during drawdowns, liquidity stress, and correlation
     convergence. Compare Gaussian, t-like, empirical, and conservative stress
     alternatives where data supports them.
144. **Correlation forecast calibration** — Score predicted pairwise or cluster
     correlations against realized outcomes using rolling calibration and error
     bands. A correlation shock control should earn trust only if its forecast is
     informative.
145. **Risk-model missingness policy** — Define what happens when an asset lacks
     enough history, a factor disappears, or a correlation estimate is stale:
     borrow a bounded peer estimate, reduce exposure, or abstain. Test each path
     rather than filling silently.
146. **Anomaly-decay curve** — Model expected edge as a function of strategy age,
     crowding proxy, implementation friction, and data-release exposure. Use the
     curve for research horizon and capacity analysis, not for optimistic
     extrapolation of returns.
147. **Forecast-noise no-trade band** — Estimate the uncertainty in expected-return
     changes and suppress rebalances whose expected benefit is inside a declared
     noise band after costs. Compare turnover saved, missed alpha, exposure drift,
     and tail behavior with an always-rebalance control.
148. **Mechanism-level idea deduplication** — Map each proposal to its claimed
     mechanism, affected layer, required data, and expected failure mode. Flag
     ideas that are only renamings or parameter variants of an existing control
     before they consume a new tuning budget.
149. **Idea status state machine** — Give every proposal explicit states such as
     captured, sourced, specified, queued, tested, rejected, deferred, promoted,
     or superseded. Record why a state changed and link the evidence bundle.
150. **Novelty and overlap review** — Before adding a research idea, compare its
     mechanism and data contract with the existing backlog, current code, and
     prior results. Keep a deliberate “not new” decision instead of forcing every
     source into a fresh feature.
151. **Failure-mode clustering** — Group ideas by the failure they address:
     leakage, crowding, capacity, tail dependence, execution, drift, or
     operations. Prioritize gaps with repeated failures and avoid stacking many
     controls on one already-covered failure.
152. **Expected information value** — Estimate what uncertainty an experiment
     could resolve, how many downstream decisions it affects, its compute/data
     cost, and the cost of waiting. Prioritize experiments that can invalidate or
     narrow many assumptions, not only those with high upside narratives.
153. **Research-risk budget** — Limit simultaneous unvalidated changes, new data
     sources, optimizer variants, and model families. Keep enough capacity for
     replication, negative controls, and untouched confirmation.
154. **Sequential experiment stopping** — Define evidence thresholds for stop,
     continue, replicate, or abandon before observing results. Do not keep running
     variants after a question has become clearly unsupported.
155. **Dependency-aware research queue** — Represent which ideas require point-in-
     time data, execution logs, broker state, risk forecasts, or prior controls.
     Block dependent experiments until prerequisites are genuinely available.
156. **Source-evidence grading** — Classify sources as disclosed mechanism,
     empirical result, hypothetical illustration, product marketing, or
     commentary. Record date, data scope, assumptions, and whether the result is
     independently reproducible before using it to prioritize work.
157. **Disclosure-completeness score** — Track whether a source gives data dates,
     universe, costs, turnover, benchmark, risk controls, and out-of-sample rules.
     Missing details lower confidence in transferability rather than being filled
     by assumptions.
158. **Claim-to-evidence mapping** — Link every statement in a research note to a
     source passage, calculation, or explicit inference. A source may inspire a
     hypothesis without supporting a performance claim.
159. **Replication and contradiction register** — Record independent replications,
     failed replications, contradictory studies, and reasons results differ. Do
     not let one favorable paper erase a broader mixed evidence base.
160. **Source-aging review** — Revisit ideas when data access, market structure,
     regulation, or implementation technology changes. Mark old findings as
     historical context when their assumptions may no longer hold.
161. **Reproducible experiment bundle** — Export code/config hashes, data
     manifests, evaluation lockfile, model card, logs, metrics, plots, and
     environment metadata as one immutable bundle. A result without its bundle is
     provisional and should not enter the promotion queue.
162. **Independent rerun package** — Provide a clean runner and instructions that
     can regenerate headline metrics without access to the original developer’s
     workspace, caches, credentials, or uncommitted files.
163. **Audit-friendly result schema** — Store metrics with units, annualization,
     missing-data policy, sample counts, confidence intervals, benchmark, costs,
     and calculation version. Reject ambiguous or unlabeled numeric fields.
164. **Review sign-off record** — Capture reviewer identity/role, questions,
     objections, resolutions, and approval scope. Approval of a report should not
     imply approval for production use unless the promotion scope says so.
165. **Versioned data/schema contracts** — Give every dataset, feature, signal,
     order, and result schema a version with explicit compatibility and migration
     rules. A reader should know whether a missing field means unavailable, zero,
     not applicable, or a breaking change.
166. **Backward-compatible replay** — Preserve adapters that can replay old model
     versions against the data/schema they actually used. Do not silently rerun a
     historical study through today’s parser or corporate-action logic.
167. **Migration differential tests** — When a schema or transformation changes,
     compare old and new outputs on overlapping fixtures, identify intended versus
     accidental differences, and require a new evidence bundle for material changes.
168. **Deprecation and retention policy** — Define how long raw data, revisions,
     model artifacts, logs, and approvals remain available for replay and audit.
     A deleted dependency should not make a published result unverifiable.
169. **AI research role separation** — Keep idea generation, data preparation,
     implementation, evaluation, and promotion as separate stages with explicit
     inputs/outputs. An AI assistant may propose or transform artifacts, but it
     cannot silently approve its own result.
170. **Hypothesis-to-code consistency check** — Compare the natural-language
     hypothesis, feature definitions, target, horizon, portfolio rule, and actual
     implementation before testing. Flag semantic drift such as a different
     horizon, universe, sign, or cost assumption.
171. **AI action and provenance log** — Record prompts/inputs, tool calls, files
     read or changed, generated code, reviewer decisions, and data snapshots for
     every AI-assisted research step. Keep secrets and private data out of the
     log while preserving reproducibility metadata.
172. **AI-generated idea trial budget** — Cap the number of variants an AI may
     generate for one hypothesis and require a human or fixed rule to close the
     question. Prevent endless automated search from converting noise into a
     selected backtest.
173. **Adversarial prompt/data checks** — Treat external text, documents, and
     tool output as untrusted research inputs. Detect instructions embedded in
     data, future-dated content, duplicate sources, and unsupported claims before
     they reach features or evaluation code.
174. **Tax-lot-aware implementation** — For taxable-account research, preserve
     lots, acquisition dates, cost basis, holding periods, wash-sale restrictions,
     and realized/unrealized P&L. Compare tax-aware orders with a tax-agnostic
     control without calling tax savings alpha.
175. **After-tax objective separation** — Report pre-tax alpha, taxes, fees,
     financing, and after-tax return separately. Keep the tax policy, jurisdiction,
     account type, and assumptions in the mandate identity.
176. **Tax-policy scenario range** — Stress uncertain rates, holding-period rules,
     dividends, loss offsets, and lot-selection policies. A strategy should not
     depend on one favorable tax assumption hidden in net returns.
177. **Realized-P&L timing ledger** — Track when gains/losses become realized,
     deferred, disallowed, or carried forward, and reconcile account cash with
     economic return. This is an implementation/accounting control, not a signal.
178. **Baseline-preserving scenario explorer** — Let a researcher vary one
     declared parameter, stress, or constraint while retaining the original
     baseline, data snapshot, and evaluation lockfile. Every scenario should show
     deltas rather than replacing the reference result.
179. **Common-period model comparison** — Compare model vintages on the exact same
     dates, assets, costs, benchmarks, and missing-data policy before allowing a
     performance difference to influence selection.
180. **Scenario provenance and replay** — Save scenario inputs, UI/API changes,
     generated configuration, outputs, and timestamps so another researcher can
     reproduce the “what if?” result without the original session.
181. **Sensitivity surface with uncertainty** — Display how metrics change across
     a declared grid of parameters and show uncertainty/feasibility masks. Avoid
     a single slider value becoming an implicit post-hoc optimum.
182. **Shareable evidence report** — Export a self-contained report with baseline,
     scenario delta, assumptions, limitations, data hashes, and links to logs.
     A screenshot or headline metric alone is not a research result.
183. **Endogenous-impact feedback** — Model how the strategy’s own orders alter
     price, spread, future liquidity, and later signal returns when size is not
     negligible. Compare exogenous-cost and feedback-aware scenarios.
184. **Shared-position crowding network** — Represent overlap between Oryntra
     sleeves, public factor proxies, and dated manager exposures as a network.
     Stress simultaneous exits and report which nodes create the largest marginal
     liquidity or correlation shock.
185. **Capacity-to-alpha feedback curve** — Re-estimate expected net alpha as
     capital scales, including impact, crowding, turnover, and signal decay. Report
     the range where adding capital reduces rather than increases expected value.
186. **Crowding early-warning validation** — Test whether a crowding proxy predicts
     future spread widening, reversal, correlation convergence, or capacity loss
     out of sample before allowing it to change exposure.
187. **Scenario-library coverage map** — Map every stress scenario to the factors,
     assets, liquidity conditions, and operational failures it exercises. Report
     untested risk drivers and scenarios that add no distinct information.
188. **Scenario freshness and drift** — Version scenario assumptions, shocks,
     instruments, and recovery rules; review whether new market structure or
     portfolio composition makes historical scenarios less representative.
189. **Scenario overlap control** — Cluster highly similar historical and
     hypothetical scenarios so repeated variants do not inflate confidence or
     turn stress selection into another tuning surface.
190. **Scenario-combination stress** — Test plausible combinations of volatility,
     correlation, liquidity, funding, and data failures instead of only one-factor
     shocks. Preserve a bounded combinatorial design and label unsupported extremes.
191. **Model inventory and dependency graph** — Maintain a registry of alpha,
     risk, cost, benchmark, data-quality, execution, and reporting models with
     owners, inputs, outputs, criticality, consumers, and failure dependencies.
192. **Validation cadence by criticality** — Set review frequency and evidence
     depth according to the model’s impact on capital, execution, safety, and
     public reporting. High-criticality components should not inherit a casual
     validation schedule from low-impact diagnostics.
193. **Model retirement and sunset** — Define objective retirement triggers for
     persistent decay, invalid data, unsupported dependencies, unacceptable error,
     or replacement. Preserve artifacts for replay after retirement.
194. **Dependency-failure propagation** — Simulate a failed data, risk, cost, or
     benchmark component and trace which models, positions, reports, and controls
     become invalid. Require safe behavior at each boundary.
195. **Model-input contract tests** — Declare units, frequency, timestamp meaning,
     null policy, allowable ranges, and adjustment state for every model input.
     Reject a run when a producer silently changes that contract.
196. **Forecast-horizon alignment** — Make signal horizon, label horizon, holding
     period, rebalance cadence, and cost window explicit and test mismatches that
     can manufacture apparent alpha through timing overlap.
197. **Uncertainty propagation through the stack** — Carry forecast, covariance,
     cost, and liquidity uncertainty into position sizes and portfolio limits
     rather than making each layer treat upstream estimates as exact.
198. **Parameter-uncertainty bands** — Refit or bootstrap plausible parameter
     ranges and report how decisions change across them. Prefer stable regions to
     the single parameter set with the best historical score.
199. **Risk-budget shadow prices** — Record the marginal expected-return cost of
     each binding risk, liquidity, leverage, and concentration constraint so a
     researcher can tell whether the optimizer is sacrificing alpha for safety.
200. **Scarce-capital allocation queue** — When sleeves compete for limited risk,
     borrow, liquidity, or compute capacity, test allocation policies and preserve
     the rejected opportunities for counterfactual review.
201. **Order-lifecycle timing integrity** — Model decision, submission, queue,
     acknowledgement, fill, cancel, and replace timestamps separately; test clock
     skew and stale acknowledgements before attributing execution alpha.
202. **Partial-fill and cancellation policy** — Compare passive, aggressive, and
     timeout-based responses to partial fills, including their effect on residual
     exposure, costs, and later signals.
203. **Emergency liquidity ladder** — Define escalating actions for widening
     spreads, halted names, broker impairment, funding loss, and stressed margin;
     validate that each step reduces risk without assuming instant execution.
204. **Decision disagreement gate** — Measure disagreement among alpha, risk,
     benchmark, and execution models. Test abstention or reduced sizing when the
     disagreement is large, rather than treating consensus as a hidden feature.
205. **Counterfactual trade attribution** — For every filled or skipped decision,
     estimate the outcome under the nearest feasible alternatives: no trade,
     delayed trade, smaller size, and opposite action. Separate selection skill
     from execution luck.
206. **Decision-latency stress** — Replay decisions with bounded delays, stale
     features, and missed rebalances to find strategies whose edge depends on
     unrealistic zero-latency assumptions.
207. **Data-revision replay** — Preserve the information set available at each
     decision and compare it with later revised values. Report how much performance
     changes when backtests accidentally use revised history.
208. **Universe-entry and exit audit** — Track why an asset enters or leaves the
     tradable universe, including delisting, suspension, liquidity, and metadata
     changes; test whether selection rules create survivorship bias.
209. **Signal-decay alarm calibration** — Evaluate decay alarms on known historical
     deterioration and stable periods, measuring false alarms, detection delay,
     and capital saved before permitting automatic de-risking.
210. **Policy monotonicity checks** — Test whether clearly worse liquidity, higher
     uncertainty, or larger drawdown can accidentally produce larger exposure due
     to an optimizer discontinuity or sign error.
211. **Risk-limit precedence table** — Make conflicts between issuer, sector,
     factor, portfolio, broker, and emergency limits explicit, deterministic, and
     replayable rather than dependent on call order.
212. **Recovery-path validation** — Test restart, reconnect, duplicate-message,
     partial-state, and stale-state recovery with open orders and positions; prove
     that recovery cannot create an unintended trade.
213. **Explanation stability** — Compare the reason codes and feature attributions
     for materially similar decisions. Investigate explanations that change while
     the underlying evidence barely changes.
214. **Research-queue value scoring** — Rank experiments by expected information
     gain, decision relevance, cost, and risk of false discovery instead of by
     headline backtest return alone.
215. **Causal event-study controls** — For event-driven features, predefine
     matched controls, estimation windows, and placebo dates so the measured
     reaction is not merely a market-wide or sector-wide move.
216. **Missingness-mechanism stress** — Distinguish missing completely at random,
     vendor outages, exchange halts, and selection-driven missingness; test each
     policy separately rather than collapsing all gaps into one imputation rule.
217. **Feature-availability SLA** — Measure when each feature becomes available,
     its expected delay, and its stale-data tolerance. Penalize or abstain when the
     service-level assumption is violated.
218. **Benchmark-exposure stability** — Re-estimate beta and benchmark mix across
     windows, regimes, and rebalance frequencies; report whether alpha survives
     plausible benchmark specification changes.
219. **Turnover decomposition** — Attribute turnover to signal change, risk change,
     benchmark drift, corporate actions, constraints, and forced exits so a cost
     increase has an actionable cause.
220. **Drawdown-recovery objective** — Measure time under water, recovery capital,
     and opportunity cost alongside maximum drawdown; test whether a risk change
     improves recovery without hiding losses through a shorter sample.
221. **Limit-utilization distribution** — Record how often each limit binds, by
     asset and regime, and distinguish useful protection from a permanently
     inactive or accidentally dominant constraint.
222. **Deterministic optimizer replay** — Re-run identical inputs across machines,
     seeds, and parallelization settings; explain any different weights before
     treating a result as reproducible.
223. **Vendor redundancy and disagreement** — Compare independent data vendors or
     transforms for the same field, quantify disagreement, and define a safe
     policy when the sources diverge.
224. **Version-pinned research package** — Pin code, dependencies, feature schemas,
     data snapshots, seeds, and configuration together so a historical result can
     be rebuilt after the surrounding environment changes.
225. **Sequential-testing alpha budget** — Track every look at a holdout and spend
     a declared false-discovery budget over time instead of treating repeated
     peeking as one test.
226. **Bayesian prior sensitivity** — Compare reasonable priors for expected return,
     risk, costs, and regime persistence; report whether conclusions depend on an
     arbitrary prior rather than observed evidence.
227. **Hierarchical cross-asset shrinkage** — Borrow strength across related assets
     only through a declared hierarchy, testing whether shrinkage improves OOS
     stability without transferring stale or spurious relationships.
228. **Neutralization-order audit** — Test whether sector, beta, size, liquidity,
     and factor neutralization order changes the signal, and freeze the order
     before evaluating performance.
229. **Feature-orthogonality drift** — Monitor whether supposedly distinct signals
     become correlated across regimes, causing hidden concentration in one driver.
230. **Order-selection bias study** — Compare fills sampled from all eligible quotes
     with fills sampled only from observed executions to quantify optimistic queue
     and selection assumptions.
231. **Benchmark-rebalance contamination** — Test whether benchmark constituent or
     weight changes leak future information into universe selection, labels, or
     factor construction.
232. **Overnight/intraday decomposition** — Split P&L, risk, costs, and drawdown by
     overnight gaps, opening auction, regular session, and closing auction.
233. **Carry and financing decomposition** — Separate price alpha from dividends,
     borrow, funding, FX carry, and other holding economics under realistic timing.
234. **Risk-of-ruin distribution** — Simulate path-dependent loss and liquidity
     outcomes, not just average return and Sharpe, under parameter uncertainty and
     clustered stress.
235. **Factor-timing versus factor-exposure attribution** — Separate returns from
     changing exposure to a persistent factor from returns attributable to timing
     or selection within that factor.
236. **Residualization leakage check** — Verify that cross-sectional residuals use
     only information available at the decision timestamp and that future universe
     membership cannot influence the neutralization fit.
237. **Cost-model calibration ladder** — Compare spread, impact, delay, borrow,
     and fee assumptions with realized execution evidence at progressively more
     conservative levels.
238. **Impact-uncertainty bands** — Treat market-impact parameters as distributions
     conditioned on size, urgency, liquidity, and volatility rather than one fixed
     coefficient.
239. **Dynamic-universe churn stress** — Perturb listing, delisting, liquidity, and
     eligibility thresholds to measure how much return and turnover depend on
     unstable membership boundaries.
240. **Allocation hysteresis test** — Compare immediate rebalancing with bounded
     turnover bands and minimum holding intervals; test whether tiny forecast noise
     causes unnecessary trading.
241. **Tail-dependence validation** — Test whether assets that look diversified in
     normal covariance become jointly exposed during drawdowns using conditional
     dependence and joint-tail scenarios.
242. **Diversification illusion report** — Decompose effective breadth after common
     factors, liquidity, currency, issuer, and crowding are accounted for; do not
     count highly redundant positions as independent bets.
243. **Covariance-estimator challenger set** — Compare shrinkage, robust, factor,
     and regime-conditioned covariance estimates on identical frozen windows and
     record when each fails.
244. **Stress-consistent position sizing** — Ensure sizing uses the same stress,
     liquidity, and correlation assumptions used in portfolio loss estimates rather
     than mixing incompatible risk views.
245. **Lineage-completeness score** — Score each reported metric by whether its
     raw inputs, transformations, filters, and versions are recoverable; flag
     attractive results with incomplete lineage as lower-confidence evidence.
246. **Semantic schema-diff review** — Detect changes in field meaning, units,
     adjustment status, category labels, and default values even when a schema
     remains syntactically compatible.
247. **Calendar and clock anomaly tests** — Stress holidays, daylight-saving
     changes, leap days, early closes, timezone conversion, and out-of-order bars
     before trusting session or event features.
248. **Venue-fragmentation execution study** — Compare routing assumptions across
     venues, dark pools, auctions, and consolidated feeds; measure whether one
     venue’s fill model is being generalized incorrectly.
249. **Broker-failover rehearsal** — Replay credential expiry, disconnects, rejects,
     duplicate acknowledgements, and broker switching with open exposure and
     idempotent order identifiers.
250. **Collateral and margin waterfall** — Model which assets are liquidated first,
     haircut changes, settlement timing, and borrowing constraints under stress.
251. **Cash and settlement forecast** — Forecast unsettled cash, dividends, fees,
     borrow charges, deposits, withdrawals, and margin calls so apparent buying
     power cannot create impossible portfolios.
252. **Ensemble stress diversity** — Measure whether model ensembles disagree only
     in normal periods but fail together under the same stress; reward independent
     error structure, not just average backtest return.
253. **Alert-fatigue calibration** — Measure alert volume, duplicate rate, response
     time, missed incidents, and escalation quality; tune thresholds against human
     attention rather than simply maximizing detection.
254. **Monitoring counterfactuals** — For each alert or guardrail, estimate the
     avoided loss, false intervention, and time-to-detection if it had been absent
     or delayed.
255. **Information embargo tests** — Apply realistic publication, vendor, and
     processing delays to event and fundamental data; test whether alpha survives
     without same-bar availability.
256. **Event-cluster treatment** — Group related announcements, revisions, and
     follow-ups into episodes so one economic shock is not counted as many
     independent observations.
257. **Corporate-action equivalence** — Verify that splits, dividends, mergers,
     symbol changes, and spin-offs preserve economically equivalent positions and
     do not create artificial jumps or missing exposure.
258. **Liquidity-horizon mismatch** — Compare the horizon used in risk forecasts
     with the time actually needed to liquidate each position under normal and
     stressed participation limits.
259. **Liquidation-order sensitivity** — Test alternative unwind sequences and
     constraints to reveal whether portfolio loss depends on an arbitrary order of
     selling correlated or illiquid positions.
260. **Change-impact attribution** — When code, data, features, costs, or limits
     change, run a paired replay and attribute metric deltas to each declared
     change rather than accepting an aggregate improvement.
261. **Rollback-readiness drill** — Verify that a model or configuration can be
     rolled back to the last accepted version without losing positions, orders,
     lineage, or audit records.
262. **Feature embargo boundary audit** — Test joins across filing, news, vendor,
     and price datasets for timestamp truncation, timezone errors, and accidental
     future rows at the exact boundary.
263. **Stress-scenario representativeness** — Compare synthetic and historical
     shocks on factor, liquidity, correlation, and recovery characteristics before
     using synthetic scenarios for promotion decisions.
264. **False-intervention cost curve** — Quantify the return, turnover, and risk
     cost of unnecessary de-risking across alert thresholds and recovery rules.
265. **Quote-staleness stress** — Mark quotes by age and test fill, spread, and
     signal decisions when the best displayed price is stale or withdrawn.
266. **Adverse-selection attribution** — Separate the cost of crossing the spread
     from the cost of trading just before unfavorable price movement.
267. **Feasibility-frontier audit** — Record infeasible optimizer requests and map
     the nearest feasible portfolio, rather than silently relaxing constraints.
268. **Risk-model backtest** — Evaluate realized volatility, correlations, factor
     exposures, and drawdowns against the risk model’s prior forecasts by horizon.
269. **Stress-coverage score** — Score stress scenarios by distinct risk drivers,
     data support, historical analogues, and recovery assumptions instead of by
     scenario count.
270. **Beta-drift hedge study** — Test hedge effectiveness as beta changes within a
     rebalance interval, including gap risk and hedge execution costs.
271. **Cash-drag attribution** — Separate uninvested cash, settlement cash,
     collateral, cash buffers, and forced cash from signal and allocation effects.
272. **Independent P&L reconciliation** — Reconcile expected, broker, accounting,
     and market-value P&L through a separate calculation path with explainable
     residuals.
273. **Data-provider survivorship audit** — Check whether vendor coverage changes,
     ticker history, and delisted-name availability alter the apparent universe or
     performance sample.
274. **Synthetic-data realism gate** — Validate simulated prices, spreads, volume,
     jumps, and cross-asset dependence against held-out real-market statistics
     before using synthetic data for model selection.
275. **Purged cross-validation** — Remove overlapping label windows and add an
     embargo between train and validation periods when observations share future
     information through holding periods.
276. **Nested model-selection replay** — Separate inner tuning folds from an outer
     evaluation fold so candidate selection cannot quietly consume the final test.
277. **Regime-balanced validation** — Ensure validation folds represent distinct
     volatility, liquidity, trend, and crisis states rather than over-weighting the
     most common calm regime.
278. **Probabilistic calibration audit** — Check whether predicted probabilities,
     intervals, and expected returns match realized frequencies and errors by
     confidence bucket.
279. **Ranking-versus-return metrics** — Compare cross-sectional rank quality,
     spread returns, top-k selection, and portfolio utility; do not let one metric
     hide failure in another.
280. **Utility-aware scorecard** — Score forecasts using net return, risk, turnover,
     liquidity, drawdown, capacity, and operational burden with declared weights.
281. **Benchmark-relative tail report** — Measure excess-return and loss tails,
     downside beta, drawdown co-movement, and recovery relative to the benchmark,
     not only absolute Sharpe.
282. **Validation-fold independence audit** — Test shared assets, events, features,
     and benchmark constituents across folds for hidden dependence.
283. **Rebalance-frequency sensitivity** — Evaluate whether an edge survives nearby
     schedules and realistic execution windows instead of one chosen rebalance day.
284. **Turnover-regularized selection** — Include the stability and implementation
     cost of changing weights when selecting among otherwise similar candidates.
285. **Immutable decision ledger** — Store the inputs, outputs, constraints,
     approvals, and timestamps for every decision in append-only form so later
     explanations cannot overwrite the original state.
286. **Policy-to-order binding** — Attach the exact model, risk policy, cost model,
     and configuration version to each order and fill for replay and attribution.
287. **Human-override budget** — Track override frequency, direction, size, reason,
     outcome, and cumulative deviation from policy; test whether overrides add
     information or mainly introduce inconsistency.
288. **Handoff completeness test** — Verify that shift or system handoffs transfer
     open orders, positions, alerts, assumptions, exceptions, and pending actions
     without relying on undocumented context.
289. **Operational tabletop drills** — Rehearse data outage, broker outage, market
     halt, bad model release, and credential compromise with explicit roles and
     measurable recovery objectives.
290. **Access-scope review** — Inventory who or what can read data, change models,
     alter limits, submit orders, or delete evidence; test least-privilege paths.
291. **Secret-rotation rehearsal** — Exercise key, token, certificate, and broker
     credential rotation without interrupting safe monitoring or creating duplicate
     order authority.
292. **Tamper-evident audit trail** — Hash or otherwise chain critical records and
     verify that edits, deletions, clock changes, and backfills are detectable.
293. **Reconciliation-tolerance policy** — Define materiality thresholds and
     escalation paths for differences among broker, accounting, market-value, and
     research ledgers.
294. **Evidence-retention policy** — Set retention, deletion, export, and access
     rules for data, decisions, orders, model artifacts, and research results so
     reproducibility does not depend on accidental files remaining available.
295. **Pre-registered stopping rules** — Define before a trial when to stop for
     futility, safety, insufficient coverage, or success; prevent continued search
     after seeing the result from becoming an untracked tuning decision.
296. **Research-debt register** — Track deferred assumptions, shortcuts, missing
     evidence, and known approximations with owners, impact, and due dates.
297. **Alpha-decay attribution** — Decompose deterioration into signal decay,
     crowding, costs, universe change, data drift, risk scaling, and execution so
     the response targets the cause.
298. **Challenger-model rotation** — Keep independently designed challengers in
     shadow evaluation and define evidence-based replacement or rollback rules.
299. **Paper-to-live discrepancy study** — Compare paper fills, delays, limits,
     data availability, and operator actions with live outcomes using identical
     decisions and timestamps.
300. **Drift-trigger calibration** — Set model, feature, cost, and risk drift
     thresholds using historical false-alarm and missed-deterioration rates.
301. **Cross-regime calibration transfer** — Test whether probability, volatility,
     cost, and liquidity calibration transfers between regimes or requires a fresh
     fit with an explicit warm-up period.
302. **Research-compute determinism** — Check parallel reductions, floating-point
     order, hardware differences, and random seeds for material result changes.
303. **Experiment-cost accounting** — Track data, compute, human review, and
     opportunity costs per experiment so research capital is allocated rationally.
304. **Negative-result preservation** — Index failed hypotheses and invalidated
     variants with their evidence so future work does not unknowingly repeat them.
305. **Source-reliability stratification** — Group news, filings, estimates, and
     alternative-data sources by historical timeliness, correction rate, coverage,
     and predictive stability instead of treating all sources equally.
306. **Entity-resolution audit** — Test issuer, subsidiary, product, executive,
     ticker, and identifier mappings through name changes, mergers, and aliases;
     a mislinked event can look like a strong signal.
307. **Publication-time integrity** — Distinguish event occurrence, publication,
     vendor-ingest, parsing, and model-availability timestamps for every event.
308. **Sentiment half-life study** — Estimate how quickly sentiment or topic
     features decay by source, event type, market regime, and liquidity rather than
     assuming one fixed holding period.
309. **News-causality placebo** — Compare event reactions with matched non-events,
     delayed publication, and randomized source labels to test whether text adds
     information beyond price and volume.
310. **Contradictory-source handling** — Define confidence, abstention, and review
     behavior when reputable sources disagree or a story is later corrected.
311. **Fundamental-restatement replay** — Reconstruct the information set before
     restatements and compare it with revised fundamentals to expose hindsight in
     valuation or quality signals.
312. **Estimate-vintage tracking** — Preserve analyst or consensus estimate
     vintages, revisions, and publication delays; test whether revisions rather
     than levels carry the signal.
313. **Alternative-data coverage bias** — Measure which issuers, regions, sizes,
     and industries are over- or under-represented by each source and stress the
     resulting selection effects.
314. **Text-model abstention** — Require a confidence, novelty, and source-quality
     threshold before text-derived features can affect sizing; log no-action cases
     for calibration.
315. **Macro-vintage replay** — Preserve the release, revision, benchmark, and
     availability vintages of economic data so a strategy cannot use revised GDP,
     employment, inflation, or rates history at the original decision time.
316. **Release-surprise normalization** — Normalize economic surprises by forecast
     dispersion, regime, and release importance; test whether raw surprise size is
     actually comparable across series and eras.
317. **Policy-announcement timing** — Separate scheduled decision time, statement,
     press conference, leak, vendor ingest, and tradable timestamp for central-bank
     or fiscal events.
318. **Yield-curve representation study** — Compare level, slope, curvature,
     forwards, real yields, breakevens, and changes with point-in-time instrument
     availability and roll conventions.
319. **Cross-asset lead-lag audit** — Test whether apparent equity signals from
     rates, credit, FX, commodities, or volatility survive asynchronous closes,
     timezone differences, and publication delays.
320. **Regime-label uncertainty** — Treat macro and market regime labels as noisy
     probabilities; test decisions under nearby labels and transition ambiguity.
321. **Macro-feature missingness** — Stress delayed, revised, discontinued, and
     country-specific releases instead of filling all macro gaps identically.
322. **Market-breadth evidence** — Test advance/decline, participation, dispersion,
     and concentration features against simpler price and volume baselines with
     sector and capitalization controls.
323. **Correlation-shock validation** — Test whether correlation, covariance, and
     copula shocks improve out-of-sample loss estimates and sizing without simply
     reducing exposure during every noisy period.
324. **Macro-to-liquidity interaction** — Measure whether macro surprises change
     spreads, depth, impact, borrow, and fill uncertainty before allowing them to
     alter position size or execution urgency.
325. **Mandate-constraint hierarchy** — Order hard safety, legal, liquidity,
     broker, portfolio, and preference constraints explicitly; test conflicts and
     document which constraints may never be relaxed.
326. **Investor-liquidity mismatch** — Compare asset liquidation horizons with
     account, mandate, redemption, withdrawal, or capital-call obligations under
     stress rather than assuming portfolio liquidity equals investor liquidity.
327. **Redemption and capital-call stress** — Simulate clustered withdrawals,
     contributions, collateral calls, and delayed cash receipts with realistic
     liquidation costs and priority rules.
328. **Fee-and-friction netting** — Attribute management fees, performance fees,
     commissions, financing, borrow, taxes, and operational costs separately from
     gross strategy return.
329. **Mandate-drift detector** — Monitor whether live exposures, instruments,
     leverage, liquidity, geography, or concentration gradually diverge from the
     declared mandate.
330. **Capital-allocation fairness** — Test whether scarce risk, borrow, liquidity,
     and compute resources are allocated consistently across sleeves rather than
     rewarding whichever process runs first.
331. **Side-pocket and restricted-asset policy** — Define valuation, reporting,
     haircut, and unwind treatment for halted, restricted, disputed, or otherwise
     non-liquid assets.
332. **Cash-buffer opportunity-cost study** — Measure the protection and forgone
     return of cash reserves across liquidity, volatility, and funding regimes.
333. **Leverage-path sensitivity** — Compare identical end exposures reached through
     different leverage, margin, and financing paths; path-dependent constraints can
     change liquidation risk materially.
334. **Capital-raising dilution study** — If strategy capacity expands, test whether
     new capital dilutes existing opportunities, changes execution priority, or
     creates incentives to hide capacity loss.
335. **Preprocessing leakage audit** — Fit scaling, imputation, winsorization,
     embeddings, and feature selection only on the training information set and
     test leakage through cached or shared preprocessing state.
336. **Hyperparameter-search accounting** — Record search space, trials, failed
     runs, stopping rules, and holdout access so an apparently simple model cannot
     hide a large multiple-testing burden.
337. **Attribution-stability study** — Compare feature importance and explanations
     across seeds, folds, regimes, perturbations, and nearby model versions.
338. **Calibration-drift monitor** — Track probability, interval, and expected-
     return calibration separately from prediction error as market conditions move.
339. **Adversarial-market perturbations** — Test small plausible changes in prices,
     spreads, missing values, timestamps, and feature ordering for unsafe or
     discontinuous decisions.
340. **Ensemble-weight uncertainty** — Stress alternative weighting, correlation,
     and member-failure assumptions instead of treating learned ensemble weights as
     certain.
341. **Retraining-trigger study** — Compare calendar, drift, performance, regime,
     and event-based retraining rules with a frozen no-retrain control.
342. **Cold-start and rare-event test** — Evaluate new assets, new regimes, sparse
     events, and unseen categories without borrowing future information.
343. **Model-capacity frontier** — Map complexity against OOS stability, latency,
     interpretability, data needs, and operational failure surface.
344. **Safe-degradation behavior** — Define and test fallback outputs when a model
     loses features, exceeds latency, fails calibration, or encounters an unseen
     input distribution.
345. **Signal catalog and lineage** — Register each signal’s economic rationale,
     horizon, inputs, transformations, owner, costs, and known failure modes so
     portfolio behavior can be traced to components.
346. **Composable sleeve contracts** — Give each alpha or hedge sleeve declared
     outputs, risk budget, turnover budget, liquidity needs, and incompatibilities;
     test composition without hidden double counting.
347. **Liquidity-aware universe selection** — Select assets jointly on expected
     opportunity, spread, depth, borrow, participation capacity, and data quality
     rather than ranking alpha in isolation.
348. **Dynamic hedge-overlay study** — Compare static, threshold, forecast-based,
     and stress-triggered hedges with explicit hedge cost and basis-risk accounting.
349. **Adaptive risk-budget controller** — Test slow, bounded changes to sleeve risk
     budgets using forecast quality, drawdown, correlation, and liquidity evidence;
     include a no-adaptation control.
350. **Execution-schedule optimizer** — Compare urgency, participation, slicing,
     auction, and passive schedules under the same order and market scenario.
351. **Cross-account order netting** — Measure savings and allocation effects from
     netting opposite orders across sleeves or accounts while preserving ownership
     and mandate boundaries.
352. **Portfolio-construction challenger** — Compare equal risk, expected utility,
     robust, hierarchical, and constrained allocations with common inputs and
     explicit failure criteria.
353. **Signal-combination ablation** — Remove one sleeve, interaction, hedge, or
     constraint at a time to identify additive value versus duplicated complexity.
354. **Strategy-interface versioning** — Version signal, risk, cost, execution, and
     reporting interfaces independently so a component can evolve without silently
     changing the meaning of a historical result.
355. **Incremental-recompute correctness** — Compare incremental feature and
     portfolio updates with full recomputation across edits, late data, revisions,
     and restarts.
356. **Cache-invalidation audit** — Track cache keys, data versions, expiration,
     and dependency changes; test that stale features cannot survive a correction.
357. **Resampling-semantics review** — Specify open, high, low, close, volume,
     corporate-action, and event aggregation rules across frequencies and sessions.
358. **Cold-cache latency study** — Measure model and data latency on cold, warm,
     partially degraded, and backfilled caches instead of benchmarking only the
     ideal warm path.
359. **Resource-quota stress** — Test memory, CPU, storage, network, and concurrency
     limits with graceful degradation and explicit fail-closed behavior.
360. **Cost-aware feature materialization** — Compare precomputed, incremental, and
     on-demand features by freshness, compute cost, storage, and failure risk.
361. **Failure-injection matrix** — Inject missing partitions, corrupt rows,
     delayed feeds, duplicate messages, and partial writes at every data boundary.
362. **Multi-horizon data consistency** — Ensure daily, intraday, and event-level
     views agree on timestamps, adjustments, identifiers, and availability rules.
363. **Parallelism-scaling study** — Measure whether adding workers changes order,
     floating-point reductions, timeouts, or selection outcomes.
364. **Backfill-versus-live parity** — Replay a live period through the backfill
     pipeline and compare features, decisions, latency, and errors field by field.
365. **Research-data minimization** — Identify the minimum data needed for each
     feature, report, and experiment; remove unnecessary sensitive fields from
     storage, logs, exports, and model artifacts.
366. **Tenant and account isolation** — Test that portfolios, credentials, files,
     model outputs, and audit records cannot cross account or workspace boundaries
     through caches, joins, exports, or error messages.
367. **Artifact-access matrix** — Define who can view, modify, promote, export, or
     delete data snapshots, model weights, configs, and research evidence.
368. **Reproducible-build verification** — Rebuild services and research packages
     from pinned inputs and compare hashes, generated schemas, and behavior.
369. **Dependency-supply-chain review** — Inventory direct and transitive packages,
     licenses, release provenance, known vulnerabilities, and update paths without
     silently changing numerical behavior.
370. **Secrets-in-artifacts scan** — Check logs, notebooks, serialized models,
     traces, screenshots, and exports for credentials, tokens, or private data.
371. **Incident-response replay** — Exercise detection, containment, credential
     revocation, evidence preservation, and recovery for data or order incidents.
372. **Privacy-preserving research export** — Test redaction, aggregation, access
     expiry, and re-identification risk for shared research results.
373. **License-compatibility audit** — Track licenses and attribution obligations
     for data, code, models, and documentation before redistribution or deployment.
374. **Security-control false-positive study** — Measure the operational cost of
     blocked jobs, quarantined data, and denied actions so controls remain usable
     without normalizing unsafe bypasses.
375. **Decision-replay workspace** — Let a researcher inspect the exact features,
     forecasts, constraints, orders, fills, and state transitions behind any
     historical decision without recomputing a different answer.
376. **Risk-driver dashboard** — Decompose current and forecast risk into issuer,
     sector, factor, macro, liquidity, currency, and model-specific drivers with
     contribution changes over time.
377. **Data-quality dashboard** — Surface freshness, missingness, revisions,
     duplicates, outliers, source disagreement, and contract violations by dataset
     and downstream consumer.
378. **Uncertainty-aware reporting** — Show confidence intervals, parameter ranges,
     scenario bands, and sample coverage beside return, alpha, beta, and Sharpe.
379. **Scenario-diff viewer** — Compare baseline and stress portfolios, decisions,
     constraints, costs, and P&L line by line rather than only showing aggregate
     metric changes.
380. **Alert-priority model** — Rank alerts by expected loss, urgency, confidence,
     reversibility, and required action; test ranking quality against response
     outcomes.
381. **Evidence-linked model cards** — Generate model cards whose claims link to
     data snapshots, experiments, tests, limitations, and approval records.
382. **Metric-definition registry** — Give alpha, beta, Sharpe, drawdown, turnover,
     capacity, and cost metrics versioned definitions, windows, units, and source
     formulas.
383. **Research-session provenance** — Capture queries, code/config changes,
     datasets, candidate versions, and discarded paths from an interactive session.
384. **Reproducible-report rendering** — Render the same evidence report in text,
     table, and chart formats from one frozen result object to prevent presentation
     layers from changing the underlying numbers.
385. **Blinded candidate review** — Hide model names, parameter authors, and
     desired outcomes during initial evaluation so selection is based on evidence
     rather than familiarity or sunk effort.
386. **Hypothesis taxonomy** — Label experiments as descriptive, predictive,
     causal, execution, risk, or operational and require the matching evidence
     standard for each class.
387. **Causal-graph review** — Draw the assumed relationships among data, signals,
     exposures, costs, and outcomes; test whether the proposed intervention is
     identified by available data.
388. **Uncertainty-budget allocation** — Track uncertainty from data, labels,
     parameters, costs, execution, and model choice, then prioritize work on the
     largest contribution rather than the easiest one to reduce.
389. **Committee aggregation audit** — Compare simple average, weighted, robust,
     and disagreement-aware aggregation of researcher or model opinions with
     hindsight-blind evaluation.
390. **Backlog-aging control** — Review old hypotheses for changed data, market
     structure, dependencies, or relevance before spending new research effort.
391. **Reproducibility challenge** — Have an independent researcher rebuild a
     result from the evidence package without private context or author assistance.
392. **Claim-to-evidence matrix** — Map every narrative claim to a metric, test,
     source, limitation, and confidence level before it enters a report or pitch.
393. **Decision-threshold sensitivity** — Test how conclusions change across
     reasonable action thresholds, utility weights, and abstention rules.
394. **Post-decision hindsight lock** — Preserve what was known and believed before
     each decision, then compare later outcomes without rewriting the original
     rationale.
395. **Shadow-decision evaluation** — Run a candidate beside the accepted model
     using identical inputs and record hypothetical orders, risk, costs, and
     abstentions without allowing it to affect capital.
396. **Canary promotion** — Expose a new model to a bounded, explicitly selected
     slice of decisions or risk budget with predefined success, failure, and stop
     criteria.
397. **Rollback-gate calibration** — Set rollback thresholds using expected loss,
     detection delay, false rollback cost, and recovery time rather than one
     arbitrary metric.
398. **Version-skew detection** — Detect incompatible model, feature, risk, cost,
     broker, and reporting versions across workers and accounts before execution.
399. **Schema-migration replay** — Re-run historical periods across old and new
     schemas to identify changed meanings, defaults, missingness, and decisions.
400. **Feature-flag experiment ledger** — Record flag assignments, exposure,
     overrides, expiry, and outcome so a rollout cannot become an untracked
     experiment.
401. **Risk-policy migration test** — Compare old and new limits, sizing, and
     liquidation behavior on identical portfolios and stress paths before rollout.
402. **Release-evidence bundle** — Require tests, data hashes, model cards,
     approvals, rollback instructions, and known limitations for every promotion.
403. **Promotion SLOs** — Define freshness, latency, availability, fill, risk,
     reconciliation, and incident objectives for research and production paths.
404. **Post-release review** — Reconcile actual behavior with the release evidence
     after a defined period and record deviations, incidents, and follow-up work.
405. **Selection-versus-allocation attribution** — Separate security selection,
     sector or sleeve allocation, interaction, and residual effects against the
     declared benchmark.
406. **Timing-versus-exposure attribution** — Distinguish returns from entering or
     exiting at useful times from returns earned by simply holding a factor or
     market exposure.
407. **Active-share and overlap report** — Measure active share, benchmark overlap,
     common holdings, and concentration so a purportedly active sleeve is not just
     expensive beta.
408. **Conditional hit-rate study** — Report win rate by regime, side, size,
     liquidity, holding period, signal confidence, and cost bucket rather than one
     unconditional average.
409. **Tail-contribution attribution** — Identify which trades, assets, factors,
     and decisions contribute to the worst loss quantiles and stress outcomes.
410. **Liquidity-adjusted performance** — Report return, alpha, Sharpe, and drawdown
     after costs scaled to realistic liquidation horizons and participation limits.
411. **Capacity-reporting standard** — Publish assumptions, turnover, impact,
     crowding, borrow, and uncertainty behind every capacity estimate.
412. **P&L bridge reconciliation** — Build a dated bridge from forecast return to
     gross fill P&L, fees, financing, borrow, taxes, slippage, and net realized P&L.
413. **Benchmark-timing fairness** — Test benchmark prices and rebalance times for
     favorable or inconsistent timing relative to the strategy’s actual fills.
414. **Attribution residual investigation** — Require an explanation for residual
     P&L that cannot be assigned to signal, exposure, cost, execution, or cash.
415. **Trade-duration survival analysis** — Model the probability a position remains
     open by age, signal strength, volatility, liquidity, and regime instead of
     assuming one fixed holding period.
416. **Exit-reason attribution** — Separate stops, targets, trailing exits, signal
     reversals, timeouts, risk limits, halts, and forced liquidations in outcome
     analysis.
417. **State-conditional expectancy** — Report payoff distribution, hit rate,
     drawdown, and cost conditional on entry state, exit state, and transition path.
418. **Uncertainty-adjusted ranking** — Rank opportunities by expected utility and
     confidence or lower-bound quality, not point forecast alone.
419. **Signal-overlap graph** — Map shared inputs, exposures, trade dates, and
     error correlation among signals to detect duplicated alpha.
420. **Model-diversity scorecard** — Measure diversity in forecasts, positions,
     turnover, risk drivers, and failure periods rather than counting models.
421. **Turnover-source decomposition** — Attribute portfolio turnover to new names,
     resizing, exits, hedges, constraints, corporate actions, and benchmark drift.
422. **Expected-versus-realized risk bridge** — Reconcile forecast risk with realized
     volatility, drawdown, tail loss, and liquidity loss by horizon and regime.
423. **Benchmark-reconstitution effects** — Test index additions, deletions, and
     rebalance flows for selection leakage, crowding, and artificial alpha.
424. **Data-availability impact report** — Quantify how stale, missing, delayed,
     or quarantined data changes decisions, exposure, return, and risk.
425. **Queue-position uncertainty** — Model fill probability by order age, queue
     depth, cancellation, venue, and urgency instead of assuming every displayed
     quote is equally reachable.
426. **Hidden-liquidity study** — Compare displayed depth with realized fills,
     midpoint behavior, and dark or conditional liquidity under different regimes.
427. **Auction-specific execution** — Treat opening and closing auctions as their
     own liquidity, price-discovery, imbalance, and gap-risk environments.
428. **Trade-sign robustness** — Compare trade-sign classification methods and test
     whether microstructure features survive feed differences and quote revisions.
429. **Borrow-locate lifecycle** — Track locate availability, borrow rate, recalls,
     buy-ins, settlement, and forced covers through the full short position.
430. **Impact-decay measurement** — Estimate temporary and permanent impact over
     multiple horizons and test whether later alpha is contaminated by the fund’s
     own prior trading.
431. **Execution-alpha decomposition** — Separate price prediction, venue choice,
     timing, slicing, spread capture, adverse selection, and opportunity cost.
432. **Order-slicing policy test** — Compare fixed, volume-adaptive, volatility-
     adaptive, and urgency-aware slices with common fill and risk assumptions.
433. **Market-data-feed challenger** — Replay decisions on independent feeds and
     quantify how quote ordering, corrections, latency, and consolidation alter
     signals and fills.
434. **Execution-stop conditions** — Define when to pause, cancel, reprice, or
     reduce an order for spread, depth, volatility, halt risk, stale data, or
     broker health rather than relying on one static timeout.
435. **Financing-term structure** — Model funding rates, haircuts, term changes,
     renewal risk, and basis across instruments and maturities instead of using a
     single static financing cost.
436. **Margin-model mismatch** — Compare internal stress and margin forecasts with
     broker or clearing calculations, including intraday calls and liquidation
     timing.
437. **Collateral-eligibility stress** — Test haircuts, concentration limits,
     eligibility changes, settlement delays, and collateral substitution under
     market stress.
438. **Borrow-recall and squeeze scenarios** — Stress recalls, fee spikes,
     locate scarcity, buy-ins, forced covering, and correlated short unwinds.
439. **Securities-lending economics** — Attribute borrow fees, rebates, utilization,
     term, availability, and recall probability to short-position expectancy.
440. **FX-hedge basis study** — Separate asset return, currency return, hedge carry,
     hedge slippage, and basis risk across currencies and rebalance schedules.
441. **Dividend and withholding treatment** — Model ex-date timing, withholding,
     substitute payments, tax recovery, and data-vendor adjustments in net P&L.
442. **Settlement-failure replay** — Simulate failed deliveries, mismatched
     instructions, late cash, partial settlement, and remediation actions.
443. **Cash-sweep and buffer policy** — Compare cash investment, reserve, sweep,
     and collateral policies under rates, withdrawals, and margin stress.
444. **Cross-broker financing comparison** — Test whether routing or financing
     provider assumptions change net alpha, capacity, or forced-liquidation risk.
445. **Restricted-list enforcement** — Test that prohibited issuers, instruments,
     strategies, or jurisdictions are blocked consistently across research,
     simulation, order generation, and exports.
446. **Pre-trade rule ordering** — Make the order of risk, mandate, compliance,
     liquidity, and broker checks deterministic and test conflicting outcomes.
447. **Benchmark/data-rights inventory** — Record license, permitted use, retention,
     redistribution, and attribution terms for every benchmark and data source.
448. **Regulatory-report replay** — Recreate required holdings, transactions,
     valuations, and disclosures from immutable source records at historical
     reporting dates.
449. **Trade-surveillance research** — Test anomaly indicators for unusual size,
     timing, cancellation, concentration, cross-account activity, and repeated
     override patterns without treating every alert as wrongdoing.
450. **Conflict-of-interest controls** — Identify personal, account, vendor,
     affiliate, and incentive conflicts that could affect research, allocation, or
     execution decisions.
451. **Data-use consent and purpose** — Link each sensitive or licensed dataset to
     an approved purpose, retention period, access scope, and deletion path.
452. **Corporate-action approval path** — Require reviewable treatment for unusual
     mergers, spin-offs, tender offers, rights issues, and symbol changes before
     they alter positions or backtests.
453. **Exception-register aging** — Track unresolved compliance, risk, data, and
     operational exceptions with owner, severity, expiry, and escalation.
454. **Control-effectiveness backtest** — Replay historical decisions to estimate
     prevented loss, false blocks, missed violations, and control degradation.
455. **Options-implied information study** — Test whether implied volatility,
     skew, term structure, open interest, and dealer positioning add information
     beyond price, volume, and realized volatility.
456. **Volatility-surface integrity** — Preserve strike, expiry, settlement,
     interpolation, and corporate-action conventions; test surface features for
     stale or arbitrage-inconsistent quotes.
457. **Implied-versus-realized decomposition** — Separate volatility risk premium,
     forecast error, jump premium, liquidity premium, and hedging cost.
458. **Options-flow timing audit** — Distinguish trade time, quote time, reporting
     time, clearing time, and feature-availability time for derivatives activity.
459. **Open-interest rollover effects** — Test expiry, roll, assignment, and
     concentration effects without leaking future contract survival or settlement.
460. **Dealer-positioning uncertainty** — Stress alternative assumptions about
     inventory, delta/gamma exposure, hedging frequency, and sign classification.
461. **ETF-flow and creation/redemption study** — Separate mechanical flow effects,
     information flow, benchmark rebalances, and underlying liquidity impact.
462. **Index-arbitrage contamination** — Test whether index futures, ETFs, and
     constituents create duplicated signals or predictable but capacity-limited
     relationships.
463. **Derivative-liquidity ladder** — Model bid/ask, depth, open interest, margin,
     exercise, assignment, and exit feasibility across strikes and expiries.
464. **Volatility-hedge opportunity cost** — Measure protection, carry drag, basis,
     rebalance slippage, and tail benefit across alternative hedge instruments.
465. **Human cognitive-load study** — Measure how many positions, alerts, exceptions,
     and explanations a reviewer can process before response quality degrades.
466. **Checklist-effectiveness test** — Compare structured pre-trade and incident
     checklists with free-form review while tracking omissions, latency, and false
     confidence.
467. **Narrative-adversarial review** — Present persuasive but unsupported stories,
     headlines, or model explanations and test whether decisions remain anchored to
     verified evidence.
468. **Dissent-preservation protocol** — Record minority views, uncertainty,
     alternatives, and unresolved objections before a committee or model decision.
469. **Human-in-loop latency budget** — Define when review is required, how long it
     may take, and what safe action occurs if the reviewer is unavailable.
470. **Role-separation study** — Test independence between research, approval,
     execution, risk, reconciliation, and postmortem roles.
471. **Decision-simulator training** — Replay historical and synthetic cases with
     hidden outcomes to evaluate operator calibration, not just completion speed.
472. **Fatigue and schedule stress** — Test overnight, weekend, high-alert, and
     incident-cluster conditions for missed controls and inconsistent overrides.
473. **Incentive-alignment audit** — Examine whether performance, promotion, fees,
     or ownership incentives reward hidden risk, turnover, or delayed loss capture.
474. **Postmortem taxonomy** — Classify incidents by data, model, execution, risk,
     process, human, vendor, and external causes with corrective-action tracking.
475. **Reverse stress testing** — Start from unacceptable loss, liquidity, margin,
     or operational outcomes and solve for plausible combinations of shocks that
     could produce them.
476. **Risk-budget waterfall** — Show how gross capital becomes net exposure after
     issuer, factor, liquidity, leverage, hedge, cash, and emergency reductions.
477. **Contagion-path simulation** — Trace how a shock moves through prices,
     correlations, margin, funding, borrow, forced sales, and signal behavior.
478. **Drawdown-policy hysteresis** — Test whether de-risking and re-risking rules
     avoid whipsaw, procyclicality, and delayed recovery after a loss.
479. **Crisis-alpha correlation** — Measure whether supposedly independent alpha
     sleeves converge during crises, including common liquidity and deleveraging
     channels.
480. **Tail-hedge overlap** — Identify whether multiple hedges pay in the same
     scenario, fail together, or merely duplicate carry cost.
481. **Recovery-path economics** — Compare recovery speed, opportunity cost, and
     trading costs after a stress-triggered reduction in exposure.
482. **Liquidity-stress propagation** — Couple spread, depth, participation,
     impact, margin, borrow, and order-queue changes rather than shocking only
     prices.
483. **Stress-parameter uncertainty** — Vary shock size, duration, correlation,
     recovery, and execution assumptions; report robust and fragile conclusions.
484. **Crisis-playbook rehearsal** — Replay severe historical and hypothetical
     events with explicit decision deadlines, fallback modes, communication, and
     evidence capture.
485. **Distributionally robust optimization** — Optimize across a bounded family
     of plausible return, covariance, cost, and liquidity distributions rather
     than one estimated distribution.
486. **Chance-constraint validation** — Test probabilistic exposure, loss, and
     liquidity limits under estimation error and finite samples.
487. **CVaR versus drawdown objective** — Compare expected shortfall, maximum
     drawdown, recovery time, and path-dependent utility instead of treating one
     tail metric as universally sufficient.
488. **Cardinality and minimum-lot stress** — Include discrete holdings, minimum
     sizes, odd lots, and rounding in optimization and execution replay.
489. **Turnover and impact regularization** — Compare penalties and constraints for
     turnover, market impact, borrow, and liquidity with realized execution data.
490. **Solver-stability study** — Re-run optimizations across tolerances, starts,
     solvers, floating-point settings, and near-degenerate covariance matrices.
491. **Constraint-relaxation ledger** — Record every relaxed, binding, or infeasible
     constraint and its marginal effect on return, risk, turnover, and feasibility.
492. **Robust factor exposure** — Stress factor definitions, loadings, signs,
     horizons, and missing exposures before using them as hard constraints.
493. **Utility-function sensitivity** — Compare reasonable risk aversion, loss
     asymmetry, drawdown penalty, liquidity penalty, and capacity assumptions.
494. **Optimization-to-order parity** — Verify that optimized weights survive lot
     sizes, cash, fees, margin, borrow, participation, and order-scheduling rules.
495. **Outlier-influence analysis** — Recompute signals and portfolio results with
     influential observations removed, winsorized, or downweighted to identify
     fragile alpha driven by a few extreme cases.
496. **Rank-stability study** — Measure asset rank persistence across nearby dates,
     data vendors, preprocessing choices, and small feature perturbations.
497. **Information-coefficient decay** — Track rank or correlation skill by horizon,
     regime, universe, liquidity, and signal age rather than one aggregate IC.
498. **Label-construction audit** — Compare forward-return, excess-return,
     residual-return, drawdown, and event labels with explicit overlap and cost
     treatment.
499. **Cross-sectional normalization study** — Test z-score, rank, robust, sector,
     beta, and volatility normalization for stability and unintended exposures.
500. **Tie-and-missing rank policy** — Define deterministic handling for ties,
     missing values, zero volume, halted names, and sparse universes.
501. **Feature-age sensitivity** — Measure how signal quality changes as inputs age
     from fresh to stale, including different vendor and ingestion delays.
502. **Feature-interaction audit** — Identify nonlinear interactions that create
     hidden concentration, leakage, or unstable behavior outside the training range.
503. **Incremental-feature value test** — Add one feature family at a time against a
     frozen baseline and require evidence of net value after costs and complexity.
504. **Signal-health score** — Combine coverage, stability, decay, calibration,
     crowding, cost, and recent error into a diagnostic without letting the score
     silently become an unvalidated trading signal.
505. **Causal-mediation attribution** — Test whether a signal affects returns
     directly or through exposure, liquidity, crowding, or market-regime changes.
506. **Signal-turnover decomposition** — Separate rank changes caused by new data,
     normalization, universe changes, corporate actions, and true signal movement.
507. **Regime-transition early warning** — Compare volatility, correlation, breadth,
     liquidity, macro, and cross-asset indicators for lead time and false alarms.
508. **Change-point uncertainty** — Treat detected regime breaks as probability
     distributions with uncertain location, duration, and recovery.
509. **Latent-factor discovery stability** — Refit latent factors across windows,
     universes, and seeds; test whether exposures and interpretations persist.
510. **Crowding-migration study** — Track whether crowding moves across names,
     factors, sectors, venues, or instruments after a signal or hedge changes.
511. **Neutralization-turnover tradeoff** — Measure the alpha, risk, and cost impact
     of increasingly aggressive factor and beta neutralization.
512. **Macro-beta decomposition** — Separate portfolio response to rates, inflation,
     growth, FX, commodities, credit, and volatility shocks from security alpha.
513. **Feedback-aware throttling** — Test position and participation reductions when
     the strategy’s own orders measurably worsen spread, depth, or future signal
     quality.
514. **Regime-conditional risk budgets** — Compare fixed, smoothly adaptive, and
     sharply adaptive risk budgets with explicit transition and whipsaw costs.
515. **Point-in-time statement replay** — Reconstruct the financial statements,
     footnotes, estimates, and availability dates known at each decision rather
     than using the latest restated history.
516. **Accounting-quality decomposition** — Separate accruals, cash conversion,
     revenue recognition, reserves, working capital, and one-time items when
     testing fundamental quality signals.
517. **Guidance-revision study** — Test management guidance changes, estimate
     revisions, surprise magnitude, and dispersion with publication and ingestion
     delays.
518. **Balance-sheet stress lens** — Model leverage, maturities, liquidity, covenant
     headroom, refinancing, and rate sensitivity under adverse scenarios.
519. **Capital-allocation behavior** — Study buybacks, issuance, dividends, M&A,
     debt repayment, and investment changes as dated, economically distinct signals.
520. **Sector-comparability audit** — Test whether margins, leverage, growth,
     valuation, and quality features are comparable across industries and business
     models before cross-sectional ranking.
521. **Fundamental-data freshness** — Measure how long filings, estimates, and
     company events remain informative after release, revision, or stale coverage.
522. **Fundamental-quant interaction** — Test whether fundamentals add incremental
     value to price/volume signals after controlling for factor, sector, size, and
     liquidity exposures.
523. **Accounting-anomaly placebo** — Compare fundamental anomaly signals with
     randomized filing dates, matched firms, and non-economic fields.
524. **Fundamental-signal capacity** — Estimate turnover, crowding, liquidity,
     event-gap risk, and capacity for slow-moving fundamental strategies.

## Current backlog triage

This is an ordering aid for future research, not an authorization to implement
everything listed above.

**Phase A — measurement integrity:** evaluation lockfile; point-in-time data;
benchmark/beta/alpha separation; trial accounting; model cards; independent
replication; event timestamps.

**Phase B — tradability:** corporate-action and delisting continuity; borrow and
financing; exchange sessions and halts; cost/fill uncertainty; capacity by
liquidity regime; forecast-to-order attribution.

**Phase C — portfolio risk:** effective breadth; factor attribution; multi-sleeve
capital allocation; order netting; regime and driver stress; risk-model
challengers; crisis-protection overlap.

**Phase D — alpha experiments:** residual signals; calibrated forecasts;
meta-labeling; holding-period adaptation; signal interactions; event features;
ensemble diversity; robust allocation.

Promotion to the next phase requires a documented result from the preceding phase;
no alpha experiment should be used to compensate for an invalid measurement or
unrealistic execution assumption.
