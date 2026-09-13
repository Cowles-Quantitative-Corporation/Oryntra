# Quant Lab model-validation log

This is an append-only, research-only record of candidate-profile evidence. A passing entry permits a profile to be selectable in Quant Lab; it does not establish live performance, suitability, or permission to place an order.

## 2026-09-13 — TBA risk supervisor implementation: awaiting frozen OOS panel

A TBA-only portfolio risk supervisor was added without changing normal Minerva. It controls correlation-cluster weight, positive marginal variance contribution, high-correlation regimes, effective-bet concentration, completed-return realized volatility, volatility acceleration, expected shortfall, rolling drawdown, and gradual recovery. Each control can only reduce risk, uses information available by the previous completed close, and is included in the research configuration and daily audit.

One comparison harness measures identical frozen Qlib scores under native top-24 construction, the legacy Oryntra risk layer, and the new supervisor. A second performs paired native TBA runs where the supervisor configuration is the only change and verifies identical dataset fingerprints.

A newly frozen 43-stock current-survivor engineering panel was used for native paired tests. The initial full supervisor reduced mean Sharpe for all six runnable native TBA configurations. Component ablation on ten development years selected only a 10% positive marginal variance-contribution cap. On eight separately selected confirmation years that cap improved mean excess-return Sharpe for every native TBA: deltas ranged from +0.0014 to +0.0599. TBA4 improved its minimum annual Sharpe; the other five worsened it by 0.0088 to 0.1257. TBA5/TBA8 improved from 0.3957 to 0.4526 mean Sharpe, improved positive-Sharpe years from 5/8 to 6/8, and improved mean annual alpha by 0.5275 percentage points.

Decision: retain the isolated cap as a private TBA research candidate, with all harmful or inactive new dynamic defaults switched off. It is not release-approved and does not pass the strict cross-model minimum-Sharpe gate. The panel is current-survivor biased, and no fresh external Qlib scores were available; no claim of beating an open-source model is recorded. Exact hashes, years, and paired results are in `docs/TBA_RISK_PAIRED_RESULTS_2026-09-13.json`.

## 2026-09-12 — Universal V2 native and open-source learner review: no promotion

This week added an explicit, auditable research stack around Universal V2: a cash/share next-open ledger; capacity, cost, covariance-shrinkage and correlation-stress controls; a causal lifecycle policy; completed-close market and peer-shock infrastructure; availability-dated SEC Company Facts tooling; seeded annual protocols; and an isolated Microsoft Qlib environment for model comparison. These are research capabilities, not a public-model replacement.

The following candidates were evaluated without changing public scanner defaults:

| Candidate | Result | Decision |
| --- | --- | --- |
| TBA1, compact OHLCV structure sleeve | Did not improve the frozen Minerva comparison. | Rejected. |
| TBA2, Qlib Alpha158 with the same portfolio-risk layer | Cached-score risk ablation was mixed; the risk bundle did not uniformly improve Qlib predictions. | Not promoted. |
| TBA3, beta-residual training label | -10.66% alpha in the first 2007 slice. | Rejected. |
| TBA4, completed-information-coefficient cash gate | +0.84% mean alpha with 5/10 positive years on one frozen panel; -5.58% with 3/10 on a separate seed/basket panel. | Rejected. |
| TBA5, completed 21/63-session residual momentum | +1.19% mean alpha but 6/10 positive years on its benchmark-complete panel. | Not promoted. |
| TBA6, TBA5 plus existing causal stop/target/trailing lifecycle | -4.15% mean alpha and 2/10 positive years on an independent seed/basket panel. | Rejected. |
| TBA7, fixed-parameter walk-forward XGBoost research scorer | -1.57% mean alpha and 3/10 positive years on the frozen ten-year screen. | Rejected. |

TBA5 could not use the original 2001 slice because its 756-session SPY/QQQ warmup had 298 missing benchmark-return sessions. Those values were not imputed. A separate benchmark-complete draw was frozen before testing. This is a data-coverage constraint, not a reason to relax the causal contract.

No candidate met the current research gate of at least eight positive annual alpha observations and mean alpha above 1%. The evidence points to an OHLCV-only information limitation rather than a missing stop, volatility, or optimizer setting. The next admissible extension is an availability-dated corporate/event panel with documented coverage; no unobserved filing date may be backfilled.

## 2026-09-05 — V1.1 long-only trend/momentum research

### Fixed candidate

