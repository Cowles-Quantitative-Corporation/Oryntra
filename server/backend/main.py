import sqlite3
import json
import os
import re
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from .database import init_db, get_app_counter
from .legal_operator import render_legal_template
from .market_cache import start_market_cache_worker, status as market_cache_status
from .phase4_scheduler import start_phase4_scheduler
from .routes import analysis, watchlist, paper_trading, ai_explain, backtest, patterns, auth, dev_tools, pro, intelligence, quant, portfolio_lab, debug_access, cqc_entitlements, control_plane
from .routes import universal

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
LEGAL_DIR = os.path.join(FRONTEND_DIR, "legal")

APP_VERSION = "1.2.0"
PUBLIC_ENGINE = "v8"
PUBLIC_ENGINE_LABEL = "V8 evidence engine"

NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

# These headers are deliberately baseline protections that do not require an
# application-specific CSP. A CSP needs a separate review because the browser
# client embeds third-party charts.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
}

def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

def public_site_url() -> str:
    return os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")


def frontend_asset_version(filename: str) -> str:
    """Return a deterministic cache-buster for a locally served frontend asset.

    The public site deliberately uses no-cache response headers.  This extra
    version marker prevents a browser or intermediary that retained an older
    HTML document from pairing it with an obsolete JavaScript client.
    """
    path = os.path.join(FRONTEND_DIR, "static", filename)
    try:
        return str(os.stat(path).st_mtime_ns)
    except OSError:
        return APP_VERSION


