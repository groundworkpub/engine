#!/usr/bin/env python3
"""
agents/economic_engine.py
Groundwork Platform — Monetization & Economic Intelligence Hub
SSOT: implementation_plan.md — Dynamic Economic Selection & Prioritization

Core Responsibilities:
1. Ingestion-layer Dynamic Economic Intent Scoring (DEIS):
   - Commercial Buyer Intent (0–40 pts)
   - Registered Interactive Tool Synergy (0–30 pts, live from Supabase 'tools')
   - Registered Affiliate & Partner Alignment (0–30 pts, live from 'affiliate_links' & 'affiliate.ts')
   - Composite Economic Score (0–100 pts)
2. Soft Gate Prioritization: Ranks Critic candidates so Scribe always generates highest-yielding,
   decision-dense articles first.
3. Closed-Loop Monetization: Dispatches offer synchronization via affiliate_harvester and detects
   monetization gaps where high-demand topics lack commercial counterparties.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Ensure repository root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("economic_engine")


def _load_env_local() -> None:
    """Native .env.local loader (Rule §2.14)."""
    p = ROOT_DIR / ".env.local"
    if p.exists():
        with open(p, encoding="utf-8") as f:
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


def get_supabase_client() -> Any:
    """Create Supabase client using environment credentials."""
    from supabase import create_client
    url = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    if not url or not key:
        return None
    try:
        return create_client(url, key)
    except Exception as e:
        logger.warning(f"Could not connect to Supabase: {e}")
        return None


# High-value transactional & commercial buyer intent tokens
COMMERCIAL_TIER1_TOKENS: set[str] = {
    "cost", "costs", "price", "pricing", "rate", "rates", "calculator", "vs", "versus",
    "review", "reviews", "quote", "quotes", "roi", "break-even", "breakeven", "salary",
    "refinance", "refi", "hysa", "loan", "loans", "mortgage", "amortization", "debt",
    "insurance", "bonus", "discount", "rebate", "tax", "taxes", "savings", "fee", "fees",
    "benchmark", "allocation", "yield", "generator", "solar", "battery", "storage",
}

COMMERCIAL_TIER2_TOKENS: set[str] = {
    "compare", "comparison", "best", "guide", "how to buy", "how to choose", "strategy",
    "budget", "save", "cut", "top", "specs", "specifications", "install", "installation",
    "alternative", "alternatives", "upgrade", "analysis", "payoff", "invest", "investment",
    "provider", "providers", "hire", "consultant", "parity", "multiplier", "formula",
}

NEGATIVE_TRIVIA_TOKENS: set[str] = {
    "beatles", "celebrity", "gossip", "song", "lyrics", "movie", "actor", "actress",
    "trailer", "drama", "rumor", "rumours", "dating", "paparazzi", "trivia", "hollywood",
}


@dataclass
class EconomicScoreDTO:
    composite_score: float
    commercial_intent_score: float
    tool_synergy_score: float
    affiliate_synergy_score: float
    matched_tool_slug: str | None
    matched_partner_slug: str | None
    intent_tier: str


class EconomicIntelligenceEngine:
    """Unified Monetization & Economic Intelligence Hub."""

    def __init__(self, supabase: Any | None = None) -> None:
        self.supabase = supabase or get_supabase_client()
        self.tools: list[dict[str, Any]] = []
        self.affiliate_partners: list[dict[str, Any]] = []
        self._load_live_catalog()

    def _load_live_catalog(self) -> None:
        """Loads real registered interactive tools and affiliate partners from Supabase & codebase."""
        # 1. Load active tools from Supabase
        if self.supabase:
            try:
                res = self.supabase.table("tools").select("id, slug, title, description, pillar").eq("is_active", True).execute()
                self.tools = res.data or []
                logger.info(f"Loaded {len(self.tools)} active interactive tools from Supabase.")
            except Exception as e:
                logger.warning(f"Failed to load tools from Supabase: {e}")

        # 2. Load affiliate offers from Supabase
        if self.supabase:
            try:
                res_aff = self.supabase.table("affiliate_links").select("slug, name, merchant, pillar, network").execute()
                self.affiliate_partners = res_aff.data or []
                logger.info(f"Loaded {len(self.affiliate_partners)} affiliate offers from Supabase.")
            except Exception as e:
                logger.warning(f"Failed to load affiliate_links from Supabase: {e}")

        # 3. Extract static partners from lib/monetization/affiliate.ts if DB returned few
        if len(self.affiliate_partners) < 10:
            static_partners = self._extract_static_affiliates()
            # Dedup by slug
            existing_slugs = {p.get("slug") for p in self.affiliate_partners}
            for sp in static_partners:
                if sp.get("slug") not in existing_slugs:
                    self.affiliate_partners.append(sp)
            logger.info(f"Total unified affiliate partners available: {len(self.affiliate_partners)}.")

    def _extract_static_affiliates(self) -> list[dict[str, Any]]:
        """Parses lib/monetization/affiliate.ts to extract static partner metadata."""
        affiliate_ts = ROOT_DIR / "lib" / "monetization" / "affiliate.ts"
        partners: list[dict[str, Any]] = []
        if not affiliate_ts.exists():
            return partners

        try:
            content = affiliate_ts.read_text(encoding="utf-8")
            blocks = re.findall(r'"([^"]+)":\s*\{\s*slug:\s*"([^"]+)",\s*name:\s*"([^"]+)",\s*merchant:\s*"([^"]+)",[^}]+?pillar:\s*"([^"]+)"', content, re.DOTALL)
            for key, slug, name, merchant, pillar in blocks:
                partners.append({
                    "slug": slug,
                    "name": name,
                    "merchant": merchant,
                    "pillar": pillar,
                    "network": "direct",
                })
        except Exception as e:
            logger.debug(f"Static affiliate extraction notice: {e}")

        return partners

    def score_candidate(
        self,
        title: str,
        content: str = "",
        pillar: str = "general",
        url: str = "",
    ) -> EconomicScoreDTO:
        """Scores candidate content across commercial intent, tool synergy, and affiliate alignment."""
        text_full = f"{title} {content} {url}".lower()
        title_lower = title.lower()
        tokens = set(re.findall(r"\b[a-z0-9-]{3,}\b", text_full))

        # 1. Commercial Buyer Intent Score (0 - 40 pts)
        commercial_pts = 0.0

        # Check negative trivia tokens first
        neg_count = sum(1 for nt in NEGATIVE_TRIVIA_TOKENS if nt in tokens)
        if neg_count > 0:
            commercial_pts -= (neg_count * 15.0)

        # Tier 1 high-intent keywords (up to 28 pts)
        tier1_matches = sum(1 for t1 in COMMERCIAL_TIER1_TOKENS if t1 in tokens)
        commercial_pts += min(28.0, tier1_matches * 7.0)

        # Tier 2 intent keywords (up to 12 pts)
        tier2_matches = sum(1 for t2 in COMMERCIAL_TIER2_TOKENS if t2 in tokens)
        commercial_pts += min(12.0, tier2_matches * 3.0)

        # Boost if strong intent appears directly in the headline/title
        if any(t1 in title_lower for t1 in COMMERCIAL_TIER1_TOKENS):
            commercial_pts += 5.0

        commercial_score = max(0.0, min(40.0, round(commercial_pts, 1)))

        # 2. Registered Interactive Tool Synergy (0 - 30 pts)
        tool_score = 0.0
        best_tool_slug: str | None = None
        best_tool_match_count = 0

        for tool in self.tools:
            tool_pillar = tool.get("pillar") or ""
            tool_title = (tool.get("title") or "").lower()
            tool_desc = (tool.get("description") or "").lower()
            tool_slug = tool.get("slug") or ""

            # Check pillar match
            pillar_multiplier = 1.2 if (pillar and pillar.lower() == tool_pillar.lower()) else 0.8

            tool_tokens = set(re.findall(r"\b[a-z0-9]{3,}\b", f"{tool_title} {tool_desc} {tool_slug.replace('-', ' ')}"))
            # Filter generic stop words
            tool_tokens -= {"calculator", "tool", "optimizer", "auditor", "modeler", "the", "and", "for"}

            common_tokens = tokens.intersection(tool_tokens)
            match_val = len(common_tokens) * pillar_multiplier

            if match_val > best_tool_match_count:
                best_tool_match_count = match_val
                best_tool_slug = tool_slug

        if best_tool_match_count >= 3:
            tool_score = 30.0
        elif best_tool_match_count == 2:
            tool_score = 20.0
        elif best_tool_match_count == 1:
            tool_score = 10.0

        # 3. Registered Affiliate & Partner Alignment (0 - 30 pts)
        affiliate_score = 0.0
        best_partner_slug: str | None = None
        best_partner_match_count = 0

        for partner in self.affiliate_partners:
            partner_pillar = partner.get("pillar") or ""
            partner_name = (partner.get("name") or "").lower()
            partner_merchant = (partner.get("merchant") or "").lower()
            partner_slug = partner.get("slug") or ""

            partner_tokens = set(re.findall(r"\b[a-z0-9]{3,}\b", f"{partner_name} {partner_merchant} {partner_slug.replace('-', ' ')}"))
            partner_tokens -= {"inc", "bank", "academy", "guide", "portal", "the", "and", "for"}

            common = tokens.intersection(partner_tokens)
            if len(common) > best_partner_match_count:
                best_partner_match_count = len(common)
                best_partner_slug = partner_slug

        if best_partner_match_count >= 2:
            affiliate_score = 30.0
        elif best_partner_match_count == 1:
            affiliate_score = 18.0

        composite_score = round(commercial_score + tool_score + affiliate_score, 1)

        # Tier classification
        if composite_score >= 65.0:
            intent_tier = "HIGH_COMMERCIAL"
        elif composite_score >= 40.0:
            intent_tier = "MODERATE_COMMERCIAL"
        else:
            intent_tier = "LOW_INFORMATIONAL"

        return EconomicScoreDTO(
            composite_score=composite_score,
            commercial_intent_score=commercial_score,
            tool_synergy_score=tool_score,
            affiliate_synergy_score=affiliate_score,
            matched_tool_slug=best_tool_slug if tool_score > 0 else None,
            matched_partner_slug=best_partner_slug if affiliate_score > 0 else None,
            intent_tier=intent_tier,
        )

    def rank_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Calculates economic scores and sorts candidates in descending order of value."""
        scored_list: list[dict[str, Any]] = []
        for item in candidates:
            title = str(item.get("title") or "")
            content = str(item.get("content") or item.get("raw_content") or "")
            pillar = str(item.get("pillar") or "general")
            url = str(item.get("url") or "")

            dto = self.score_candidate(title=title, content=content, pillar=pillar, url=url)

            enriched = {
                **item,
                "economic_score": dto.composite_score,
                "commercial_intent_score": dto.commercial_intent_score,
                "tool_synergy_score": dto.tool_synergy_score,
                "affiliate_synergy_score": dto.affiliate_synergy_score,
                "matched_tool": dto.matched_tool_slug,
                "matched_partner": dto.matched_partner_slug,
                "intent_tier": dto.intent_tier,
            }
            scored_list.append(enriched)

        # Sort descending by economic score
        scored_list.sort(key=lambda x: x.get("economic_score", 0.0), reverse=True)
        return scored_list

    def sync_affiliate_offers(self, dry_run: bool = False) -> int:
        """Triggers affiliate offer harvest across networks and syncs to Supabase."""
        try:
            from agents.affiliate_harvester import harvest_all_offers, sync_to_supabase
            logger.info("Triggering modular affiliate harvest across networks...")
            offers = harvest_all_offers()
            synced = sync_to_supabase(offers, dry_run=dry_run)
            logger.info(f"Affiliate sync complete: {synced} offers processed.")
            # Reload internal catalog
            self._load_live_catalog()
            return synced
        except Exception as e:
            logger.error(f"Error during affiliate synchronization: {e}")
            return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Groundwork Economic Intelligence Engine CLI")
    parser.add_argument("--evaluate-stubs", action="store_true", help="Score and rank all 21 review/draft stubs in Supabase")
    parser.add_argument("--sync-affiliates", action="store_true", help="Sync latest affiliate offers from networks")
    parser.add_argument("--dry-run", action="store_true", help="Run without mutating database")
    args = parser.parse_args()

    engine = EconomicIntelligenceEngine()
    logger.info("Economic Intelligence Engine Initialized.")
    logger.info(f"Active Tools: {len(engine.tools)} | Affiliate Partners: {len(engine.affiliate_partners)}")

    if args.sync_affiliates:
        engine.sync_affiliate_offers(dry_run=args.dry_run)

    if args.evaluate_stubs:
        if not engine.supabase:
            logger.error("Supabase credentials missing.")
            return

        logger.info("Fetching draft/review stubs for economic evaluation...")
        res = engine.supabase.table("articles").select("id, slug, title, content, pillar, word_count, status").in_("status", ["review", "draft"]).execute()
        stubs = res.data or []
        logger.info(f"Evaluating {len(stubs)} stub articles...")

        ranked = engine.rank_candidates(stubs)

        print("\n" + "=" * 105)
        print(f"{'SLUG':<42} | {'PILLAR':<7} | {'WORDS':<5} | {'SCORE':<5} | {'TIER':<18} | {'TOOL / PARTNER'}")
        print("-" * 105)
        for r in ranked:
            match_info = r.get("matched_tool") or r.get("matched_partner") or "none"
            print(f"{r['slug'][:42]:<42} | {r['pillar']:<7} | {r.get('word_count', 0):<5} | {r['economic_score']:<5} | {r['intent_tier']:<18} | {match_info[:22]}")
        print("=" * 105 + "\n")


if __name__ == "__main__":
    main()
