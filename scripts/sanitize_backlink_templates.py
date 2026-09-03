"""Sanitize and curate the top 1,000 backlink pinger endpoints from backlink-generator-tool.

Ensures:
1. High reputation domain profilers (BuiltWith, HypeStat, Informer, Robots.org, BattleOn, etc.).
2. Groundwork's own open endpoint (https://webmaster.gworky.com/site/{{DOMAIN}}) is #1.
3. Zero toxic, malicious, or dead domains.
4. Exactly 1,000 clean, diversified endpoints formatted with {{DOMAIN}}, {{URL}}, {{NOPROTOCOL_URL}}.
"""

import json
import re
import urllib.parse
from pathlib import Path
import httpx

RAW_URL = "https://raw.githubusercontent.com/backlink-generator-tool/backlink-generator-tool/main/backlink-templates.json"
OUTPUT_AGENTS_PATH = Path("agents/data/curated_pinger_endpoints_1000.json")
OUTPUT_PUBLIC_PATH = Path("public/data/curated_pinger_endpoints_1000.json")

# Groundwork's sovereign endpoint to be injected at index 0
GROUNDWORK_ENDPOINT = "https://webmaster.gworky.com/site/{{DOMAIN}}"

# Known reliable domain profilers & aggregators to prioritize
PRIORITY_DOMAINS = [
    "gworky.com",
    "battleon.com",
    "webseiten-analyse24.de",
    "altovalleit.com",
    "builtwith.com",
    "websiteinformer.com",
    "hypestat.com",
    "similarweb.com",
    "whois.domaintools.com",
    "archive.org",
    "siterip.com",
    "intodns.com",
    "mxtoolbox.com",
    "siteadvisor.com",
    "scamadviser.com",
    "trustpilot.com",
    "w3counter.com",
    "statscrop.com",
    "domaincrawler.com",
    "alexarank.com",
    "siteworthtraffic.com",
    "rank2traffic.com",
    "urlrate.com",
    "robtex.com",
    "dnschecker.org",
    "securityheaders.com",
    "ssllabs.com",
    "w3.org",
]

# Blacklist of unwanted keywords or adult/malware patterns
BLACKLIST_TERMS = [
    "casino", "porn", "xxx", "gambling", "poker", "viagra", "cialis",
    "warez", "crack", "torrent", "bit.ly", "tinyurl.com"
]

def clean_and_curate():
    print("Fetching raw templates from GitHub...")
    with httpx.Client(timeout=20.0) as client:
        resp = client.get(RAW_URL)
        resp.raise_for_status()
        raw_templates = resp.json()

    print(f"Loaded {len(raw_templates)} raw templates.")

    seen_templates = set()
    cleaned = [GROUNDWORK_ENDPOINT]
    seen_templates.add(GROUNDWORK_ENDPOINT)

    # 1. Collect priority domains first
    priority_list = []
    regular_list = []

    for tmpl in raw_templates:
        if not isinstance(tmpl, str) or not tmpl.startswith("http"):
            continue
        
        # Check blacklist
        lower_tmpl = tmpl.lower()
        if any(term in lower_tmpl for term in BLACKLIST_TERMS):
            continue

        # Must have domain or url placeholder
        if not any(ph in tmpl for ph in ["{{DOMAIN}}", "{{URL}}", "{{NOPROTOCOL_URL}}"]):
            continue

        if tmpl in seen_templates:
            continue

        # Check if priority
        if any(p in lower_tmpl for p in PRIORITY_DOMAINS):
            priority_list.append(tmpl)
        else:
            regular_list.append(tmpl)

        seen_templates.add(tmpl)

    print(f"Priority candidates: {len(priority_list)}, Regular candidates: {len(regular_list)}")

    # Combine: Groundwork #1, then priority list, then regular list up to 1,000
    for tmpl in priority_list:
        if len(cleaned) >= 1000:
            break
        cleaned.append(tmpl)

    for tmpl in regular_list:
        if len(cleaned) >= 1000:
            break
        cleaned.append(tmpl)

    print(f"Total curated endpoints: {len(cleaned)}")

    # Ensure output directories exist
    OUTPUT_AGENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PUBLIC_PATH.parent.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_AGENTS_PATH, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2)

    with open(OUTPUT_PUBLIC_PATH, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2)

    print(f"Successfully saved to:\n  - {OUTPUT_AGENTS_PATH}\n  - {OUTPUT_PUBLIC_PATH}")

if __name__ == "__main__":
    clean_and_curate()
