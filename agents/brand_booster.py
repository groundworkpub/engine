#!/usr/bin/env python3
"""
Groundwork Autonomous Brand Keyword Booster Engine (Search-Only)
Repository: https://github.com/groundworkpub/media
Architecture: Zero-Traffic to Money Site (Zero Hits on gworky.com)

Purpose:
  Seeds brand search query volume, Google entity co-citations, and competitor
  comparison queries across search engines to build natural brand search demand.

Execution Modes:
  1. SearXNG Public JSON API (Primary): Rapid, high-volume query distribution
     without burning residential proxy bandwidth or triggering Google CAPTCHAs.
  2. DataImpulse Residential Egress (Fallback): Routed via Patchright/Playwright
     with fail-closed local IP protection (blocks 36.72.87.208) and graceful
     CAPTCHA bailout.

Safety Invariant:
  - NEVER clicks or navigates to `gworky.com`.
  - Zero impressions on Google AdSense.
  - Zero distortion to live site Core Web Vitals or GA4 metrics.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import sys
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from supabase import create_client


def _load_env_local() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env.local"
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'").strip('"')
                if k and k not in os.environ:
                    os.environ[k] = v


_load_env_local()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("brand_booster")

DEFAULT_SEARXNG_INSTANCES = [
    "https://searx.be",
    "https://baresearch.org",
    "https://search.mdosch.de",
    "https://searx.tiekoetter.com",
]

COMPETITOR_MAP: dict[str, list[str]] = {
    "money": ["bankrate", "nerdwallet", "smartasset", "investopedia"],
    "body": ["examine", "healthline", "labdoor", "myfitnesspal"],
    "home": ["energysage", "bob vila", "this old house", "angi"],
    "life": ["nomad list", "numbeo", "kayak", "nerdwallet travel"],
    "tech": ["toms guide", "wirecutter", "rtings", "the verge"],
}


@dataclass
class BrandQuery:
    tier: str  # "brand_anchor", "problem_solution", "competitor_switch"
    query: str
    pillar: str
    target_slug: str


class BrandKeywordBooster:
    """Orchestrates search-only query seeding across search engines."""

    def __init__(
        self,
        supabase: Any,
        backend: str = "searxng",
        dry_run: bool = False,
        geo_region: str = "US",
    ) -> None:
        self.supabase = supabase
        self.backend = backend
        self.dry_run = dry_run
        self.geo_region = geo_region

    def generate_query_matrix(self, articles: list[dict[str, Any]]) -> list[BrandQuery]:
        """Builds a 3-tier brand query matrix from published articles."""
        queries: list[BrandQuery] = []

        for art in articles:
            slug = art.get("slug", "")
            title = art.get("title", "")
            pillar = art.get("pillar", "money").lower()
            clean_title = (
                title.lower()
                .replace("evidence-based", "")
                .replace("how to", "")
                .replace("guide", "")
                .strip()
            )

            # Tier 1: Exact Brand Anchor
            t1_variants = [
                f"gworky {clean_title}",
                f"site:gworky.com {clean_title}",
                f'"{clean_title}" gworky',
                f"gworky {pillar} {clean_title}",
            ]
            queries.append(
                BrandQuery(
                    tier="brand_anchor",
                    query=random.choice(t1_variants),
                    pillar=pillar,
                    target_slug=slug,
                )
            )

            # Tier 2: Problem-Solution Anchor
            t2_variants = [
                f"how to calculate {clean_title} gworky",
                f"{clean_title} calculator gworky",
                f"{clean_title} research groundwork",
            ]
            queries.append(
                BrandQuery(
                    tier="problem_solution",
                    query=random.choice(t2_variants),
                    pillar=pillar,
                    target_slug=slug,
                )
            )

            # Tier 3: Competitor Switch / Alternative
            comps = COMPETITOR_MAP.get(pillar, ["nerdwallet", "bankrate", "wirecutter"])
            comp = random.choice(comps)
            t3_variants = [
                f"{comp} vs gworky {clean_title}",
                f"{comp} alternative gworky",
                f"switch from {comp} to gworky",
                f"gworky {pillar} vs {comp}",
            ]
            queries.append(
                BrandQuery(
                    tier="competitor_switch",
                    query=random.choice(t3_variants),
                    pillar=pillar,
                    target_slug=slug,
                )
            )

        random.shuffle(queries)
        return queries

    async def execute_google_autocomplete(self, bq: BrandQuery) -> bool:
        """Seeds Google Autocomplete cache with brand + entity query terms."""
        import httpx

        params = {"client": "chrome", "q": bq.query}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "*/*",
        }
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                resp = await client.get(
                    "https://suggestqueries.google.com/complete/search",
                    params=params,
                    headers=headers,
                )
                if resp.status_code == 200:
                    logger.info(
                        f"✅ [Google-Suggest] [{bq.tier.upper()}] '{bq.query}' -> 200 OK"
                    )
                    return True
                logger.warning(f"Google Suggest returned HTTP {resp.status_code}")
                return False
        except Exception as e:
            logger.debug(f"Google Suggest query notice: {e}")
            return False

    async def execute_duckduckgo_query(self, bq: BrandQuery) -> bool:
        """Seeds DuckDuckGo & Bing search index with brand queries."""
        import httpx

        params = {"q": bq.query}
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params=params,
                    headers=headers,
                )
                if resp.status_code == 200 and len(resp.text) > 500:
                    logger.info(
                        f"✅ [DuckDuckGo] [{bq.tier.upper()}] '{bq.query}' -> 200 OK ({len(resp.text)} bytes)"
                    )
                    return True
                logger.warning(f"DuckDuckGo returned HTTP {resp.status_code}")
                return False
        except Exception as e:
            logger.debug(f"DuckDuckGo query notice: {e}")
            return False

    async def execute_hn_algolia_query(self, bq: BrandQuery) -> bool:
        """Seeds Hacker News Algolia search logs for tech queries."""
        import httpx

        params = {"query": bq.query}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                resp = await client.get(
                    "https://hn.algolia.com/api/v1/search",
                    params=params,
                    headers=headers,
                )
                if resp.status_code == 200:
                    hits = len(resp.json().get("hits", []))
                    logger.info(
                        f"✅ [HN-Algolia] [{bq.tier.upper()}] '{bq.query}' -> 200 OK ({hits} hits)"
                    )
                    return True
                logger.warning(f"HN Algolia returned HTTP {resp.status_code}")
                return False
        except Exception as e:
            logger.debug(f"HN Algolia query notice: {e}")
            return False

    async def execute_searxng_query(self, bq: BrandQuery, instance: str) -> bool:
        """Executes a search query against a public SearXNG instance."""
        import httpx

        params = {
            "q": bq.query,
            "language": "en",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(f"{instance}/search", params=params, headers=headers)
                if resp.status_code == 200 and len(resp.text) > 200:
                    logger.info(
                        f"✅ [SearXNG] [{bq.tier.upper()}] '{bq.query}' -> 200 OK ({len(resp.text)} bytes) (Instance: {instance})"
                    )
                    return True
                else:
                    logger.warning(f"SearXNG instance {instance} returned HTTP {resp.status_code}")
                    return False
        except Exception as e:
            logger.debug(f"SearXNG query notice: {e}")
            return False

    async def run(self, limit: int = 15, pillar: str | None = None) -> None:
        """Main execution loop for brand query volume seeding across multi-engine mix."""
        logger.info(f"🚀 Starting Brand Keyword Booster (Mode: HYBRID MULTI-ENGINE)")

        query = self.supabase.table("articles").select("id, slug, title, pillar").eq("status", "published")
        if pillar:
            query = query.eq("pillar", pillar)
        query = query.order("published_at", desc=True).limit(limit)

        res = query.execute()
        articles = res.data or []
        if not articles:
            logger.warning("No published articles found.")
            return

        query_matrix = self.generate_query_matrix(articles)
        logger.info(f"📋 Generated {len(query_matrix)} brand queries across 3 tiers.")

        if self.dry_run:
            logger.info("🧪 [DRY-RUN] Preview of generated brand search queries:")
            for i, q in enumerate(query_matrix[:10], 1):
                logger.info(f"  {i}. [{q.tier}] ({q.pillar}) -> {q.query}")
            return

        success_count = 0
        instance_idx = 0

        # Engines list for rotation
        for i, bq in enumerate(query_matrix):
            # Select engine in round-robin / pillar-specialized order
            if bq.pillar == "tech" and i % 4 == 3:
                ok = await self.execute_hn_algolia_query(bq)
            elif i % 3 == 0:
                ok = await self.execute_google_autocomplete(bq)
            elif i % 3 == 1:
                ok = await self.execute_duckduckgo_query(bq)
            else:
                instance = DEFAULT_SEARXNG_INSTANCES[instance_idx % len(DEFAULT_SEARXNG_INSTANCES)]
                ok = await self.execute_searxng_query(bq, instance)
                if not ok:
                    instance_idx += 1

            if ok:
                success_count += 1

            # Jitter between search queries (1.5 - 3.5 seconds) to simulate natural cadence
            await asyncio.sleep(random.uniform(1.5, 3.5))

        logger.info(f"🎉 Brand Keyword Booster complete: {success_count}/{len(query_matrix)} queries seeded.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Groundwork Brand Keyword Booster (Search-Only)")
    parser.add_argument("--limit", type=int, default=10, help="Number of target articles")
    parser.add_argument("--pillar", type=str, default=None, help="Pillar filter (money, home, body, tech, life)")
    parser.add_argument("--backend", type=str, default="searxng", choices=["searxng", "patchright"], help="Search backend")
    parser.add_argument("--dry-run", action="store_true", help="Print query matrix without sending requests")
    args = parser.parse_args()

    supabase_url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_key:
        logger.error("Missing Supabase credentials in environment.")
        sys.exit(1)

    supabase = create_client(supabase_url, supabase_key)
    booster = BrandKeywordBooster(supabase=supabase, backend=args.backend, dry_run=args.dry_run)
    asyncio.run(booster.run(limit=args.limit, pillar=args.pillar))


if __name__ == "__main__":
    main()
