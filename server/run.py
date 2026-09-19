import os
import sys

import uvicorn

try:
    from dotenv import load_dotenv

    load_dotenv(os.getenv("ORYNTRA_ENV_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")))
except ImportError:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8001"))
    polygon_ready = bool(os.getenv("POLYGON_API_KEY", "").strip())
    private = env_bool("ORYNTRA_PRIVATE_RESEARCH_ROUTES", False)
    public_ui = env_bool("ORYNTRA_PUBLIC_SCANNER_WEBSITE", private)
    license_mode = os.getenv("ORYNTRA_MARKET_DATA_LICENSE_MODE", "personal_research")
    public_analysis = env_bool("ORYNTRA_PUBLIC_DERIVED_ANALYSIS_ENABLED", False)
    browser_direct = env_bool("ORYNTRA_BROWSER_DIRECT_ANALYSIS_ENABLED", False)
    access_label = "browser-direct enabled" if browser_direct else ("enabled" if public_analysis else "owner/private only")
    print(
        f"""
╔══════════════════════════════════════════════════╗
║            O R Y N T R A  A I  V1.0             ║
║           Market Intelligence API                ║
╠══════════════════════════════════════════════════╣
║  API        →  http://localhost:{port:<5}              ║
║  Health     →  http://localhost:{port}/health       ║
║  Polygon    →  {'configured' if polygon_ready else 'API key required':<27}║
║  License    →  {license_mode:<27}║
║  Public AI  →  {access_label:<27}║
║  Public UI  →  {'enabled' if public_ui else 'disabled':<27}║
╚══════════════════════════════════════════════════╝
"""
    )
    uvicorn.run(
        "backend.main:app",
        host=os.getenv("ORYNTRA_BIND_HOST", "0.0.0.0"),
        port=port,
        reload=env_bool("ORYNTRA_RELOAD", False),
        reload_dirs=["backend"] if env_bool("ORYNTRA_RELOAD", False) else None,
        log_level=os.getenv("ORYNTRA_LOG_LEVEL", "info"),
    )
