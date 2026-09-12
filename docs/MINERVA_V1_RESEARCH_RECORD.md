# Minerva V1 research record

Status: **candidate, not release-approved**. Last updated: 2026-09-06.

Minerva is the first named candidate in Oryntra's next Universal generation. It remains research-only: it does not place orders, replace the public scanner, or make a performance claim.

## Frozen price-only baseline

The baseline is a causal walk-forward ridge model trained on 756 completed sessions and retrained every 21 sessions. It labels five-session next-open-to-close relative returns, uses ridge penalty 1.0, allocates weekly, targets 18% volatility, holds at most 24 names, and applies next-open cash/share fills, 12 bps one-way costs, square-root participation impact, and a 2% prior-dollar-volume fill ceiling. Market context, peer shocks, and position policy were disabled.

The saved configuration is [`server/examples/minerva_v1_baseline.json`](../server/examples/minerva_v1_baseline.json). Its `research_profile` is `minerva_v1`; all new sleeves are explicitly disabled, so rerunning this file reproduces the frozen price-only candidate rather than an untested hybrid.

The study used adjusted daily Yahoo OHLCV for 250 deterministic symbols per year from a current-constituent S&P 500 source, SPY/QQQ as the equally weighted beta benchmark, and Kenneth French daily risk-free returns. It covers the nonconsecutive seeded years 2003, 2005, 2008, 2010, 2012, 2014, 2016, 2018, 2021, and 2024. It is exploratory because it has current-survivor bias and is not a historical index-membership universe.

Each annual report now records its resolved symbol list, symbol-selection seed, eligible-universe count, and full dataset fingerprint. That is an audit trail, not a cure for current-survivor bias.

| Metric | Result |
| --- | ---: |
| Mean annual beta-adjusted alpha | +3.68% |
| Median annual alpha | +4.46% |
| Negative years | 3 of 10 |
| Acceptance threshold | ≥5.00% mean and at most 1 negative year |

Annual alpha: 2003 +14.25%, 2005 +10.02%, 2008 −2.11%, 2010 +2.81%, 2012 +5.74%, 2014 −4.02%, 2016 +13.97%, 2018 −12.74%, 2021 +4.88%, 2024 +4.05%.

## Retained breadth tune

On the five early development years, 12 and 18 holdings produced +7.62% and +7.55% respectively. The 18-holding version was selected because it had zero negative development years. Frozen later-year confirmation then produced −0.31% mean alpha with two negative years (2014 −7.04%, 2016 +12.31%, 2018 −14.50%, 2021 +3.69%, 2024 +4.00%). This was worse than the 24-holding baseline's +1.23% on those same years, so the 18-holding tune is rejected and retained here to prevent result-shopping.

## Next information sleeves

The first directly testable Minerva addition is a pair of 21- and 63-session residual-momentum features. They subtract each stock's trailing 126-session beta exposure to the completed SPY/QQQ benchmark before entering the walk-forward ridge learner. This is an explicit, separate candidate—not a change to the frozen price-only baseline—and it requires full benchmark coverage.

Minerva also supports two inactive corporate sleeves: an availability-dated cross-sectional fundamental-quality score and a decaying filing-acceleration score. Filing acceleration measures a change from the issuer's previously public filing-derived growth score; it does not claim to be analyst consensus surprise data. Both corporate weights are zero by default. A run with either nonzero requires a full availability-dated panel, a frozen development weight, and a later confirmation run against the same alpha gates.

The SEC path is explicit: `tools/fetch_sec_companyfacts.py` requires a declared `ticker,cik` mapping and an identifying user agent, rate-limits its requests, stores raw Company Facts JSON plus a file-hash manifest, and never infers tickers or imports directly. `tools/normalize_sec_companyfacts.py` then emits conservative next-session availability-dated revenue-growth, gross-margin, operating-margin and net-income-margin facts for review/import. A current CIK mapping is not a point-in-time security-identity history; that remains a separate promotion requirement.

`tools/build_sec_cik_manifest.py` builds the declared mapping from a saved SEC `company_tickers.json` snapshot and writes unresolved symbols separately. It uses exact ticker matching on purpose: an unresolved historical identity blocks coverage rather than being guessed into a current issuer.

`tools/normalize_sec_companyfacts_batch.py` normalizes one complete declared raw acquisition into a fact file with hashes for each source JSON. Normalization must finish and meet the observed-coverage gate before either corporate sleeve is tested.

## Retained feature and consistency trials

| Candidate | Development result | Decision |
| --- | --- | --- |
| 21-session residual momentum | +6.28%, 1 negative year | Later years: +0.51%, 2 negative years; rejected. |
| 63-session residual momentum | +4.85%, 2 negative years | Rejected before confirmation; worse consistency. |
| 21 + 63 residual momentum | +5.93%, 1 negative year | Rejected before confirmation; below the base's +6.14% development result. |
| 25% / 50% score persistence | +7.41% / +7.73%, 2 negative years each | Rejected before confirmation; higher alpha did not meet the consistency goal. |
| 50% / 75% annual-volatility exclusion | +5.78% / +5.99%, 1 negative year each | Rejected before confirmation; lower alpha without better consistency. |
| 20% SEC corporate-quality overlay | 2014–24 eligible years only; 2 negative years | Rejected. The gross/operating/net-income-margin version averaged +1.11% over 2014, 2016, 2018, 2021, and 2024, below the base's +1.23% on those same years and with the same two negative years. |

These trials were all run with the same five early seeded years and 250 deterministic symbols per year. The later confirmation years were used only once for the selected 21-session residual candidate. The [residual-momentum literature](https://repub.eur.nl/pub/22252/ResidualMomentum-2011.pdf) motivated the hypothesis; it does not validate Minerva.

## Corporate-data readiness result

The declared current-survivor research universe resolved 475 current SEC CIKs. A rate-limited SEC Company Facts acquisition yielded revenue growth plus gross, operating, and net-income margins. The availability-dated quality panel still failed the 80% observed-data requirement in 2010 (43.5%) and 2012 (79.6%). It was therefore not spliced into the ten-year price study. Later eligible years did not improve the consistency result. SEC XBRL coverage begins in 2009 and a current CIK snapshot is not point-in-time membership/delisting history, so this source cannot independently qualify Minerva for the original ten-year release gate.

## Decision

Minerva's 24-holding price-only configuration is the saved active research candidate. It is not ready for release as a validated alpha model: it needs point-in-time membership/delisting history, historical timestamped corporate data, and an unseen confirmation that meets the documented gates before promotion.
