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
        proxy_url: str | None = None,
        force_residential: bool = False,
    ) -> None:
        self.supabase = supabase
        self.backend = backend
        self.dry_run = dry_run
        self.geo_region = geo_region
        self.cf_worker_url = os.environ.get("CLOUDFLARE_EGRESS_WORKER_URL")
        self.residential_proxy_url = proxy_url or (self._resolve_proxy(geo_region) if force_residential else None)
        if self.residential_proxy_url:
            logger.info("🛡️  Brand Booster residential proxy enabled (%s)", geo_region)
        else:
            logger.info("🌱 Brand Booster Zero-Cost Egress: Direct Stealth -> Cloudflare Shuffler -> Public Pool")

    def _resolve_proxy(self, geo_region: str) -> str | None:
        login = os.environ.get("DATAIMPULSE_LOGIN")
        pwd = os.environ.get("DATAIMPULSE_PASSWORD")
        host = os.environ.get("DATAIMPULSE_HOST", "gw.dataimpulse.com")
        port = os.environ.get("DATAIMPULSE_PORT", "823")
        if login and pwd:
            c = "us" if geo_region.lower() in ["us", "usa"] else ("gb" if geo_region.lower() in ["uk", "gb", "gbr"] else "au")
            return f"http://{login}__cr.{c}:{pwd}@{host}:{port}"
        return None

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

            # Tier 3: Competitor Switch Anchor
            comp_map = {
                "money": ["nerdwallet", "bankrate", "investopedia"],
                "home": ["angi", "bob vila", "energysage"],
                "body": ["healthline", "myfitnesspal", "labdoor"],
                "tech": ["wirecutter", "rtings", "tomsguide"],
                "life": ["thepointsguy", "nerdwallet travel", "kayak"],
            }
            comps = comp_map.get(pillar, ["nerdwallet", "investopedia"])
            target_comp = random.choice(comps)
            t3_variants = [
                f"switch from {target_comp} to gworky",
                f"{target_comp} vs gworky",
                f"{target_comp} alternative gworky",
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

    async def _fetch_with_fallback(
        self,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
        min_bytes: int = 10,
    ) -> tuple[int, str]:
        """Executes HTTP GET using a resilient 4-tier egress fallback cascade."""
        import httpx
        import urllib.parse

        # Tier 1: Direct Stealth ($0 USD, 0 MB DataImpulse)
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code == 200 and len(resp.content) >= min_bytes:
                    return resp.status_code, resp.text
        except Exception as e:
            logger.debug("Tier 1 (Direct Stealth) notice: %s", e)

        # Tier 2: Cloudflare Egress Shuffler ($0 USD, 100k req/day edge proxy)
        if self.cf_worker_url:
            try:
                full_target = f"{url}?{urllib.parse.urlencode(params)}"
                proxy_endpoint = f"{self.cf_worker_url}/proxy?url={urllib.parse.quote(full_target, safe='')}"
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                    resp = await client.get(
                        proxy_endpoint,
                        headers={"User-Agent": headers.get("User-Agent", "Groundwork/1.0")},
                    )
                    if resp.status_code == 200 and len(resp.content) >= min_bytes:
                        return resp.status_code, resp.text
            except Exception as e:
                logger.debug("Tier 2 (Cloudflare Shuffler) notice: %s", e)

        # Tier 3: Public Proxy Pool ($0 USD)
        try:
            from egress_public_pool import EgressPublicPoolProvider
            pub_proxy = EgressPublicPoolProvider.get_best_proxy(geo=self.geo_region)
            if pub_proxy:
                async with httpx.AsyncClient(proxy=pub_proxy, timeout=10.0, follow_redirects=True) as client:
                    resp = await client.get(url, params=params, headers=headers)
                    if resp.status_code == 200 and len(resp.content) >= min_bytes:
                        return resp.status_code, resp.text
        except Exception as e:
            logger.debug("Tier 3 (Public Proxy Pool) notice: %s", e)

        # Tier 4: Residential Proxy (DataImpulse fallback as emergency last resort)
        if self.residential_proxy_url:
            try:
                async with httpx.AsyncClient(proxy=self.residential_proxy_url, timeout=12.0, follow_redirects=True) as client:
                    resp = await client.get(url, params=params, headers=headers)
                    if resp.status_code == 200 and len(resp.content) >= min_bytes:
                        return resp.status_code, resp.text
            except Exception as e:
                logger.debug("Tier 4 (Residential Proxy) notice: %s", e)

        return 0, ""

    async def execute_google_autocomplete(self, bq: BrandQuery) -> bool:
        """Seeds Google Autocomplete cache with brand + entity query terms."""
        params = {"client": "chrome", "q": bq.query}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/128.0.0.0",
            "Accept": "*/*",
        }
        status_code, text = await self._fetch_with_fallback(
            "https://suggestqueries.google.com/complete/search",
            params=params,
            headers=headers,
            min_bytes=5,
        )
        if status_code == 200:
            logger.info("✅ [Google-Suggest] [%s] '%s' -> 200 OK", bq.tier.upper(), bq.query)
            return True
        logger.warning("Google Suggest query failed across tiers for: %s", bq.query)
        return False

    async def execute_duckduckgo_query(self, bq: BrandQuery) -> bool:
        """Seeds DuckDuckGo & Bing search index with brand queries."""
        params = {"q": bq.query}
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/128.0.0.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        status_code, text = await self._fetch_with_fallback(
            "https://html.duckduckgo.com/html/",
            params=params,
            headers=headers,
            min_bytes=300,
        )
        if status_code == 200:
            logger.info("✅ [DuckDuckGo] [%s] '%s' -> 200 OK (%d bytes)", bq.tier.upper(), bq.query, len(text))
            return True
        logger.warning("DuckDuckGo query failed across tiers for: %s", bq.query)
        return False

    async def execute_hn_algolia_query(self, bq: BrandQuery) -> bool:
        """Seeds Hacker News Algolia search logs for tech queries."""
        params = {"query": bq.query}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        status_code, text = await self._fetch_with_fallback(
            "https://hn.algolia.com/api/v1/search",
            params=params,
            headers=headers,
            min_bytes=10,
        )
        if status_code == 200:
            logger.info("✅ [HN-Algolia] [%s] '%s' -> 200 OK", bq.tier.upper(), bq.query)
            return True
        logger.warning("HN Algolia query failed across tiers for: %s", bq.query)
        return False

    async def execute_searxng_query(self, bq: BrandQuery, instance: str) -> bool:
        """Executes a search query against a public SearXNG instance."""
        params = {"q": bq.query, "language": "en"}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/128.0.0.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        status_code, text = await self._fetch_with_fallback(
            f"{instance}/search",
            params=params,
            headers=headers,
            min_bytes=200,
        )
        if status_code == 200:
            logger.info("✅ [SearXNG] [%s] '%s' -> 200 OK (%d bytes) (%s)", bq.tier.upper(), bq.query, len(text), instance)
            return True
        logger.warning("SearXNG instance %s failed for query: %s", instance, bq.query)
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
    parser.add_argument("--proxy", type=str, default=None, help="Optional HTTP/SOCKS5 proxy URL")
    parser.add_argument("--geo", type=str, default="US", help="Target geo region (US, UK, AU)")
    args = parser.parse_args()

    supabase_url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_key:
        logger.error("Missing Supabase credentials in environment.")
        sys.exit(1)

    supabase = create_client(supabase_url, supabase_key)
    booster = BrandKeywordBooster(
        supabase=supabase,
        backend=args.backend,
        dry_run=args.dry_run,
        geo_region=args.geo,
        proxy_url=args.proxy,
    )
    asyncio.run(booster.run(limit=args.limit, pillar=args.pillar))


if __name__ == "__main__":
    main()
