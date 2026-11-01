# Delta forecasting findings and remaining evidence gap

The two completed forecasting comparisons answer different questions. Neither
qualifies a trading strategy or demonstrates Sharpe improvement.

| Comparison | Target | Fixed descriptive result | Evidence status |
| --- | --- | --- | --- |
| Earlier acquired-group OFI versus OFI + completed 5m/30m returns | Signed next-60s midpoint return | 0.2894116% balanced MSE reduction, below the 1% screen: **FAIL** | Reused April development; preserved |
| Existing three inputs versus those inputs + four unsigned activity features | Absolute next-60s midpoint return | 9.976554629% balanced MSE reduction: descriptive screen **PASS** | **Contaminated reused-April development only** |

## Fixed magnitude study

The universe was AAPL, AMZN, GOOGL, JPM, MSFT, WMT and XOM. The original ten April
TRAIN sessions and eleven April EVAL sessions supplied 4560 and 4994 common
observations, respectively. The comparison retained the exact existing origins,
endpoint references and 83 endpoint exclusions. It introduced no execution/fill
selection or cash/exposure filter.

Baseline inputs were acquired-group 30-second BBO increment divided by 100 and
completed 300-second/1800-second trade-price returns. The challenger added
`log1p(reported shares / period seconds)` and
`log1p(reported occurrences / period seconds)` for each completed period.
These are recorded-data quantities, not complete-market order flow or VPIN.
Unknown trade side was not imputed.

Both arms used own-name TRAIN-only population centering/scaling, a scale of one
when standard deviation was at most 1e-12, standardized-input clipping at eight,
mean-loss ridge penalty one and an unpenalized intercept. Exactly fourteen models
were fitted and frozen, with no EVAL refitting, parameter search or winning-name
selection. The magnitude target was the absolute value of the established
exact-to-binary64 60-second endpoint return. Negative ridge predictions were
retained without post-hoc clipping; none occurred in this EVAL.

The metric is an equal mean of the 77 name/date MSE cells:

`gain = 1 - balanced_MSE_challenger / balanced_MSE_baseline`.

A zero baseline denominator is undefined and cannot pass. It is not the mean of
cell-level percentage gains. Both arms report the same own-name TRAIN-mean
magnitude reference losses.

| Fixed descriptive metric | Result |
| --- | ---: |
| Baseline balanced magnitude MSE | 1.4687293688563928e-7 |
| Activity challenger balanced magnitude MSE | 1.3222007810153886e-7 |
| Both arms' TRAIN-mean reference balanced MSE | 1.4746638395197013e-7 |
| Challenger MSE reduction versus baseline | 9.976554629% |
| Baseline / challenger reduction versus TRAIN-mean reference | 0.40243% / 10.33883% |
| Names / dates with strict improvement | 7 of 7 / 11 of 11 |
| First five / last six date-half gains | 6.8485967% / 11.4048028% |
| Negative predictions, baseline / challenger | 0 / 0 |
| Leave-one-date-out gain range, no refits | 8.8910104% to 10.7009137% |

The unchanged descriptive screen required at least 1% balanced gain, improvement
in at least five names and eight dates, and improvement in both fixed halves.
The leave-one-date-out summaries did not select a deletion or trigger refitting.

## Access deviation and independent verification

A complete existing April 16 EVAL value JSON was parsed before the new model
freeze during schema discovery. Keys/counts-only output and no influence are the
author's account, **not independently proven history**. The independent reviewer
allowed exactly one fixed continuation as contaminated descriptive development.
The original deviation, failed pre-value enum attempt and frozen artifacts were
preserved. This cannot be represented as untouched or clean prospective evidence.

Independent result review verified all 4994 identities, 19,976 exact squared
errors, 77 balanced cells and fourteen frozen models. The reported TRAIN normal-
equation residual was 2.78e-17. It accepted the arithmetic, not a clean validation
history or economic qualification. Eleven synthetic tests pass; the broader
local suite passed 190 tests and 58 subtests, with 29 optional skips and one
separately identified sklearn deselection. No new dependency was installed.

Origins were 300 seconds apart and target intervals approximately 60 seconds:
within-name targets did not overlap. Feature histories overlap and names/market
dates remain dependent. Historical feature availability is distinct from the
actual retrospective November 1 fitting clock. Conditional archive/capture,
missingness and common-observed-cohort selection limitations remain.

Absolute midpoint movement has no direction, spread, fees, fill assumptions or
portfolio construction. A magnitude accuracy gain does not establish executable
profit, Sharpe, causal feature benefit, risk-policy suitability or adoption.
The earlier signed-return FAIL and both earlier NO_SUPPORT outcomes remain intact.

## Canonical implementation and boundaries

The source is in `server/backend/delta/research/activity_magnitude.py` and
`activity_runner.py`; synthetic tests are in
`server/tests/research/delta/test_activity_magnitude.py`. The staged runner
requires authenticated, externally supplied private cache manifests and a bound
continuation receipt. These inputs, target values, fitted models, cache exports,
local receipts and the private vault are deliberately absent from the repository.
No model/trading consumer is enabled. Source/tests can be checked without those
private datasets:

```sh
cd server
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python -m unittest discover \
  -s tests/research/delta -p test_activity_magnitude.py -v
```

Separately, the canonical tiny single-frame archive → exact decoder → supervised
native volume path is an engineering integration. Its opening-minute profile
cannot produce completed 5m/30m contexts; its bounded tiny reader is not a general
vendor-file loader. The earlier private research pipeline already produced the
contexts used here. Replaying that pipeline is not new predictive evidence.

## Single next scientific dependency

The next question is whether the **unchanged magnitude improvement persists on
an independently designated out-of-time cohort**. Another nearby April parameter
search cannot answer that. No new fit, replay, benchmark or infrastructure layer
is proposed here. Before evaluating, an independently reviewed data-use record
must identify a genuinely unconsumed cohort, causal input/endpoint support and
explicit evaluation authority for that exact scope. Existing reserved validation
and final cohorts stay protected; broader user-reported holdings do not establish
an untouched or usable cohort by filename alone. No reserved targets have been
opened for this next question. This dependency does not justify a purchase,
credit spending, live activation or economic deployment.