- 60% time-series trend and 40% cross-sectional momentum.
- Long-only; no mean-reversion, low-volatility, or corporate-quality sleeve.
- 126-session trend and momentum lookbacks; weekly rebalance; 12% annual target volatility that may only reduce exposure; 1.0 maximum gross exposure; 35% name cap.
- 12 bps base cost, 50 bps annual borrow assumption, $1 million portfolio-value assumption, 18 bps square-root impact coefficient, and 2% ADV participation limit.
- Regime-conditioned sleeve weights, capacity scenarios, correlation-convergence diagnostics, next-session timing, and the standard chronological validation report remained enabled.

### Selection discipline

The candidate was evaluated only after the existing long/short price profiles failed a separate predeclared comparison on a volatility-selected universe. The long-only candidate was then tested once on a fresh candidate list: symbols were ranked by two-year realized annualized volatility only, the twelve highest were alternated into development and untouched-stock universes, and performance was not used to select the symbols.

### Results

| Universe | Symbols | Chronological holdout | Sharpe | Max drawdown | Positive walk-forward slices | Gate |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Development | NVTS, MRNA, SMR, PLUG, CHPT, UUUU | +10.82% | 1.44 | -7.86% | 3 / 3 | Passed |
| Untouched stocks | SANA, FCEL, BE, LEU, ACHR, JOBY | +7.74% | 1.48 | -4.67% | 3 / 3 | Passed |

The local immutable experiment records are `659c5ca356614d5286db617eb9888976` and `ab676a328f7a4a8a87191946909ecb8f`. Raw public daily bars were used in memory for the study; the experiment ledger retains configuration, data fingerprints, coverage, and results rather than vendor data.

### Interpretation and limits

This evidence is sufficient to expose the profile as a research candidate with a long-only default. It is not sufficient to make it the app default, claim alpha, compare it fairly to an unscaled buy-and-hold reference, or treat the daily-bar liquidity proxy as actual execution. Future attempts to change its weights, lookbacks, universe, or shorting policy require a new frozen hypothesis and a new untouched evaluation.

## 2026-09-05 — Existing long/short price-profile comparison

Using a separate volatility-selected development universe (QBTS, CIFR, IONQ, HIMS, SMCI, CRDO), none of the five existing price profiles passed the predefined gate of positive chronological-holdout return and Sharpe, maximum drawdown above -25%, and at least two positive walk-forward slices. The holdout returns ranged from -5.51% to -13.23%; no existing long/short profile was promoted or reweighted from that sample. The corporate profile was excluded from this comparison because no point-in-time corporate-score panel was supplied, so it would not have been a like-for-like fully invested price-profile test.

## 2026-09-05 — Ten-year ETF audit of V1.1 long-only trend/momentum

### Frozen audit

- Candidate: the V1.1 long-only profile above, with its existing 60/40 trend/momentum allocation and parameters unchanged.
- Universe: SPY, QQQ, IWM, EFA, EEM, TLT, IEF, GLD, DBC, XLE, XLK, and XLF; selected before the run as a liquid cross-asset ETF set rather than from prior candidate results.
- Window: 2015-01-02 through 2025-12-31 (eleven complete calendar years of available daily data).
- Comparator: a static equal-weight portfolio of the same ETFs, using the same base-cost proxy. This is a transparent implementation benchmark, **not** a factor-model alpha estimate.

### Results

| Calendar year | Candidate | Comparator | Relative return |
| --- | ---: | ---: | ---: |
| 2015 | -5.06% | -4.29% | -0.77% |
| 2016 | +12.44% | +12.94% | -0.50% |
| 2017 | +17.56% | +17.83% | -0.27% |
| 2018 | -6.40% | -7.20% | +0.79% |
| 2019 | +6.84% | +23.64% | -16.80% |
| 2020 | +16.79% | +15.19% | +1.60% |
| 2021 | +12.96% | +18.82% | -5.87% |
| 2022 | -5.14% | -10.37% | +5.23% |
| 2023 | +6.63% | +16.67% | -10.04% |
| 2024 | +8.45% | +12.67% | -4.22% |
| 2025 | +9.08% | +20.93% | -11.85% |

The average annual benchmark-relative return was **-3.88%**. One year met the +5% relative-return threshold; eight years were negative, eight were below +0.5%, and nine were below +1.5%. The candidate therefore fails the requested multi-year gate and is not promoted, reweighted, or made a default from this audit. Immutable experiment record: `5e6fa8c66a0d4c728d3da90fc35a2bf1`.

### Measurement boundaries

