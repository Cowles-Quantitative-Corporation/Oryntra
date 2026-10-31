"""Public client boundaries use invented demo data and no advertising loader."""
from pathlib import Path
from backend import main
from backend.routes import demo


def test_retired_ad_settings_cannot_inject_an_ad_loader(monkeypatch):
    monkeypatch.setenv("WEB_ADS_ENABLED", "true")
    monkeypatch.setenv("ADSENSE_VERIFY_ENABLED", "true")
    source = (Path(main.FRONTEND_DIR)/"index.html").read_text()
    html = main.render_frontend_html(source).lower()
    assert "adsbygoogle" not in html
    assert "googlesyndication.com" not in html
    assert "data-ad-slot" not in html


def test_demo_bars_are_repeatable_invented_illustrations():
    a = demo._build_bars("AAPL")
    b = demo._build_bars("AAPL")
    assert a == b
    assert a and all(row["low"] <= min(row["open"], row["close"]) <= row["high"] for row in a)
    assert a[-1]["timestamp"] == demo._DEMO_END_DATE.isoformat()
