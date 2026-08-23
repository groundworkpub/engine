#!/usr/bin/env python3
"""
Groundwork Competitor Gap Engine (T6.1) — Brand Hijack Support.

Detects white-hat SERP hijack opportunities by comparing live search data
against Groundwork's own keyword footprint:

  1. HIJACK_KEYWORD — a brand-adjacent query surfaced by Google Suggest
     (e.g. "nerdwallet retirement calculator", "smartasset alternatives")
     where a competitor brand or its calculator/guide is the dominant result.
     These feed T6.2 programmatic comparison pages ("vs / alternative").
  2. KEYWORD_GAP    — a query where a configured competitor ranks in top-N but
     gworky.com is absent (live SearXNG scan, optional when instance available).
  3. MENTION_GAP    — a site that appears in results mentioning competitors but
     never mentions Groundwork (live SearXNG scan, optional).

Uses live Google Suggest (no-captcha, same source as keyword_graph) as the
primary surface, with optional SearXNG SERP scan via DataImpulse residential
egress for rank-level verification. No mock data (§2.5 live ground-truth).

Upserts findings to Supabase ``seo_competitor_gap`` table and reports a delta
summary. Designed for weekly cron + Telegram delta report.

Usage:
    python agents/competitor_gap.py                # full run, upsert to Supabase
    python agents/competitor_gap.py --dry-run      # scan only, no DB writes
    python agents/competitor_gap.py --pillar money --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from google_serp import GoogleSerpScanner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("competitor_gap")

DEFAULT_INSTANCE = "https://searx.be"
DEFAULT_TIMEOUT = 10
DEFAULT_RETRIES = 1
DEFAULT_TOP_N = 10
SUGGEST_ENDPOINT = "https://suggestqueries.google.com/complete/search"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

HIJACK_TRIGGERS = (
    "calculator",
    "alternative",
    "alternatives",
    " vs ",
    "vs ",
    "review",
    "reviews",
    "comparison",
    "best",
    "how much",
    "quote",
    "rates",
    "cost",
    "top",
    "calculator for",
)

PILLAR_SEEDS: dict[str, list[str]] = {
    "money": [
        "refinance mortgage rates",
        "high yield savings account",
        "life insurance quotes",
        "best credit card rewards",
    ],
    "body": [
        "best treadmill for home",
        "daily protein intake",
        "how to lower blood pressure",
        "sleep hygiene tips",
    ],
    "home": [
        "solar panel cost",
        "heat pump installation",
        "whole home generator",
        "smart door lock",
    ],
    "life": [
        "travel insurance comparison",
        "estate planning checklist",
        "how to negotiate salary",
        "best car insurance rates",
    ],
    "tech": [
        "best mesh wifi router",
        "ai note taking apps",
        "how to build a pc",
        "smart home hub comparison",
    ],
}

DEFAULT_COMPETITORS: dict[str, list[str]] = {
    "money": ["nerdwallet", "bankrate", "investopedia", "smartasset", "wallethub"],
    "body": ["healthline", "verywellhealth", "webmd", "medicalnewstoday"],
    "home": ["energysage", "angi", "bobvila", "forbes home improvement"],
    "life": ["policygenius", "thesimpledollar", "lifehacker", "nerdwallet"],
    "tech": ["pcmag", "tomsguide", "cnet", "theverge"],
}


@dataclass
class GapFinding:
    pillar: str
    seed_keyword: str
    gap_type: str  # HIJACK_KEYWORD | KEYWORD_GAP | MENTION_GAP
    competitor_domain: str
    ranking_url: str
    position: int
    gworky_present: bool
    detail: str = ""

    def to_row(self) -> dict[str, Any]:
        return {
            "pillar": self.pillar,
            "seed_keyword": self.seed_keyword,
            "gap_type": self.gap_type,
            "competitor_domain": self.competitor_domain,
            "ranking_url": self.ranking_url[:500],
            "position": self.position,
            "gworky_present": self.gworky_present,
            "detail": self.detail[:1000],
        }


@dataclass
class GapRunResult:
    queries_run: int = 0
    findings: list[GapFinding] = field(default_factory=list)
    upserted: int = 0

    @property
    def keyword_gaps(self) -> list[GapFinding]:
        return [f for f in self.findings if f.gap_type == "KEYWORD_GAP"]

    @property
    def hijack_keywords(self) -> list[GapFinding]:
        return [f for f in self.findings if f.gap_type == "HIJACK_KEYWORD"]

    @property
    def mention_gaps(self) -> list[GapFinding]:
        return [f for f in self.findings if f.gap_type == "MENTION_GAP"]


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


def fetch_suggestions(query: str, max_results: int = 12) -> list[str]:
    """Google Autocomplete via firefox client (no captcha). Raises on failure."""
    url = f"{SUGGEST_ENDPOINT}?client=firefox&q={urllib.parse.quote(query)}"
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
        data = json.loads(response.read().decode("utf-8"))
    if len(data) > 1 and isinstance(data[1], list):
        return [str(s) for s in data[1][:max_results]]
    return []


def is_hijack_candidate(keyword: str) -> bool:
    """A brand-adjacent suggestion that feeds comparison/alternative pages."""
    k = keyword.lower()
    if "gworky" in k or "groundwork" in k:
        return False
    return any(trigger in k for trigger in HIJACK_TRIGGERS)


def search_searxng(
    instance: str,
    query: str,
    results_per_query: int = 15,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    proxy_url: str | None = None,
) -> list[dict[str, Any]]:
    """Query the SearXNG JSON API (live). Raises to caller on persistent failure.

    Optionally routes through a residential proxy (DataImpulse) to bypass
    datacenter-IP rate-limiting — required for reliable public SearXNG instances.
    """
    params: dict[str, Any] = {
        "q": query,
        "format": "json",
        "language": "en",
        "number_of_results": results_per_query,
    }
    url = f"{instance.rstrip('/')}/search?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    opener: Any = urllib.request
    if proxy_url:
        proxy_handler = urllib.request.ProxyHandler(
            {"http": proxy_url, "https": proxy_url}
        )
        opener = urllib.request.build_opener(proxy_handler)
    attempts = retries + 1
    for attempt in range(attempts):
        try:
            with opener.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            results = data.get("results") or []
            return results[:results_per_query]
        except Exception as e:
            if attempt == attempts - 1:
                raise
            logger.warning(
                "SearXNG fetch failed for '%s' (attempt %d/%d): %s",
                query, attempt + 1, attempts, e,
            )
            time.sleep(2 * (attempt + 1))
    return []


def extract_domain(url: str) -> str:
    """Extract registrable domain (best-effort, keeps known subdomains like cnet.com)."""
    import re

    m = re.match(r"https?://(?:www\.)?([^/]+)", url or "")
    if not m:
        return ""
    return m.group(1).lower().replace("www.", "")


def run_hijack_scan(
    competitors: dict[str, list[str]],
    *,
    max_brands_per_run: int = 4,
    max_suggestions_per_brand: int = 12,
) -> GapRunResult:
    """Brand-hijack surface via live Google Suggest.

    For each competitor brand (e.g. "nerdwallet"), pull autocomplete queries and
    flag brand-adjacent decision keywords ("nerdwallet retirement calculator",
    "smartasset alternatives") that feed T6.2 comparison/alternative pages.
    This is the PRIMARY hijack signal (Suggest is captcha-free and reliable).
    """
    result = GapRunResult()
    for pillar, brands in competitors.items():
        for brand in brands[:max_brands_per_run]:
            try:
                suggestions = fetch_suggestions(brand, max_results=max_suggestions_per_brand)
            except Exception as e:
                logger.warning("Suggest fetch failed for '%s': %s", brand, e)
                continue
            result.queries_run += 1
            for s in suggestions:
                if not is_hijack_candidate(s):
                    continue
                result.findings.append(
                    GapFinding(
                        pillar=pillar,
                        seed_keyword=brand,
                        gap_type="HIJACK_KEYWORD",
                        competitor_domain=brand,
                        ranking_url=f"https://www.google.com/search?q={urllib.parse.quote(s)}",
                        position=1,
                        gworky_present=False,
                        detail=(
                            f"Brand-adjacent decision keyword surfaced: '{s}' — "
                            f"candidate for T6.2 comparison page ('{brand} alternative' / 'vs {brand}')"
                        ),
                    )
                )
    return result


def run_gap_scan(
    seeds: dict[str, list[str]],
    competitors: dict[str, list[str]],
    *,
    instance: str = DEFAULT_INSTANCE,
    top_n: int = DEFAULT_TOP_N,
    results_per_query: int = 15,
    max_seeds_per_run: int = 8,
    ignore_brands: list[str] | None = None,
    proxy_url: str | None = None,
    google_backend: bool = False,
) -> GapRunResult:
    """Scan live SERP per seed; flag keyword gaps & mention gaps.

    ``google_backend`` selects the real Google SERP scanner (patchright + native
    proxy-auth) instead of SearXNG. It is OPTIONAL: SearXNG remains the default
    (zero-cost) path; Google backend is opt-in for rank-level verification via
    residential egress.
    """
    ignore_brands = [b.lower() for b in (ignore_brands or [])]
    result = GapRunResult()
    serp = GoogleSerpScanner(proxy_url=proxy_url, top_n=top_n) if google_backend else None

    for pillar, pillar_seeds in seeds.items():
        comps = competitors.get(pillar, [])
        if not comps:
            continue
        for seed in pillar_seeds[:max_seeds_per_run]:
            try:
                if serp is not None:
                    outcome = asyncio.run(serp.scan(seed))
                    if outcome.error == "no-egress-refused":
                        logger.info(
                            "[%s] '%s' skipped (Google backend needs egress).", pillar, seed
                        )
                        continue
                    results = outcome.as_search_row()
                    if outcome.bot_gated:
                        logger.warning("[%s] '%s' bot-gated (skipped)", pillar, seed)
                        continue
                else:
                    results = search_searxng(
                        instance, seed, results_per_query=results_per_query,
                        proxy_url=proxy_url,
                    )
            except Exception as e:
                logger.warning("[%s] '%s' skipped: %s", pillar, seed, e)
                continue

            result.queries_run += 1
            # Track domains in top-N and whether gworky appears anywhere
            gworky_present = False
            top_results: list[dict[str, Any]] = []
            seen_domains: set[str] = set()

            for rank, r in enumerate(results[:top_n], start=1):
                url = r.get("url", "")
                domain = extract_domain(url)
                if not domain:
                    continue
                if "gworky.com" in domain or "groundwork" in domain:
                    gworky_present = True
                if domain in seen_domains:
                    continue
                seen_domains.add(domain)
                top_results.append({"rank": rank, "domain": domain, "url": url})

            # KEYWORD_GAP: competitor ranks top-N but we are absent
            if not gworky_present:
                for comp in comps:
                    comp_base = comp.lower().replace("www.", "")
                    for tr in top_results:
                        if comp_base in tr["domain"]:
                            result.findings.append(
                                GapFinding(
                                    pillar=pillar,
                                    seed_keyword=seed,
                                    gap_type="KEYWORD_GAP",
                                    competitor_domain=comp_base,
                                    ranking_url=tr["url"][:500],
                                    position=tr["rank"],
                                    gworky_present=False,
                                    detail=f"'{seed}' ranks {comp_base} #{tr['rank']} (top-{top_n}) but gworky.com absent",
                                )
                            )
                            break  # one finding per competitor per query

            # MENTION_GAP: non-competitor result page likely cites brands without us
            # Best-effort: a high-authority aggregator in top-N when we're absent
            if not gworky_present:
                for tr in top_results[:3]:
                    domain = tr["domain"]
                    if any(b in domain for b in ignore_brands):
                        continue
                    if any(c in domain for c in comps):
                        continue
                    if domain.endswith((".gov", ".edu", ".org")):
                        continue
                    result.findings.append(
                        GapFinding(
                            pillar=pillar,
                            seed_keyword=seed,
                            gap_type="MENTION_GAP",
                            competitor_domain=domain,
                            ranking_url=tr["url"][:500],
                            position=tr["rank"],
                            gworky_present=False,
                            detail=f"'{seed}' — {domain} #1-3 ranks, gworky.com absent; candidate for mention generation",
                        )
                    )
                    break

    return result


def upsert_findings(result: GapRunResult) -> int:
    """Upsert findings to Supabase `seo_competitor_gap`. Returns count."""
    from supabase import create_client

    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get(
        "NEXT_PUBLIC_SUPABASE_ANON_KEY"
    )
    if not url or not key:
        logger.error("Supabase credentials missing. Set NEXT_PUBLIC_SUPABASE_URL + key.")
        return 0

    supabase = create_client(url, key)
    rows = [f.to_row() for f in result.findings]
    if not rows:
        return 0
    resp = supabase.table("seo_competitor_gap").insert(rows).execute()
    count = len(resp.data) if resp.data else 0
    logger.info("Upserted %d competitor-gap findings → seo_competitor_gap", count)
    return count


def build_delta_report(result: GapRunResult) -> str:
    """Human-readable delta summary for Telegram."""
    hijack = result.hijack_keywords
    kw = result.keyword_gaps
    mention = result.mention_gaps
    lines = [
        "📊 **Competitor Gap Scan**",
        f"- Queries: {result.queries_run}",
        f"- Hijack keywords: {len(hijack)}",
        f"- Keyword gaps: {len(kw)}",
        f"- Mention gaps: {len(mention)}",
    ]
    for f in hijack[:6]:
        # Extract the quoted keyword from detail ("Brand-adjacent ...: '<kw>' — ...")
        kw_txt = f.detail
        if "'" in kw_txt:
            kw_txt = kw_txt.split("'")[1] if len(kw_txt.split("'")) > 1 else kw_txt
        lines.append(f"  • `{kw_txt}` → {f.competitor_domain}")
    for f in kw[:4]:
        lines.append(f"  • rank-gap: `{f.seed_keyword}` → {f.competitor_domain} #{f.position}")
    for f in mention[:3]:
        lines.append(f"  • mention-gap: {f.competitor_domain} ({f.seed_keyword})")
    if not hijack and not kw and not mention:
        lines.append("✅ No gaps detected this run.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Groundwork Competitor Gap Engine (T6.1 — white-hat SERP hijack)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Scan only, no DB writes")
    parser.add_argument(
        "--pillar",
        choices=list(PILLAR_SEEDS.keys()),
        default=None,
        help="Restrict scan to one pillar",
    )
    parser.add_argument("--config", default=None, help="Path to config.yml override")
    parser.add_argument("--report", action="store_true", help="Print delta report")
    parser.add_argument("--proxy", type=str, default=None, help="Force proxy URL for SearXNG queries")
    parser.add_argument(
        "--google-backend",
        action="store_true",
        help="Use live Google SERP scanner (patchright + residential egress) instead of SearXNG. Opt-in; requires egress.",
    )
    args = parser.parse_args()

    # Load config
    seeds: dict[str, list[str]] = PILLAR_SEEDS
    competitors: dict[str, list[str]] = DEFAULT_COMPETITORS
    instance = DEFAULT_INSTANCE
    top_n = DEFAULT_TOP_N
    results_per_query = 15
    max_seeds = 8
    ignore_brands: list[str] = []

    config_path = args.config or (Path(__file__).resolve().parent / "config.yml")
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        cg = cfg.get("competitor_gap", {}) or {}
        if cg.get("enabled", True):
            instance = cg.get("searxng_instance", instance)
            top_n = int(cg.get("top_n", top_n))
            results_per_query = int(cg.get("results_per_query", results_per_query))
            max_seeds = int(cg.get("max_seeds_per_run", max_seeds))
            ignore_brands = cg.get("ignore_brands", []) or []
            competitors = cg.get("competitors") or competitors
            # seeds fall back to pillar seeds
            if isinstance(cg.get("seeds"), dict):
                seeds = cg["seeds"]

    if args.pillar:
        seeds = {args.pillar: seeds.get(args.pillar, [])}
        competitors = {args.pillar: competitors.get(args.pillar, [])}

    # Egress: explicit proxy > DataImpulse residential (US geo) auto-select
    proxy_url = args.proxy
    if not proxy_url and os.environ.get("DATAIMPULSE_LOGIN"):
        try:
            from egress_dataimpulse import DataImpulseProxyRouter

            proxy_url = DataImpulseProxyRouter.get_proxy_url("us", f"cg_{int(time.time())}")
            logger.info("🌐 SearXNG via DataImpulse residential (US): %s", proxy_url.split("@")[-1])
        except Exception as e:
            logger.debug("DataImpulse egress unavailable: %s", e)

    logger.info(
        "Competitor Gap scan — instance=%s top_n=%d queries<=%d proxy=%s",
        instance, top_n, max_seeds, bool(proxy_url),
    )
    # Primary: brand-hijack surface via live Google Suggest
    hijack_result = run_hijack_scan(competitors)
    logger.info("HIJACK scan complete — %d queries, %d hijack keywords", hijack_result.queries_run, len(hijack_result.hijack_keywords))

    # Optional: SERP rank-level verification via SearXNG (only if instance live)
    result = run_gap_scan(
        seeds,
        competitors,
        instance=instance,
        top_n=top_n,
        results_per_query=results_per_query,
        max_seeds_per_run=max_seeds,
        ignore_brands=ignore_brands,
        proxy_url=proxy_url,
        google_backend=args.google_backend,
    )
    # Merge findings (hijack surface + any live rank gaps)
    merged = GapRunResult()
    merged.queries_run = hijack_result.queries_run + result.queries_run
    merged.findings = hijack_result.findings + result.findings

    if args.report:
        print(build_delta_report(merged))

    if args.dry_run:
        logger.info("[DRY-RUN] Would upsert %d findings", len(merged.findings))
        for f in merged.findings[:12]:
            logger.info("  %s | %s | %s | #%d", f.gap_type, f.seed_keyword, f.competitor_domain, f.position)
        return

    merged.upserted = upsert_findings(merged)
    logger.info("Done. %d findings upserted.", merged.upserted)


if __name__ == "__main__":
    main()