No discrete-trade win rate is reported: the strategy emits target portfolio weights and this study has no predeclared entry/exit pairing rule. No factor-model alpha is reported: that would require a separately specified, point-in-time factor dataset and regression methodology. Neither missing metric may be inferred from benchmark-relative return.

### Next step

This audit is a diagnostic for the frozen candidate, not a target for parameter fitting. A follow-up must use a distinct written hypothesis and fresh development data or a fresh universe; it may not tune V1.1 directly from these annual results.

## 2026-09-05 — V1.2 defensive-sleeve repair: rejected on fresh confirmation basket

### Predeclared repair

The ETF diagnostic suggested that the V1.1 trend/momentum mix did not compensate for whipsaw and concentration on a diversified cross-asset set. The proposed repair was a fixed, long-only **45% time-series trend / 30% cross-sectional momentum / 25% defensive low-volatility** mix. It retained the existing 126-session signal windows, weekly rebalance, 12% volatility cap, 12 bps base cost, liquidity-cost proxy, regime-conditioned weights, and next-session timing. The purpose was to test whether the defensive sleeve improved net return and Sharpe while lowering turnover—not to fit the ETF result.

### Rotating-basket protocol

The predeclared non-retired pool was COIN, DKNG, RBLX, UPST, RIVN, LCID, AFRM, CVNA, MARA, RIOT, GME, AMC, HOOD, SOFI, PATH, U, and PTON. Symbols were ranked by annualized daily-return volatility from 2022-01-01 through 2023-12-29; the top twelve were selected, then alternating volatility ranks were assigned before the 2024-2025 return period was inspected.

| Stage | Basket | Fixed V1.1 return / Sharpe | V1.2 repair return / Sharpe | Result |
| --- | --- | ---: | ---: | --- |
| Development | CVNA, MARA, AFRM, COIN, U, RIVN | +15.10% / 0.66 | +16.06% / 0.71 | Advanced once |
| One-time confirmation | UPST, AMC, RIOT, PTON, GME, LCID | +1.74% / 0.13 | -0.35% / 0.06 | Rejected |

The development record is `bb585c5499e14706ba1b135b2562da62`; the one-time confirmation record is `8159e240fd0a4a8c813b2c01c74755bf`. The confirmation basket was worse on both predeclared measures, so V1.2 was not added to the product, its weights were not adopted, and all twelve selected names are retired from later V1.2 parameter selection. This is a model-selection comparison, not a benchmark-relative return or factor-alpha study.

## 2026-09-05 — V1.2 ten-year beta-adjusted alpha test: consistency gate failed

### Frozen study

At the user's direction, the rejected V1.2 45/30/25 candidate was evaluated on a separate, fresh 2015-2025 equity basket: AAPL, MSFT, AMZN, META, GOOGL, NVDA, AMD, NFLX, TSLA, JPM, XOM, and CAT. None overlapped the earlier V1.1/V1.2 development or confirmation baskets. The daily beta proxy was a 50/50 SPY/QQQ return blend, rebalanced daily. For each calendar year, alpha is the annualized intercept from the daily regression `strategy return − ^IRX/252 = alpha + beta × (50/50 SPY/QQQ return − ^IRX/252)`. The ^IRX conversion is an observed 13-week Treasury-bill-yield proxy, not a full risk-free return series.

| Year | Beta | CAPM alpha | Alpha t-statistic |
| --- | ---: | ---: | ---: |
| 2015 | 0.44 | +10.88% | 1.50 |
| 2016 | 0.62 | +10.45% | 1.22 |
| 2017 | 1.07 | -0.58% | -0.09 |
| 2018 | 0.56 | +9.18% | 1.11 |
| 2019 | 0.66 | -4.55% | -0.71 |
| 2020 | 0.24 | +22.61% | 2.44 |
| 2021 | 0.69 | +4.53% | 0.75 |
| 2022 | 0.35 | -0.88% | -0.14 |
| 2023 | 0.65 | -3.99% | -0.68 |
| 2024 | 0.68 | +5.18% | 0.87 |
| 2025 | 0.39 | +1.77% | 0.23 |

The annual-alpha average was **+4.96%**, narrowly below the requested +5% threshold. The pooled 2015-2025 daily regression returned +8.09% annualized alpha with a 3.30 alpha t-statistic, but that pooled figure does **not** replace the requested yearly-average test. The candidate also failed the consistency requirements: four negative-alpha years, four years below +0.5%, and four years below +1.5%. The experiment record is `74d40add57fd49b680df2d3c23da5cfa`.

