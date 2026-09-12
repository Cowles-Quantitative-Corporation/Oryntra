#!/usr/bin/env python3
"""Build a dated taxonomy snapshot from provider data or FinanceDatabase.

FinanceDatabase mode creates a useful, clearly labelled automated seed. It is
not point-in-time historical classification data and cannot validate alpha.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
from backend.universal_taxonomy import FamilyExposure, load_family_catalog, taxonomy_contract, validate_exposures


REQUIRED_UNIVERSE = {"security_id", "symbol", "market_cap", "as_of_date", "exchange", "country", "asset_type"}
REQUIRED_MEMBERSHIP = {"security_id", "symbol", "family_id", "membership_weight", "downside_sensitivity", "confidence", "source", "as_of_date"}
SIZE_ORDER = {"Mega Cap": 0, "Large Cap": 1, "Mid Cap": 2, "Small Cap": 3, "Micro Cap": 4, "Nano Cap": 5}
EMERGING = {"Argentina", "Brazil", "Chile", "China", "Colombia", "Egypt", "Greece", "Hungary", "India", "Indonesia", "Korea, Republic Of", "Malaysia", "Mexico", "Peru", "Philippines", "Poland", "Qatar", "Saudi Arabia", "South Africa", "Taiwan", "Thailand", "Turkey", "United Arab Emirates"}
SECTORS = {"information technology": "information_technology", "communication services": "communication_services", "consumer discretionary": "consumer_discretionary", "consumer staples": "consumer_staples", "financials": "financials", "financial services": "financials", "health care": "health_care", "industrials": "industrials", "materials": "materials", "energy": "energy", "real estate": "real_estate", "utilities": "utilities"}


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def require_columns(items: list[dict[str, str]], required: set[str], label: str) -> None:
    columns = set(items[0]) if items else set()
    missing = required - columns
    if missing:
        raise ValueError(f"{label} CSV is missing: {', '.join(sorted(missing))}")


def _identity(row: dict[str, str]) -> str:
    return row.get("composite_figi") or row.get("shareclass_figi") or row.get("isin") or f"{row.get('mic', '')}:{row['symbol']}"


def _seed_key(row: dict[str, str]) -> tuple:
    return (SIZE_ORDER.get(row.get("market_cap", ""), 99), row.get("country", ""), row.get("exchange", ""), row.get("name", ""), row.get("symbol", ""))


def _region(country: str) -> str:
    if country.strip().lower() in {"united states", "usa", "us"}:
        return "us_equity"
    if country.strip() in EMERGING:
        return "emerging_equity"
    return "developed_ex_us" if country.strip() else "global_other_equity"


def _industry(group: str, industry: str) -> str | None:
    text = f"{group} {industry}".lower()
    checks = (("semiconductors", ("semiconductor",)), ("software", ("software",)), ("it_services_hardware", ("technology hardware", "it services", "electronic equipment")), ("interactive_media", ("interactive media",)), ("media_telecom", ("media", "telecom", "entertainment")), ("autos", ("automobile", "auto components")), ("retail_travel", ("retail", "hotel", "restaurant", "leisure", "travel")), ("food_beverage_household", ("food", "beverage", "household")), ("banks", ("bank",)), ("insurance", ("insurance",)), ("capital_markets", ("capital markets", "diversified financial")), ("biopharma", ("biotechnology", "pharmaceutical")), ("health_equipment_services", ("health care equipment", "health care providers")), ("aerospace_defense", ("aerospace", "defense")), ("transportation", ("transportation", "air freight", "airline", "logistics")), ("capital_goods", ("machinery", "electrical equipment", "building products")), ("metals_mining", ("metals", "mining")), ("chemicals", ("chemical",)), ("oil_gas", ("oil", "gas")), ("reits", ("reit",)), ("electric_utilities", ("electric utilities",)))
    return next((family for family, terms in checks if any(term in text for term in terms)), None)


def _revision(root: Path) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _auto_edges(item: dict[str, str], row: dict[str, str], as_of: str, revision: str) -> list[FamilyExposure]:
    source = "FinanceDatabase automated taxonomy seed"
    url = f"https://github.com/JerBouma/FinanceDatabase/tree/{revision}/database/equities"
    def edge(family: str, weight: float, sensitivity: float, confidence: float) -> FamilyExposure:
        return FamilyExposure(item["security_id"], item["symbol"], family, weight, sensitivity, confidence, source, as_of, url, as_of)
    sector = SECTORS.get((row.get("sector") or "").strip().lower(), "unclassified_sector")
    result = [edge(_region(row.get("country", "")), 1, 1, .75), edge(sector, 1, 1, .70)]
    industry = _industry(row.get("industry_group", ""), row.get("industry", ""))
    if industry:
        result.append(edge(industry, 1, 1.05, .65))
    text = " ".join(row.get(key, "") for key in ("name", "summary", "industry_group", "industry")).lower()
    for family, terms, sensitivity in (("ai_compute", ("artificial intelligence", "data center", "semiconductor", "gpu"), .75), ("cloud_enterprise", ("cloud", "saas", "enterprise software"), .70), ("cybersecurity", ("cybersecurity", "cyber security"), .75), ("digital_payments", ("digital payment", "payment processing", "fintech"), .65), ("defense_security", ("defense", "aerospace"), .70), ("oil_linked", ("oil", "petroleum"), .75), ("natural_gas_linked", ("natural gas",), .75), ("industrial_metals_linked", ("copper", "aluminum", "steel", "iron ore"), .70), ("precious_metals_linked", ("gold", "silver", "precious metal"), .70)):
        if any(term in text for term in terms):
            result.append(edge(family, .60, sensitivity, .40))
    if sector == "information_technology":
        result.append(edge("long_duration_equity", .75, .70, .45))
    elif sector == "financials":
        result.append(edge("rate_sensitive_financials", .80, .80, .50))
    elif sector in {"consumer_discretionary", "industrials", "materials", "energy"}:
        result.append(edge("credit_sensitive", .60, .70, .45))
    return result


def _finance_database_seed(root: Path, limit: int):
    equity_dir = root / "database" / "equities"
    if not equity_dir.is_dir():
        raise ValueError("FinanceDatabase checkout needs database/equities/")
    raw = [row for path in sorted(equity_dir.glob("*.csv")) for row in rows(path)]
    live = [row for row in raw if row.get("symbol") and row.get("name") and row.get("delisted", "").strip().lower() != "true"]
    dedup = {}
    for row in live:
        key = _identity(row)
        if key not in dedup or _seed_key(row) < _seed_key(dedup[key]):
            dedup[key] = row
    chosen = sorted(dedup.values(), key=_seed_key)[:limit]
    if len(chosen) < limit:
        raise ValueError(f"FinanceDatabase yielded only {len(chosen)} active deduplicated equities")
    as_of, revision = datetime.now(timezone.utc).date().isoformat(), _revision(root)
    universe, exposures = [], []
    for source in chosen:
        item = {"security_id": _identity(source), "symbol": source["symbol"], "name": source["name"], "market_cap": source.get("market_cap") or "Unknown tier", "as_of_date": as_of, "exchange": source.get("exchange") or "", "country": source.get("country") or "", "asset_type": "equity"}
        universe.append(item)
        exposures.extend(_auto_edges(item, source, as_of, revision))
    metadata = {"mode": "automated_financedatabase_seed", "source": "FinanceDatabase", "source_url": "https://github.com/JerBouma/FinanceDatabase", "source_revision": revision, "license": "MIT; source attribution retained", "classification_method": "provider sector/industry mapping plus low-confidence text themes", "research_status": "seed_only_not_point_in_time_backtest_data", "universe_definition": f"{limit} active, deduplicated FinanceDatabase equities ordered by categorical market-cap tier then stable metadata; not an exact market-cap ranking"}
    return universe, exposures, metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--universe-csv", type=Path, help="Provider universe CSV; also requires --memberships-csv.")
    source.add_argument("--finance-database-root", type=Path, help="Checkout of the public FinanceDatabase repository.")
    parser.add_argument("--memberships-csv", type=Path, help="Provider memberships CSV.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=25_000)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite an existing snapshot")
    if not (1 <= args.limit <= 25_000):
        raise ValueError("limit must be between 1 and 25,000")
    if args.finance_database_root:
        ordered, exposures, metadata = _finance_database_seed(args.finance_database_root, args.limit)
    else:
        if not args.memberships_csv:
            raise ValueError("--memberships-csv is required with --universe-csv")
        universe, membership_rows = rows(args.universe_csv), rows(args.memberships_csv)
        require_columns(universe, REQUIRED_UNIVERSE, "Universe")
        require_columns(membership_rows, REQUIRED_MEMBERSHIP, "Membership")
        eligible = [row for row in universe if row["asset_type"].strip().lower() in {"common_stock", "common equity", "equity"}]
        ordered = sorted(eligible, key=lambda row: float(row["market_cap"]), reverse=True)[:args.limit]
        if len(ordered) < args.limit:
            raise ValueError(f"Only {len(ordered)} eligible common equities; cannot build requested {args.limit}-security snapshot")
        as_of_dates = {row["as_of_date"] for row in ordered}
        if len(as_of_dates) != 1:
            raise ValueError("Universe must contain one shared as_of_date")
        selected = {row["security_id"] for row in ordered}
        exposures = [_exposure_from_row(row) for row in membership_rows if row["security_id"] in selected]
        metadata = {"mode": "provider_membership_import", "universe_definition": f"Top {args.limit} eligible common equities by provider market_cap"}
    validate_exposures(exposures, load_family_catalog())
    selected = {row["security_id"] for row in ordered}
    covered = {item.security_id for item in exposures}
    if len(covered) != len(selected):
        raise ValueError(f"Membership data covers {len(covered)} of {len(selected)} selected securities; require full coverage")
    payload = {"schema_version": "universal-taxonomy-snapshot-v1", "generated_at": datetime.now(timezone.utc).isoformat(),
               "as_of_date": ordered[0]["as_of_date"], "universe_definition": metadata.pop("universe_definition"), "source_metadata": metadata, "universe": ordered,
               "exposures": [asdict(item) for item in exposures], "coverage": {"securities": len(selected), "exposures": len(exposures), "families": len({item.family_id for item in exposures})},
               "contract": taxonomy_contract()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {args.output} with {len(selected)} securities and {len(exposures)} memberships")
    return 0


def _exposure_from_row(row: dict[str, str]) -> FamilyExposure:
    """Convert a CSV row explicitly; never let provider strings bypass validation."""
    values = {key: value for key, value in row.items() if key in FamilyExposure.__dataclass_fields__}
    for key in ("membership_weight", "downside_sensitivity", "confidence"):
        try:
            values[key] = float(values[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Membership CSV has an invalid {key}") from exc
    return FamilyExposure(**values)


if __name__ == "__main__":
    raise SystemExit(main())
