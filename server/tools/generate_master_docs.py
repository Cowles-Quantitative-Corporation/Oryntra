#!/usr/bin/env python3
"""Generate a plain-text Oryntra technical reference from reviewed source.

The output is intentionally source-derived: it inventories every Git-tracked
file plus explicitly reviewed additions, parses readable source and configuration, and catalogues binary assets
without reading private `.env` values, local market-data files, or untracked
build products.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import textwrap
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
OUTPUT = ROOT / "docs" / "Oryntra_AI_Master_Technical_Documentation.txt"

# Explicitly reviewed new sources are included before staging, never all untracked files.
REVIEWED_ADDITIONS = (
    "server/backend/universal_engine.py", "server/backend/portfolio_execution.py",
    "server/backend/alpha_evaluation.py", "server/backend/universal_research.py",
    "server/backend/universal_position_policy.py", "server/backend/universal_risk_supervisor.py", "server/backend/universal_market_context.py", "server/backend/universal_taxonomy.py", "server/backend/universal_research_blueprint.py",
    "server/backend/routes/universal.py", "server/tools/run_universal_study.py",
    "server/tools/build_universal_taxonomy.py", "server/data/universal_taxonomy/family_catalog.json",
    "server/data/universal_taxonomy/financedatabase_seed_25000.json",
    "server/tests/test_universal_engine.py", "server/tests/test_universal_risk_supervisor.py", "server/tools/build_tba_paired_panel.py", "server/tools/run_tba_paired_risk_study.py", "server/tools/run_tba_risk_component_study.py", "server/examples/universal_study_manifest.json",
    "docs/UNIVERSAL_V2_BACKEND.md", "docs/UNIVERSAL_V2_VALIDATION.json", "docs/ASTRA_UNIVERSAL_TUNING_PLAYBOOK.md",
    "server/backend/legal_operator.py", "server/backend/internal_access.py",
    "server/frontend/legal/terms_canonical.html", "server/frontend/legal/privacy_canonical.html",
    "server/frontend/legal/risk-disclaimer_canonical.html", "server/frontend/legal/methodology_canonical.html",
    "server/frontend/legal/refund_canonical.html", "server/tests/test_legal_operator.py",
    "docs/LEGAL_AND_REGULATORY_CHANGELOG.md", "docs/TBA_RISK_SUPERVISOR.md", "docs/TBA_RISK_PAIRED_RESULTS_2026-09-13.json",
)


def source_files() -> list[Path]:
    """Return the version-controlled surface plus explicitly reviewed additions.

    Git is the authority here. This prevents local Flutter output, credentials,
    databases, caches, virtual environments, and editor files from leaking into
    the manual while still documenting extensionless files and binary assets.
    """
    try:
        raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError("A Git checkout is required to generate the complete file reference.") from error
    files = {ROOT / item.decode("utf-8") for item in raw.split(b"\0") if item}
    files.update(ROOT / item for item in REVIEWED_ADDITIONS)
    return sorted((path for path in files if path.is_file()), key=lambda item: rel(item))


def source_text(path: Path) -> str | None:
    """Read a tracked text file; return None for a binary asset."""
    if path == OUTPUT:
        return ""
    if path.stat().st_size > 2_000_000:
        return None
    payload = path.read_bytes()
    if b"\0" in payload[:8192]:
        return None
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return None


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def git_value(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def line_count(text: str) -> int:
    return text.count("\n") + (1 if text else 0)


def section(title: str, level: int = 1) -> str:
    return f"\n{'=' * 88 if level == 1 else '-' * 88}\n{title}\n{'=' * 88 if level == 1 else '-' * 88}\n"


def paragraph(text: str) -> str:
    return textwrap.fill(" ".join(text.split()), width=94) + "\n\n"


def category_for(path: Path) -> str:
    value = rel(path)
    if path == OUTPUT:
        return "Generated documentation"
    if value == "README.md":
        return "Product and operating model"
    if value.startswith("docs/"):
        return "Product or technical documentation"
    if value.startswith("brand-assets/"):
        return "Brand asset"
    if value.startswith("server/backend/routes/"):
        return "HTTP API route"
    if value.startswith("server/backend/patterns/"):
        return "Deterministic pattern analysis"
    if value.startswith("server/backend/"):
        return "Backend service or research engine"
    if value.startswith("server/frontend/legal/"):
        return "Legal and policy surface"
    if value.startswith("server/frontend/static/"):
        return "Browser presentation or client behaviour"
    if value.startswith("server/frontend/"):
        return "Browser application structure"
    if value.startswith("server/maintenance_site/"):
        return "Maintenance-mode application"
    if value.startswith("server/tests/"):
        return "Automated regression test"
    if value.startswith("server/tools/"):
        return "Operator or research utility"
    if value.startswith("server/"):
        return "Server configuration or operator asset"
    if value.startswith("ios-app/lib/"):
        return "Flutter mobile application"
    if value.startswith("ios-app/ios/"):
        return "Native iOS integration or build configuration"
    if value.startswith("ios-app/assets/") or value.startswith("ios-app/web/icons/"):
        return "Mobile or web application asset"
    if value.startswith("ios-app/"):
        return "Mobile application configuration or operator asset"
    return "Repository asset"


def purpose_for(path: Path) -> str:
    name = path.name
    mapping = {
        "main.py": "Composes the FastAPI application, its middleware, route boundaries, static assets, and health surface.",
        "run.py": "Loads local environment configuration and starts the Uvicorn service.",
        "market_repository.py": "Normalizes cache and provider history into a provenance-carrying market-data interface.",
        "quant_research.py": "Defines transparent strategy sleeves, portfolio controls, simulations, validation, and visual diagnostics for Quant Lab.",
        "twelvedata_client.py": "Provides rate-limited, server-side Twelve Data access when configured.",
        "market_cache.py": "Maintains cached market history and scheduled refresh behaviour.",
        "database.py": "Owns SQLite schema initialization and durable application-data helpers.",
        "app.js": "Implements browser interactions, API calls, result rendering, navigation, and private Quant Desk behaviour.",
        "refined.css": "Provides the current restrained blue research-desk visual system.",
        "index.html": "Defines the browser workspace structure, controls, panels, and accessibility hooks.",
        "QUANT_LAB.md": "Documents Quant Lab scope, provider configuration, and research limitations.",
        "main.dart": "Composes the Flutter mobile application and its navigation/runtime configuration.",
        "api_service.dart": "Defines the mobile client interface to Oryntra API endpoints.",
        "app_config.dart": "Centralizes mobile application configuration and endpoint selection.",
    }
    if name in mapping:
        return mapping[name]
    if path.parent.name == "tests":
        return "Exercises a focused behavioural or regression contract for the named subsystem."
    if path.parent.name == "routes":
        return "Defines request validation and API behaviour for the named capability."
    if path.parent.name == "patterns":
        return "Implements a deterministic component of the pattern-detection layer."
    if path.parent.name == "tools":
        return "Provides a command-line or maintenance workflow for the named subsystem."
    if path.suffix == ".css":
        return "Defines visual layout, typography, responsive behaviour, and component states."
    if path.suffix == ".dart":
        return "Implements a Flutter mobile screen, service, widget, or configuration component."
    if path.suffix == ".swift":
        return "Implements native iOS application or widget behaviour."
    if path.suffix == ".html":
        return "Defines a rendered document or browser surface and its semantic structure."
    return "Contributes configuration, implementation, or documentation to the Oryntra system."


SYSTEM_CHAPTERS = [
    (
        "1. WHAT ORYNTRA IS AND IS NOT",
        [
            "Oryntra is a market-intelligence and systematic-research product. It accepts a request for a listed symbol or a group of symbols, obtains historical price data through server-side provider adapters, derives technical and pattern observations, and returns a structured interpretation to a browser or mobile client. The important word is structured: the product is built around intermediate evidence such as indicators, detected patterns, score components, confidence limits, source metadata, and scenario diagnostics. A conclusion is therefore intended to be inspectable rather than presented as an unexplained prediction.",
            "The project is not an order-management system, a brokerage connection, a live portfolio-management service, or an investment adviser. The paper-trading features record simulated actions inside the application, while the Quant Lab simulates fixed rules against daily historical data. Neither system submits an order. That boundary is practical as well as legal: it allows research and education to move faster than a regulated execution product while still requiring the documentation to be candid about model limits.",
            "The repository contains several generations of analytical engines and historical delivery material. A reader must distinguish the current GitHub checkout from older NAS deployment records and from experimental engine work. This manual describes the checked-out source. Historical Notion pages are useful evidence of past decisions, but they are not treated as confirmation of a current live deployment, database, provider account, or worker process.",
        ],
    ),
    (
        "2. APPLICATION COMPOSITION AND REQUEST LIFECYCLE",
        [
            "The application starts in server/run.py, which loads a private environment file when present and launches Uvicorn. server/backend/main.py then becomes the composition root. It initializes SQLite during the FastAPI lifespan, configures CORS and gzip, serves the browser files, attaches policy-aware API routers, and publishes health and version information. The main module also owns the distinction between a public derived-analysis site and private research routes. That decision is made at startup from environment configuration rather than from hidden client-side flags.",
            "A typical browser scan is a staged pipeline. The route validates the request, asks the market repository for a normalized history, calculates deterministic indicators, selects the configured pattern and setup logic, derives an educational plan, optionally persists noncritical history, and returns a JSON object. The public payload boundary then removes raw market-data content that should not be exposed by the public product. The browser renders this structured response; its AI explanation request is a separate step and does not replace the numerical pipeline.",
            "The product has a deliberately different lifecycle for compute-heavy research. Pattern Lab jobs and Quant Lab runs are private capabilities. Pattern Lab can use persistent job state and a separate worker so that long-running evaluations do not occupy the web server’s request loop. Quant Lab performs a bounded synchronous research calculation in a worker thread after histories have been obtained. The two systems share a research discipline—explicit data, timing, costs, and diagnostics—but they solve different questions.",
        ],
    ),
    (
        "3. DATA, PROVENANCE, AND CACHE CONTROL",
        [
            "The market repository exists to prevent the rest of the application from caring which provider supplied a candle. It normalizes ticker and period inputs, reads locally cached OHLCV history, validates price and timestamp integrity, selects a configured provider when necessary, stores usable bars, and returns both the frame and provenance metadata. The metadata records source, provider, freshness, coverage, and fallback use. Quant Lab adds a fingerprint over its selected histories and configuration so a completed run can be tied to the exact material it evaluated.",
            "Cache-first behavior is a reproducibility control. A live provider can revise data, impose a rate limit, or fail in the middle of an experiment. When research uses a local cache, the user can state what data were available for the run. The cache is durable state, not disposable build output. Deployment procedures must preserve it alongside the application database, and no documentation task should copy a populated database or credential file into a public artifact.",
            "Provider integration is intentionally server-side. Polygon and Twelve Data modules keep keys in environment variables, limit calls, redact secrets in errors, and separate response normalization from higher-level analysis. The application does not claim that the presence of a provider adapter grants permission to redistribute vendor data. The browser receives derived results under the configured policy boundary rather than an unrestricted raw-data API.",
        ],
    ),
    (
        "4. ANALYTICAL ENGINE AND EXPLANATION BOUNDARY",
        [
            "The deterministic core begins with indicators. The indicator module calculates common measures such as moving averages, RSI, MACD, Bollinger Bands, ATR, ADX, volume measures, VWAP, Ichimoku values, pivots, and trend labels from the supplied history. Pattern modules then evaluate candle configurations, chart structures, fair-value gaps, and structure events. The pattern analyzer coordinates broader engine generations, while the setup detector scores candidate contexts such as breakout, pullback, continuation, reversal, overextended, and no-trade states.",
            "These components are not independent proof generators. A pattern detector labels observations according to its rules; a setup scorer aggregates evidence according to its selected engine. The trade scorer produces an educational plan-shaped object with risk/target calculations and descriptive projections. The released product labels the public scanner model V1.0 Official Momentum; some internal functions and fallback messages retain the historical V7 name. V8 is a separate deterministic evidence-scoring candidate, VAI 1.0 is a legacy experimental logistic model, and VAI 2.2 is the newer chronological point-in-time experimental model. None of those candidate names means it has silently replaced the public scanner.",
            "AI explanation is intentionally downstream. The explanation route takes structured analysis and turns it into readable language through configured model providers or a rule-based fallback. It does not calculate RSI, decide numeric pattern confidence, or receive a free-form raw market-data feed as the primary source of truth. This is an important audit property: any language output can be compared against the structured facts it is supposed to explain.",
        ],
    ),
    (
        "5. QUANT LAB RESEARCH MODEL",
        [
            "Universal V2 is a new, separate shared-engine path selected as universal_v2. The same causal price features feed scanner, Pattern Lab and portfolio research; a new cash-and-shares ledger handles next-open transactions, drifting positions, no-trade bands, explicit cash returns, hard prior-volume capacity limits and costs. Stressed covariance affects target risk, rather than only adding a chart. The dedicated authenticated upload routes expose annual benchmark-adjusted alpha with HAC uncertainty. Initial 24-candidate development work and two separate-stock studies did not meet the requested consistency/alpha criteria; the public engine was not replaced. docs/UNIVERSAL_V2_BACKEND.md contains the exact contract and docs/UNIVERSAL_V2_VALIDATION.json retains the evidence.",
            "Quant Lab is the most explicit part of the repository about simulation mechanics. It supports time-series trend, cross-sectional momentum, short-horizon mean reversion, defensive low volatility, and a point-in-time corporate-quality sleeve. Each sleeve converts information available by the simulated date into target weights. Seven named profiles choose transparent starting allocations: the corporate system, diversified price, balanced price, trend-first, relative-strength, equal-weight baselines, and a validation-gated V1.1 long-only trend/momentum research candidate. The V1.1 profile defaults to 60% trend and 40% relative strength without shorts unless a caller explicitly overrides that construction. An ensemble is therefore a named combination of documented component rules rather than a hidden optimizer.",
            "Legacy Quant profiles calculate following-session returns using shifted target weights and charge target changes, not the trades needed to maintain actual drifting shares. That is a material execution limitation, not equivalence to V2's ledger. Their controls cap targets and gross exposure, apply trailing volatility scaling, and optionally blend regime-conditioned sleeve weights. They remain historical research comparators rather than execution-validated portfolio engines. The scanner backtest's missing stop/target return, final-bar handling and one-sided commission were corrected during the V2 work; earlier scanner-backtest results require rerunning.",
            "The report treats path, capacity, and data quality as first-class outputs. It includes return, volatility, drawdown, turnover, historical VaR and expected shortfall, concentration, current gross/net exposure, chronological development-versus-holdout results, walk-forward slices, regime results, factor/relative-value attribution, strategy-health decay, liquidity participation, a trailing correlation matrix, explicit correlation-convergence scenarios, a monthly net-return heatmap, equity/drawdown/rolling-volatility paths, and source coverage. The correlation scenarios preserve current marginal volatility and move pairwise correlations toward positive one; they diagnose diversification failure but do not forecast a loss or change allocations.",
        ],
    ),
    (
        "6. DURABLE STATE, ACCESS, AND PAPER RECORDS",
        [
            "SQLite is the application’s durable memory. The database module initializes tables and exposes helpers for accounts, sessions, subscriptions, watchlists, paper trades, analysis history, market bars, pattern events, outcomes, cache metadata, and model-training records. The database is deliberately treated as operational state. A code release that overwrites it can destroy user records and reproducibility, so deployment and recovery documentation must keep it outside ordinary source replacement.",
            "Authentication uses server-side session handling, password hashing, active-subscription checks, and request helpers that distinguish optional identity from a required signed-in user. Analysis access adds an independent policy and quota layer. This allows the product to expose a limited public derived-analysis experience while preserving owner and private-research controls. It is not a substitute for a full enterprise identity, audit, or regulatory-compliance system.",
            "Paper-trading routes intentionally store simulated opening and closing records rather than interact with a broker. They are useful for user workflow and for separating a user’s record of a hypothetical action from a research report. They must not be described as evidence that a strategy can be executed at the displayed price, size, or time in a live market.",
        ],
    ),
    (
        "7. CLIENT, MOBILE, AND OPERATIONS",
        [
            "The browser client is a static workspace served by FastAPI. Its user-facing areas are Scanner, Watchlist, Paper trades, Backtest, Quant Lab, and Settings. The same bundle also contains signed-in onboarding, browser-only provider-key storage, AI explanations, theme persistence, optional advertising, and a hidden private Pattern Lab panel. index.html establishes the structure, app.js owns state and API interaction, and the two stylesheets supply the presentation. The maintenance site is deliberately independent, while legal pages are served through an explicit allowlist.",
            "The ios-app directory is a Flutter client plus the iOS runner and widget integration. After sign-in and provider setup, its five navigation destinations are Scanner, Watchlist, Paper, Quant Lab, and Account. Mobile-only services add secure session and provider-key storage, consent-gated AdMob support, scan-result notifications, daily research reminders, tracked-symbol alerts, background time for Quant Lab requests, local saved Quant Lab reports, a TradingView chart, and an iOS home-screen widget updated from the latest scan. Native Swift and Xcode configuration bridge these services. A successful backend test does not validate device permissions, widget refresh, signing, or App Store behavior.",
            "Operational tools exist for cache inspection and warming, research execution, bias auditing, training, database-related safeguards, and local launch. These tools are intentionally explicit rather than magical. They should be run from the server directory with the correct environment, and destructive deployment actions must preserve credentials, data, generated models, training history, and the virtual environment. A service that listens on a port is not automatically healthy; check the real health response and the intended user flow.",
        ],
    ),
]


DEEP_FILE_NOTES = {
    "server/backend/universal_engine.py": "Universal V2 defines shared causal price features, validated configuration, signal provenance and portfolio target construction. Scanner, Pattern Lab and the V2 portfolio adapter use the same signal implementation. Shrunk and correlation-stressed covariance scales risk down once; the engine is a research candidate, not a proven alpha source.",
    "server/backend/portfolio_execution.py": "The Universal V2 ledger maintains actual simulated cash and shares. Prior-close targets transact at the next open, with two-sided costs, prior-dollar-volume fill ceilings, a no-trade band and no borrowing. Shares drift between trades. Partial/unfilled orders and flat-to-flat episode outcomes are explicit; this is not live execution or a calibrated order-book simulator.",
    "server/backend/alpha_evaluation.py": "Annual excess-return regressions estimate arithmetic CAPM alpha against an explicit benchmark and daily cash-return series. Newey-West uncertainty, full-calendar checks and the exact ten-year consistency gates prevent raw return, partial-year or eleven-year results being presented as the requested alpha test. Model-search uncertainty is not eliminated.",
    "server/backend/universal_research.py": "This adapter joins shared targets, the cash/shares ledger and alpha statistics into a Quant-compatible report. It enforces evaluation warmup, keeps data/config/source fingerprints and distinguishes absent alpha inputs from zero alpha. Its output is research evidence, not an automatic promotion.",
    "server/backend/universal_position_policy.py": "This disabled-by-default pure state machine defines the future adaptive-exit contract: entry risk plan, non-widening long stops, precommitted daily-bar stop/target behavior and close-only next-open directives. It is deliberately separate from the ledger until a frozen-manifest integration and accounting tests exist; it cannot place a real order.",
    "server/backend/universal_risk_supervisor.py": "This TBA-only causal risk layer caps correlated clusters and marginal variance contribution, scales concentrated books, and applies completed-return volatility and rolling-drawdown exposure ceilings. It is disabled for frozen Minerva and carries no performance claim until the declared out-of-sample Sharpe gate passes.",
    "server/backend/universal_market_context.py": "This disabled-by-default pure market overlay turns completed market return, breadth and correlation observations into an interpretable next-open exposure directive. It requires multiple confirming stress signals rather than treating one down index day as an automatic liquidation, and is not yet connected to the V2 ledger.",
    "server/backend/universal_taxonomy.py": "This module validates a versioned many-to-many security/family graph with dated source, membership weight, downside sensitivity and confidence fields. It prevents parent/child double counting and requires point-in-time snapshot coverage before a classification can enter research.",
    "server/backend/universal_research_blueprint.py": "This is the machine-readable roadmap used by the authenticated Quant Lab foundation panel. It names position management, residual selection, event data, regime exposure, diversification and meta-label workstreams, spelling out their inputs, tunable knobs, fixed invariants and evidence gates instead of treating ideas as deployed features.",
    "server/backend/routes/universal.py": "Authenticated configurable scan/run uploads expose Universal V2 when full Quant or private-research routes are enabled. It bounds history sizes, validates daily bars and explicit cash/benchmark series, computes away from the event loop, and persists derived experiment records without raw vendor bars.",
    "server/tools/run_universal_study.py": "The offline CSV/manifest runner selects among a bounded predeclared candidate list using development-period alpha with a fixed consistency penalty, then evaluates temporal and separate-stock tests. It records all attempts and refuses to overwrite an earlier result. Different stocks in reused eras remain exploratory, not independent prospective evidence.",
    "server/tools/build_universal_taxonomy.py": "This operator tool builds a dated top-25,000 eligible-equity membership snapshot from a declared provider export and refuses incomplete coverage or an overwrite. It does not scrape, infer or silently treat a present-day universe as historical data.",
    "server/data/universal_taxonomy/family_catalog.json": "This curated nested family tree supplies reusable market, sector, industry, structural-theme, commodity and rate/credit categories for Universal V2. It contains no claimed current stock memberships; source-backed snapshots supply those separately.",
    "server/data/universal_taxonomy/financedatabase_seed_25000.json": "This 25,000-security generated seed maps active FinanceDatabase equities into multiple region, sector, industry, theme and macro-sensitivity families. It preserves attribution and confidence but is explicitly a current automated coverage seed, not point-in-time historical membership or backtest evidence.",
    "server/backend/main.py": "This is the server's composition root. It decides which routers exist in a given operating mode, protects frontend responses with no-cache headers, serves legal and static assets, and exposes health/version/ads diagnostics. A change here can alter the public attack surface or make a private research feature visible, so changes should be followed by a startup and route-boundary check rather than a syntax check alone.",
    "server/backend/market_repository.py": "This module is the canonical price-history abstraction. Its job is not merely to download bars: it validates symbol format, controls provider preference, normalizes cache and provider payloads into one frame shape, records source metadata, and calculates the reproducibility fingerprint used by research. Any provider change belongs here before downstream engines are allowed to rely on it.",
    "server/backend/quant_research.py": "This module is the Quant Lab calculation engine. It builds each transparent signal sleeve, applies weight and volatility controls, simulates next-session returns after modeled costs, and produces the risk, validation, data-quality, and visual-diagnostic objects rendered by the Quant Desk. It is deliberately daily-bar research code; it should not be extended with execution claims without new data, modeling, and policy work.",
    "server/backend/routes/quant.py": "This route module forms the private Quant Lab API contract. Pydantic validation constrains ticker count, period, provider, model, costs, leverage-related limits, and rebalancing choice before histories are fetched. It then returns the simulator report together with configuration, provider metadata, errors, timestamps, and a fingerprint so that the UI does not have to infer what ran.",
    "server/backend/routes/analysis.py": "This route module controls the ordinary market-scan lifecycle. It coalesces equivalent requests, uses a bounded result cache, runs blocking calculations away from the event loop, separates response-critical work from deferred persistence, and provides single-symbol, watchlist, screening, comparison, statistics, and history surfaces. Its response schema is central to browser compatibility.",
    "server/backend/indicators.py": "This module transforms an OHLCV history into a normalized technical state. It deliberately centralizes numerical calculations so setup, pattern, plan, and explanation layers receive the same inputs. If an indicator convention changes here, every consumer can change behaviour, so regression tests and a representative scan comparison are appropriate.",
    "server/backend/pattern_analyzer.py": "This module is the switchboard for pattern-engine generations. It retains legacy and candidate behavior while defining the official V7 path and later V8/VAI-related experimental paths. Its job is to compose a comparable report from lower-level pattern evidence, not to make an unqualified profitability claim.",
    "server/backend/setup_detector.py": "This module converts indicator and pattern context into named setup candidates. The successive adjustment functions preserve the history of engine generations, including V7 official adjustments and V8 candidate logic. Because this is a scoring layer, even a small threshold change can affect public classifications and must be validated across more than one ticker and date range.",
    "server/backend/trade_scorer.py": "This module converts a selected setup into an educational trade-plan-shaped object. It calculates plan levels, quality/conviction labels, sizing suggestions, and projections from previously derived facts. The output must remain framed as educational analysis because it is not connected to real execution, liquidity, or user suitability information.",
    "server/backend/twelvedata_client.py": "This provider adapter implements the private Twelve Data integration with conservative request pacing and response normalization. It belongs beside the other server-side provider clients rather than in the browser so keys, vendor limits, failure details, and licensing boundaries remain controlled by the backend.",
    "server/backend/database.py": "This is the durable-state boundary. It owns table setup and persistence operations for the application’s account, trade, analysis, cache, and research histories. It should be changed with a migration mindset: inspect existing data, preserve the database file, and test initialization against a nonproduction copy before deploying.",
    "server/backend/market_cache.py": "This module manages the trading-calendar-aware cache lifecycle: planned backfill dates, grouped-day imports, reference-symbol synchronization, retention, and an optional worker. It handles operations rather than user-facing analysis, so its primary concerns are data completeness, rate discipline, recoverability, and avoiding unnecessary provider load.",
    "server/backend/pattern_lab.py": "Pattern Lab evaluates engine observations across a universe and historical windows. It builds causal observations, simulates target/stop outcomes under costs, creates baselines, groups results by relevant context, and reports walk-forward and bootstrap diagnostics. It is a research harness, not a production model-promotion authority.",
    "server/backend/pattern_lab_jobs.py": "This module makes long Pattern Lab work resumable. It writes requests, status, checkpoints, logs, and results atomically; launches or terminates a separate worker; and provides enough filesystem state to report progress after a web request ends. Its file layout is operational state and should be preserved during a deployment.",
    "server/backend/pattern_lab_worker.py": "This is the isolated process entry point for Pattern Lab. It configures resource use, restores request/checkpoint state, runs the evaluation, writes terminal status, and turns worker exceptions into inspectable job results. Its role is to protect the normal web request path from CPU-heavy research work.",
    "server/backend/v8_engine.py": "This module contains the symmetric candidate-scoring building blocks used for V8-oriented research. It scores directional alignment by factors such as trend, momentum, volatility, and pattern confirmation. It is separate from the publicly labeled V7 engine so research comparisons do not silently change ordinary scan behaviour.",
    "server/backend/vai_model.py": "This is an earlier learned-model path built from Pattern Lab rows. It assembles numeric and categorical features, fits simple model components, writes candidate artifacts, and returns a prediction-shaped result for comparison. The presence of model code does not make the prediction deployable without separate evidence, model governance, and held-out validation.",
    "server/backend/vai2_model.py": "This later learned-model path adds training-quality metrics, promotion comparison, and richer feature processing. It still belongs to the research side of the application: it can produce candidate or promoted artifacts inside the project, but its evaluation must remain separate from a claim that the public scanner is using an institutionally validated model.",
    "server/frontend/index.html": "This document is the browser application’s structural contract. Its element IDs, tab containers, forms, dialogs, and data attributes are consumed by app.js. Removing apparently cosmetic markup can break client initialization, so interface changes should preserve or intentionally migrate those hooks.",
    "server/frontend/static/js/app.js": "This is the browser orchestrator. It owns client-side API calls, account transitions, tab selection, scans, results, charts, paper records, settings, and Quant Desk rendering. It contains legacy assumptions and multiple startup paths, so browser-console checks after a clean reload are especially important when the HTML changes.",
    "server/frontend/static/css/refined.css": "This stylesheet is the current blue research-desk presentation layer. It defines the visual hierarchy and responsive behavior for the shared workspace and Quant Desk. Its selectors must continue to match the actual HTML and JavaScript state classes; visual review at desktop and mobile widths is the appropriate validation.",
    "ios-app/lib/main.dart": "This is the Flutter client’s application shell. It composes navigation and high-level page state while coordinating backend access through the client services. It should be validated as a mobile application rather than assumed correct because the web client works.",
    "ios-app/lib/services/api_service.dart": "This service owns the Flutter-side HTTP contract. It defines how the mobile client reads, transforms, and handles Oryntra API responses. Contract changes on the Python side should be checked here before an iOS release.",
    "server/backend/corporate_repository.py": "This point-in-time research repository validates and stores public corporate documents, company facts, and five supported macro series. It owns source-class allowlists, HTTPS provenance, availability timestamps, issuer snapshots, corporate factor panels, and macro panels so historical research cannot use a fact before its recorded public availability.",
    "server/backend/provider_credentials.py": "This module is the encrypted server-side provider-credential store used only by eligible operating modes. It derives per-user encryption, redacts keys from status responses, and explicitly disables HTTP key storage in browser-direct mode, where credentials remain on the client device.",
    "server/backend/quant_system.py": "This module supplies Quant Lab's cross-sleeve mechanics: probability-like regime states, regime-conditioned allocation multipliers, ADV-based square-root execution costs, factor/relative-value attribution, and recent strategy-health decay. These are research diagnostics and cost assumptions rather than live execution or causal risk models.",
}


FILE_NOTES = {
    "README.md": "The repository README is the public orientation document. It explains Oryntra's research scope, the major product surfaces, local setup, and the intended limits of the system. It should stay aligned with the actual release label and should never imply that a research or paper-trading feature executes securities transactions.",
    "LICENSE": "The license establishes the repository's permitted reuse terms. It is a legal artifact rather than an application component, so any change requires a deliberate licensing decision instead of ordinary product editing.",
    "server/.env.example": "This template records the names and broad purpose of runtime configuration without containing live values. It is the safe place to document optional provider, ads, authentication, or operating-mode settings; populated environment files remain private operational state.",
    "server/run.py": "This is the local server launcher. It loads environment configuration when available and starts the ASGI service, making it the narrow handoff from a developer's shell to the FastAPI composition root. A successful process start is only the beginning of validation; the health response and intended client flow still need to be exercised.",
    "server/backend/analysis_access.py": "This module centralizes whether a request may use an analysis capability and whether it consumes a quota. Keeping that decision server-side prevents the browser from becoming the authority for access policy and makes public, signed-in, owner, and private research modes explicit.",
    "server/backend/backtest.py": "This module supplies the lighter historical backtest path used outside the full Quant Lab. It is useful for quick analytical context, but it should not be confused with the broader portfolio simulation, data-fingerprint, or validation diagnostic framework implemented in quant_research.py.",
    "server/backend/documented_beta_counts.py": "This module computes the small public-facing counts displayed in the beta product. It deliberately separates a presentation statistic from private research or operational data, so changes should preserve that distinction and avoid exposing raw histories or account records.",
    "server/backend/fetcher.py": "This provider-facing adapter acquires historical market data for the ordinary analysis pipeline. Its responsibility is to make a usable, normalized history available to the rest of the backend while leaving provenance, cache policy, and provider fallback governed by the repository layer.",
    "server/backend/indicators.py": "This is the deterministic indicator library. It turns supplied OHLCV history into technical observations used by the scanner, setup logic, and reports. The calculations are evidence inputs, not independent trading recommendations, and changes can shift every downstream score or label.",
    "server/backend/lab_grading.py": "This module grades Pattern Lab output against explicit research criteria. It gives the product a structured way to report whether an experiment is sufficiently complete or internally consistent, while stopping short of treating a grade as proof of future profitability.",
    "server/backend/polygon_client.py": "This adapter contains the Polygon-specific request and response handling. It is kept separate from analysis logic so provider quirks, credentials, rate limits, and payload normalization do not leak into indicators or user-facing route code.",
    "server/backend/public_payload.py": "This module is the public-data boundary. It reduces internal analysis objects to the derived information that the public product is allowed to return and strips fields that would otherwise create an uncontrolled raw-market-data surface.",
    "server/backend/research_experiments.py": "This module holds reusable experimental helpers for research comparisons. It belongs to the evidence-producing side of the system and should be read alongside its datasets, assumptions, and tests rather than promoted into the public scanner merely because it produces a favorable result.",
    "server/backend/research_training.py": "This module prepares and evaluates training-oriented research work. It connects stored observations to candidate-model workflows while preserving a distinction between a trained artifact and an approved production decision rule.",
    "server/backend/research_universe.py": "This module defines research symbol universes and related selection controls. Universe design materially affects reported results, so a run must identify whether it used the intended membership and should not imply survivorship-free coverage unless the data actually provides it.",
    "server/backend/patterns/candle_patterns.py": "This detector identifies named candlestick configurations from bar relationships. It produces labels and supporting measurements for the wider pattern report; the labels are deterministic descriptions of the implemented rules rather than guarantees about the next price movement.",
    "server/backend/patterns/chart_patterns.py": "This detector looks for broader price-structure formations such as consolidations, breakouts, and reversals. Its output feeds the pattern engine and should be compared on fixed histories after any threshold or lookback change because detection frequency can move significantly.",
    "server/backend/patterns/fair_value_gaps.py": "This module identifies gap-like price structures using the project's defined bar relationships. The resulting zones are analytical annotations, not executable liquidity levels, and they must remain tied to the timestamp and data source that produced them.",
    "server/backend/patterns/outcome_tracker.py": "This module evaluates what followed a recorded pattern observation. It is central to Pattern Lab's causal framing because outcomes must be assessed after a signal is formed, with stated cost and timing assumptions, rather than inferred from a chart inspected in hindsight.",
    "server/backend/patterns/pattern_engine.py": "This module coordinates the lower-level pattern detectors into a single evidence object. It is the integration point where a consumer receives coherent pattern context instead of having to combine candle, chart, structure, and gap outputs independently.",
    "server/backend/patterns/structure_patterns.py": "This detector describes market-structure events from successive swings and price relationships. Its role is to supply contextual evidence to the pattern engine; it does not establish a trading instruction by itself.",
    "server/backend/patterns/utils.py": "This file contains shared numerical and data-shape helpers used across pattern detectors. It is a high-leverage dependency: a small change can alter multiple pattern families, so fixed-example regression checks are more informative than inspecting one visual chart.",
    "server/backend/routes/ai_explain.py": "This route produces a readable explanation from structured analysis. It keeps language generation downstream of deterministic calculations and provides a controlled fallback path, which makes it possible to compare prose with the numerical evidence that supports it.",
    "server/backend/routes/auth.py": "This route implements account and session flows for the browser client. It validates authentication requests and delegates durable identity work to the database layer; cookie, session, and authorization changes here should be tested as full browser flows rather than only as JSON responses.",
    "server/backend/routes/backtest.py": "This route exposes the quick historical backtest capability. It is a thin HTTP boundary around the backtest helper, so validation should focus on request limits, clean errors, and making its simplified assumptions distinct from a full Quant Lab report.",
    "server/backend/routes/dev_tools.py": "This private route group controls operational and research workflows such as cache warming, model status, VAI training, and Pattern Lab jobs. It is intentionally not a normal public product surface; authorization and long-running-job behavior are more important here than a polished consumer response.",
    "server/backend/routes/intelligence.py": "This route group serves the derived intelligence scan and its quota/status information. It is the controlled bridge between scanner analysis and the public payload policy, including the multiple-symbol path that must remain bounded under concurrent use.",
    "server/backend/routes/paper_trading.py": "This route group records and retrieves simulated trades and paper statistics. It preserves an account-level historical record for educational use but does not obtain market fills, transmit orders, or verify live execution conditions.",
    "server/backend/routes/patterns.py": "This route presents deterministic pattern-analysis results over the API. It should retain a clear separation between descriptive pattern evidence and any later explanation or plan-shaped output shown by the client.",
    "server/backend/routes/pro.py": "This route contains subscription-aware or professional workspace endpoints. It is an access boundary as well as a response layer, so its authorization checks must remain server-enforced even if the client hides or reveals the corresponding screen.",
    "server/backend/routes/pro_live.py": "This route provides the protected live-oriented professional surface. The name does not turn the product into a brokerage or order system; it still needs data-source provenance, access enforcement, and careful product language.",
    "server/backend/routes/watchlist.py": "This route group persists and returns user watchlist selections. It couples account identity with durable state, so tests should cover ownership and empty-state behavior as well as normal create, list, and removal actions.",
    "server/backend/routes/__init__.py": "This package initializer marks the route collection as an importable backend boundary. It carries little standalone behavior but provides the namespace that the main application uses when composing routers.",
    "server/backend/__init__.py": "This package initializer establishes the backend namespace. It is intentionally small; meaningful application behavior lives in the modules that it makes importable.",
    "server/backend/patterns/__init__.py": "This package initializer establishes the deterministic pattern-analysis namespace. It is supporting structure for the detector modules rather than a separate analytical engine.",
    "server/frontend/static/css/main.css": "This stylesheet contains the older or shared browser presentation rules. It needs to coexist predictably with refined.css; when a visual issue appears, determine which layer owns the selector instead of adding an override that obscures the active design system.",
    "server/frontend/static/js/theme-init.js": "This small startup script applies the persisted visual theme before the main client has rendered. Its main purpose is to avoid an unnecessary flash of the wrong appearance and to keep user preference behavior independent of the larger application bundle.",
    "server/maintenance_site/app.py": "This is a deliberately separate, minimal maintenance-mode server. It gives operators a controlled response surface when the primary application should not be presented, and therefore must avoid depending on normal scanner, database, or frontend startup behavior.",
    "server/maintenance_site/index.html": "This is the maintenance-mode landing document. It communicates temporary availability status without relying on the primary web application's selectors, client bundle, or API routes.",
    "server/maintenance_site/static/js/maintenance.js": "This client script supports the small maintenance-mode experience. Its limited scope is intentional: an outage or operator-mode page should have fewer moving parts than the normal workspace.",
    "server/maintenance_site/static/js/theme-init.js": "This is the maintenance site's early theme initializer. It maintains a consistent visual preference without importing the main application's larger stateful browser code.",
    "server/requirements.txt": "This dependency manifest defines the Python packages required to run the backend and its research features. Version changes can affect numerical results, provider behavior, and deployment reproducibility, so dependency updates deserve their own validation rather than being treated as housekeeping.",
    "server/shared/ad_slots.json": "This configuration defines named advertising placement metadata separate from templates and application logic. It allows the server to reason about enabled slots without hard-coding placement decisions throughout the frontend.",
    "server/oryntra.code-workspace": "This editor workspace file groups the server project for local development. It does not affect production behavior, but it can encode useful workspace-level conventions for contributors.",
    "server/tests/conftest.py": "This test support file prepares shared fixtures and import behavior for the backend suite. It is part of the testing environment itself, so a change can affect many tests even when no product module has changed.",
    "server/tests/test_analysis_access_policy.py": "This regression suite verifies analysis-access and quota policy. Its value is that it tests a product boundary the browser must not be able to bypass.",
    "server/tests/test_dev_tools_jobs.py": "This suite exercises the private job-control behavior used by development and research tools. It guards against job-state regressions that can otherwise appear only after a long-running task starts or resumes.",
    "server/tests/test_intelligence_route.py": "This suite verifies the public intelligence route's request and response behavior. It protects the derived scanner contract, including error handling and bounded multi-symbol work.",
    "server/tests/test_market_cache.py": "This suite verifies cache calendar, freshness, and update behavior. It protects reproducibility and provider discipline rather than only testing that a request can fetch data once.",
    "server/tests/test_market_repository.py": "This suite exercises the provenance-carrying market repository. It is the main regression check for provider normalization, cache preference, validation, and the metadata that later research reports rely on.",
    "server/tests/test_pattern_lab_next.py": "This suite checks the newer Pattern Lab evaluation path. It helps ensure that historical outcomes are generated causally and that experiment diagnostics remain present after implementation changes.",
    "server/tests/test_public_payload_boundary.py": "This suite verifies the public-payload reduction policy. It is a security and product test: a successful internal analysis must not automatically mean that every underlying field is public.",
    "server/tests/test_quant_research.py": "This suite checks core Quant Lab mechanics and report outputs. It should be expanded whenever a sleeve, timing rule, cost treatment, or risk diagnostic changes because aggregate performance alone is not a sufficient regression signal.",
    "server/tests/test_research_pipeline.py": "This suite covers the handoff among research preparation, candidate-model work, and the surrounding evaluation pipeline. It protects the evidence workflow from becoming disconnected after a refactor.",
    "server/tests/test_v8_engine.py": "This suite checks the V8 candidate-scoring path separately from the public V7 path. That separation matters because experimental comparisons must not silently change the labeled public engine.",
    "server/tests/test_ads_configuration.py": "This suite proves web advertising is blank and disabled by default and requires explicit enablement before AdSense markup or slots are considered active.",
    "server/tests/test_corporate_quant_system.py": "This suite proves corporate/macro facts obey their availability timestamps and verifies the corporate Quant profile returns structured regime, liquidity, attribution, and health outputs.",
    "server/tests/test_paper_trade_routes.py": "This route-level regression check confirms an authenticated owner can reach the supported paper-trade deletion contract.",
    "server/tests/test_provider_credentials.py": "This suite verifies per-user encrypted provider-key storage, key redaction, and the retirement of server-side HTTP key storage in browser-direct mode.",
    "server/tests/test_public_backtest_route.py": "This suite proves the authenticated browser-upload backtest is mounted, receives normalized bars without provider keys, and does not convert the upload path into a raw-data persistence surface.",
    "server/tests/test_quant_public_route.py": "This suite proves the authenticated Quant Lab upload route remains available when broader administrative Quant routes are not public.",
    "server/tests/test_scanner_result_rendering.py": "This static browser regression check ensures optional scanner-result widgets are guarded so partial API responses do not crash the rendering path.",
    "server/tests/test_vai2_leakage_controls.py": "This suite verifies VAI 2.2 train, validation, and untouched test dates are chronological, non-overlapping, and separated by the required purge gaps.",
    "server/tools/audit_v7_bias.py": "This command-line research tool audits V7 behavior for obvious bias or evaluation issues. It is a diagnostic aid, not a certification process, and its output must be interpreted with the exact universe, dates, and assumptions it used.",
    "server/tools/cache_guard.py": "This operational utility inspects cache size and supports guarded backup or restore behavior. It exists to reduce the risk of silently replacing a useful local market-data history during maintenance.",
    "server/tools/check_cache_status.py": "This small wrapper reports the current cache condition through the shared guard logic. It is intended for an operator who needs a quick evidence check before a data refresh or deployment.",
    "server/tools/generate_master_docs.py": "This generator produces this manual from the checked-out source without reading private environment values, local databases, cached histories, or generated build products. Its responsibility is to keep the file reference synchronized with the repository while explicitly favoring narrative explanation over pasted source text.",
    "server/tools/market_cache_cli.py": "This is the command-line interface for cache inspection, synchronization, and backfill operations. It is an operator tool with durable-data consequences, so it should be pointed only at a known environment and run with a documented provider configuration.",
    "server/tools/run_backtest_cli.py": "This utility runs the lighter backtest workflow from the command line. It is useful for reproducible local checks, provided the input period, symbols, source state, and simplified assumptions are recorded with the result.",
    "server/tools/run_pattern_lab_cli.py": "This utility invokes Pattern Lab from a terminal rather than through the browser. It is useful for repeatable research runs, but long executions and outputs should still be treated as job state rather than transient console text.",
    "server/tools/seed_app_review_account.py": "This operator helper prepares a controlled account state for mobile review or testing. It should be used only in the intended database environment and never be mistaken for a general production-account administration path.",
    "server/tools/train_vai2_cli.py": "This utility starts the VAI2 candidate-model training workflow from the command line. It produces research artifacts whose inputs, metrics, and promotion criteria must be retained with the experiment.",
    "server/tools/vai_full_train_pipeline.py": "This command-line pipeline coordinates the longer end-to-end VAI training sequence. It is an experiment runner, not a live prediction service, and it should preserve outputs and failure evidence for later review.",
    "server/tools/warm_cache_cli.py": "This utility warms historical-data cache coverage before research or scanner work. Its purpose is to make provider use more predictable and to improve reproducibility, not to guarantee that data are complete or vendor-correct.",
    "server/tools/__init__.py": "This package initializer establishes the tools namespace. The individual commands contain the operator behavior; this file merely supports their import structure.",
}


def safe_docstring(node: ast.AST) -> str:
    text = ast.get_docstring(node) or ""
    return " ".join(text.split())[:280]


def format_args(node: ast.arguments) -> str:
    items = [arg.arg for arg in node.posonlyargs + node.args]
    if node.vararg:
        items.append("*" + node.vararg.arg)
    items.extend(arg.arg for arg in node.kwonlyargs)
    if node.kwarg:
        items.append("**" + node.kwarg.arg)
    return ", ".join(items)


def decorator_name(decorator: ast.expr) -> str:
    try:
        return ast.unparse(decorator)
    except Exception:
        return decorator.__class__.__name__


class SymbolVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stack: list[str] = []
        self.symbols: list[dict[str, object]] = []
        self.imports: list[str] = []
        self.routes: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.extend(alias.name for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = "." * node.level + (node.module or "")
        self.imports.append(module or ".")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified = ".".join([*self.stack, node.name])
        self.symbols.append({"kind": "class", "name": qualified, "line": node.lineno, "detail": safe_docstring(node)})
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualified = ".".join([*self.stack, node.name])
        decorators = [decorator_name(item) for item in node.decorator_list]
        signature = f"{qualified}({format_args(node.args)})"
        self.symbols.append({"kind": "async function" if isinstance(node, ast.AsyncFunctionDef) else "function", "name": signature, "line": node.lineno, "detail": safe_docstring(node), "decorators": decorators})
        for decorator in decorators:
            if any(marker in decorator for marker in ("router.get", "router.post", "router.put", "router.patch", "router.delete", "app.get", "app.post")):
                self.routes.append(f"line {node.lineno}: {decorator} -> {qualified}")
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function


def python_entry(path: Path, text: str) -> str:
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        return paragraph(f"Python parsing failed: {error}. This should be investigated before relying on the file.")
    visitor = SymbolVisitor()
    visitor.visit(tree)
    chunks = []
    doc = safe_docstring(tree)
    if doc:
        chunks.append(paragraph("Module statement: " + doc))
    if visitor.imports:
        chunks.append(paragraph("Imports: " + ", ".join(sorted(dict.fromkeys(visitor.imports))[:40]) + (" …" if len(visitor.imports) > 40 else "")))
    if visitor.routes:
        chunks.append("HTTP route decorators:\n" + "\n".join(f"  - {item}" for item in visitor.routes) + "\n\n")
    if visitor.symbols:
        chunks.append("Public code inventory:\n")
        for symbol in visitor.symbols:
            decorators = symbol.get("decorators", [])
            extra = f" [decorators: {', '.join(decorators)}]" if decorators else ""
            detail = f" — {symbol['detail']}" if symbol["detail"] else ""
            chunks.append(f"  - {symbol['kind']} {symbol['name']} (line {symbol['line']}){extra}{detail}\n")
        chunks.append("\n")
    else:
        chunks.append(paragraph("No classes or functions were found at module scope. The file is likely declarative configuration, package initialization, or command glue."))
    return "".join(chunks)


def js_entry(text: str) -> str:
    functions = re.findall(r"(?m)^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)", text)
    const_functions = re.findall(r"(?m)^\s*(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>", text)
    api_calls = sorted(dict.fromkeys(re.findall(r"\bAPI\.([A-Za-z_$][\w$]*)", text)))
    chunks = []
    if functions or const_functions:
        chunks.append("Client function inventory:\n")
        for name, args in [*functions, *const_functions]:
            chunks.append(f"  - {name}({args.strip()})\n")
        chunks.append("\n")
    if api_calls:
        chunks.append(paragraph("Referenced browser API namespaces or methods: " + ", ".join(api_calls) + "."))
    return "".join(chunks) or paragraph("No named JavaScript function declarations were detected by the static inventory.")


def language_entry(text: str, language: str) -> str:
    classes = re.findall(r"(?m)^\s*(?:abstract\s+)?class\s+([A-Za-z_][\w<>]*)", text)
    functions = re.findall(r"(?m)^\s*(?:Future(?:<[^>]+>)?|void|Widget|String|bool|int|double|static\s+\w+)\s+([A-Za-z_][\w]*)\s*\(([^)]*)\)", text)
    imports = re.findall(r"(?m)^\s*import\s+['\"]([^'\"]+)", text)
    chunks = []
    if imports:
        chunks.append(paragraph(f"{language} imports: " + ", ".join(imports[:40]) + (" …" if len(imports) > 40 else "")))
    if classes:
        chunks.append("Class inventory:\n" + "\n".join(f"  - {item}" for item in classes) + "\n\n")
    if functions:
        chunks.append("Function inventory:\n" + "\n".join(f"  - {name}({args.strip()})" for name, args in functions) + "\n\n")
    return "".join(chunks) or paragraph(f"No simple {language} class/function inventory was detected; inspect this configuration or declarative source directly.")


def html_entry(text: str) -> str:
    ids = sorted(dict.fromkeys(re.findall(r'\bid=["\']([^"\']+)', text)))
    forms = re.findall(r"<form\b[^>]*", text, flags=re.I)
    nav_items = re.findall(r"data-tab=[\"']([^\"']+)", text)
    chunks = [paragraph(f"HTML identifiers: {len(ids)}. Forms: {len(forms)}. Tab hooks: {len(nav_items)}.")]
    if ids:
        chunks.append("Representative IDs (first 80):\n" + "\n".join(f"  - {item}" for item in ids[:80]) + "\n\n")
    if nav_items:
        chunks.append(paragraph("Tab identifiers: " + ", ".join(sorted(dict.fromkeys(nav_items))) + "."))
    return "".join(chunks)


def css_entry(text: str) -> str:
    selectors = re.findall(r"(?m)^([^@/][^{]+)\{", text)
    normalized = []
    for selector in selectors:
        value = " ".join(selector.split())
        if value and len(value) < 160:
            normalized.append(value)
    return paragraph(f"Approximate stylesheet selector blocks: {len(normalized)}. The first selectors are: " + "; ".join(normalized[:40]) + ("." if normalized else ""))


def json_entry(text: str) -> str:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return paragraph("JSON parsing failed. This file should be reviewed for syntax or non-JSON templating.")
    if isinstance(value, dict):
        return paragraph("Top-level object keys: " + ", ".join(map(str, value.keys())) + ".")
    if isinstance(value, list):
        return paragraph(f"Top-level array entries: {len(value)}.")
    return paragraph(f"Top-level JSON value type: {type(value).__name__}.")


def text_entry(path: Path, text: str) -> str:
    if path.suffix == ".py":
        return python_entry(path, text)
    if path.suffix == ".js":
        return js_entry(text)
    if path.suffix == ".dart":
        return language_entry(text, "Dart")
    if path.suffix == ".swift":
        return language_entry(text, "Swift")
    if path.suffix == ".html":
        return html_entry(text)
    if path.suffix == ".css":
        return css_entry(text)
    if path.suffix == ".json":
        return json_entry(text)
    preview = " ".join(line.strip() for line in text.splitlines() if line.strip())[:700]
    return paragraph("Content overview: " + (preview if preview else "Empty or whitespace-only file.") )


def short_list(values: list[str], limit: int = 12) -> str:
    visible = values[:limit]
    if not visible:
        return "no named public symbols"
    text = ", ".join(visible)
    return text + (", and additional private helpers" if len(values) > limit else "")


def python_story(path: Path, text: str) -> tuple[list[str], list[str], list[str]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [], [], []
    visitor = SymbolVisitor()
    visitor.visit(tree)
    names = [str(item["name"]).split("(")[0] for item in visitor.symbols]
    imports = sorted(dict.fromkeys(visitor.imports))
    return names, imports, visitor.routes


def change_guidance(category: str, path: Path) -> str:
    if category == "HTTP API route":
        return "Any change here can alter request validation or the response contract. Check the matching browser or mobile caller, then exercise the endpoint with both valid and invalid requests."
    if category == "Backend service or research engine":
        return "Treat this as shared backend behaviour: identify its callers, test a representative success path and failure path, and avoid changing numerical conventions without recording the effect on downstream reports."
    if category == "Deterministic pattern analysis":
        return "Changes here can change detected labels and scores across historical reports. Compare the old and new output on a fixed set of symbols and dates before treating a revised detector as an improvement."
    if category == "Automated regression test":
        return "This file is evidence of a contract, not disposable scaffolding. A failure should first be read as a possible change in product behaviour rather than patched around to make the suite green."
    if category == "Browser presentation or client behaviour":
        return "Validate this change with a clean browser reload, console inspection, keyboard interaction where applicable, and both desktop and narrow viewport checks."
    if category == "Browser application structure":
        return "Preserve the IDs and data attributes consumed by the browser script unless their migration is deliberate and tested; structural markup is part of the client contract."
    if category == "Flutter mobile application":
        return "Validate this change in Flutter as well as against the backend contract. A web success path does not prove the mobile client handles the same data, error state, or platform permission correctly."
    if category == "Native iOS integration or build configuration":
        return "Changes should be checked in the Xcode workspace and with the relevant simulator or device build. Native configuration can fail independently of Dart compilation."
    if category == "Operator or research utility":
        return "Run this only with an explicit target environment and treat its inputs and outputs as operational evidence. Never point a maintenance tool at an unknown database or populated credential directory."
    if category == "Legal and policy surface":
        return "This text is product policy, not ordinary UI copy. Keep it consistent with actual application behaviour and obtain appropriate legal review before presenting it as final public policy."
    return "Review this file in the context of the subsystem named above and confirm that its assumptions remain consistent with the current server configuration."


def declarative_story(path: Path, text: str) -> str:
    if path.suffix in {".yaml", ".yml"}:
        keys = re.findall(r"(?m)^\s{0,4}([A-Za-z][A-Za-z0-9_-]+):", text)
        return f"Its declared configuration keys include {short_list(list(dict.fromkeys(keys)), 18)}. These values define how a build or runtime assembles the surrounding code, so the important review question is whether the declaration still matches the software component that consumes it."
    if path.suffix == ".json":
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return "The file is expected to be JSON but could not be parsed during documentation generation. Its syntax should be corrected before relying on the configured client or build behaviour."
        if isinstance(value, dict):
            return f"Its top-level configuration keys are {short_list([str(key) for key in value], 18)}. These values are declarative inputs for the associated client, asset catalog, or shared configuration surface."
        if isinstance(value, list):
            return f"It contains a top-level list with {len(value)} entries, used as declarative input by the adjacent subsystem rather than as executable application logic."
    if path.suffix == ".plist":
        keys = re.findall(r"<key>([^<]+)</key>", text)
        return f"Its Apple property-list keys include {short_list(keys, 18)}. They control iOS metadata, capabilities, privacy declarations, or build-time framework behaviour; a mismatch can be visible only during archive, review, or device execution."
    if path.suffix in {".xcconfig", ".entitlements"}:
        names = re.findall(r"(?m)^\s*([A-Za-z][A-Za-z0-9_]+)\s*=", text) or re.findall(r"<key>([^<]+)</key>", text)
        return f"Its declared native settings include {short_list(names, 18)}. These settings belong to the Xcode build and entitlement boundary, where a missing or inconsistent value can prevent signing, capabilities, or runtime services from working."
    if path.suffix in {".pbxproj", ".xcscheme", ".xcworkspacedata"}:
        targets = re.findall(r"(?:PRODUCT_BUNDLE_IDENTIFIER|PBXNativeTarget|BlueprintName|location)\s*=\s*([^;]+)", text)
        return f"It is an Xcode workspace/project declaration. The file describes target membership, build settings, or workspace references; representative declared values include {short_list([item.strip().strip(chr(34)) for item in targets], 10)}. Edit this only with the Xcode target structure in mind, because textual changes can break a build without changing application source."
    if path.suffix == ".md":
        headings = re.findall(r"(?m)^#{1,4}\s+(.+)$", text)
        return f"Its substantive sections are {short_list(headings, 14)}. This document is part of the product's operating contract: revise it when the corresponding user-visible or operational behaviour changes, not merely to make it sound more complete."
    if path.suffix in {".sh", ".command", ".rb"}:
        commands = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        return f"Its operational sequence begins with {short_list(commands, 8)}. Shell and project-configuration helpers should be read as procedures with side effects; validate their target paths and environment before executing them on any persistent installation."
    preview = " ".join(line.strip() for line in text.splitlines() if line.strip())[:420]
    return "The file is declarative or supporting material. Its relevant content begins with: " + (preview or "no substantive text.")


def mobile_file_story(path: Path) -> str:
    name = path.stem.replace("_", " ")
    value = rel(path)
    if "/screens/" in value:
        focus = {
            "account_screen": "identity, subscription, analysis-access, provider settings, notification preferences, tracked-symbol alerts, stored Quant Lab reports, policy links, and account deletion",
            "auth_gate_screen": "startup, sign-in/create-account, legal acceptance, and provider-connection gating before the main workspace",
            "paper_screen": "simulated-trade records and paper-performance presentation",
            "quant_lab_screen": "multi-symbol Quant Lab configuration, progress, local report saving, and portfolio diagnostics",
            "scanner_screen": "symbol analysis, scanner results, and their loading/error states",
            "watchlist_screen": "saved-symbol management and its account-scoped empty states",
        }.get(path.stem, "a distinct mobile product workflow")
        return f"This Flutter screen presents {focus}. It should treat server responses as untrusted remote state, preserving loading, empty, error, and signed-out behavior rather than assuming that a successful desktop interaction proves the mobile flow is complete."
    if "/services/" in value:
        focus = {
            "background_task_service": "the Flutter method-channel wrapper that requests and ends iOS background time for Quant Lab",
            "consent_service": "the platform-neutral consent interface",
            "consent_service_mobile": "mobile-platform consent behavior",
            "consent_service_web": "web-platform consent behavior",
            "interstitial_ad_service": "the platform-neutral interstitial-ad interface",
            "interstitial_ad_service_mobile": "mobile interstitial-ad lifecycle behavior",
            "interstitial_ad_service_web": "the no-native-ad web implementation",
            "notification_service": "local notification scheduling and user permission handling",
            "provider_key_store": "encrypted device-local storage for the selected market-data provider and API key",
            "quant_lab_store": "device-local persistence, reopening, and deletion of completed Quant Lab reports",
            "session_store": "secure or persistent mobile session storage",
            "widget_service": "communication between the Flutter client and the iOS widget extension",
        }.get(path.stem, f"the {name} mobile service boundary")
        return f"This service implements {focus}. Keeping this work behind a service boundary lets screens remain focused on presentation and makes platform-specific behavior explicit; any change needs both a Flutter check and the relevant device or browser test."
    if "/widgets/" in value:
        focus = {
            "adaptive_banner": "the platform-neutral banner-ad widget contract",
            "adaptive_banner_mobile": "the mobile banner-ad implementation",
            "adaptive_banner_web": "the web fallback or web-specific banner treatment",
            "common": "shared visual primitives used by more than one screen",
            "glass": "the reusable translucent visual treatment used by the mobile interface",
            "tradingview_chart": "the platform-neutral chart widget contract",
            "tradingview_chart_mobile": "the mobile chart embedding implementation",
            "tradingview_chart_web": "the web chart embedding implementation",
        }.get(path.stem, f"the {name} reusable visual component")
        return f"This Flutter widget provides {focus}. Its success is visual and behavioral: it must preserve layout, accessibility, lifecycle safety, and the correct empty or unavailable state on the platforms where it is used."
    if path.name == "app_config.dart":
        return "This file centralizes Flutter application configuration, including the server endpoint selection used by the mobile client. Keeping that decision in one place prevents individual screens from inventing incompatible URLs or release assumptions."
    specific = {
        "analysis_options.yaml": "This is the Dart analyzer configuration inherited from Flutter's recommended lint set; it defines the static-quality rules applied by `flutter analyze`.",
        "build_ipa.sh": "This release helper cleans the Flutter project, restores packages and CocoaPods, builds the iOS release, and creates the Xcode archive/export path for App Store delivery.",
        "configure_admob.sh": "This helper validates and inserts the configured AdMob application identifier into the iOS Runner property list.",
        "configure_widget_target.rb": "This Ruby/xcodeproj helper creates or repairs the OryntraWidget target, source membership, entitlements, app-group capability, and embedding relationship.",
        "prepare_ios_project.sh": "This helper restores Flutter/CocoaPods dependencies and runs the widget-target configuration before the workspace is archived.",
        "pubspec.yaml": "This Flutter manifest declares the app version, SDK constraints, dependencies, bundled icon, and platform packages used by the client.",
        "pubspec.lock": "This lockfile pins the resolved Dart/Flutter dependency graph for reproducible analysis, tests, and release builds.",
        "flutter_bootstrap.js": "This Flutter web bootstrap loads the compiled application in a browser preview build.",
        "index.html": "This Flutter web host document supplies preview metadata, icons, startup markup, and the bootstrap script.",
        "manifest.json": "This web-app manifest defines the Flutter preview's name, icons, display mode, colors, and install metadata.",
        "oryntra_palette_test.dart": "This Flutter test locks the product palette and semantic light/dark color relationships against accidental visual drift.",
        "quant_lab_store_test.dart": "This Flutter test verifies device-local Quant Lab report ordering, retention, reading, and deletion.",
        "AppFrameworkInfo.plist": "This Flutter framework property list defines bundle and minimum-platform metadata used when embedding the engine in iOS.",
        "Debug.xcconfig": "This debug configuration includes Flutter-generated settings and CocoaPods integration for Runner.",
        "Release.xcconfig": "This release configuration includes Flutter-generated settings and CocoaPods integration for archives.",
        "Flutter.podspec": "This local CocoaPods specification describes the Flutter engine framework dependency.",
        "Info.plist": "This property list declares app or widget identity, capabilities, URL schemes, background modes, permission strings, and extension metadata for its target.",
        "OryntraWidget.entitlements": "This entitlement grants the widget access to the shared Oryntra app group used for latest-scan values.",
        "OryntraWidget.swift": "This WidgetKit source defines the latest-scan timeline, app-group read, card UI, refresh policy, and scanner deep link.",
        "Podfile": "This CocoaPods manifest configures Runner and widget targets, Flutter pods, the iOS platform, and post-install settings.",
        "Podfile.lock": "This lockfile records the exact native iOS pod graph used by the Flutter project.",
        "project.pbxproj": "This is the Xcode project graph for Runner and OryntraWidget targets, files, phases, signing, app groups, versions, and build settings.",
        "contents.xcworkspacedata": "This Xcode workspace declaration links the Runner project and, where applicable, the Pods project.",
        "Runner.xcscheme": "This shared Xcode scheme defines Runner build, test, profile, analyze, archive, and launch actions.",
        "AppDelegate.swift": "This native delegate registers plugins and APNs, implements notification/background/widget channels, schedules reminders, writes latest-scan values, and refreshes WidgetKit.",
        "Contents.json": "This Apple asset-catalog manifest maps adjacent icon or launch-image files to required scales and roles.",
        "LaunchScreen.storyboard": "This storyboard defines the native launch screen shown before Flutter renders.",
        "Main.storyboard": "This storyboard provides the native Flutter host view-controller entry point.",
        "GeneratedPluginRegistrant.h": "This generated header declares Flutter plugin registration for Runner.",
        "GeneratedPluginRegistrant.m": "This generated implementation registers the iOS plugins used by the Flutter application.",
        "PrivacyInfo.xcprivacy": "This Apple privacy manifest declares required-reason API and data-access categories for the bundle.",
        "Runner-Bridging-Header.h": "This bridging header exposes Objective-C plugin registration to Swift Runner code.",
        "Runner.entitlements": "This file grants Runner the shared app group, associated domains, and push environment used by the app.",
    }
    if path.name in specific:
        return specific[path.name]
    return "This mobile-support file contributes to the Flutter or iOS client boundary. Read it with the screen, service, widget, or native target that consumes it, because a local source change can fail later at archive time or on a real device."


def binary_story(path: Path) -> str:
    value = rel(path)
    size = path.stat().st_size
    if value == "brand-assets/oryntra-ai-master-logo.png":
        role = "the master repository and documentation logo"
    elif "AppIcon.appiconset" in value:
        role = "one required iOS application-icon rendition"
    elif "LaunchImage.imageset" in value:
        role = "one native iOS launch-image rendition"
    elif value.startswith("ios-app/assets/"):
        role = "the Flutter application's bundled Oryntra icon"
    elif value.startswith("ios-app/web/"):
        role = "a Flutter web icon or favicon"
    elif value.startswith("server/maintenance_site/"):
        role = "a maintenance-site brand or favicon asset"
    elif value.startswith("server/frontend/"):
        role = "a browser-site brand, profile, icon, or favicon asset"
    else:
        role = "a version-controlled binary asset"
    return f"This {size:,}-byte binary file provides {role}. It has no executable source to inventory, but its exact path and dimensions are part of the relevant HTML, Flutter, iOS asset-catalog, or branding contract."


def support_file_story(path: Path, text: str) -> str:
    value = rel(path)
    if value == ".gitignore":
        return "This ignore policy keeps credentials, databases, model artifacts, market caches, virtual environments, generated Flutter settings, build output, and machine-local state out of version control. It is part of the repository's privacy and deployment-safety boundary."
    if value.startswith("server/frontend/legal/") or value.startswith("server/maintenance_site/legal/"):
        page = path.stem.replace("-", " ")
        return f"This {page} document is a policy-facing page served by the product. Its statements must remain consistent with actual data, account, subscription, advertising, and research behavior; legal wording should not be changed merely as interface copy."
    if value.startswith("server/frontend/") and path.suffix == ".html":
        return "This HTML document provides a browser-facing structural surface. Its markup is intentionally documented at the interaction-contract level rather than reproduced: client identifiers, forms, dialogs, and accessibility labels must remain compatible with the browser application that consumes them."
    if value.startswith("server/maintenance_site/static/css/"):
        return "This stylesheet supports the independent maintenance-mode presentation. It should remain deliberately smaller and more failure-tolerant than the primary workspace, because the point of maintenance mode is to work when ordinary application rendering is unavailable."
    if value.startswith("server/maintenance_site/static/"):
        return "This asset supports the independent maintenance-mode experience. It should not create a dependency on normal scanner routes, account state, or the primary browser bundle."
    if value.startswith("ios-app/ios/"):
        return mobile_file_story(path)
    if value.startswith("ios-app/"):
        return mobile_file_story(path)
    if path.suffix in {".yaml", ".yml", ".json", ".plist", ".xcconfig", ".pbxproj", ".entitlements", ".xcscheme", ".xcworkspacedata", ".md", ".sh", ".command", ".rb"}:
        return declarative_story(path, text)
    return f"This supporting file belongs to the {category_for(path).lower()} layer. Its role is bounded by the adjacent source and configuration files; a change should be evaluated in the workflow that actually consumes it rather than judged from the file in isolation."


def public_surface_sentence(path: Path, text: str) -> str:
    if path.suffix != ".py":
        return ""
    names, _, routes = python_story(path, text)
    if routes:
        endpoints = []
        for route in routes:
            match = re.search(r"(?:router|app)\.(get|post|put|patch|delete)\((['\"])([^'\"]+)\2", route)
            if match:
                endpoints.append(f"{match.group(1).upper()} {match.group(3)}")
        if endpoints:
            return "Its declared HTTP boundary includes " + ", ".join(endpoints[:12]) + (", among other private operations." if len(endpoints) > 12 else ".")
    return ""


def narrative_file_entry(path: Path, text: str | None) -> str:
    key = rel(path)
    category = category_for(path)
    if path == OUTPUT:
        return (
            f"\n[{key}]\n"
            f"Classification: {category}.\n\n"
            + paragraph("This is the generated manual currently being produced. It is the exhaustive tracked-file catalogue and source-derived architectural reference; regenerate it with server/tools/generate_master_docs.py whenever the repository structure or documented product behavior changes.")
        )
    if text is None:
        return (
            f"\n[{key}]\n"
            f"Classification: {category}. Source size: {path.stat().st_size:,} bytes.\n\n"
            + paragraph(binary_story(path))
        )
    lines = line_count(text)
    chunks = [f"\n[{key}]\n", f"Classification: {category}. Source size: {lines} lines.\n\n"]
    role = DEEP_FILE_NOTES.get(key) or FILE_NOTES.get(key) or support_file_story(path, text)
    chunks.append(paragraph(role))
    surface = public_surface_sentence(path, text)
    if surface:
        chunks.append(paragraph(surface))
    if path.suffix in {".py", ".js", ".dart", ".swift", ".html", ".css", ".json"}:
        chunks.append(text_entry(path, text))
    return "".join(chunks)


def environment_inventory(files: Iterable[Path]) -> list[str]:
    names: set[str] = set()
    for path in files:
        if path.suffix not in {".py", ".sh", ".command", ".md", ".txt"}:
            continue
        text = source_text(path)
        if text is None:
            continue
        names.update(re.findall(r"\b(?:ORYNTRA|POLYGON|TWELVEDATA|ADSENSE|WEB_ADS|PORT|PUBLIC_[A-Z_]+)_[A-Z0-9_]+\b", text))
        names.update(re.findall(r"os\.getenv\([\"']([A-Z0-9_]+)", text))
    return sorted(name for name in names if "KEY" not in name or name in {"POLYGON_API_KEY", "TWELVEDATA_API_KEY"})


def main() -> None:
    files = source_files()
    counts = Counter(category_for(path) for path in files)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    output = []
    output.append("ORYNTRA AI — MASTER TECHNICAL MANUAL\n")
    output.append("NARRATIVE ARCHITECTURE, OPERATIONS, AND FILE REFERENCE\n")
    output.append("Generated: " + now + "\n")
    output.append("Git base revision at generation: " + git_value("rev-parse", "HEAD") + "\n")
    output.append("Git branch: " + git_value("branch", "--show-current") + "\n")
    output.append("\n")
    output.append(paragraph("This manual is written for a reader who wants to understand how the checked-out Oryntra AI system behaves, how its pieces relate, and where a change can create risk. It is not a source-code mirror. It documents every Git-tracked file plus explicitly reviewed new sources listed by the generator, parses readable source and configuration, and catalogues binary assets while excluding private environment files, local databases, cached market data, virtual environments, untracked build products, and credentials. A documented file proves only what this checkout contains; it does not by itself prove a live server, provider account, database, or App Store release is current and healthy."))
    output.append(paragraph("The document distinguishes released product labels from historical internal names and experimental candidates. It also distinguishes code capability from operating-mode availability: a route or screen in the repository may be private, feature-flagged, preview-only, or dependent on provider rights and device permissions. Those distinctions are part of the feature, not footnotes."))
    output.append("\nHIGH-LEVEL FLOW\n\n")
    output.append("  Data providers / local cache\n")
    output.append("              |\n")
    output.append("              v\n")
    output.append("  Market repository -> validation + provenance -> deterministic analysis\n")
    output.append("              |                              |\n")
    output.append("              v                              v\n")
    output.append("  Private Quant Lab                    Derived scanner output\n")
    output.append("              |                              |\n")
    output.append("              v                              v\n")
    output.append("  risk/return diagnostics             browser workspace + explanations\n\n")
    for title, paragraphs in SYSTEM_CHAPTERS:
        output.append(section(title))
        for item in paragraphs:
            output.append(paragraph(item))
    output.append(section("8. REPOSITORY COVERAGE AND READING MAP"))
    output.append(f"Included files: {len(files)}\n")
    for category, count in sorted(counts.items()):
        output.append(f"  - {category}: {count}\n")
    output.append("\n")
    output.append(paragraph("The catalogue that follows is intentionally file-by-file, but it is not a reproduction of the code. Each entry identifies the file's real role in the system, explains the boundaries it owns, and notes the operational or product implication that matters when it changes. The descriptions are densest for modules that determine data provenance, analytical logic, access boundaries, durable state, or client contracts; small configuration and platform files are described by the particular build or runtime contract they carry."))
    output.append("INCLUDED FILES\n\n")
    for path in files:
        text = source_text(path)
        size = f"{line_count(text)} lines" if text is not None and path != OUTPUT else ("generated output" if path == OUTPUT else f"{path.stat().st_size:,} bytes")
        output.append(f"  - {rel(path)} ({size}; {category_for(path)})\n")
    output.append(section("9. ENVIRONMENT VARIABLE REFERENCE"))
    output.append(paragraph("The following names were found in safe source/configuration files. Values are intentionally omitted. Keep populated environment files and service credentials out of source control and out of public documentation."))
    for name in environment_inventory(files):
        output.append(f"  - {name}\n")
    output.append("\n")
    output.append(section("10. FILE-BY-FILE NARRATIVE REFERENCE"))
    for path in files:
        text = source_text(path)
        output.append(narrative_file_entry(path, text))
    output.append(section("11. TEST, RELEASE, AND REVIEW DISCIPLINE"))
    output.append(paragraph("Run tests from `server/` so imports resolve from the application root: `PYTHONPATH=. .venv/bin/python3 -m unittest discover -s tests -v`. For a focused Quant Lab check, run `PYTHONPATH=. .venv/bin/python3 -m unittest tests.test_quant_research`. Validate the browser client with `node --check frontend/static/js/app.js`. A release candidate should also be started locally and checked through the actual `/health` endpoint and intended access modes."))
    output.append(paragraph("The manual should be regenerated when source architecture changes, and the generated commit identifier should be recorded with any environment-specific verification. Do not treat this document as legal, investment, tax, security, or market-data licensing advice. For a production release, pair this manual with live verification of health, the configured route boundary, provider behavior, database preservation, and the actual client flows that users will encounter."))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("".join(output), encoding="utf-8")
    print(f"Wrote {OUTPUT}")
    print(f"Documented {len(files)} files")


if __name__ == "__main__":
    main()