This result neither promotes V1.2 nor reverses its prior fresh-basket rejection. The twelve names in this alpha study are now retired from later V1.2 parameter selection. It is a research-only single-factor evaluation using public daily data; it does not provide multi-factor alpha, point-in-time delisted-security coverage, or live-trading evidence.

## Current evidence gate (updated 2026-09-05)

The target remains at least **+5% average annual beta-adjusted alpha** over a ten-calendar-year scorecard. The annual consistency requirement is now +2.5% for all remaining years, allowing at most one negative-alpha year and at most two total years below +1.0%. The primary beta proxy is the daily rebalanced 50/50 SPY/QQQ blend; same-universe equal weight is secondary only. A trade-level 55% win-rate requirement applies only after a strategy has explicit, predeclared trade-entry and trade-exit rules.

## 2026-09-05 — V1.3 momentum-tilt repair: rejected in development

The next repair was a fixed 35% trend / 55% cross-sectional momentum / 10% low-volatility candidate. It was motivated by V1.2's negative alpha in 2019 and 2023, where broader growth-market leadership may have penalized the 25% defensive sleeve. A new, non-retired pool (PLTR, APP, CELH, CRSP, RUN, FUBO, OPEN, BILI, NIO, XPEV, BNTX, WBD, NET, DDOG, TOST, and CHWY) was ranked on 2022-2023 annualized volatility. Alternating ranks produced the development set OPEN, BILI, RUN, NIO, CHWY, and PLTR, and the reserved confirmation set FUBO, XPEV, NET, APP, CELH, and TOST.

On the 2024-2025 development set, V1.2 returned **+37.43%** at a **1.50** Sharpe, while V1.3 returned **+37.10%** at a **1.45** Sharpe. Because it failed both predeclared development measures, V1.3 did not advance to confirmation, was not added to source, and its reserved confirmation symbols remain unused. Development experiment: `74af95fb0b554f7c8164e4243e0a2f0e`.

### User-requested V1.3 retune: rejected in development

The first bounded V1.3 retune changed 35/55/10 to **45% trend / 45% momentum / 10% low volatility**, on the same development basket only. Its hypothesis was that the original V1.3 reduced trend exposure too much. It returned **+35.64%** at a **1.40** Sharpe, weaker than both V1.2 (+37.43%, 1.50) and the original V1.3 (+37.10%, 1.45). It did not advance; FUBO, XPEV, NET, APP, CELH, and TOST remain untouched confirmation symbols. Retune experiment: `6782842930224f26bebc646a8f4b4394`.

### Opposite-direction V1.3 retune: rejected on one-time confirmation

Following the weaker 45/45/10 retune, the opposite **25% trend / 65% momentum / 10% low-volatility** allocation was tested on the same development basket. It advanced: **+38.76%** return at **1.50** Sharpe versus V1.2's +37.43% at 1.50. The candidate was then frozen and evaluated once on the untouched FUBO, XPEV, NET, APP, CELH, and TOST basket. V1.2 returned **+45.32%** at **1.45** Sharpe; the candidate returned **+44.87%** at **1.53** Sharpe. Because return was 0.45 percentage points lower, it failed the predeclared requirement to improve return without reducing Sharpe. It is rejected, not added to source, and both V1.3 baskets are now retired from V1.3 parameter selection. Development record: `e357f08db949472c807496381c4bd09e`; confirmation record: `2e03210db8b248d4b85006c48468618d`.

## 2026-09-05 — V1.4 multi-signal repair: rejected in development

V1.4 combined a 63/252-session trend composite, 55% cross-sectional momentum, 10% low-volatility, and a predeclared 50% exposure overlay when fewer than 40% of names had positive 126-session returns. The fresh pool was ROKU, SNAP, ZM, DOCU, TWLO, ETSY, PINS, CCL, NCLH, UAL, DAL, UBER, DASH, BABA, SEDG, and ALB; it was ranked by 2022-2023 volatility before 2024-2025 returns were inspected. On the development basket SNAP, TWLO, SEDG, CCL, ETSY, and BABA, V1.2 returned **+14.65%** at **0.63** Sharpe while V1.4 returned **+12.78%** at **0.62**. It did not advance; ROKU, DASH, DOCU, PINS, NCLH, and UBER remain unused confirmation symbols. Experiment: `35913e2486da4c44b7cec087697ac21a`.

## 2026-09-05 — Universal V2 shared-backend implementation: no promotion

