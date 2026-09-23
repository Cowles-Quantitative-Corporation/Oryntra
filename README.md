<div align="center">

<img src="brand-assets/oryntra-ai-master-logo.png" width="220" alt="Oryntra AI logo">

# Oryntra AI

### Market Intelligence · Systematic Research · Explainable Analysis

**Research before the decision.**

Oryntra is a market-research platform from **Cowles Quantitative Corporation (CQC)** designed to help investors inspect securities, test ideas, understand risk, and make their own decisions using a more structured and evidence-driven process.

</div>

---

> **Research software, not an investment adviser.**
> Oryntra provides research, historical analysis, and educational tools. It does not manage customer assets, place brokerage orders, guarantee results, or tell users what securities they must buy or sell. Historical simulations are analytical outputs, not forecasts or promises of future performance.

> **Ownership and product boundary.**
> Oryntra is owned and operated by **Cowles Quantitative Corporation**. Oryntra is a customer-facing research product. CQC's private proprietary quantitative research, portfolio-allocation research, and any future use of company-owned capital are separate from the public Oryntra workflow.

---

# What Oryntra Is

Oryntra is built around a simple idea:

**Good research should make the evidence, assumptions, risks, and limitations visible.**

Instead of presenting users with a mysterious score or pretending markets can be predicted with certainty, Oryntra combines deterministic market analysis, structured research workflows, historical testing, portfolio diagnostics, and plain-language explanation.

The goal is not to manufacture certainty.

The goal is to make research more disciplined.

Oryntra can help a user:

- inspect a security before acting;
- review technical and market conditions;
- identify structured market setups;
- test clearly defined historical rules;
- compare return and risk;
- evaluate concentration and portfolio behavior;
- preserve research over time;
- inspect assumptions behind a result;
- understand when historical evidence is weak;
- separate quantitative calculations from explanatory AI.

---

# Where Oryntra Fits Inside CQC

Cowles Quantitative Corporation is developing customer software alongside a separate private quantitative research system.

The customer workflow is intentionally simple:

### Oryntra
**Research before a decision.**

Investigate securities, market conditions, historical behavior, risk, and systematic research evidence.

### Rule Mirror
**Review after a decision.**

Import brokerage activity, reconstruct trades, analyze outcomes, and measure whether actual behavior followed the process or rules the user intended to follow.

### CQC Proprietary Research
**Private quantitative R&D.**

CQC separately develops and evaluates quantitative models, portfolio-construction systems, risk controls, data infrastructure, and research methods for internal company use.

The public Oryntra product is not a public interface to CQC's private capital-allocation research.

---

# What Oryntra Provides

| Area | Oryntra provides | Oryntra does not provide |
| --- | --- | --- |
| Market intelligence | Derived technical states, indicators, pattern observations, setup analysis, and explainable summaries | Guaranteed predictions |
| Security research | Structured single-ticker research and historical context | Personalized investment advice |
| Systematic research | Transparent rules, historical simulations, configurable assumptions, holdouts, and diagnostics | Proof that a strategy will work in the future |
| Portfolio analysis | Risk, concentration, volatility, turnover, correlation, and historical portfolio diagnostics | Customer asset management |
| Data handling | Provider-aware retrieval, local caching, validation, metadata, and reproducibility controls | A license to redistribute restricted vendor data |
| AI | Plain-language interpretation of structured server-side research | AI-generated market facts or untraceable numeric calculations |
| Product operation | Authentication, watchlists, saved research, research workspaces, and account controls | Brokerage execution or discretionary trading |

---

# Product Philosophy

## 1. Evidence before narrative

Numerical calculations should come from deterministic systems wherever practical.

AI explanation can help communicate a result, but it should not quietly invent the result.

Indicators, pattern states, portfolio statistics, historical returns, and risk measurements are produced from structured calculations before they reach the explanation layer.

---

## 2. A signal is a hypothesis

Oryntra does not treat an indicator or historical pattern as automatic proof of future direction.

RSI, moving averages, MACD, Bollinger Bands, ATR, ADX, breakouts, pullbacks, relative strength, volatility conditions, and other measurements are evidence describing a market state.

They remain hypotheses until tested.

---

## 3. Risk belongs beside return

A historical result without its risk profile is incomplete.

Oryntra's research architecture is designed to evaluate return alongside measurements such as:

- drawdown;
- volatility;
- turnover;
- concentration;
- correlation;
- exposure;
- liquidity assumptions;
- historical value at risk;
- expected shortfall;
- data coverage;
- regime behavior;
- transaction-cost assumptions.

---

## 4. Reproducibility matters

A useful research result should be possible to inspect and reproduce.

Where practical, Oryntra records information such as:

- requested universe;
- model or rule configuration;
- historical period;
- dataset source;
- provider metadata;
- cost assumptions;
- rebalance assumptions;
- portfolio constraints;
- dataset fingerprints;
- holdout periods;
- generated diagnostics.

---

## 5. Failed research is still information

Oryntra is not designed around showing only attractive historical results.

A research hypothesis that fails under realistic costs, later periods, different securities, or stricter controls should be documented as a failure rather than quietly retuned until the chart looks better.

That principle is central to CQC's broader research culture.

---

# System at a Glance

```mermaid
flowchart LR
    A["Market data providers<br/>and local cache"] --> B["Repository and validation layer<br/>normalization · freshness · metadata"]

    B --> C["Deterministic analysis<br/>indicators · patterns · scoring"]
    B --> D["Quant research<br/>rules · portfolio controls · simulation"]

    C --> E["Derived market-intelligence API"]
    D --> F["Authenticated research API"]

    E --> G["Oryntra research workspace"]
    F --> H["Quant research workspace"]

    C --> I["Structured quantitative context"]
    I --> J["AI explanation layer<br/>plain-language interpretation"]
    J --> G
