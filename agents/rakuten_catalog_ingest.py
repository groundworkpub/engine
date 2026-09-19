#!/usr/bin/env python3
"""
agents/rakuten_catalog_ingest.py — Groundwork Rakuten Merchant Catalog Ingestion Engine

Fetches the complete directory of Rakuten Advertising advertisers via the official REST API,
parses merchant IDs and names, maps them into Groundwork pillars (Tech, Home, Money, Body, Life),
and flags high-priority Tier-1 targets for the batch application engine.
"""

import json
import logging
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Any
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.rakuten_client import RakutenClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("rakuten_catalog")

PILLAR_KEYWORDS = {
    "tech": [
        "tech", "software", "computer", "electronics", "vpn", "security", "cloud", "hosting",
        "laptop", "hardware", "phone", "wireless", "mobile", "lenovo", "dell", "hp", "samsung",
        "newegg", "cyber", "norton", "mcafee", "apple", "audio", "camera", "gadget", "pc",
        "antivirus", "data", "backup", "server", "semiconductor", "intel", "amd"
    ],
    "home": [
        "solar", "energy", "battery", "power", "hvac", "tool", "lawn", "garden", "hardware",
        "appliance", "home", "kitchen", "furniture", "mattress", "security", "outdoor",
        "electric", "eco", "light", "renovation", "lowes", "walmart", "target", "ace", "generator"
    ],
    "money": [
        "bank", "credit", "loan", "finance", "invest", "tax", "insurance", "debt", "mortgage",
        "wealth", "trading", "crypto", "turbotax", "experian", "quicken", "lloyds", "capital",
        "savings", "funding", "borrow", "card", "advisor", "pay", "money"
    ],
    "body": [
        "health", "fitness", "supplement", "wellness", "medical", "vitamin", "nutrition",
        "gym", "workout", "skin", "care", "pharma", "clinical", "longevity", "metabolic", "protein"
    ],
    "life": [
        "travel", "hotel", "flight", "auto", "car", "career", "legal", "tour", "cruise",
        "luggage", "rental", "booking", "airline", "vacation", "trip"
    ]
}

TIER1_GIANTS = {
    "tech": ["lenovo", "dell", "newegg", "hp", "samsung", "norton", "mcafee", "cyberghost", "logitech", "bose"],
    "home": ["walmart", "target", "lowes", "ace hardware", "northern tool", "homedepot", "goal zero"],
    "money": ["turbotax", "h&r block", "experian", "quicken", "lloyds bank", "western union"]
}


def classify_merchant(name: str) -> Dict[str, Any]:
    name_lower = name.lower()
    scores = {}
    matched_words = {}

    for pillar, keywords in PILLAR_KEYWORDS.items():
        matches = [kw for kw in keywords if re.search(r'\b' + re.escape(kw) + r'\b', name_lower) or kw in name_lower]
        if matches:
            scores[pillar] = len(matches)
            matched_words[pillar] = matches

    assigned_pillar = max(scores, key=scores.get) if scores else "lifestyle_retail"

    is_tier1 = False
    for p, brands in TIER1_GIANTS.items():
        if any(b in name_lower for b in brands):
            is_tier1 = True
            assigned_pillar = p
            break

    return {
        "pillar": assigned_pillar,
        "is_tier1": is_tier1,
        "matched_keywords": matched_words.get(assigned_pillar, [])
    }


def ingest_catalog() -> List[Dict[str, Any]]:
    client = RakutenClient()
    token = client.get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/xml",
        "User-Agent": "GroundworkResearch/1.0 (+https://gworky.com)"
    }

    url = "https://api.linksynergy.com/advertisersearch/1.0"
    logger.info("Fetching complete Rakuten advertiser directory from %s...", url)
    resp = httpx.get(url, headers=headers, timeout=20.0)

    if resp.status_code != 200:
        logger.error("Failed to query advertisersearch API: %d - %s", resp.status_code, resp.text[:200])
        raise RuntimeError(f"API Error {resp.status_code}")

    root = ET.fromstring(resp.text)
    merchants_xml = root.findall(".//merchant")
    logger.info("Found %d raw merchant records in XML response.", len(merchants_xml))

    catalog = []
    pillar_counts = {"tech": 0, "home": 0, "money": 0, "body": 0, "life": 0, "lifestyle_retail": 0}
    tier1_count = 0

    for m in merchants_xml:
        mid = m.find("mid").text.strip() if m.find("mid") is not None and m.find("mid").text else ""
        name = m.find("merchantname").text.strip() if m.find("merchantname") is not None and m.find("merchantname").text else ""
        if not mid or not name:
            continue

        classification = classify_merchant(name)
        pillar = classification["pillar"]
        pillar_counts[pillar] = pillar_counts.get(pillar, 0) + 1

        if classification["is_tier1"]:
            tier1_count += 1

        catalog.append({
            "mid": mid,
            "name": name,
            "pillar": pillar,
            "is_tier1": classification["is_tier1"],
            "matched_keywords": classification["matched_keywords"]
        })

    scratch_dir = Path("scratch")
    scratch_dir.mkdir(exist_ok=True)
    out_file = scratch_dir / "rakuten_merchants_catalog.json"
    out_file.write_text(json.dumps(catalog, indent=2), encoding="utf-8")

    logger.info("==================================================")
    logger.info("Catalog Ingestion & Pillar Classification Summary:")
    logger.info("  Total Processed: %d merchants", len(catalog))
    logger.info("  Tier-1 Flagged:   %d priority brands", tier1_count)
    for p, count in pillar_counts.items():
        logger.info("  Pillar [%s]: %d merchants", p.upper(), count)
    logger.info("Catalog saved to: %s", out_file)

    return catalog


if __name__ == "__main__":
    ingest_catalog()