V2 introduces a shared scanner/portfolio signal contract, a next-open cash/shares ledger, hard prior-volume fill limits, no-trade bands and explicit annual CAPM alpha with HAC uncertainty. Initial selection used 12 configurations on 2016–2020 development stocks: selected mean alpha +3.56%, later-period alpha -2.10%, separate-stock ten-year mean alpha +1.01%. A further 12 development candidates tested rebalance frequency, concentration and trading bands. The selected 8% band improved development alpha to +3.97% but failed the next predeclared twelve-stock ten-year basket at -5.12%, with six negative years. Neither candidate passed or replaced a public model. Every attempt and manifest is retained in [the derived validation record](UNIVERSAL_V2_VALIDATION.json); see [the backend guide](UNIVERSAL_V2_BACKEND.md) for formulas, code paths and caveats.

Methodology correction: 2015–2025 is eleven years, not ten. Annual alpha here is 252 times the excess-return regression intercept against daily 50/50 SPY/QQQ, using actual published daily risk-free returns rather than an annual yield divided by 252. These new alpha-based selection criteria differ from earlier raw-return/Sharpe comparisons in this log. Separate tickers sharing the same historical eras are exploratory cross-sectional evidence, not independent prospective validation.

Implementation correction: the older scanner backtest failed to return computed stop/target exits, omitted normal exit checking on its last bar, and charged commission only once. Those defects are corrected; prior scanner-backtest results require rerunning. Legacy Quant profiles still use shifted target-weight simulation rather than V2's drifting-share ledger. Historical entries above are preserved and must not be presented as validation of the new accounting engine.

## 2026-09-10 — Minerva lifecycle and market-context trials: rejected

### Frozen protocol

This study tested the already-implemented Universal V2 lifecycle and market-context features as separate hypotheses, not as a bundle. It used adjusted daily Yahoo OHLCV downloaded on 2026-09-10, published Kenneth French daily RF, the daily-rebalanced 50/50 SPY/QQQ benchmark, next-open cash/share accounting, declared costs, and the frozen Minerva price-only signal. The development basket was ABT, AEP, AIG, ALL, AMGN, APD, AXP, BAX, BDX, BMY, CAT, and CL over 2008–2015. The untouched basket was CME, COP, CSCO, CVS, DUK, EMR, EOG, EXC, FDX, GILD, HRL, and KMB over 2016–2025. HCA was replaced by HRL before any scoring because HCA did not meet the mandatory 2005–2025 source-history requirement.

The selection order was fixed before results: maximize the worst complete-year alpha, then minimize negative years, then maximize mean annual alpha. Raw vendor bars were used only in-process and are not committed.

### Lifecycle result

Four declared lifecycle policies were compared with the disabled-policy control: balanced 2.5/3.0-volatility stops with a 2R 50% first target; wider 3.5/4.0-volatility stops with a 3R 33% target; protective 2.0/2.5-volatility stops with a 1.5R 50% target; and a patient 3.0/3.5-volatility policy with a 2.5R 25% target. All lifecycle candidates were worse than the disabled-policy control on development. The control's mean annual alpha was +0.11% with four negative years; the lifecycle candidates ranged from -5.60% to -6.58% mean alpha with six to eight negative years.

The control was frozen and evaluated without retuning. Its same-symbol 2016–2025 result was -0.67% mean annual alpha with six negative years; its untouched-stock 2016–2025 result was -2.64% with five negative years. No lifecycle policy is promoted.

### Market-context result

The second independent trial compared no context with three completed-close market/breadth/correlation overlays: 70%/35%, 85%/55%, and 55%/20% caution/risk-off exposure multipliers. The no-context control again won development at +0.11% mean annual alpha with four negative years. The three overlays returned -2.49%, -1.73%, and -2.92%, respectively, each with six negative years. No context overlay advanced to confirmation.

### Data-validation correction

The first lifecycle run exposed an OHLC validation edge case: an adjusted daily low and close differing by about 2e-15 were incorrectly treated as an invalid range. The position-policy range check now accepts machine-precision rounding while retaining the high/low invariant, and the real provider-roundoff case has a regression test. This affects data validation only; it does not relax execution timing, prices, costs, capacity, or any alpha gate.

### Decision

Both hypotheses are rejected for Minerva. The evidence says the current weakness is not solved by adding stop/target rules or broad market de-risking to this signal. The public scanner, public defaults, and Minerva candidate configuration remain unchanged. These exploratory current-survivor tests are not live-trading evidence and do not satisfy the target alpha or consistency gate.
