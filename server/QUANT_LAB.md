# Oryntra Quant Lab

Quant Lab is a daily-bar historical research and paper-simulation workspace. It has no broker client, does not create orders, and does not choose a real trade for a user.

## System flow

`daily market history + eligible point-in-time corporate/macro facts → deterministic sleeves → optional regime-conditioned weights → portfolio limits → next-session simulation → costs and diagnostics`

The legacy Quant Lab profiles are independent of the scanner models. V1.0 Official Momentum, V8, VAI 1.0, and VAI 2.2 are scanner/setup research paths. New `universal_v2` explicitly shares its signal implementation across scanner, Pattern Lab and portfolio research. Its cash/shares simulator, controls, alpha definition and actual failed validation results are documented in [Universal V2 backend](../docs/UNIVERSAL_V2_BACKEND.md). The remainder of the sleeve/timing sections below describes the legacy profiles unless stated otherwise.

## Model profiles

| UI profile | Internal identifier | Starting allocations |
| --- | --- | --- |
| Oryntra V1.0 corporate quant system | `v1_corporate_quant_system` | 25% trend, 20% relative strength, 10% mean reversion, 10% defensive low volatility, 35% corporate quality |
| V1.0 diversified price baseline | `v8_regime_diversified` | 35% trend, 30% relative strength, 15% mean reversion, 20% defensive low volatility |
| V1.0 balanced price baseline | `v8_balanced` | 45% trend, 40% relative strength, 15% mean reversion |
| V1.0 trend-first price baseline | `v8_trend_first` | 65% trend, 25% relative strength, 10% mean reversion |
| V1.0 relative-strength price baseline | `v8_relative_strength` | 25% trend, 65% relative strength, 10% mean reversion |
| V1.0 equal-weight baseline | `equal_weight_baseline` | 34% trend, 33% relative strength, 33% mean reversion |
| Oryntra V1.1 long-only trend/momentum research | `v1_long_only_trend_momentum_research` | 60% trend, 40% relative strength; long-only |
| Universal V2 research engine | `universal_v2` | Shared bounded price features; separate cash/shares ledger, not legacy sleeve allocations |

The server keeps the older `v8_*` identifiers for compatibility. They do not mean these profiles use the scanner’s V8 evidence model. Selected positive sleeve allocations are normalized to 100%.

Universal V2's full configurable scan and alpha-test endpoints are under `/api/universal` when the full Quant/private route flag is enabled. The existing Quant request also accepts its model ID and maps risk/cost settings, but does not supply the benchmark/cash series needed for alpha. Its report preserves exact V2 parameters as `engine_configuration`. This candidate is not automatically selected in the browser or mobile app.

Implementation audit: legacy portfolio profiles maintain shifted target weights and charge target changes rather than maintaining actual drifting shares. They must not be treated as execution-equivalent to V2. Old scanner backtests also need rerunning after the missing stop/target return and two-sided-commission fixes. Earlier research entries are historical evidence, not validation of the corrected implementation.

The V1.1 long-only trend/momentum profile defaults to long-only when a caller has not explicitly selected a shorting policy. It is a research candidate, not a default or trading recommendation. It was added only after a fixed 12 bps cost, 12% one-way volatility-cap, weekly-rebalance study passed the stated chronological-holdout and walk-forward gate on one volatility-selected development universe and an untouched volatility-selected stock universe. The study does not establish live performance, broad-market robustness, or suitability; the profile remains subject to the same experiment records, capacity scenarios, correlation stress diagnostics, and review process as every other Quant Lab profile.

## Sleeves

### Time-series trend

Measures each symbol’s trailing return and takes its sign. Long/short mode permits positive and negative weights; long-only mode removes negative signals. Each date is normalized to a gross weight of one before portfolio combination.

### Cross-sectional momentum

Ranks the universe by trailing return. It selects the top 30% and, in long/short mode, the bottom 30%. Fewer than four available symbols produces no momentum position.

### Mean-reversion comparator

Measures the configured short-horizon move, default five sessions, relative to a rolling 63-session standard deviation. It opposes moves whose standardized value reaches ±1.5. This simple comparator can fight genuine trends and should be read with turnover and cost results.

### Defensive low volatility

Ranks trailing 63-session realized volatility. It favors the lowest 35% and can offset them with the highest 35% in long/short mode. It requires at least four available symbols.

### Corporate quality and change

Ranks eligible point-in-time public corporate scores. It forms relative top/bottom baskets only when at least four symbols have nonzero coverage. Zero or sparse coverage is not treated as neutral proof of quality.

Supported corporate metrics are revenue growth, operating margin, free-cash-flow margin, earnings surprise, guidance revision, estimate revision, insider net buying, share-count growth, and net debt to EBITDA.

## Timing and controls

