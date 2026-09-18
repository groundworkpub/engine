"""Groundwork Curated Aggregator & Web Profiler Submitter.

Automates clean, high-reputation domain profiling and search aggregator indexing
(Website Informer, BuiltWith, SimilarWeb, SEOBegin, TradeDoubler, HostStats)
inspired by backlink-generator-tool, with strict spam filtering and rate-limiting.

Usage:
    python agents/aggregator_indexer.py --target gworky.com --limit 20
    python agents/aggregator_indexer.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

load_dotenv(REPO_ROOT / ".env.local")
load_dotenv(".env.local")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("aggregator_indexer")

# Curated High-Reputation Aggregators & Profilers (Filtered for Zero Toxic Spam)
CURATED_AGGREGATOR_PATTERNS = [
    {"name": "Website Informer", "pattern": "http://website.informer.com/{domain}/", "dr": 88},
    {"name": "SEOBegin", "pattern": "https://www.seobegin.com/domain/{domain}", "dr": 65},
    {"name": "SpyWords", "pattern": "https://spywords.ru/sword.php?sword={domain}", "dr": 74},
    {"name": "TradeDoubler Redirect", "pattern": "https://redirects.tradedoubler.com/utm/td_redirect.php?url=https%3A%2F%2F{domain}", "dr": 89},
    {"name": "SiteIndices", "pattern": "https://{domain}.siteindices.com/", "dr": 62},
    {"name": "HypeStat", "pattern": "https://hypestat.com/info/{domain}", "dr": 78},
    {"name": "HostSearch", "pattern": "https://www.hostsearch.com/hostsearch/{domain}", "dr": 67},
    {"name": "Statsaholic", "pattern": "http://www.statsaholic.com/{domain}/", "dr": 58},
    {"name": "Robots.org Profiler", "pattern": "https://robots.org/audit/{domain}", "dr": 64},
    {"name": "BuiltWith Lookup", "pattern": "https://builtwith.com/{domain}", "dr": 91},
]


def run_aggregator_ping(domain: str = "gworky.com", limit: int = 10, dry_run: bool = False, tier2_url: str | None = None) -> list[dict[str, Any]]:
    """Submits domain to high-authority aggregator/profiler engines to trigger live bot scraping.
    P0 fix (blueprint §3.2): tier2_url must be Tier 2 URL (telegra.ph/huggingface), never gworky.com directly.
    """
    # Tier 2 shield: if tier2_url provided, use sanitized 1.5k templates with full URL
    if tier2_url and tier2_url.startswith("http"):
        # Use sanitized templates for Tier 2 blast
        try:
            with open(REPO_ROOT / "agents" / "data" / "backlink_templates_1500.json") as f:
                templates = json.load(f)
            targets = [{"name": f"Tier2-{i}", "pattern": t, "dr": 50} for i, t in enumerate(templates[:limit])]
            clean_domain = tier2_url  # for logging
            # For Tier 2, pattern may contain {{URL}} or {{DOMAIN}} — replace accordingly
            use_tier2 = True
        except Exception:
            targets = CURATED_AGGREGATOR_PATTERNS[:limit]
            clean_domain = tier2_url
            use_tier2 = False
    else:
        if domain == "gworky.com" and not dry_run:
            logger.warning("Direct Tier 1 blast to gworky.com blocked — use tier2_url for Tier 2 shield")
            return []
        clean_domain = domain.replace("https://", "").replace("http://", "").strip("/")
        targets = CURATED_AGGREGATOR_PATTERNS[:limit]
        use_tier2 = False
    logger.info(f"Pinging {len(targets)} curated aggregators for [{clean_domain}]...")

    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    with httpx.Client(timeout=15.0, headers=headers, follow_redirects=True) as client:
        for t in targets:
            pat = t["pattern"]
            # Tier 2: replace full URL placeholders if present, else domain
            if use_tier2 and tier2_url:
                url = pat.replace("{{URL}}", tier2_url).replace("{{NOPROTOCOL_URL}}", tier2_url.replace("https://","").replace("http://","")).replace("{{DOMAIN}}", tier2_url).replace("${{NOPROTOCOL_URL}}", tier2_url.replace("https://","").replace("http://",""))
                # Fallback to domain format if no URL placeholder
                if "{{" not in pat and "${{" not in pat:
                    url = pat.format(domain=clean_domain)
                elif url == pat:  # no replacement happened, try domain
                    try:
                        url = pat.format(domain=clean_domain)
                    except Exception:
                        url = pat
            else:
                url = pat.format(domain=clean_domain)
            name = t["name"]
            dr = t["dr"]

            if dry_run:
                logger.info(f"[DRY-RUN] Would ping: {url} ({name})")
                results.append({"name": name, "url": url, "dr": dr, "status": "dry_run"})
                continue

            try:
                resp = client.get(url)
                logger.info(f"Pinged [{name}] (DR {dr}) -> {url[:60]} (Status: {resp.status_code})")
                results.append({
                    "name": name,
                    "url": url,
                    "dr": dr,
                    "status_code": resp.status_code,
                    "status": "success",
                    "timestamp": datetime.now(UTC).isoformat(),
                })
            except Exception as exc:
                logger.warning(f"Failed ping for [{name}]: {exc}")
                results.append({"name": name, "url": url, "dr": dr, "status": "failed", "error": str(exc)[:100]})

            time.sleep(1.0)  # Gentle spacing to avoid WAF trip

    out_file = REPO_ROOT / "agents" / "output" / "aggregator_ping_report.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps({"total": len(results), "results": results}, indent=2), encoding="utf-8")
    logger.info(f"Saved report to {out_file}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Curated Web Profiler & Aggregator Submitter")
    parser.add_argument("--target", default="gworky.com", help="Domain to submit (Tier 1, blocked without tier2_url)")
    parser.add_argument("--tier2-url", default=None, help="Tier 2 URL (telegra.ph/huggingface) — shield Tier 1")
    parser.add_argument("--limit", type=int, default=10, help="Number of aggregators to ping")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without making network requests")
    args = parser.parse_args()

    results = run_aggregator_ping(domain=args.target, limit=args.limit, dry_run=args.dry_run, tier2_url=args.tier2_url)

    print("\n" + "=" * 60)
    print(f"📡 CURATED AGGREGATOR PING REPORT [{'SIMULATION' if args.dry_run else 'LIVE'}]")
    print("=" * 60)
    for r in results:
        print(f"🌐 Aggregator: {r['name']} (DR {r.get('dr', 'N/A')})")
        print(f"🔗 URL: {r['url']}")
        print(f"📌 Status: {r['status']} ({r.get('status_code', 'N/A')})")
        print("-" * 60)


if __name__ == "__main__":
    main()