def render_frontend_html(source: str) -> str:
    """Render public HTML and pin its application bundle to this deployment."""
    html = render_legal_template(source)
    asset_version = frontend_asset_version(os.path.join("js", "app.js"))
    return re.sub(
        r"(/static/js/app\.js)(?:\?v=[^\"']*)?",
        lambda match: f"{match.group(1)}?v={asset_version}",
        html,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    print("✅ Oryntra DB initialized")
    market_worker = start_market_cache_worker() if env_bool("ORYNTRA_PRIVATE_RESEARCH_ROUTES", False) else None
    phase4_scheduler = start_phase4_scheduler() if env_bool("ORYNTRA_PRIVATE_RESEARCH_ROUTES", False) else None
    try:
        yield
    finally:
        if phase4_scheduler is not None:
            phase4_scheduler.stop()
        if market_worker is not None:
            market_worker.stop()
        print("🔴 Oryntra shutting down")


_private_research = env_bool("ORYNTRA_PRIVATE_RESEARCH_ROUTES", False)
_public_scanner_website = env_bool(
    "ORYNTRA_PUBLIC_SCANNER_WEBSITE",
    _private_research,
)
_public_quant_lab = env_bool("ORYNTRA_PUBLIC_QUANT_LAB_ENABLED", False)

app = FastAPI(
    title="Oryntra AI API",
    description="Oryntra derived market-intelligence API with a strict raw-data boundary",
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if _private_research else None,
    redoc_url="/redoc" if _private_research else None,
    openapi_url="/openapi.json" if _private_research else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("ORYNTRA_CORS_ORIGINS", "*").split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Oryntra-Session"],
)

app.add_middleware(
    GZipMiddleware,
    minimum_size=1000,
    compresslevel=5,
)


@app.middleware("http")
async def oryntra_release_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Oryntra-Version"] = APP_VERSION
    response.headers["X-Oryntra-Public-Engine"] = PUBLIC_ENGINE
    response.headers.update(SECURITY_HEADERS)
    if (
        request.url.path == "/"
        or request.url.path.startswith("/static/")
        or request.url.path.startswith("/legal/")
        or request.url.path.startswith("/api/")
    ):
        response.headers.update(NO_CACHE_HEADERS)
    return response

app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(intelligence.router, prefix="/api/intelligence", tags=["Market Intelligence"])
app.include_router(watchlist.router, prefix="/api/watchlist", tags=["Watchlist"])
app.include_router(paper_trading.router, prefix="/api/paper", tags=["Paper Trading"])
app.include_router(ai_explain.router, prefix="/api/ai", tags=["AI Explanation"])
app.include_router(portfolio_lab.router, prefix="/api/portfolio-lab", tags=["Portfolio Lab"])
app.include_router(debug_access.router, prefix="/api/internal/debug", tags=["Internal Debug"])
app.include_router(cqc_entitlements.router, prefix="/api/cqc", tags=["CQC Entitlements"])


if _private_research:
    app.include_router(analysis.router, prefix="/api/analysis", tags=["Private Analysis"])
    app.include_router(backtest.router, prefix="/api/backtest", tags=["Private Backtesting"])
    app.include_router(patterns.router, prefix="/api/patterns", tags=["Private Patterns"])
    app.include_router(dev_tools.router, prefix="/api/dev", tags=["Private Developer Tools"])
    app.include_router(pro.router, prefix="/api/pro", tags=["Private Oryntra Pro"])
else:
    # Signed-in public users may submit browser-fetched daily bars for one
    # research backtest. Provider keys never enter this service.
    app.include_router(backtest.public_router, prefix="/api/backtest", tags=["Browser Backtesting"])

if _private_research:
    app.include_router(universal.router, prefix="/api/universal", tags=["Universal Research"])
    app.include_router(quant.router, prefix="/api/quant", tags=["Quant Lab"])
    app.include_router(control_plane.router, prefix="/api/control", tags=["CQC Private Control"])

# The public API exposes only frozen historical demonstrations. Full custom
# Quant Lab controls are always an internal, server-authorized capability.
app.include_router(quant.public_router, prefix="/api/quant", tags=["Quant Lab"])

app.mount(
    "/static",
    StaticFiles(directory=os.path.join(FRONTEND_DIR, "static")),
    name="static",
)
if _private_research:
    app.mount(
        "/control-static",
        StaticFiles(directory=os.path.join(FRONTEND_DIR, "control", "static")),
        name="control-static",
    )


@app.get("/control", include_in_schema=False)
async def serve_control_plane():
    if not _private_research:
        raise HTTPException(status_code=404, detail="Not found")
    path = os.path.join(FRONTEND_DIR, "control", "index.html")
    return FileResponse(path, headers=NO_CACHE_HEADERS)


@app.get("/", include_in_schema=False)
async def serve_frontend():
    if not _public_scanner_website:
        return JSONResponse(
            {
                "service": "oryntra-ai-api",
                "status": "online",
                "version": APP_VERSION,
                "market_data": "server-side analysis; public raw market data disabled",
                "public_scanner_website": "offline",
            },
            headers=NO_CACHE_HEADERS,
        )
    path = os.path.join(FRONTEND_DIR, "index.html")
    with open(path, "r", encoding="utf-8") as handle:
        html = render_frontend_html(handle.read())
    return HTMLResponse(html, headers=NO_CACHE_HEADERS)


@app.get("/legal/{page_name}", include_in_schema=False)
async def serve_legal_page(page_name: str):
    legal_pages = {
        "terms": "terms_canonical.html",
        "privacy": "privacy_canonical.html",
        "refund": "refund_canonical.html",
        "risk-disclaimer": "risk-disclaimer_canonical.html",
        "contact": "contact_canonical.html",
        "methodology": "methodology_canonical.html",
    }
    filename = legal_pages.get(page_name)
    if not filename:
        raise HTTPException(status_code=404, detail="Legal page not found")
    path = os.path.join(LEGAL_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Legal page not found")
    with open(path, "r", encoding="utf-8") as handle:
        html = render_legal_template(handle.read())
    return HTMLResponse(html, headers=NO_CACHE_HEADERS)


@app.get("/health")
async def health_check():
    return {"status": "ok", "version": APP_VERSION, "public_engine": PUBLIC_ENGINE, "public_engine_label": PUBLIC_ENGINE_LABEL}


@app.get("/api/app/version")
async def app_version():
    turnstile_site_key = os.getenv("ORYNTRA_TURNSTILE_SITE_KEY", "").strip()
    turnstile_enabled = bool(turnstile_site_key and os.getenv("ORYNTRA_TURNSTILE_SECRET_KEY", "").strip())
    return {
        "version": APP_VERSION,
        "public_engine": PUBLIC_ENGINE,
        "public_engine_label": PUBLIC_ENGINE_LABEL,
        "public_scanner_website": _public_scanner_website,
        "private_research_routes": _private_research,
        "quant_lab_upload_enabled": True,
        "market_data_provider": "server_side_configured_provider",
        "public_raw_market_data": False,
        "chart_provider": "TradingView",
        "partner_ads": {
            "enabled": env_bool("ORYNTRA_PARTNER_ADS_ENABLED", False),
            "placements": {
                "native": env_bool("ORYNTRA_PARTNER_AD_NATIVE_ENABLED", True),
                "desktop": env_bool("ORYNTRA_PARTNER_AD_DESKTOP_ENABLED", True),
                "mobile": env_bool("ORYNTRA_PARTNER_AD_MOBILE_ENABLED", True),
            },
        },
        "subscription_offers_enabled": env_bool("ORYNTRA_SUBSCRIPTION_OFFERS_ENABLED", True),
        "turnstile": {
            "enabled": turnstile_enabled,
            "site_key": turnstile_site_key if turnstile_enabled else "",
        },
    }


@app.get("/api/app/stats")
async def app_public_stats():
    return {"total_stock_searches": get_app_counter("stock_searches")}


@app.get("/api/app/market-cache")
async def app_market_cache_status():
    if not env_bool("ORYNTRA_PRIVATE_RESEARCH_ROUTES", False):
        raise HTTPException(status_code=404, detail="Not found")
    return market_cache_status()

@app.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    site = public_site_url()
    lines = ["User-agent: *", "Allow: /"]
    if site:
        lines.append(f"Sitemap: {site}/sitemap.xml")
    return PlainTextResponse("\n".join(lines) + "\n")

@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml():
    site = public_site_url()
    if not site:
        raise HTTPException(status_code=404, detail="Public site URL is not configured")
    paths = ["/", "/legal/terms", "/legal/privacy", "/legal/risk-disclaimer", "/legal/methodology", "/legal/refund", "/legal/contact"]
    urls = "".join(f"<url><loc>{escape(site + path)}</loc></url>" for path in paths)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return Response(content=xml, media_type="application/xml")
