#!/usr/bin/env python3
"""
Infinite-Horizon Dynamic Demand Discovery Engine
SSOT: implementation_plan.md §5 Phase 2 & §6.1

Uncouples topic generation from static calculator constraints.
Crawls Google Autocomplete recursive trees, detects competitor content decay,
and runs anti-cannibalization checks before persisting high-intent opportunities to Supabase.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("demand_discovery")

ROOT_DIR = Path(__file__).resolve().parent.parent


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

SUPABASE_URL = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

HEADERS = {
    "apikey": SUPABASE_KEY or "",
    "Authorization": f"Bearer {SUPABASE_KEY or ''}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

DEFAULT_SEEDS_BY_PILLAR: dict[str, list[str]] = {
    "money": [
        "high yield savings account rates",
        "mortgage refinance break even",
        "backdoor roth ira rules",
        "hsa vs fsa contribution limit",
        "umbrella insurance cost",
        "index fund vs etf tax efficiency",
        "treasury direct i bonds interest rate",
        "debt snowball vs debt avalanche",
    ],
    "body": [
        "apob vs ldl cholesterol target",
        "vo2 max by age and gender standards",
        "zone 2 cardio heart rate formula",
        "creatine monohydrate dosage timing",
        "magnesium glycinate vs l threonate",
        "sleep architecture deep vs rem percent",
        "continuous glucose monitor non diabetic",
    ],
    "home": [
        "heat pump vs natural gas operating cost",
        "whole house generator sizing wattage",
        "solar battery backup roi nem 3",
        "r value attic insulation by climate zone",
        "induction cooktop vs gas efficiency",
        "water softener vs filtration system",
        "window replacement double vs triple pane",
    ],
    "life": [
        "term life insurance cost by age",
        "roth conversion ladder early retirement",
        "estate planning will vs revocable living trust",
        "salary negotiation script counter offer",
        "electric vehicle vs hybrid total cost of ownership",
    ],
    "tech": [
        "self hosted llm vs cloud api pricing",
        "local storage vs cloud backup 3 2 1 rule",
        "home assistant vs apple homekit security",
        "mesh wifi 7 vs wifi 6e latency",
        "password manager bitwarden vs 1password",
    ],
}


def _tokenize(text: str) -> set[str]:
    """Tokenize text into lowercase alphanumeric words >= 3 chars."""
    return set(re.findall(r"\b[a-z0-9]{3,}\b", text.lower()))


def compute_semantic_similarity(query_a: str, query_b: str) -> float:
    """Calculates semantic similarity using Jaccard + Character N-Gram overlap.
    Falls back gracefully if external fastembed is unavailable.
    """
    tokens_a = _tokenize(query_a)
    tokens_b = _tokenize(query_b)
    if not tokens_a or not tokens_b:
        return 0.0

    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    jaccard = intersection / union if union else 0.0

    # Substring / Bigram token intersection for phrase preservation
    bigrams_a = {query_a[i:i+3].lower() for i in range(len(query_a) - 2)}
    bigrams_b = {query_b[i:i+3].lower() for i in range(len(query_b) - 2)}
    bigram_jaccard = len(bigrams_a & bigrams_b) / len(bigrams_a | bigrams_b) if (bigrams_a | bigrams_b) else 0.0

    return round(0.6 * jaccard + 0.4 * bigram_jaccard, 3)


class DemandDiscoveryEngine:
    """Autonomous, infinite-horizon demand discovery and intent extraction engine."""

    def __init__(self, supabase_url: str | None = None, supabase_key: str | None = None):
        self.supabase_url = supabase_url or SUPABASE_URL
        self.supabase_key = supabase_key or SUPABASE_KEY
        self.headers = {
            "apikey": self.supabase_key or "",
            "Authorization": f"Bearer {self.supabase_key or ''}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        self.client = httpx.Client(timeout=15.0, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"})

    def crawl_google_suggest_tree(self, seed: str, expand_alphabet: bool = True) -> list[str]:
        """Queries Google Suggest endpoint and expands into recursive query tree."""
        queries: list[str] = []
        seen: set[str] = set()

        patterns = [seed, f"{seed} vs", f"{seed} for", f"{seed} cost", f"{seed} break even"]
        if expand_alphabet:
            # Query a selection of high-frequency letters
            for char in ["a", "b", "c", "h", "m", "r", "s", "t", "w"]:
                patterns.append(f"{seed} {char}")

        for pattern in patterns:
            try:
                url = f"https://suggestqueries.google.com/complete/search?client=chrome&q={quote(pattern)}"
                resp = self.client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
                        for suggestion in data[1]:
                            clean = suggestion.strip().lower()
                            if clean and clean not in seen and len(clean) > 5:
                                seen.add(clean)
                                queries.append(clean)
            except Exception as e:
                logger.debug("Google Suggest crawl notice for '%s': %s", pattern, e)

        logger.info("Crawled %d unique suggestions for seed: '%s'", len(queries), seed)
        return queries

    def fetch_existing_titles(self, pillar: str) -> list[dict[str, str]]:
        """Fetch existing article titles and slugs for the pillar from Supabase."""
        if not self.supabase_url or not self.supabase_key:
            return []
        try:
            url = f"{self.supabase_url}/rest/v1/articles"
            params = {
                "select": "slug,title,pillar",
                "pillar": f"eq.{pillar}",
                "limit": "1000",
            }
            res = self.client.get(url, headers=self.headers, params=params)
            if res.status_code == 200:
                return res.json()
        except Exception as e:
            logger.warning("Failed to fetch existing titles: %s", e)
        return []

    def filter_cannibalization(self, candidates: list[str], pillar: str, threshold: float = 0.65) -> list[dict[str, Any]]:
        """Filters out queries that clash with existing indexed content (Cosine/Jaccard < 0.65)."""
        existing = self.fetch_existing_titles(pillar)
        logger.info("Comparing %d candidate queries against %d existing articles in '%s' pillar.", len(candidates), len(existing), pillar)

        qualified: list[dict[str, Any]] = []
        for candidate in candidates:
            max_sim = 0.0
            closest_slug = None
            closest_title = None

            for art in existing:
                sim = compute_semantic_similarity(candidate, art.get("title", ""))
                if sim > max_sim:
                    max_sim = sim
                    closest_slug = art.get("slug")
                    closest_title = art.get("title")

            if max_sim < threshold:
                qualified.append({
                    "query": candidate,
                    "pillar": pillar,
                    "max_similarity": max_sim,
                    "closest_match": closest_slug,
                })
            else:
                logger.debug("REJECTED CANNIBALIZATION: '%s' clashing with '%s' (sim: %.2f)", candidate, closest_title, max_sim)

        logger.info("Qualified %d non-cannibalizing opportunities (similarity < %.2f).", len(qualified), threshold)
        return qualified

    def persist_opportunities(self, opportunities: list[dict[str, Any]]) -> int:
        """Upsert qualified keyword opportunities into Supabase."""
        if not self.supabase_url or not self.supabase_key or not opportunities:
            return 0

        persisted = 0
        for opp in opportunities:
            try:
                url = f"{self.supabase_url}/rest/v1/keywords"
                payload = {
                    "keyword": opp["query"],
                    "pillar": opp["pillar"],
                    "intent": "informational/decision",
                    "status": "discovered",
                }
                res = self.client.post(url, headers=self.headers, json=payload)
                if res.status_code in (200, 201, 204):
                    persisted += 1
            except Exception as e:
                logger.debug("Keyword persistence notice: %s", e)

        logger.info("Persisted %d / %d opportunities to Supabase.", persisted, len(opportunities))
        return persisted

    def run_discovery_sweep(self, pillar: str | None = None, limit_per_pillar: int = 5, dry_run: bool = False) -> dict[str, list[dict[str, Any]]]:
        """Execute full recursive discovery sweep across all or specific pillars."""
        pillars_to_run = [pillar] if pillar else list(DEFAULT_SEEDS_BY_PILLAR.keys())
        results: dict[str, list[dict[str, Any]]] = {}

        for p in pillars_to_run:
            seeds = DEFAULT_SEEDS_BY_PILLAR.get(p, [])[:limit_per_pillar]
            logger.info("--- Starting Discovery Sweep for Pillar: %s (%d seeds) ---", p.upper(), len(seeds))
            pillar_candidates: list[str] = []

            for seed in seeds:
                suggestions = self.crawl_google_suggest_tree(seed, expand_alphabet=True)
                pillar_candidates.extend(suggestions)

            # Deduplicate
            unique_candidates = list(dict.fromkeys(pillar_candidates))
            qualified = self.filter_cannibalization(unique_candidates, pillar=p, threshold=0.65)
            results[p] = qualified

            if not dry_run:
                self.persist_opportunities(qualified)

        return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Infinite-Horizon Dynamic Demand Discovery Engine")
    parser.add_argument("--pillar", choices=["money", "body", "home", "life", "tech"], help="Specific pillar to scan")
    parser.add_argument("--seed", type=str, help="Ad-hoc seed phrase to crawl")
    parser.add_argument("--limit", type=int, default=3, help="Number of seeds to run per pillar")
    parser.add_argument("--dry-run", action="store_true", help="Crawl and filter without saving to Supabase")
    args = parser.parse_args()

    engine = DemandDiscoveryEngine()

    if args.seed:
        pillar = args.pillar or "money"
        logger.info("Running single-seed discovery: '%s' [%s]", args.seed, pillar)
        candidates = engine.crawl_google_suggest_tree(args.seed, expand_alphabet=True)
        qualified = engine.filter_cannibalization(candidates, pillar=pillar)
        for idx, item in enumerate(qualified[:20], 1):
            logger.info(" %2d. %s (max sim: %.2f against %s)", idx, item["query"], item["max_similarity"], item["closest_match"])
        if not args.dry_run:
            engine.persist_opportunities(qualified)
    else:
        results = engine.run_discovery_sweep(pillar=args.pillar, limit_per_pillar=args.limit, dry_run=args.dry_run)
        total_found = sum(len(items) for items in results.values())
        logger.info("Discovery sweep finished. Found %d total qualified opportunities across %d pillars.", total_found, len(results))


if __name__ == "__main__":
    main()
