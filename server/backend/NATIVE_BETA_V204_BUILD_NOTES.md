# CQC Native Beta / Model C + Universal Risk V2.0.4

## What changed

- Added organized Alpha compatibility package under `backend/models/alpha/` without breaking legacy imports.
- Added native Beta / Model C package under `backend/models/beta/` and public sibling entry point `backend/beta_v1.py`.
- Beta mirrors the pure Alpha V1.2 Alt architecture boundary: no TBA8 anchor, no Alpha forecast/score input.
- Beta can reuse Alpha's raw feature families as market-level context: momentum 21/63/126, residual momentum 21/63, short reversion, low volatility, sector-relative, value, quality.
- Beta retains its own PIT macro, surprise, regime, walk-forward, calibration, holdout and promotion machinery.
- Renamed Beta helper terminology so `Gamma` is reserved for the future next proprietary model. `gamma.py` became `promotion.py`; related fields/functions use promotion/downstream naming.
- Added Universal Risk V2.0.4 under `backend/risk/v204.py` plus `backend/universal_risk_v204.py` compatibility entry point.
- Risk V2.0.4 runs V2.0.3 first and then permits only bounded Beta-informed de-risking. Beta cannot increase exposure or feed risk outputs back into Model C.
- Added production-frozen/calibrated Beta gates, freshness/quality/uncertainty checks, persistence, disagreement guard, no-trade band, turnover cap, and V2.0.3 fallback.
- Extended `universal_risk_supervisor.py`, `universal_engine.py`, `universal_research.py`, `minerva.py`, and `phase4_control.py` for V2.0.4.
- Added private Control Beta routes in `backend/routes/beta.py` and mounted them from `backend/main.py`.
- Preserved existing root module paths to avoid breaking current code.

## Validation

The original backend test suite was run in batches to avoid the execution environment's single-command time limit. All 54 original test files passed after restoring the same auxiliary frontend/tools/taxonomy fixtures expected by the uploaded test suite: **229 existing tests passed**.

Native Beta regression tests adapted from the standalone Beta package also passed: **26 Beta-core tests passed**.

Targeted final regression covering Beta V1, Risk V2.0.4, Risk V2.0.3, Alpha V1.2 and Phase 4 Control also passed.

Total unique passing test cases across the original suite plus native Beta core suite: **255**.

## Important boundary

Risk V2.0.4 is research-only. If Beta state is absent or fails its gates, V2.0.4 falls back to V2.0.3. Nothing here authorizes live trading.
