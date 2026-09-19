#!/usr/bin/env python3
"""
agents/awin_feed_ingest.py — Groundwork Awin Darwin Product Feed Ingestion & Caching Engine

Downloads official product data feeds for all active/joined merchants via Awin Darwin Publisher API:
- Endpoint: https://ui.awin.com/productdata-darwin-download/publisher/{PUBLISHER_ID}/{API_KEY}/1/feedList
- Filters for active merchants
- Streams and parses gzipped CSV product feeds
- Normalizes schema into Groundwork standard product entities
- Saves curated, lightweight JSON to public/data/feeds/curated_products.json (< 2MB, Zero-Cost)
"""

import os
import io
import csv
import gzip
import json
import logging
import urllib.request
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env.local")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("awin_feed_ingest")

PUBLISHER_ID = os.getenv("AWIN_PUBLISHER_ID", "3081079").replace('"', '').strip()
API_KEY = os.getenv("AWIN_API_KEY", "").replace('"', '').strip()

if not API_KEY:
    raise ValueError("Missing AWIN_API_KEY in .env.local")

FEED_LIST_URL = f"https://ui.awin.com/productdata-darwin-download/publisher/{PUBLISHER_ID}/{API_KEY}/1/feedList"

OUTPUT_DIR = Path("public/data/feeds")
OUTPUT_FILE = OUTPUT_DIR / "curated_products.json"
SUMMARY_FILE = Path("scratch/awin_feed_summary.json")

# Map known active Awin MIDs to Groundwork pillars & primary categories
MID_PILLAR_MAP = {
    "29299": {"pillar": "body", "category": "Biomarker Panels & Clinical Health"},
    "99013": {"pillar": "tech", "category": "Ergonomic Workspace & Hardware"},
    "120101": {"pillar": "life", "category": "Professional Career Diplomas & CPD"},
    "89509": {"pillar": "life", "category": "Outdoor Performance & Marine Gear"},
    "121298": {"pillar": "home", "category": "Structural Waterproofing & Defense"},
    "130331": {"pillar": "home", "category": "Architectural Art & Interior Decor"},
    "95201": {"pillar": "home", "category": "Living Essentials & Keepsakes"},
    "102013": {"pillar": "body", "category": "Personal Grooming & Aesthetics"},
}

# Max curated items per merchant to keep JSON file lightweight (< 2MB)
MAX_ITEMS_PER_MERCHANT = 25