1. A sleeve calculates its target with information available at the close of session `t`.
2. Single-name weights are clipped to the configured absolute limit.
3. Gross exposure is scaled down to the configured maximum.
4. Targets are held daily, every fifth session for weekly mode, or from the first observation of each calendar month for monthly mode.
5. A 21-session realized annual volatility estimate determines a one-way scaler. The scaler is lagged and clipped between zero and one, so it can reduce but not amplify the target.
6. Holdings are shifted again for the return calculation, so the target formed at `t` earns returns beginning on the following session.
7. Changes in target weight incur the selected execution-cost model; negative held weights incur annualized borrow cost.

Default controls are 12% target annual volatility, 1.0 maximum gross exposure, 35% maximum single name, weekly rebalancing, 12 bps base trading cost, 50 annual bps short borrow, three walk-forward folds, $1 million assumed portfolio value, 18 bps impact coefficient, and 2% maximum ADV participation.

## Regime-conditioned allocations

The optional regime model uses prior benchmark trend, realized volatility, drawdown, yield-curve slope, credit spread, and inflation context to produce probabilities for persistent trend, stressed, reversal-risk, and normal states. Those probabilities multiply the configured sleeve weights and the result is renormalized.

This changes the sleeve mixture; it does not train a return predictor, forecast a regime with certainty, or place an order. When macro coverage is absent, the price-history components still operate and the report exposes the missing structured coverage.

When more than one sleeve is selected, their target weights are combined symbol by symbol before the shared concentration, gross-exposure, volatility, timing, and cost controls are applied. That is transparent signal netting—not a style optimizer—and it can still create an overfit historical blend.

## Liquidity-aware execution costs

When enabled, the engine:

- converts each target-weight change into notional using the assumed portfolio value;
- estimates 20-session median daily dollar volume from price × volume;
- calculates assumed one-day ADV participation;
- applies base turnover cost plus `impact coefficient × sqrt(participation)`; and
- reports maximum participation, missing-liquidity observations, and days above the selected limit.

This is a capacity-sensitive cost proxy, not a complete execution or fire-sale model. It does not spread an order across days, reject an infeasible target, simulate a limit-order book, stress ADV/spreads, model financing/redemptions, or feed market impact back into later prices.

The report also re-runs that same formula at 1×, 2×, and 5× the selected portfolio-value assumption. It shows estimated aggregate proxy cost, maximum estimated participation, and participation-limit breaches at each size. This is a capacity sensitivity table, not an institutional liquidation model: it still does not know order-book depth, spreads, multi-day trading, other participants, or feedback from a sale into prices.

## Point-in-time corporate and macro data

Use the authenticated `POST /api/quant/corporate/import` endpoint in a mode that mounts the full Quant router. It accepts `documents`, `facts`, and `macro_observations`. Imports reject unsupported metrics, disallowed source classes, non-HTTPS sources, and missing availability timestamps.

Example corporate fact:

```json
{
  "ticker": "ACME",
  "metric": "operating_margin",
  "value": 18.4,
  "period_end": "2025-12-31",
  "published_at": "2026-02-05T21:10:00Z",
  "available_at": "2026-02-05T21:10:00Z",
  "source_class": "sec_filing",
  "source_url": "https://www.sec.gov/Archives/example"
}
```

Supported macro metrics are `policy_rate`, `yield_2y`, `yield_10y`, `credit_spread_bps`, and `inflation_yoy`. Each observation is eligible only after `available_at`. The current repository does not provide a broad macro dashboard or complete real-time macro ingestion service.

## Report interpretation

### Performance and path

- Total/annualized return and annualized volatility describe the simulated net series.
- Sharpe uses a zero cash rate; Calmar divides annualized return by absolute maximum drawdown.
- Maximum drawdown, longest drawdown, worst day, historical 95% VaR, and expected shortfall expose path/tail behavior.
- Annualized turnover and average gross exposure help identify implementation intensity.
- Equity, drawdown, and rolling 63-session volatility charts are derived from the same net portfolio series.

### Validation

The engine reserves at least 63 sessions or the final 20% of history as a chronological holdout when at least 126 sessions exist. Earlier development history is divided into sequential walk-forward report slices. Development and holdout summaries retain their actual simulated turnover and gross exposure, so implementation intensity remains visible in the validation comparison. The fixed sleeve rules are not fitted by this function, so the split is a robustness comparison rather than model training.

Every report also includes an equal-weight buy-and-hold reference over the exact selected symbols. It is intentionally simple and has no rebalancing, borrow, or liquidity cost, so it is a comparison reference rather than an apples-to-apples executable portfolio. A higher strategy result alone is not a pass: compare its holdout path, drawdown, turnover, cost assumptions, coverage, and diagnostics with the reference.

### Regime and exposure

- Regime breakdown groups net returns by broad market trend and volatility states.
- Latest gross/net exposure, largest name, effective number of positions, and displayed weights describe the final simulated holdings.
- The 126-session average absolute correlation is a compact concentration/diversification diagnostic.

