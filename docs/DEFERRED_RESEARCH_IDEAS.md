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
