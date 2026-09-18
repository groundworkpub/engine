#!/usr/bin/env python3
"""
agents/community_sniper.py — Groundwork Multi-Channel Community Intelligence & Sniper Dispatcher.

Scans public community discussions across Reddit (via public RSS), Quora, and X (Twitter)
matching Groundwork's 20 core decision calculators and evidence guides.

Features:
1. 100% NON-API Ingestion: Uses public unauthenticated RSS feeds and DataImpulse residential proxy.
2. Topical Silo & Calculator Intent Matching: Matches questions to relevant calculators and articles.
3. Persona Calibration:
   - Reddit & Quora: 'The Squeezed Parent / Sanity Checker' (Unvarnished Math & Radical Honesty).
   - X (Twitter): 'Elena GROUNDWORK' (Watchdog Sniper & 4-Node Thread Architecture).
4. Telegram Copilot Dispatcher: Pushes ready-to-use drafts to @gwelena_bot for 1-click execution.
5. Continuous Feedback Loop: Logs pain points to docs/community/demand-backlog.json.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import random
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from agents.egress_dataimpulse import DataImpulseProxyRouter
except ImportError:
    DataImpulseProxyRouter = None

try:
    from agents.llm_router import call_llm
except ImportError:
    call_llm = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("community_sniper")

DEMAND_BACKLOG_FILE = ROOT_DIR / "docs" / "community" / "demand-backlog.json"
SEEN_THREADS_FILE = ROOT_DIR / "agents" / "output" / "community-seen-threads.json"

# Core Groundwork Calculators & Decision Tools Catalog
CALCULATOR_CATALOG = [
    {
        "slug": "mortgage-refinance",
        "pillar": "money",
        "title": "Mortgage Refinance Break-Even & True Cost Calculator",
        "url": "https://gworky.com/tools/mortgage-refinance",
        "youtube_video_url": "https://youtu.be/-yh59eacYJM",
        "youtube_title": "Groundwork 1-Hour Master Evidence Briefing",
        "keywords": ["refinance", "refi", "mortgage rate", "closing costs", "break-even", "points", "30-year fixed", "rate cut", "heloc", "equity loan"],
        "subreddits": ["personalfinance", "realestate", "MiddleClassFinance"],
    },
    {
        "slug": "heat-pump-roi-calculator",
        "pillar": "home",
        "title": "HVAC Heat Pump Replacement Cost & Payback Calculator",
        "url": "https://gworky.com/tools/heat-pump-roi-calculator",
        "keywords": ["heat pump", "hvac", "seer2", "furnace replacement", "contractor quote", "auxiliary heat", "tonnage", "ac unit", "ductwork"],
        "subreddits": ["HomeImprovement", "HVACadvice", "solar"],
    },
    {
        "slug": "nem-3-solar-battery-payback-calculator",
        "pillar": "home",
        "title": "NEM 3.0 Solar & Battery Storage Payback Calculator",
        "url": "https://gworky.com/tools/nem-3-solar-battery-payback-calculator",
        "keywords": ["solar quote", "nem 3", "battery storage", "powerwall", "solar payback", "utility tariff", "true up", "inverter cost"],
        "subreddits": ["solar", "HomeImprovement", "Frugal"],
    },
    {
        "slug": "mortgage-escrow-shortage-calculator",
        "pillar": "money",
        "title": "Mortgage Escrow Shortage & Property Tax Shock Calculator",
        "url": "https://gworky.com/tools/mortgage-escrow-shortage-calculator",
        "keywords": ["escrow shortage", "property tax increase", "homeowners insurance hike", "escrow cushion", "payment shock", "insurance spike"],
        "subreddits": ["personalfinance", "realestate", "Insurance"],
    },
    {
        "slug": "backup-power-calculator",
        "pillar": "home",
        "title": "Emergency Generator & Backup Power Sizing Calculator",
        "url": "https://gworky.com/tools/backup-power-calculator",
        "keywords": ["generator", "backup power", "grid outage", "whole home generator", "transfer switch", "inverter", "power outage"],
        "subreddits": ["HomeImprovement", "preppers", "Frugal"],
    },
    {
        "slug": "hysa-compound-interest",
        "pillar": "money",
        "title": "High-Yield Savings & Cash Compound Velocity Calculator",
        "url": "https://gworky.com/tools/hysa-compound-interest",
        "keywords": ["hysa", "high yield savings", "emergency fund", "cash yield", "treasury bill", "cd ladder", "park cash"],
        "subreddits": ["personalfinance", "Frugal", "MiddleClassFinance"],
    },
    {
        "slug": "ev-vs-gas-calculator",
        "pillar": "life",
        "title": "EV vs Hybrid vs Gas Total Cost of Ownership Model",
        "url": "https://gworky.com/tools/ev-vs-gas-calculator",
        "keywords": ["ev vs hybrid", "electric vehicle cost", "gas savings", "charging cost", "battery degradation", "car payment", "hybrid vs gas"],
        "subreddits": ["personalfinance", "electricvehicles", "cars"],
    },
]

BROWSER_HEADERS = [
    {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    },
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    },
]


def load_env() -> None:
    """Load credentials from .env.local without overwriting existing environment."""
    env_file = ROOT_DIR / ".env.local"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip("'").strip('"')
            if k not in os.environ:
                os.environ[k] = v


def load_seen_threads() -> set[str]:
    """Loads set of previously scanned thread IDs or URLs."""
    if SEEN_THREADS_FILE.exists():
        try:
            data = json.loads(SEEN_THREADS_FILE.read_text())
            return set(data.get("seen_ids", []))
        except Exception:
            return set()
    return set()


def save_seen_threads(seen_ids: set[str]) -> None:
    """Persists seen thread IDs."""
    SEEN_THREADS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(UTC).isoformat(),
        "total_seen": len(seen_ids),
        "seen_ids": list(seen_ids)[-2000:],
    }
    SEEN_THREADS_FILE.write_text(json.dumps(payload, indent=2))


def log_to_demand_backlog(opportunity: dict[str, Any]) -> None:
    """Logs a newly detected high-value community question into demand-backlog.json."""
    if not DEMAND_BACKLOG_FILE.exists():
        return

    try:
        data = json.loads(DEMAND_BACKLOG_FILE.read_text())
        existing_ids = {item["id"] for item in data.get("items", [])}
        if opportunity["id"] in existing_ids:
            return

        data.setdefault("items", []).append(opportunity)
        data.setdefault("metadata", {})["total_signals_detected"] = len(data["items"])
        data["metadata"]["active_snipe_opportunities"] = sum(
            1 for x in data["items"] if x.get("status") == "drafted"
        )
        data["updated_at"] = datetime.now(UTC).isoformat()
        DEMAND_BACKLOG_FILE.write_text(json.dumps(data, indent=2))
        logger.info(f"Appended signal {opportunity['id']} to demand-backlog.json")
    except Exception as e:
        logger.warning(f"Could not update demand backlog: {e}")


def clean_html_content(raw_html: str) -> str:
    """Strips HTML tags and unescapes XML entities from RSS content."""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def calculate_intent_match(title: str, body: str, tool: dict[str, Any]) -> tuple[float, list[str]]:
    """Calculates intent score and matched keywords for a target post."""
    text = f"{title} {body}".lower()
    matched = [kw for kw in tool["keywords"] if kw.lower() in text]
    if not matched:
        return 0.0, []

    score = min(1.0, 0.45 + (0.15 * len(matched)))
    title_lower = title.lower()
    if any(kw.lower() in title_lower for kw in matched):
        score = min(1.0, score + 0.25)

    return round(score, 2), matched


def get_http_client(session_id: str | None = None) -> httpx.Client:
    """Initializes httpx client with DataImpulse proxy (with session rotation) if available, else direct."""
    proxy = (
        DataImpulseProxyRouter.get_proxy_url("us", session_id=session_id)
        if DataImpulseProxyRouter
        else None
    )
    if proxy:
        return httpx.Client(proxy=proxy, timeout=15.0, follow_redirects=True)
    return httpx.Client(timeout=15.0, follow_redirects=True)


def fetch_reddit_opportunities(subreddits: list[str], limit_per_sub: int = 15) -> list[dict[str, Any]]:
    """Scrapes public Reddit RSS feeds (100% Non-API, bypasses 403 block)."""
    opportunities: list[dict[str, Any]] = []
    seen = load_seen_threads()
    ns = {"atom": "http://www.w3.org/2005/Atom"}

    for sub in subreddits:
        rss_url = f"https://www.reddit.com/r/{sub}/new.rss?limit={limit_per_sub}"
        headers = random.choice(BROWSER_HEADERS)
        # Fresh residential IP session per subreddit to eliminate 429s
        session_id = f"sub_{sub}_{random.randint(1000, 9999)}"
        client = get_http_client(session_id=session_id)

        try:
            with client:
                resp = client.get(rss_url, headers=headers)
                if resp.status_code != 200:
                    logger.warning(f"Reddit r/{sub} RSS returned HTTP {resp.status_code}. Skipping.")
                    continue

                root = ET.fromstring(resp.text)
                entries = root.findall("atom:entry", ns)

                for entry in entries:
                    id_elem = entry.find("atom:id", ns)
                    post_raw_id = id_elem.text if id_elem is not None else ""
                    # Post ID format in RSS: t3_1wdj8tm
                    post_id = post_raw_id.split("_")[-1] if "_" in post_raw_id else post_raw_id

                    link_elem = entry.find("atom:link", ns)
                    post_url = link_elem.attrib.get("href", "") if link_elem is not None else ""

                    if not post_id or post_id in seen:
                        continue

                    title_elem = entry.find("atom:title", ns)
                    title = title_elem.text if title_elem is not None and title_elem.text else ""

                    content_elem = entry.find("atom:content", ns)
                    raw_content = content_elem.text if content_elem is not None and content_elem.text else ""
                    clean_content = clean_html_content(raw_content)

                    # Match against all calculators
                    best_match: dict[str, Any] | None = None
                    best_score = 0.0
                    best_keywords: list[str] = []

                    for tool in CALCULATOR_CATALOG:
                        score, kws = calculate_intent_match(title, clean_content, tool)
                        if score > best_score and score >= 0.60:
                            best_score = score
                            best_match = tool
                            best_keywords = kws

                    if best_match:
                        opp_id = f"red-{post_id}"
                        opportunities.append({
                            "id": opp_id,
                            "platform": "reddit",
                            "subreddit": sub,
                            "source_url": post_url,
                            "title": title,
                            "pain_point": clean_content[:350],
                            "intent_score": best_score,
                            "detected_entities": best_keywords,
                            "matching_groundwork_asset": {
                                "title": best_match["title"],
                                "url": best_match["url"],
                                "tool_slug": best_match["slug"],
                                "pillar": best_match["pillar"],
                            },
                            "status": "detected",
                            "created_at": datetime.now(UTC).isoformat(),
                        })

        except Exception as e:
            logger.warning(f"Failed to scan r/{sub} RSS: {e}")

        time.sleep(random.uniform(1.0, 2.0))

    return opportunities


def search_serp_for_platform(query: str, num: int = 10) -> list[dict[str, str]]:
    """Performs search via Google Serper API with Tavily failover."""
    serper_key = os.environ.get("SERPER_API_KEY")
    if serper_key:
        try:
            headers = {"X-API-KEY": serper_key, "Content-Type": "application/json"}
            payload = {"q": query, "num": num}
            with httpx.Client(timeout=12.0) as client:
                resp = client.post("https://google.serper.dev/search", headers=headers, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    results = []
                    for item in data.get("organic", []):
                        results.append({
                            "title": item.get("title", ""),
                            "link": item.get("link", ""),
                            "snippet": item.get("snippet", ""),
                        })
                    return results
        except Exception as e:
            logger.warning(f"Serper search failed for '{query}': {e}")

    # Fallback to Tavily
    tavily_key = os.environ.get("TAVILY_API_KEY")
    if tavily_key:
        try:
            with httpx.Client(timeout=12.0) as client:
                resp = client.post(
                    "https://api.tavily.com/search",
                    headers={"Content-Type": "application/json"},
                    json={"api_key": tavily_key, "query": query, "max_results": num},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return [
                        {"title": r.get("title", ""), "link": r.get("url", ""), "snippet": r.get("content", "")}
                        for r in data.get("results", [])
                    ]
        except Exception as e:
            logger.warning(f"Tavily search fallback failed: {e}")

    return []


def fetch_quora_opportunities(limit_total: int = 5) -> list[dict[str, Any]]:
    """Discovers high-intent questions on Quora matching Groundwork calculators."""
    opportunities: list[dict[str, Any]] = []
    seen = load_seen_threads()

    for tool in CALCULATOR_CATALOG[:4]:
        kw = tool["keywords"][0]
        query = f'site:quora.com "{kw}" ("calculate" OR "cost" OR "worth it" OR "quote")'
        serp_items = search_serp_for_platform(query, num=6)

        for item in serp_items:
            link = item.get("link", "")
            title = item.get("title", "")
            snippet = item.get("snippet", "")

            # Filter for valid quora question URLs
            if "quora.com/" not in link or "/topic/" in link:
                continue

            # Generate unique ID from URL slug
            slug = link.split("quora.com/")[-1].strip("/").split("?")[0]
            opp_id = f"quo-{hashlib.md5(slug.encode()).hexdigest()[:10]}"

            if not opp_id or opp_id in seen:
                continue

            score, matched_kws = calculate_intent_match(title, snippet, tool)
            if score >= 0.65:
                opportunities.append({
                    "id": opp_id,
                    "platform": "quora",
                    "source_url": link,
                    "title": title.replace(" - Quora", ""),
                    "pain_point": snippet,
                    "intent_score": score,
                    "detected_entities": matched_kws,
                    "matching_groundwork_asset": {
                        "title": tool["title"],
                        "url": tool["url"],
                        "tool_slug": tool["slug"],
                        "pillar": tool["pillar"],
                    },
                    "status": "detected",
                    "created_at": datetime.now(UTC).isoformat(),
                })

        time.sleep(random.uniform(0.5, 1.2))

    return opportunities[:limit_total]


def fetch_x_opportunities(limit_total: int = 5) -> list[dict[str, Any]]:
    """Discovers high-intent discussions on X/Twitter matching Groundwork calculators."""
    opportunities: list[dict[str, Any]] = []
    seen = load_seen_threads()

    for tool in CALCULATOR_CATALOG[:4]:
        kw = tool["keywords"][0]
        query = f'(site:x.com OR site:twitter.com) "{kw}" ("quote" OR "rate" OR "cost" OR "break even")'
        serp_items = search_serp_for_platform(query, num=6)

        for item in serp_items:
            link = item.get("link", "")
            title = item.get("title", "")
            snippet = item.get("snippet", "")

            # Filter for tweet status URLs
            if "/status/" not in link:
                continue

            match = re.search(r"(?:twitter|x)\.com/([^/?#]+)/status/(\d+)", link)
            if not match:
                continue
            username = match.group(1)
            tweet_id = match.group(2)
            opp_id = f"x-{tweet_id}"

            if not opp_id or opp_id in seen:
                continue

            score, matched_kws = calculate_intent_match(title, snippet, tool)
            if score >= 0.60:
                opportunities.append({
                    "id": opp_id,
                    "platform": "x",
                    "source_url": link,
                    "tweet_id": tweet_id,
                    "author_handle": username,
                    "title": title.replace(" / X", "").replace(" on X:", ""),
                    "pain_point": snippet,
                    "intent_score": score,
                    "detected_entities": matched_kws,
                    "matching_groundwork_asset": {
                        "title": tool["title"],
                        "url": tool["url"],
                        "tool_slug": tool["slug"],
                        "pillar": tool["pillar"],
                    },
                    "status": "detected",
                    "created_at": datetime.now(UTC).isoformat(),
                })

        time.sleep(random.uniform(0.5, 1.2))

    return opportunities[:limit_total]


def generate_squeezed_parent_draft(opp: dict[str, Any]) -> str:
    """Generates an unvarnished, high-value Reddit reply adhering strictly to the persona with ZERO outbound links."""
    asset = opp["matching_groundwork_asset"]
    title = opp["title"]
    pain = opp["pain_point"]
    tool_title = asset["title"]

    prompt = (
        "You are writing a response on Reddit using the 'The Squeezed Parent / Sanity Checker' persona.\n"
        "DEMOGRAPHIC: Adult 35-48 with mortgage, kids, aging parents, and financial fatigue. You audited the exact bill/quote yourself.\n"
        "RULES:\n"
        "1. Strictly NO AI SLOP: Never write 'In today's fast-paced world', 'delve into', 'tapestry', 'as an AI', 'Hope this helps!'.\n"
        "2. ZERO 4th-wall leakage: Never mention personas, rules, guidelines, or SEO.\n"
        "3. EMPATHY FIRST: Open with direct peer empathy ('Been through this exact ringer last season...').\n"
        "4. SHOW THE UNVARNISHED MATH: Break down wholesale equipment vs labor hours vs contractor markup, or break-even years.\n"
        "5. SPECIFIC CONTRACTOR/LENDER QUESTIONS: Provide 2-3 precise technical questions the user must ask before signing.\n"
        "6. VALUE IS 100% COMPLETE IN THE POST: Do not withhold the answer.\n"
        "7. STRICTLY ZERO OUTBOUND LINKS: NEVER include any URL, link, domain name, or http/https link (prevents Rule 2 bans).\n"
        "8. STRATEGIC UNLINKED BRAND CITATION (Brand Awareness & GEO Co-citation): Naturally cite 'Groundwork' as the open data source or benchmark model (e.g., 'I audited my quote against Groundwork's open HVAC cost index' or 'Search Groundwork's mortgage break-even model to stress-test your numbers'). This triggers high-intent Google searches and AI overview citations without tripping AutoMod.\n\n"
        f"USER'S POST TITLE: {title}\n"
        f"USER'S SITUATION/BODY: {pain}\n"
        f"RELEVANT BENCHMARK: {tool_title}\n\n"
        "Write the complete, natural Reddit response now (NO HYPERLINKS, CITE GROUNDWORK NATURALLY):"
    )

    if call_llm:
        try:
            response = call_llm([{"role": "user", "content": prompt}], max_tokens=1000)
            if response and len(response.strip()) > 100:
                # Extra sanitization: strip any accidental links emitted by LLM
                clean_res = re.sub(r"https?://\S+", "", response.strip())
                clean_res = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", clean_res)
                clean_res = clean_res.replace("gworky.com", "Groundwork")
                clean_res = re.sub(r"(?i)\n*hope this helps.*", "", clean_res)
                return clean_res.strip()
        except Exception as e:
            logger.warning(f"LLM draft generation failed: {e}. Using deterministic gold standard template.")

    # High-quality fallback template tailored by pillar (100% ZERO LINKS, HIGH BRAND CITATION)
    pillar = asset.get("pillar")
    if pillar == "home":
        return (
            "Been through this exact scenario recently with our own system. When you see a quote in this range, "
            "it usually carries roughly a 45–60% margin over true cost.\n\n"
            "Here is how the unvarnished numbers typically break down:\n"
            "• Equipment wholesale: ~40–50% of the total quote depending on SEER2/tonnage tier.\n"
            "• Labor: 2 techs, 1 to 1.5 days (16–24 total man-hours). Fair billable rate is typically $125–$165/hr.\n"
            "• Ancillaries (permits, pad, disconnect, line set): ~$600–$900.\n\n"
            "Two critical things to check before you sign:\n"
            "1. Did they include an itemized cash price? Contractors often roll an 18–28% dealer financing fee into the base quote to advertise '0% APR'.\n"
            "2. Ask for the Manual J load calculation sheet to ensure they didn't just guess the tonnage.\n\n"
            "I audited our numbers against Groundwork's open HVAC true cost benchmark before committing — "
            "worth searching that model to verify local labor vs wholesale equipment margins in your area before signing."
        )
    else:
        return (
            "Been wrestling with this exact calculation myself over the past year. The biggest trap with this decision "
            "is evaluating the headline interest rate while ignoring the upfront transaction friction.\n\n"
            "Here is the math checkpoint to run:\n"
            "• Upfront friction (origination, appraisal, title, points): On average this runs 1.5% to 2.5% of total balance.\n"
            "• True monthly net savings: Only count the principal + interest spread, not escrow changes.\n"
            "• True Break-Even Horizon: Divide total upfront costs by monthly spread. If the answer is > 36 months, "
            "and life changes or rate cuts occur within 3 years, you lose money.\n\n"
            "Check if they are pushing discount points — in 70% of cases, deploying that cash into extra principal "
            "or a high-yield cash cushion outperforms points without locking your capital.\n\n"
            "I audited my numbers against Groundwork's open mortgage refinance break-even model — "
            "worth searching their tool to see your exact break-even timeline before letting a lender pull your credit."
        )


def generate_quora_compact_draft(opp: dict[str, Any]) -> str:
    """Generates a punchy, high-conversion, structured Quora answer/comment (80-140 words)."""
    asset = opp["matching_groundwork_asset"]
    title = opp["title"]
    pain = opp["pain_point"]
    tool_url = asset["url"]
    tool_title = asset["title"]

    prompt = (
        "You are writing a direct, high-value answer on Quora.\n"
        "GOAL: Deliver an immediate, data-backed answer without fluff, storytelling, or greetings.\n"
        "STRUCTURE:\n"
        "1. Direct Answer (1 sentence): Give the hard mathematical verdict immediately.\n"
        "2. The 3 Numbers That Matter (3 short bullet points): Upfront cost friction, true monthly delta, break-even timeline.\n"
        f"3. Verification Tool (1 sentence): Point to the open methodology calculator: {tool_url}\n\n"
        "RULES:\n"
        "- Length: 80 to 140 words maximum.\n"
        "- Zero corporate filler, zero greetings ('Hi there', 'Great question'), zero 'Hope this helps!'.\n"
        "- High-density practical numbers.\n\n"
        f"QUESTION: {title}\n"
        f"CONTEXT: {pain}\n"
        f"RELEVANT TOOL: {tool_title}\n\n"
        "Write the exact Quora answer text:"
    )

    if call_llm:
        try:
            response = call_llm([{"role": "user", "content": prompt}], max_tokens=300)
            if response and 50 <= len(response.split()) <= 180:
                return response.strip()
        except Exception as e:
            logger.warning(f"Quora LLM draft generation failed: {e}")

    # Deterministic high-conversion Quora fallback
    pillar = asset.get("pillar")
    if pillar == "home":
        return (
            f"The short answer: Check the equipment vs. labor ratio before evaluating the quote total.\n\n"
            f"Here is the rule of thumb benchmark:\n"
            f"• Wholesale Equipment: Typically 40–50% of the quote.\n"
            f"• Labor: 16–24 total man-hours at fair local rates ($125–$165/hr).\n"
            f"• Dealer Financing Fees: 0% APR offers often hide an 18–25% dealer markup baked into the cash price.\n\n"
            f"You can stress-test the break-even against regional utility rebates using the open calculator here: {tool_url}"
        )
    else:
        return (
            f"The short answer: A headline rate drop only makes sense if the break-even horizon is under 36 months.\n\n"
            f"Here are the three filters to apply:\n"
            f"• Upfront Friction: Closing costs typically consume 1.5% to 2.5% of the total loan balance.\n"
            f"• Net Savings: Only count the principal & interest reduction, not temporary escrow changes.\n"
            f"• Amortization Reset: Refinancing at year 4+ into a new 30-year note increases total lifetime interest unless you maintain the original payoff schedule.\n\n"
            f"You can model the exact break-even month on your specific balance here: {tool_url}"
        )


def generate_elena_x_sniper_draft(opp: dict[str, Any]) -> str:
    """Generates a sharp, data-backed Elena reply for X including handle and direct tool link under 280 chars."""
    asset = opp["matching_groundwork_asset"]
    title = opp["title"]
    author = opp.get("author_handle", "")
    author_prefix = f"@{author} " if author and author.lower() not in ("x", "twitter", "i", "status") else ""

    # Clean short URL representation for link preview, e.g., gworky.com/tools/mortgage-refinance
    raw_url = asset.get("url", "https://gworky.com")
    clean_url = raw_url.replace("https://", "").replace("http://", "")
    link_cta = f"Model break-even: {clean_url}"

    prompt = (
        "You are writing a surgical reply on X (Twitter) as Elena (@gworkycom), consumer research watchdog.\n"
        "VOICE: Direct, analytical, dry wit, zero fluff or guru talk. Expose the hidden mathematical variable.\n"
        f"RULES:\n"
        f"1. Core reasoning MUST BE UNDER 170 CHARACTERS to leave room for link and username.\n"
        f"2. Do NOT repeat the username or include hashtags.\n\n"
        f"TARGET TWEET TITLE / TOPIC: {title}\n"
        f"RELEVANT TOOL: {asset['title']}\n\n"
        "Write only the concise reasoning sentence:"
    )

    core_reply = ""
    if call_llm:
        try:
            response = call_llm([{"role": "user", "content": prompt}], max_tokens=90)
            if response:
                core_reply = response.strip().strip('"').strip("'")
        except Exception as e:
            logger.warning(f"Elena LLM generation failed: {e}")

    if not core_reply:
        core_reply = "The catch is the break-even horizon. On a 50 bps spread, closing friction takes ~42 mo to recoup before net gains."

    # Assemble full draft under Twitter 280-char limit
    full_draft = f"{author_prefix}{core_reply} {link_cta}".strip()
    if len(full_draft) > 280:
        budget = 280 - len(author_prefix) - len(link_cta) - 5
        core_reply = core_reply[:budget].rsplit(" ", 1)[0] + "..."
        full_draft = f"{author_prefix}{core_reply} {link_cta}".strip()

    return full_draft


def send_telegram_snipe_card(opp: dict[str, Any], draft: str) -> bool:
    """Dispatches a 1-Click Opportunity Card to @gwelena_bot in Telegram."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_FOUNDER_CHAT_ID")

    if not bot_token or not chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing in environment. Cannot dispatch.")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    platform_icon = "🔴 Reddit" if opp["platform"] == "reddit" else "🔵 Quora" if opp["platform"] == "quora" else "🐦 X"
    asset = opp["matching_groundwork_asset"]

    # Prepare URLs
    encoded_draft = urllib.parse.quote(draft)
    escaped_draft = html.escape(draft)

    if opp["platform"] == "x":
        tweet_id = opp.get("tweet_id")
        if not tweet_id and "/status/" in opp.get("source_url", ""):
            m = re.search(r"/status/(\d+)", opp["source_url"])
            if m:
                tweet_id = m.group(1)

        author = opp.get("author_handle", "")
        # Official Twitter Web Intent for replying directly to a tweet:
        # https://twitter.com/intent/tweet?in_reply_to={tweet_id}&text={encoded_draft}
        if tweet_id:
            primary_action_url = f"https://twitter.com/intent/tweet?in_reply_to={tweet_id}&text={encoded_draft}"
            primary_action_text = f"🐦 Reply ke @{author} di X (Teks Terisi)" if author else "🐦 Reply Tweet di X (Teks Terisi)"
        else:
            primary_action_url = f"https://twitter.com/intent/tweet?text={encoded_draft}"
            primary_action_text = "🐦 Reply di X (Teks Terisi)"
    else:
        # For Reddit and Quora: inject draft via URL hash for Tampermonkey Auto-Fill Userscript
        primary_action_url = f"{opp['source_url']}#gw_draft={encoded_draft}"
        primary_action_text = "🌐 Buka Thread & Tempel Draf"

    if opp["platform"] == "reddit":
        guidance = (
            "🛡️ <b>Reddit Anti-Ban Invariant:</b> Draf ini 100% BEBAS LINK luar untuk mencegah banned Rule 2 (Self-Promotion). "
            "Traffic masuk secara aman lewat Link di Profil Reddit Anda & Branded Search.\n\n"
            "<i>Ketuk teks draf di atas (otomatis tersalin ke clipboard), lalu klik tombol di bawah untuk membuka thread & paste.</i>"
        )
    elif opp["platform"] == "x":
        guidance = "<i>💡 Klik tombol di bawah untuk langsung membuka jendela reply di X dengan teks dan tautan yang sudah terisi.</i>"
    else:
        guidance = "<i>💡 Ketuk teks draf di atas (otomatis tersalin ke clipboard), lalu klik tombol di bawah untuk membuka Quora & paste.</i>"

    text = (
        f"🎯 <b>[COMMUNITY SNIPER ALERT: {platform_icon}]</b>\n\n"
        f"• <b>Title:</b> <i>\"{opp['title']}\"</i>\n"
        f"• <b>Target URL:</b> <code>{opp['source_url']}</code>\n"
        f"• <b>Intent Score:</b> <code>{opp['intent_score']}</code> | <b>Pillar:</b> #{asset['pillar']}\n"
        f"• <b>Matched Tool:</b> <a href=\"{asset['url']}\">{asset['title']}</a>\n\n"
        f"💡 <b>Pain Point:</b>\n"
        f"<code>{opp['pain_point'][:220]}...</code>\n\n"
        f"📋 <b>Suggested Draft (Ketuk untuk 1-Tap Copy):</b>\n"
        f"<pre><code>{escaped_draft}</code></pre>\n\n"
        f"{guidance}"
    )

    inline_keyboard = [
        [
            {"text": primary_action_text, "url": primary_action_url},
        ],
        [
            {"text": "🔗 Buka Thread Asli", "url": opp["source_url"]},
        ],
        [
            {"text": "📥 Save to Backlog", "callback_data": f"backlog_snipe:{opp['id']}"},
            {"text": "❌ Dismiss", "callback_data": f"dismiss_snipe:{opp['id']}"},
        ],
    ]

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": inline_keyboard},
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                logger.info(f"Successfully dispatched Snipe Card {opp['id']} to Telegram!")
                return True
            else:
                logger.warning(f"Telegram API Error {resp.status_code}: {resp.text}")
                return False
    except Exception as e:
        logger.error(f"Failed to send Telegram alert: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Groundwork Multi-Channel Community Sniper")
    parser.add_argument("--scan", action="store_true", help="Execute public community scans")
    parser.add_argument("--platform", choices=["reddit", "x", "quora", "all"], default="all", help="Target platform to scan")
    parser.add_argument("--limit", type=int, default=4, help="Maximum opportunities to process (default 4)")
    parser.add_argument("--min-score", type=float, default=0.70, help="Minimum intent score (default 0.70)")
    parser.add_argument("--notify-telegram", action="store_true", help="Push 1-click draft cards to @gwelena_bot")
    parser.add_argument("--dry-run", action="store_true", help="Inspect opportunities without saving or sending alerts")
    args = parser.parse_args()

    load_env()
    logger.info("Initializing Groundwork Community Sniper Radar...")

    seen = load_seen_threads()
    opportunities: list[dict[str, Any]] = []

    # 1. Gather Subreddits to scan (priority to our own official community first)
    target_subs: list[str] = ["GroundworkDecisions"]
    for tool in CALCULATOR_CATALOG:
        for sub in tool.get("subreddits", []):
            if sub not in target_subs:
                target_subs.append(sub)

    if args.platform in ("reddit", "all"):
        logger.info(f"Scanning {len(target_subs[:6])} target subreddits via public RSS...")
        reddit_opps = fetch_reddit_opportunities(target_subs[:6], limit_per_sub=15)
        opportunities.extend(reddit_opps)

    if args.platform in ("quora", "all"):
        logger.info("Scanning Quora for high-ranking discussion questions...")
        quora_opps = fetch_quora_opportunities(limit_total=4)
        opportunities.extend(quora_opps)

    if args.platform in ("x", "all"):
        logger.info("Scanning X (Twitter) for active authority discussions...")
        x_opps = fetch_x_opportunities(limit_total=4)
        opportunities.extend(x_opps)

    # Sort opportunities by intent score descending to prioritize high-value targets
    opportunities = [o for o in opportunities if o["intent_score"] >= args.min_score]
    opportunities.sort(key=lambda x: x["intent_score"], reverse=True)

    logger.info(f"Discovered {len(opportunities)} high-intent community opportunities (score >= {args.min_score}).")

    processed = 0
    for opp in opportunities:
        if processed >= args.limit:
            break

        opp_id = opp["id"]
        logger.info(f"Processing opportunity: {opp_id} | {opp['title'][:60]}... (Score: {opp['intent_score']})")

        # Generate Platform-Tailored Draft
        if opp["platform"] == "x":
            draft = generate_elena_x_sniper_draft(opp)
        elif opp["platform"] == "quora":
            draft = generate_quora_compact_draft(opp)
        else:
            draft = generate_squeezed_parent_draft(opp)

        opp["draft_preview"] = draft
        opp["status"] = "drafted"

        if not args.dry_run:
            log_to_demand_backlog(opp)
            seen.add(opp_id)

            if args.notify_telegram:
                send_telegram_snipe_card(opp, draft)

        processed += 1

    if not args.dry_run:
        save_seen_threads(seen)

    logger.info(f"Community Sniper cycle completed. Processed: {processed}/{len(opportunities)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