def fetch_feed_list() -> List[Dict[str, str]]:
    """Fetch and parse Awin Darwin feedList."""
    logger.info("Fetching Awin Darwin feedList from %s...", FEED_LIST_URL)
    req = urllib.request.Request(FEED_LIST_URL, headers={"User-Agent": "GroundworkResearch/1.0 (+https://gworky.com)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        content = resp.read().decode("utf-8", errors="ignore")

    reader = csv.DictReader(io.StringIO(content))
    active_feeds = []
    for row in reader:
        if row.get("Membership Status", "").strip().lower() == "active":
            active_feeds.append(row)

    logger.info("Discovered %d active feeds for Publisher %s.", len(active_feeds), PUBLISHER_ID)
    return active_feeds


def parse_price(val: Any) -> float:
    """Safely parse numeric currency value."""
    if not val:
        return 0.0
    try:
        clean = str(val).replace("$", "").replace("£", "").replace("€", "").replace(",", "").strip()
        return round(float(clean), 2)
    except Exception:
        return 0.0


def download_and_parse_feed(feed_info: Dict[str, str]) -> List[Dict[str, Any]]:
    """Downloads a gzipped CSV feed and extracts normalized product records."""
    mid = str(feed_info.get("Advertiser ID", "")).strip()
    merchant_name = feed_info.get("Advertiser Name", "").strip()
    feed_url = feed_info.get("URL", "").strip()
    meta = MID_PILLAR_MAP.get(mid, {"pillar": "life", "category": "Lifestyle Retail"})

    logger.info("Downloading feed for MID %s (%s) from %s...", mid, merchant_name, feed_url[:80])
    
    req = urllib.request.Request(feed_url, headers={"User-Agent": "GroundworkResearch/1.0 (+https://gworky.com)"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            compressed_data = resp.read()
    except Exception as e:
        logger.error("Failed to download feed for %s: %s", merchant_name, e)
        return []

    try:
        decompressed = gzip.decompress(compressed_data).decode("utf-8", errors="ignore")
    except Exception:
        decompressed = compressed_data.decode("utf-8", errors="ignore")

    reader = csv.DictReader(io.StringIO(decompressed))
    products = []
    
    for row in reader:
        # Resolve column names (Google format vs standard Awin format)
        title = (
            row.get("product_name")
            or row.get("title")
            or row.get("name")
            or ""
        ).strip()
        
        if not title:
            continue

        raw_link = (
            row.get("aw_deep_link")
            or row.get("link")
            or row.get("merchant_deep_link")
            or ""
        ).strip()
        
        if not raw_link:
            continue

        image_url = (
            row.get("aw_image_url")
            or row.get("image_link")
            or row.get("merchant_image_url")
            or row.get("large_image")
            or ""
        ).strip()

        price = parse_price(row.get("search_price") or row.get("price") or row.get("store_price"))
        reg_price = parse_price(row.get("rrp_price") or row.get("base_price") or row.get("product_price_old"))
        
        # Calculate savings if applicable
        savings_pct = 0.0
        if reg_price > price > 0:
            savings_pct = round(((reg_price - price) / reg_price) * 100, 1)

        pid = (
            row.get("aw_product_id")
            or row.get("id")
            or row.get("merchant_product_id")
            or f"{mid}_{len(products)}"
        ).strip()

        currency = (row.get("currency") or "USD").upper()
        if not currency or len(currency) > 3:
            currency = "USD"

        description = (
            row.get("description")
            or row.get("product_short_description")
            or ""
        ).strip()[:350]

        brand = (row.get("brand_name") or row.get("brand") or merchant_name).strip()
        category = (row.get("category_name") or row.get("merchant_category") or meta["category"]).strip()
        in_stock = (row.get("in_stock", "").lower() in ("1", "true", "yes", "in stock", "in_stock", ""))

        norm_product = {
            "id": f"awin_{mid}_{pid}",
            "merchantId": int(mid),
            "merchantName": merchant_name,
            "pillar": meta["pillar"],
            "title": title,
            "brand": brand,
            "description": description,
            "price": price,
            "regularPrice": reg_price if reg_price > price else None,
            "savingsPercent": savings_pct if savings_pct > 0 else None,
            "currency": currency,
            "inStock": in_stock,
            "trackingUrl": raw_link,
            "imageUrl": image_url,
            "category": category,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        products.append(norm_product)

        if len(products) >= MAX_ITEMS_PER_MERCHANT:
            break

    logger.info("✓ Extracted %d curated products for %s.", len(products), merchant_name)
    return products


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    Path("scratch").mkdir(exist_ok=True)

    active_feeds = fetch_feed_list()
    all_curated_products: List[Dict[str, Any]] = []
    seen_mids = set()

    for feed in active_feeds:
        mid = feed.get("Advertiser ID", "")
        # Avoid downloading duplicate feeds for same merchant
        if mid in seen_mids and mid not in ("29299", "89509"):
            continue
        seen_mids.add(mid)

        try:
            prods = download_and_parse_feed(feed)
            all_curated_products.extend(prods)
        except Exception as e:
            logger.error("Error processing feed %s: %s", feed.get("Feed ID"), e)

    # Save to public/data/feeds/curated_products.json
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_curated_products, f, indent=2, ensure_ascii=False)

    size_kb = round(os.path.getsize(OUTPUT_FILE) / 1024, 1)
    logger.info("✓ Successfully saved %d products to %s (%s KB)", len(all_curated_products), OUTPUT_FILE, size_kb)

    # Generate breakdown summary
    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "totalProducts": len(all_curated_products),
        "fileSizeBytes": os.path.getsize(OUTPUT_FILE),
        "merchantsCount": len(seen_mids),
        "byPillar": {},
    }
    for p in all_curated_products:
        pillar = p["pillar"]
        summary["byPillar"][pillar] = summary["byPillar"].get(pillar, 0) + 1

    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    logger.info("Summary report generated: %s", summary)


if __name__ == "__main__":
    main()
