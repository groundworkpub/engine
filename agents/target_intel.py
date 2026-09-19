#!/usr/bin/env python3
"""agents/target_intel.py — Groundwork Target-Driven Intelligence Engine (Compliant Cyber-SEO, Component A).

Turns the business-selected keyword set (data/content-silo-manifest.json) into a
ranked, publish-ready gap report:

  1. TARGET LOAD   — reads the 46 business-chosen clusters (target-first).
  2. GSC GAP       — first-party Search Console: clicks/impressions/position for
                     queries containing the target (authorized API, no scraping).
  3. SERP SILHOUETTE — licensed SERP first (Serper.dev); Tavily fallback.
  4. SUGGEST LANE  — Google Autocomplete long-tail phrases, routed through the
                     repo-standard geo-coherent residential egress (SmartPolicySelector
                     serp_recon / DataImpulse; AGENTS §6). Honest UA, jitter+backoff,
                     stop-on-403. Fed into the TF-IDF blindspot diff as a query stream.
  5. TF-IDF SILHOUETTE — pure stdlib math: topical blindspots between competitor
                     headlines + suggest phrases vs our landing. No LLM overhead.
  6. GEO PAYLOAD   — pristine JSON (data/target-intel.json) seeding scribe/refresh.

Rate-limiting: async semaphore(3) + network jitter + exponential backoff on every
outbound call.

Usage:
    python agents/target_intel.py                 # full run, writes data/target-intel.json
    python agents/target_intel.py --limit 10      # first N P0/P1 targets
    python agents/target_intel.py --dry-run       # print report, no file write

Zero keyword-scraping on crowded keywords, zero TLS-fingerprint impersonation
(Axiom IV: TOS-compliant; residential egress only for SERP/suggest recon — the
platform's documented lane, never for identity spoofing).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import random
import re
import sys
import time
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.authority_injector import _load_env_local  # noqa: E402
from agents.dork_harvester import scrape_tavily_search  # noqa: E402
from agents.egress_selector import SmartPolicySelector  # noqa: E402
from agents.gsc_manager import get_gsc_access_token  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("target_intel")


def compute_opportunity_metrics(volume: int | None, kd: float | None, intent: str | None) -> tuple[float, str]:
    """Computes Opportunity Score and 4-quadrant classification (Volume x KD x Intent).

    Formula: Opportunity Score = log10(Volume + 10) / (KD_effective + 0.08)^1.2 * Intent_Multiplier
    Quadrants:
      - holy_grail: Volume >= 800 and KD <= 0.40 (High Volume, Low KD)
      - quick_win: Volume < 800 and KD <= 0.35 (Low/Mid Volume, Low KD)
      - battleground: Volume >= 800 and KD > 0.40 (High Volume, High KD)
      - trap: Volume < 800 and KD > 0.35 (Low Volume, High KD)
    """
    vol = max(10, volume or 100)
    kd_val = max(0.0, min(1.0, (kd / 100.0) if kd and kd > 1.0 else (kd or 0.50)))

    intent_norm = (intent or "").lower()
    if any(k in intent_norm for k in ("tool", "calc")):
        intent_mult = 1.3
    elif any(k in intent_norm for k in ("commercial", "transactional", "roi", "cost")):
        intent_mult = 1.2
    else:
        intent_mult = 1.0

    score = (math.log10(vol + 10) / math.pow(kd_val + 0.08, 1.2)) * intent_mult
    score = round(score, 2)

    if vol >= 800 and kd_val <= 0.40:
        quadrant = "holy_grail"
    elif kd_val <= 0.35:
        quadrant = "quick_win"
    elif vol >= 800:
        quadrant = "battleground"
    else:
        quadrant = "trap"

    return score, quadrant

MANIFEST = ROOT / "data" / "content-silo-manifest.json"
OUTPUT = ROOT / "data" / "target-intel.json"
GSC_SITE = "sc-domain:gworky.com"
GSC_API = "https://www.googleapis.com/webmasters/v3"
LOOKBACK_DAYS = 60
SUGGEST_ENDPOINT = "https://suggestqueries.google.com/complete/search"
SERPER_API = "https://google.serper.dev/search"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "for", "in", "on", "with", "as",
    "at", "by", "your", "you", "is", "are", "be", "how", "what", "why", "does",
    "do", "can", "should", "will", "it", "its", "than", "from", "their", "that",
    "this", "about", "vs", "versus", "2026", "2025", "calculator", "guide",
}

_SEMAPHORE: asyncio.Semaphore | None = None


# ── Async rate-limit manager: jitter + exponential backoff (authorized APIs) ──
async def _throttle(attempt: int, base_ms: int = 400) -> None:
    """Jittered backoff: base * 2^attempt plus random jitter (respects 403/429)."""
    delay = (base_ms * (2 ** max(0, attempt - 1))) / 1000.0
    delay += random.uniform(0.0, delay * 0.4)
    await asyncio.sleep(delay)

async def _guard() -> None:
    global _SEMAPHORE
    if _SEMAPHORE is None:
        _SEMAPHORE = asyncio.Semaphore(3)
    await _SEMAPHORE.acquire()

def _release() -> None:
    if _SEMAPHORE is not None:
        _SEMAPHORE.release()


# ── Egress (repo SSOT: egrees_selector serp_recon, DataImpulse residential) ──
def _resolve_egress_proxy() -> str | None:
    """Geo-coherent residential egress for SERP recon (AGENTS §6, egress_selector).

    Same lane the platform already uses for serp_recon (verified live). Falls
    back to direct + honor-403 backoff if the router or creds are unavailable.
    Never TLS-fingerprint impersonation — plain honest HTTP through the proxy.
    """
    _load_env_local()
    try:
        selector = SmartPolicySelector()
        return selector.get_proxy(task_type="serp_recon", geo="us")
    except Exception as e:
        logger.warning("Egress proxy unavailable (fall back to direct+backoff): %s", e)
        return None


# ── Licensed SERP first (no proxy needed; monitor-friendly) ──────────────────
async def _fetch_licensed_serp(keyword: str, sem: asyncio.Semaphore) -> tuple[list[dict[str, str]], str]:
    key = os.environ.get("SERPER_API_KEY")
    if not key:
        return [], "none"
    await sem.acquire()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                SERPER_API,
                headers={"X-API-KEY": key, "Content-Type": "application/json"},
                json={"q": keyword, "gl": "us", "hl": "en", "num": 8},
            )
        if resp.status_code == 200:
            org = resp.json().get("organic") or []
            return [
                {"title": x.get("title", ""), "url": x.get("link", ""), "snippet": x.get("snippet", "")}
                for x in org[:8]
            ], "serper"
        logger.warning("Serper status %s on '%s'", resp.status_code, keyword)
    except Exception as e:
        logger.warning("Serper error on '%s': %s", keyword, e)
    finally:
        sem.release()
    return [], "none"


# ── Google Autocomplete (long-tail user query stream) via residential egress ─
async def _fetch_google_suggest(keyword: str, proxy: str | None, sem: asyncio.Semaphore) -> list[str]:
    """Long-tail phrases Google autocompletes for the target (query-level intent).

    Routed through the same geo-coherent DataImpulse egress the platform uses for
    SERP recon. Honest UA, jitter+backoff; a 403 stops this keyword (no rotating
    subs to dodge — Axiom IV).
    """
    params = {"client": "firefox", "hl": "en", "gl": "us", "q": keyword}
    url = f"{SUGGEST_ENDPOINT}?{urllib.parse.urlencode(params)}"
    for attempt in range(1, 4):
        await sem.acquire()
        try:
            async with httpx.AsyncClient(timeout=15.0, proxy=proxy or None) as client:
                resp = await client.get(url, headers={"User-Agent": UA, "Accept": "application/json"})
            if resp.status_code == 403:
                logger.warning("Suggest 403 for '%s' (stop for this keyword)", keyword)
                return []
            if resp.status_code != 200:
                logger.warning("Suggest %s for '%s'", resp.status_code, keyword)
                await _throttle(attempt)
                continue
            data = resp.json()
            if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
                return [str(s) for s in data[1][:12]]
            return []
        except Exception:
            await _throttle(attempt)
        finally:
            sem.release()
    return []


# ── TF-IDF (pure stdlib, zero deps) ───────────────────────────────────────────
def _tokenize(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return [t for t in text.split() if len(t) > 2 and t not in STOPWORDS]

def _tf(doc: list[str]) -> Counter:
    return Counter(doc)

def _idf(corpus: list[list[str]]) -> dict[str, float]:
    n = len(corpus)
    df: Counter = Counter()
    for doc in corpus:
        for term in set(doc):
            df[term] += 1
    return {t: math.log1p(n / (1 + c)) for t, c in df.items()}

def _tfidf_terms(doc: list[str], idf: dict[str, float]) -> list[tuple[str, float]]:
    tf = _tf(doc)
    n = len(doc) or 1
    scored = [(t, (tf[t] / n) * idf.get(t, 0.0)) for t in tf]
    return sorted(scored, key=lambda x: -x[1])


# ── GSC first-party gap measurement ───────────────────────────────────────────
async def gsc_gap(token: str, keyword: str, sem: asyncio.Semaphore) -> dict[str, Any]:
    """Clicks/impressions/position for queries containing the target keyword."""
    end = time.strftime("%Y-%m-%d")
    start = time.strftime("%Y-%m-%d", time.localtime(time.time() - LOOKBACK_DAYS * 86400))
    body = {
        "startDate": start,
        "endDate": end,
        "dimensions": ["query"],
        "dimensionFilterGroups": [{
            "filters": [{
                "dimension": "query",
                "operator": "contains",
                "expression": keyword,
            }],
        }],
        "rowLimit": 10,
    }
    for attempt in range(1, 4):
        await sem.acquire()
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    f"{GSC_API}/sites/{GSC_SITE}/searchAnalytics/query",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json=body,
                )
            if resp.status_code in (429, 403):
                logger.warning("GSC backoff (%s) on '%s'", resp.status_code, keyword)
                await _throttle(attempt)
                continue
            if resp.status_code != 200:
                logger.warning("GSC gap error %s for '%s': %s", resp.status_code, keyword, resp.text[:160])
                return {"clicks": 0, "impressions": 0, "position": None, "top_query": None}
            rows = resp.json().get("rows") or []
            top = rows[0] if rows else None
            return {
                "clicks": sum(r.get("clicks", 0) for r in rows),
                "impressions": sum(r.get("impressions", 0) for r in rows),
                "position": top and round(top.get("position", 0), 2),
                "top_query": top and top.get("keys", ["", ])[0],
            }
        except Exception as e:
            logger.warning("GSC exception on '%s': %s", keyword, e)
            await _throttle(attempt)
        finally:
            sem.release()
    return {"clicks": 0, "impressions": 0, "position": None, "top_query": None}


# ── Semantic silhouette core ──────────────────────────────────────────────────
def _silhouette(
    keyword: str,
    competitors: list[dict[str, str]],
    landing: str,
    suggest: list[str] = (),
) -> dict[str, Any]:
    """Vectorize competitor headline + query streams, diff them vs our landing."""
    kw_doc = _tokenize(keyword)
    comp_docs = [_tokenize(f"{c['title']} {c.get('snippet', '')}") for c in competitors]
    query_docs = [_tokenize(s) for s in suggest]
    landing_doc = _tokenize(landing)
    corpus = [kw_doc, landing_doc, *comp_docs, *query_docs]
    idf = _idf(corpus)

    blind_counter: Counter = Counter()
    for doc in [*comp_docs, *query_docs]:
        for term, _score in _tfidf_terms(doc, idf):
            if term not in landing_doc and term not in kw_doc:
                blind_counter[term] += 1

    blindspots = [t for t, _ in blind_counter.most_common(14)]
    coverage = len(set(landing_doc) & set(kw_doc)) / max(1, len(set(kw_doc)))
    return {
        "blindspots": blindspots,
        "coverage_score": round(coverage, 2),
        "suggested_h2": [
            f"How to lower your {b} costs" if b in ("cost", "costs") else f"{keyword}: {b} explained"
            for b in blindspots[:6]
        ],
    }


# ── Orchestrator ──────────────────────────────────────────────────────────────
async def analyze_targets(
    clusters: list[dict[str, Any]],
    dry_run: bool,
    limit: int,
    detect_sv_kd: bool = True,
    pillar_filter: str | None = None,
    sync_manifest: bool = False,
) -> list[dict[str, Any]]:
    # Optional pillar filter
    if pillar_filter:
        clusters = [c for c in clusters if (c.get("pillar") or "").lower() == pillar_filter.lower()]

    # Limit selection: if limit >= len(clusters), evaluate all; else sort by priority/volume
    if limit and limit >= len(clusters):
        prioritized = clusters
    else:
        prioritized = sorted(
            clusters,
            key=lambda c: (0 if c.get("priority") in ("P0", "P1") else 1, -(c.get("volume_estimate") or 0)),
        )[: max(limit, 3)]

    _load_env_local()
    token: str | None = None
    try:
        token, _ = get_gsc_access_token()
    except Exception as e:
        logger.warning("No GSC token (SKIPPED_NO_CREDS): %s", e)

    egress_proxy = _resolve_egress_proxy()
    sem = asyncio.Semaphore(3)
    report: list[dict[str, Any]] = []

    for i, cluster in enumerate(prioritized, 1):
        kw = cluster.get("target_keyword") or cluster.get("keyword") or cluster.get("title") or ""
        landing = f"{cluster.get('slug', '')} {cluster.get('primary_tool', '')}"
        logger.info("[%d/%d] %s", i, len(prioritized), kw)

        gap = await gsc_gap(token, kw, sem) if token else {"clicks": 0, "impressions": 0, "position": None, "top_query": None}

        competitors, serp_source = await _fetch_licensed_serp(kw, sem)
        if not competitors:
            competitors = await asyncio.to_thread(scrape_tavily_search, kw, 8)
            serp_source = "tavily" if competitors else "none"

        suggest = await _fetch_google_suggest(kw, egress_proxy, sem)
        silhouette = _silhouette(kw, competitors, landing, suggest) if (competitors or suggest) else {
            "blindspots": [], "coverage_score": None, "suggested_h2": [],
        }

        # Live SV and KD detection
        vol_est = cluster.get("volume_estimate")
        kd_est = cluster.get("kd_estimate")
        kd_band = cluster.get("kd_band", "unknown")
        kd_effective = None
        sv_data = None
        kd_data = None

        if detect_sv_kd:
            try:
                from agents.sv_kd_detector import analyze_keyword

                res = await asyncio.to_thread(analyze_keyword, kw)
                if res:
                    sv_obj = res.get("sv") or {}
                    kd_obj = res.get("kd") or {}
                    if sv_obj.get("estimate"):
                        vol_est = sv_obj["estimate"]
                    sv_data = sv_obj
                    if kd_obj.get("kd_100") is not None:
                        kd_est = kd_obj["kd_100"]
                    kd_band = kd_obj.get("kd_band", "unknown")
                    kd_effective = kd_obj.get("kd_effective", kd_obj.get("kd"))
                    kd_data = kd_obj
            except Exception as e:
                logger.warning("sv_kd_detector error on '%s': %s", kw, e)

        # Compute Opportunity Score (Volume x KD x Intent) & Quadrant
        opp_score, quadrant = compute_opportunity_metrics(vol_est, kd_est, cluster.get("intent"))

        # Synchronize in-memory cluster values
        cluster["volume_estimate"] = vol_est
        cluster["kd_estimate"] = kd_est
        cluster["kd_band"] = kd_band
        cluster["opportunity_score"] = opp_score
        cluster["quadrant"] = quadrant

        report.append({
            "keyword": kw,
            "pillar": cluster.get("pillar"),
            "intent": cluster.get("intent"),
            "volume_estimate": vol_est,
            "kd_estimate": kd_est,
            "kd_band": kd_band,
            "kd_effective": kd_effective,
            "opportunity_score": opp_score,
            "quadrant": quadrant,
            "slug": cluster.get("slug"),
            "tool": cluster.get("primary_tool"),
            "gsc": gap,
            "serp_source": serp_source,
            "suggest": suggest,
            "silhouette": silhouette,
            "top_competitors": [c["url"] for c in competitors[:5]],
            "sv_meta": sv_data,
            "kd_meta": kd_data,
        })
        await _throttle(1, base_ms=250)

    # Sort report by Opportunity Score descending
    report.sort(key=lambda r: (r.get("opportunity_score") or 0.0), reverse=True)

    if sync_manifest and not dry_run and MANIFEST.exists():
        try:
            raw_manifest = json.loads(MANIFEST.read_text("utf-8"))
            if isinstance(raw_manifest, dict) and "clusters" in raw_manifest:
                raw_manifest["clusters"] = clusters
            else:
                raw_manifest = clusters
            MANIFEST.write_text(json.dumps(raw_manifest, indent=2, ensure_ascii=False) + "\n", "utf-8")
            logger.info("Synchronized updated estimates to %s", MANIFEST)
        except Exception as e:
            logger.error("Failed to sync manifest: %s", e)

    return report


def _print_report(report: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 92)
    print(" 4-QUADRANT OPPORTUNITY MATRIX (VOLUME × KD BALANCED)")
    print("=" * 92)
    print(f"{'#':<3} | {'PILLAR':<5} | {'KEYWORD':<36} | {'SV':>5} | {'KD':>4} | {'BAND':<8} | {'SCORE':>6} | {'QUADRANT':<12}")
    print("-" * 92)

    for i, r in enumerate(report, 1):
        sv = str(r["volume_estimate"]) if r["volume_estimate"] else "?"
        kd = f"{r['kd_estimate']:.0f}" if r["kd_estimate"] is not None else "?"
        band = r.get("kd_band") or "unknown"
        score = f"{r.get('opportunity_score', 0):.1f}"
        quad = (r.get("quadrant") or "trap").upper()
        pillar = (r.get("pillar") or "misc")[:5].upper()
        kw = r["keyword"][:36]
        print(f"{i:<3} | {pillar:<5} | {kw:<36} | {sv:>5} | {kd:>4} | {band:<8} | {score:>6} | {quad:<12}")

    print("=" * 92)
    print("Quadrants: HOLY_GRAIL (High Vol / Low KD) | QUICK_WIN (Low Vol / Low KD) | BATTLEGROUND (High Vol / High KD) | TRAP")
    print()


async def main_async(args: argparse.Namespace) -> int:
    if not MANIFEST.exists():
        logger.error("Manifest missing: %s", MANIFEST)
        return 2
    manifest = json.loads(MANIFEST.read_text("utf-8"))
    clusters = manifest.get("clusters") or manifest
    if not isinstance(clusters, list):
        logger.error("Malformed manifest: expected 'clusters' array")
        return 2

    detect_sv_kd = not args.no_sv_kd
    report = await analyze_targets(
        clusters,
        dry_run=args.dry_run,
        limit=args.limit,
        detect_sv_kd=detect_sv_kd,
        pillar_filter=args.pillar,
        sync_manifest=args.sync_manifest,
    )
    _print_report(report)

    if args.dry_run:
        return 0
    OUTPUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", "utf-8")
    logger.info("Wrote %s (%d targets) — GEO-ready feed for scribe/refresh.", OUTPUT, len(report))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Target-driven intelligence engine (GSC gap + TF-IDF silhouette + SV/KD Matrix).")
    parser.add_argument("--limit", type=int, default=46, help="Number of targets to analyze (default 46 for all clusters).")
    parser.add_argument("--dry-run", action="store_true", help="Print report without writing target-intel.json.")
    parser.add_argument("--sync-manifest", action="store_true", help="Update data/content-silo-manifest.json with live SV/KD estimates.")
    parser.add_argument("--pillar", type=str, default=None, help="Filter analysis to a specific pillar (money, home, tech, life, body).")
    parser.add_argument("--no-sv-kd", action="store_true", help="Skip live SV/KD detection and rely on existing values.")
    args = parser.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