### Correlation matrix and stress

The correlation matrix contains pairwise daily-return correlations over at most 126 recent sessions. The correlation-convergence report freezes the latest active weights and trailing marginal volatilities, then moves all pairwise correlations partway toward +1 over a 21-session horizon:

- moderate convergence: 50% of the distance toward +1;
- severe convergence: 85% of the distance toward +1.

It reports baseline and stressed 21-session/annualized volatility plus a risk multiplier. This is a hypothetical diversification-failure sensitivity test. It is not a price shock, loss forecast, causal crisis model, or allocation instruction.

### Structured data and attribution

- Corporate/macro coverage says how much of the selected history had eligible structured evidence.
- Factor attribution reports recent market beta, residual, long, short, and component contributions; it is descriptive, not causal.
- Strategy health compares the most recent 63 sessions with earlier sleeve history and labels deterioration as a prompt for investigation.
- Data-quality rows report usable bars, first/last dates, missing-session percentage, and complete overlap.
- The dataset fingerprint identifies the selected histories and configuration for reproducibility.

### Assumption ledger

Every completed report returns and displays an assumption ledger. It records the exact timing convention, rebalancing schedule, one-way volatility target, gross and single-name limits, short-borrow assumption, base cost, liquidity-proxy inputs, corporate/macro point-in-time coverage, and correlation-stress construction used for that run. It also lists material omissions: order-book/venue depth, bid-ask series, fill or best-execution simulation, delisted-security point-in-time coverage, taxes, financing beyond stated borrow, and unfilled-order opportunity cost.

The ledger is an audit aid. It does not convert daily OHLCV into a liquidity classification or execution claim, and it does not change a portfolio’s simulated holdings.

Each report also includes a shared `research_governance` record used across Oryntra’s research paths. For Quant Lab it preserves the profile, configured universe and fingerprint, validation expectation, portfolio scope, and the boundary between daily-bar cost/capacity scenarios and a real order-book or fire-sale model.

## Reproducible strategy experiments

Quant Lab now records completed API/browser reports as `quant_strategy` experiments with their configuration, dataset fingerprint, history range, symbols, primary result, reference result, and chronological validation summary. The record is metadata only; browser-uploaded raw bars are still evaluated in memory and are not persisted by that route.

For deliberately testing a new research hypothesis without changing application code, use the declarative CSV-and-manifest runner. It accepts fixed daily `date,ticker,close,volume` rows and a JSON manifest; it does **not** execute arbitrary strategy code. This preserves a readable research contract and makes it possible to compare like with like.

```bash
cd server
PYTHONPATH=. .venv/bin/python3 tools/run_quant_experiment_cli.py \
  --bars-csv /absolute/path/daily_bars.csv \
  --manifest examples/quant_strategy_manifest.example.json \
  --output /absolute/path/quant_report.json
```

Start from `examples/quant_strategy_manifest.example.json`. Give every run a plain-language hypothesis, choose only the supported sleeve identifiers, and explicitly retain its lookbacks, allocations, limits, schedule, costs, liquidity-proxy inputs, and validation settings. The runner creates an output report and a local experiment record; it never sends an order, retains provider credentials, or promotes a result automatically.

An experiment is worth discussing only after the hypothesis and rules were fixed before looking at its holdout, and the holdout is compared against the disclosed reference with realistic limitations in mind. The runner is not a tool for scanning parameter combinations until one looks good.

## Client and route behavior

Browser and mobile direct clients fetch daily bars directly from the selected provider and submit normalized bars to `POST /api/quant/run-upload`. The provider key does not enter the request, and uploaded histories are evaluated in memory. Mobile supports two to eight unique symbols, caps Polygon/Massive Basic runs at four, and saves completed reports on the device.

Private/server-provider mode also exposes the catalog, corporate import/query, and server-repository `POST /api/quant/run` path. This path can use the local market cache and server-side provider configuration.

## Local use

```bash
cd server
PYTHONPATH=. .venv/bin/python3 run.py
```

Set `ORYNTRA_PRIVATE_RESEARCH_ROUTES=true` for the full local research surface. A public signed-in Quant Lab can instead be mounted with `ORYNTRA_PUBLIC_QUANT_LAB_ENABLED=true`, but only after reviewing provider rights, access policy, resource limits, and public product claims.

## Known limitations

- No point-in-time delisted-security universe, so survivorship concerns remain.
- Daily bars are insufficient for precise fill, spread, borrow availability, and intraday market-impact reconstruction.
- Macro/corporate records depend on correct source classification and availability timestamps.
- Regime formulas, sleeve thresholds, and impact parameters are research assumptions, not established universal constants.
- Correlation convergence does not include simultaneous marginal-volatility shocks or nonlinear cross-asset fire-sale feedback.
- A favorable backtest or promoted experimental model does not demonstrate live profitability.
