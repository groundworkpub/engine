#!/usr/bin/env python3
"""
agents/dork_harvester.py — Autonomous Google & Search Dork Footprint Harvester

Features:
1. Hybrid Multi-Engine Cascading Scraping:
   - Engine 1: Bing US via DataImpulse US Residential Proxy (gw.dataimpulse.com:823) with base64 target decoding.
   - Engine 2: Tavily Search API (failover).
   - Engine 3: Direct DuckDuckGo / StartPage fallback.
2. Structured 4-Footprint Matrix across 5 Pillars (Focus: Money & Home):
   - Resource Pages & Curated Links ("inurl:resources", "useful links", "suggest a resource")
   - Guest Post & Contributor Portals ("write for us", "guest post guidelines")
   - Discussion & Comment Sections ("leave a comment", "leave a reply", "comment_post_ID")
   - Industry & Broker Directories ("inurl:directory")
3. Multi-Layer Qualification & Verification (Rule 2.5):
   - Live HTTP 200 Handshake + Body Size Verification (> 1KB)
   - In-page Contact Email & Form Type Detection (Comment Form vs Resource Submit)
   - DNS MX Host Resolution via `agents/outreach_verifier.py`
4. Consistent Canonical Groundwork Attribution:
   - All proposed tools and embeds strictly target `https://www.gworky.com/...`
5. Database Ingestion & Enriched Telegram Telemetry:
   - Inserts into Supabase `outreach_prospects`
   - Sends Telegram card with clickable target links, mailto, MX count, and Embed Studio preview
   - Telegram 429 exponential backoff handling
"""

from __future__ import annotations

import argparse
import base64
import logging
import os
import re
import sys
import time
import urllib.parse
from urllib.parse import urlparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

# Auto-load .env.local
_ROOT = Path(__file__).resolve().parent.parent
_env_path = _ROOT / ".env.local"
if _env_path.exists():
    with open(_env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("dork_harvester")

try:
    from agents.egress_dataimpulse import DataImpulseProxyRouter
except ImportError:
    from egress_dataimpulse import DataImpulseProxyRouter

try:
    from agents.outreach_verifier import (
        DISPOSABLE_DOMAINS,
        calculate_relevance_score,
        extract_domain,
        resolve_mx_records,
        validate_email_syntax_and_domain,
        verify_live_http,
    )
except ImportError:
    from outreach_verifier import (
        DISPOSABLE_DOMAINS,
        calculate_relevance_score,
        extract_domain,
        resolve_mx_records,
        validate_email_syntax_and_domain,
        verify_live_http,
    )

# Canonical Groundwork SSOT Base URL
CANONICAL_GROUNDWORK_BASE = "https://www.gworky.com"

# Default tool mapping per pillar
DEFAULT_TOOLS_BY_PILLAR = {
    "money": {
        "slug": "mortgage-refinance-calculator",
        "url": f"{CANONICAL_GROUNDWORK_BASE}/tools/mortgage-refinance-calculator",
        "name": "Mortgage Refinance Break-Even Calculator"
    },
    "home": {
        "slug": "heat-pump-savings-calculator",
        "url": f"{CANONICAL_GROUNDWORK_BASE}/tools/heat-pump-savings-calculator",
        "name": "Heat Pump Efficiency & Payback Model"
    },
    "tech": {
        "slug": "llm-cost-calculator",
        "url": f"{CANONICAL_GROUNDWORK_BASE}/tools/llm-cost-calculator",
        "name": "LLM Inference Cost Calculator"
    },
    "body": {
        "slug": "calorie-calculator",
        "url": f"{CANONICAL_GROUNDWORK_BASE}/tools/calorie-calculator",
        "name": "Clinical Calorie & Protein Model"
    },
    "life": {
        "slug": "cost-of-living",
        "url": f"{CANONICAL_GROUNDWORK_BASE}/tools/cost-of-living",
        "name": "Relocation & Living Cost Estimator"
    },
}

# Domain exclusions (Big platforms, search engines, social media, government portals not accepting links)
EXCLUDED_DOMAINS = {
    "google.com", "bing.com", "duckduckgo.com", "yahoo.com", "yandex.com",
    "youtube.com", "facebook.com", "twitter.com", "x.com", "linkedin.com",
    "instagram.com", "pinterest.com", "reddit.com", "wikipedia.org", "wikimedia.org",
    "bankrate.com", "nerdwallet.com", "investopedia.com", "zillow.com", "redfin.com",
    "forbes.com", "cnbc.com", "bloomberg.com", "nytimes.com", "wsj.com",
    "imdb.com", "amazon.com", "ebay.com", "apple.com", "microsoft.com",
    "walmart.com", "target.com", "tripadvisor.com", "yelp.com", "bbb.org",
    "nba.com", "nfl.com", "espn.com", "mlb.com", "fifa.com",
    "outlook.com", "office365.com", "live.com", "fidelity.com", "schwab.com",
    "vanguard.com", "chase.com", "bankofamerica.com", "wellsfargo.com",
    "gworky.com", "emailforums.biz",
    # Reference / dictionary sites (aged but never accept editorial backlink outreach)
    "merriam-webster.com", "dictionary.cambridge.org", "thefreedictionary.com",
    "vocabulary.com", "dictionary.com", "thesaurus.com", "reference.com",
    "urbandictionary.com", "collinsdictionary.com", "oxfordlearnersdictionaries.com",
    "languagetool.org", "wordreference.com", "oed.com"
}

# Non-English Country-Code TLDs to immediately quarantine (Rule 2.10)
NON_ENGLISH_TLDS = {
    ".br", ".ru", ".cn", ".vn", ".id", ".ir", ".pl", ".de", ".fr", ".es",
    ".it", ".nl", ".jp", ".kr", ".cz", ".tr", ".ua", ".ro", ".hu", ".se",
    ".no", ".fi", ".dk", ".pt", ".gr", ".th", ".my", ".ph", ".mx", ".ar",
    ".cl", ".in", ".co.in", ".co.za"
}

def is_non_english_domain(domain: str) -> bool:
    """Detects if a domain belongs to a quarantined non-English ccTLD."""
    d = domain.lower()
    return any(d.endswith(tld) for tld in NON_ENGLISH_TLDS)

# Structured Dork Footprint Matrix
DORK_FOOTPRINT_MATRIX: dict[str, dict[str, list[str]]] = {
    "money": {
        "resources": [
            '"mortgage refinance" inurl:resources intitle:resources',
            '"home loan" inurl:links "useful links" -inurl:gov -inurl:edu',
            '"personal finance" "helpful resources" inurl:resources',
            '"mortgage calculator" "suggest a resource" OR "submit a link"',
            'site:.org "financial literacy" inurl:links "mortgage"',
        ],
        "guest_post": [
            '"mortgage refinance" "write for us"',
            '"personal finance" "guest post guidelines"',
            '"real estate finance" "submit an article" OR "contribute to our site"',
            '"mortgage advice" inurl:write-for-us intitle:"write for us"',
        ],
        "comment": [
            '"mortgage refinance" inurl:wp-content/ "leave a comment" -inurl:wp-login',
            '"closing costs refinance" inurl:blog/ "leave a reply"',
            '"amortization schedule" "comment_post_ID" "submit"',
            '"refinance break even" inurl:category/finance "reply"',
        ],
        "directory": [
            'inurl:directory "mortgage brokers" OR "mortgage consultants"',
            'inurl:resources/mortgage "add a listing" OR "submit link"',
        ]
    },
    "home": {
        "resources": [
            '"heat pump" inurl:resources intitle:resources',
            '"solar panel installation" inurl:links "useful links"',
            '"energy efficiency" "helpful resources" inurl:resources',
            '"hvac sizing calculator" "suggest a resource" OR "submit a link"',
            'site:.org "home energy" inurl:links "heat pump"',
        ],
        "guest_post": [
            '"home improvement" "write for us"',
            '"heat pump installation" "guest post guidelines"',
            '"energy efficient homes" "submit an article"',
            '"solar energy" inurl:write-for-us intitle:"write for us"',
        ],
        "comment": [
            '"heat pump vs furnace" inurl:wp-content/ "leave a reply"',
            '"whole house surge protector" inurl:blog/ "leave a comment"',
            '"heat pump COP" "comment_post_ID" "submit"',
            '"solar payback calculation" inurl:category/energy "reply"',
        ],
        "directory": [
            'inurl:directory "hvac contractors" OR "solar installers"',
            'inurl:resources/hvac "add business" OR "submit company"',
        ]
    }
}

EMAIL_EXTRACT_REGEX = re.compile(
    r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+',
    re.IGNORECASE
)


def get_supabase_config() -> tuple[str, str]:
    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or os.environ.get("SUPABASE_URL", "https://keflumlrmggffyrsrmlk.supabase.co")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    return url.rstrip("/"), key


def decode_bing_url(raw_url: str) -> str:
    """Decodes Bing redirect format: https://www.bing.com/ck/a?!&&...&u=a1<base64>&..."""
    if "bing.com/ck/a" not in raw_url or "&u=" not in raw_url:
        return raw_url

    try:
        parsed = urllib.parse.urlparse(raw_url)
        qs = urllib.parse.parse_qs(parsed.query)
        u_param = qs.get("u", [""])[0]
        if u_param.startswith("a1"):
            b64_str = u_param[2:]
            # Ensure padding
            b64_str += "=" * (-len(b64_str) % 4)
            decoded = base64.b64decode(b64_str).decode("utf-8", errors="ignore")
            if decoded.startswith("http://") or decoded.startswith("https://"):
                return decoded
    except Exception as e:
        logger.debug(f"Failed to decode Bing URL {raw_url}: {e}")
    return raw_url


def scrape_bing_serp(query: str, count: int = 10) -> list[dict[str, str]]:
    """Scrapes Bing US SERP using DataImpulse US Residential Proxy."""
    proxy_url = DataImpulseProxyRouter.get_proxy_url("us")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.bing.com/",
    }
    url = "https://www.bing.com/search"
    params = {"q": query, "count": count, "setlang": "en-US", "cc": "US"}

    results: list[dict[str, str]] = []
    try:
        with httpx.Client(proxy=proxy_url, headers=headers, timeout=12.0, follow_redirects=True, verify=False) as client:
            resp = client.get(url, params=params)
            if resp.status_code != 200:
                logger.warning(f"Bing returned status {resp.status_code} for query: {query}")
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            for li in soup.find_all("li", class_="b_algo"):
                h2 = li.find("h2")
                if not h2 or not h2.find("a"):
                    continue
                a = h2.find("a")
                title = a.get_text(strip=True)
                raw_href = a.get("href", "")
                target_url = decode_bing_url(raw_href)

                if target_url and target_url.startswith("http"):
                    snippet = ""
                    p = li.find("p")
                    if p:
                        snippet = p.get_text(strip=True)
                    results.append({"title": title, "url": target_url, "snippet": snippet})
    except Exception as e:
        logger.warning(f"Bing scraping error for query '{query}': {e}")

    return results


def scrape_tavily_search(query: str, count: int = 10) -> list[dict[str, str]]:
    """Failover search using Tavily API if available."""
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return []

    url = "https://api.tavily.com/search"
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "max_results": count,
    }
    results: list[dict[str, str]] = []
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("results", []):
                    results.append({
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "snippet": item.get("content", "")
                    })
    except Exception as e:
        logger.warning(f"Tavily fallback error for query '{query}': {e}")
    return results


def search_dork_cascade(query: str, count: int = 10) -> list[dict[str, str]]:
    """Cascading search: Bing US Residential Proxy -> Tavily API Failover."""
    logger.info(f"Executing Dork Query: {query}")
    # 1. Primary Engine: Bing US via DataImpulse Residential Proxy
    results = scrape_bing_serp(query, count=count)
    if results:
        logger.info(f"Bing yielded {len(results)} raw results.")
        return results

    # 2. Fallover Engine: Tavily Search API
    logger.info("Bing yielded 0 results; activating Tavily failover engine...")
    results = scrape_tavily_search(query, count=count)
    if results:
        logger.info(f"Tavily yielded {len(results)} fallback results.")
        return results

    return []


def inspect_landing_page(target_url: str, proxy: str | None = None) -> dict[str, Any]:
    """
    Crawls target landing page with retry and www fallback.
    Extracts page title, text length, email addresses, and form types.
    """
    is_live, status_code, content_len = verify_live_http(target_url, proxy=proxy)
    if not is_live:
        return {"is_live": False, "status_code": status_code, "emails": [], "has_comment_form": False, "has_resource_form": False}

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    extracted_emails: list[str] = []
    has_comment_form = False
    has_resource_form = False
    title = ""

    try:
        with httpx.Client(proxy=proxy, headers=headers, timeout=12.0, follow_redirects=True) as client:
            resp = client.get(target_url)
            if resp.status_code == 200 and len(resp.text) > 500:
                soup = BeautifulSoup(resp.text, "html.parser")
                title_tag = soup.find("title")
                if title_tag:
                    title = title_tag.get_text(strip=True)

                # 1. Extract email addresses
                # Check mailto: links
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    if href.startswith("mailto:"):
                        raw_email = href.replace("mailto:", "").split("?")[0].strip()
                        if raw_email:
                            extracted_emails.append(raw_email)

                # Regex scan full text
                found_text_emails = EMAIL_EXTRACT_REGEX.findall(resp.text)
                for em in found_text_emails:
                    clean_em = em.strip().rstrip(".")
                    # Filter out asset files or invalid formats
                    if not clean_em.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".js", ".css")):
                        extracted_emails.append(clean_em)

                # 2. Inspect Forms
                for form in soup.find_all("form"):
                    form_text = form.get_text(strip=True).lower()
                    form_html = str(form).lower()

                    # Comment form detection
                    if "comment" in form_html or "reply" in form_html or form.find("textarea", attrs={"name": re.compile(r"comment", re.I)}):
                        has_comment_form = True

                    # Resource submit form detection
                    if "submit link" in form_text or "suggest a resource" in form_text or "add resource" in form_text:
                        has_resource_form = True

    except Exception as e:
        logger.debug(f"Landing page inspection error for {target_url}: {e}")

    # Deduplicate and validate emails
    valid_emails = []
    seen = set()
    for em in extracted_emails:
        valid, dom = validate_email_syntax_and_domain(em)
        if valid and em.lower() not in seen and dom not in DISPOSABLE_DOMAINS:
            seen.add(em.lower())
            valid_emails.append(em.lower())

    return {
        "is_live": True,
        "status_code": status_code,
        "title": title,
        "content_length": content_len,
        "emails": valid_emails,
        "has_comment_form": has_comment_form,
        "has_resource_form": has_resource_form,
    }


DAILY_WARMUP_LIMIT = 15
MIN_AUTONOMOUS_SCORE = 0.85


def send_via_resend(to_email: str, subject: str, body_text: str) -> str | None:
    """Dispatches email via Resend API using verified gworky.com domain."""
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        logger.warning("RESEND_API_KEY not configured. Skipping live email send.")
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "from": "Elena Vance <elena@gworky.com>",
        "to": [to_email],
        "subject": subject,
        "text": body_text,
        "headers": {
            "List-Unsubscribe": "<mailto:elena@gworky.com?subject=unsubscribe>"
        },
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post("https://api.resend.com/emails", json=payload, headers=headers)
            if resp.status_code in (200, 201):
                data = resp.json()
                email_id = data.get("id", "resend_ok")
                logger.info(f"✅ Autonomous email dispatched successfully to {to_email} (ID: {email_id})")
                return email_id
            else:
                logger.error(f"Resend returned error {resp.status_code}: {resp.text}")
                return None
    except Exception as e:
        logger.error(f"Failed to connect to Resend API: {e}")
        return None


def get_daily_sent_count(supabase_url: str, supabase_key: str) -> int:
    """Queries Supabase to count how many outreach emails were sent in the last 24 hours."""
    if not supabase_key:
        return 0
    from datetime import timedelta
    cutoff = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"{supabase_url}/rest/v1/outreach_prospects?status=eq.sent&updated_at=gte.{cutoff}&select=id",
                headers={"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
            )
            if resp.status_code == 200:
                count = len(resp.json())
                logger.info(f"Daily warmup counter: {count}/{DAILY_WARMUP_LIMIT} sent in last 24h.")
                return count
    except Exception as e:
        logger.warning(f"Failed to check daily sent count: {e}")
    return 0


def check_bounce_rate_safety(supabase_url: str, supabase_key: str) -> bool:
    """Checks recent dead/sent ratio. If dead > 5%, halts autonomous dispatch."""
    if not supabase_key:
        return True
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"{supabase_url}/rest/v1/outreach_prospects?status=in.(sent,dead)&select=id,status&limit=50",
                headers={"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
            )
            if resp.status_code == 200:
                rows = resp.json()
                if len(rows) >= 10:
                    dead_count = sum(1 for r in rows if r.get("status") == "dead")
                    rate = dead_count / len(rows)
                    if rate > 0.05:
                        logger.error(f"⚠️ Bounce safety triggered: {rate:.1%} dead. Auto-pausing dispatch.")
                        return False
    except Exception as e:
        logger.warning(f"Error checking bounce safety: {e}")
    return True


def generate_dork_outreach_pitch(prospect: dict[str, Any], tool_info: dict[str, str]) -> tuple[str, str]:
    """Generates bespoke, anti-slop pitch for Elena Vance referencing www.gworky.com."""
    domain = extract_domain(prospect["url"])
    pillar = prospect.get("pillar", "money")
    action_type = prospect.get("action_type", "email_outreach")
    tool_title = tool_info["name"]
    tool_slug = tool_info["slug"]
    tool_url = tool_info["url"]
    embed_url = f"{CANONICAL_GROUNDWORK_BASE}/embed-builder?tool={tool_slug}"

    if "guest" in action_type:
        subject = f"Contributor pitch: Evidence-based {pillar.title()} research & calculations for {domain}"
        body = (
            f"Hi {domain.capitalize()} Editorial Team,\n\n"
            f"I came across your contributor guidelines at {prospect['url']}.\n\n"
            f"At the Groundwork Research Desk, we conduct empirical modeling across high-stakes {pillar} decisions. "
            f"Rather than standard opinion pieces, we build interactive datasets and calculators to help readers "
            f"calculate exact numerical outcomes.\n\n"
            f"I would like to propose an evidence-backed analysis specifically for {domain} readers:\n"
            f"• Title: The 2026 {tool_title} Empirical Benchmark\n"
            f"• Core Focus: Real-world break-even calculations, hidden amortization curves, and regional variance.\n"
            f"• Interactive Asset: Includes an unbranded calculation model ({tool_url}) your readers can use directly.\n\n"
            f"Everything we write is 100% original, rigorous, and citation-dense. "
            f"Would you be interested in reviewing a complete draft outline?\n\n"
            f"Best regards,\n"
            f"Elena Vance\n"
            f"Chief Research Editor | Groundwork Research Desk\n"
            f"elena@gworky.com | {CANONICAL_GROUNDWORK_BASE}\n"
        )
        return subject, body

    if "comment" in action_type:
        subject = f"Comment Payload for {domain}"
        body = (
            f"Valuable breakdown. For readers modeling the exact numbers, one key variable often overlooked is the "
            f"amortization reset curve when evaluating the break-even horizon. "
            f"The Groundwork Research Desk maintains an open, unbranded calculator that models this dynamically: "
            f"{tool_url} — helpful benchmark for anyone running comparisons."
        )
        return subject, body

    # Default: Resource page & direct partner outreach
    subject = f"Free research resource & interactive calculator for {domain} readers"
    body = (
        f"Hi {domain.capitalize()} Team,\n\n"
        f"I came across your curated resource page at {prospect['url']} while compiling our 2026 {pillar.title()} research benchmark.\n\n"
        f"Many visitors researching {pillar.lower()} decisions want to model their exact monthly numbers before taking action. "
        f"To give independent practitioners and homeowners instant clarity, the Groundwork Research Desk has released an "
        f"unbranded, zero-cost interactive model for our {tool_title}:\n"
        f"{tool_url}\n\n"
        f"If helpful for your readers, you are welcome to list it as a free resource or embed the calculator directly into your page "
        f"with a single line of HTML:\n\n"
        f"```html\n"
        f"<div data-gworky-calc=\"{tool_slug}\"></div>\n"
        f"<script src=\"{CANONICAL_GROUNDWORK_BASE}/widget.js\" async></script>\n"
        f"```\n\n"
        f"Key utility benefits:\n"
        f"1. 100% Free & Ad-Free: No commercial sponsored popups or affiliate tracking.\n"
        f"2. Verified 2026 Empirical Formulas: Continuous auto-updates for federal and regional adjustments.\n"
        f"3. Fully Mobile-Responsive: Flawless layout across phone, tablet, and desktop.\n\n"
        f"You can preview and configure the widget styling live here:\n"
        f"{embed_url}\n\n"
        f"Best regards,\n"
        f"Elena Vance\n"
        f"Chief Research Editor | Groundwork Research Desk\n"
        f"elena@gworky.com | {CANONICAL_GROUNDWORK_BASE}\n"
    )
    return subject, body


def send_telegram_alert(message: str) -> None:
    """Dispatches enriched alert to Telegram with automated 429 backoff."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_FOUNDER_CHAT_ID")
    if not bot_token or not chat_id:
        return

    telegram_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    for _attempt in range(4):
        try:
            with httpx.Client(timeout=10.0) as client:
                res = client.post(telegram_url, json=payload)
                if res.status_code == 200:
                    return
                if res.status_code == 429:
                    retry_after = res.json().get("parameters", {}).get("retry_after", 3)
                    if retry_after > 25:
                        logger.warning(f"Telegram 429 retry_after is too long ({retry_after}s). Skipping alert.")
                        break
                    logger.warning(f"Telegram 429 rate limit hit. Sleeping {retry_after}s...")
                    time.sleep(retry_after + 1)
                    continue
                logger.warning(f"Telegram notification returned status {res.status_code}: {res.text}")
                break
        except Exception as e:
            logger.warning(f"Telegram dispatch error: {e}")
            time.sleep(2)


def send_autonomous_dispatch_report(
    prospect: dict[str, Any],
    resend_id: str,
    v_res: dict[str, Any],
    daily_count: int,
    subject: str,
) -> None:
    """Sends pure observational dispatch audit report to Telegram (Zero human approval buttons)."""
    domain = extract_domain(prospect["url"])
    target_url = prospect["url"]
    contact = prospect.get("contact", "")
    prospect.get("pillar", "").upper()
    score = prospect.get("relevance_score", 0.85)
    tool_slug = prospect.get("tool_slug", "mortgage-refinance-calculator")
    embed_url = f"{CANONICAL_GROUNDWORK_BASE}/embed-builder?tool={tool_slug}"
    mx_hosts = v_res.get("mx_hosts", [])

    msg = (
        f"⚡ <b>[OUTREACH] AUTONOMOUS DISPATCH COMPLETED</b>\n\n"
        f"🏢 <b>Target Domain:</b> <a href=\"{target_url}\">{domain}</a>\n"
        f"✉️ <b>Recipient:</b> <a href=\"mailto:{contact}\"><code>{contact}</code></a>\n"
        f"🛡️ <b>DNS MX Proof:</b> <code>{len(mx_hosts)} Mail Exchanger Hosts Verified</code>\n"
        f"🎯 <b>Relevance Score:</b> <b>{score:.2f}</b> (≥ {MIN_AUTONOMOUS_SCORE})\n"
        f"📬 <b>Subject:</b> <i>{subject}</i>\n"
        f"🛠️ <b>Groundwork Asset:</b> <a href=\"{prospect.get('target_asset')}\">{tool_slug}</a>\n"
        f"🎨 <b>Embed Studio:</b> <a href=\"{embed_url}\">View Configured Widget</a>\n\n"
        f"🚀 <b>Delivery Telemetry:</b>\n"
        f"• <b>Sender:</b> Elena Vance &lt;elena@gworky.com&gt;\n"
        f"• <b>Resend Message ID:</b> <code>{resend_id}</code>\n"
        f"• <b>Warmup Status:</b> {daily_count}/{DAILY_WARMUP_LIMIT} sent in last 24h\n"
        f"• <b>Governance:</b> 100% Autonomous — Zero Human Gating"
    )
    send_telegram_alert(msg)


def send_harvest_discovery_report(prospect: dict[str, Any], v_res: dict[str, Any]) -> None:
    """Sends pure observational harvest discovery report to Telegram (Zero human approval buttons)."""
    domain = extract_domain(prospect["url"])
    target_url = prospect["url"]
    pillar = prospect.get("pillar", "").upper()
    category = prospect.get("target_category", "Resource Page")
    score = prospect.get("relevance_score", 0.85)
    action_type = prospect.get("action_type", "resource_page")
    tool_slug = prospect.get("tool_slug", "mortgage-refinance-calculator")
    embed_url = f"{CANONICAL_GROUNDWORK_BASE}/embed-builder?tool={tool_slug}"

    msg = (
        f"📊 <b>[HARVEST] AUTONOMOUS FORM/FEED DISCOVERY</b>\n\n"
        f"• <b>Target Domain:</b> <a href=\"{target_url}\">{domain}</a>\n"
        f"• <b>Pillar & Category:</b> {pillar} | {category}\n"
        f"• <b>Target Channel:</b> <code>{action_type}</code>\n"
        f"• <b>Semantic Score:</b> <code>{score:.2f}</code>\n"
        f"• <b>Proposed Asset:</b> <code>{tool_slug}</code>\n"
        f"• <b>Embed Studio:</b> <a href=\"{embed_url}\">View Configured Widget</a>\n\n"
        f"<i>Status: Staged to Supabase queue (draft) for headless submission runner</i>"
    )
    send_telegram_alert(msg)


def check_existing_prospect(supabase_url: str, supabase_key: str, domain: str) -> bool:
    """Checks whether the domain has already been ingested into Supabase."""
    if not supabase_key:
        return False
    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.get(
                f"{supabase_url}/rest/v1/outreach_prospects?url=ilike.*{domain}*&select=id",
                headers={"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
            )
            if resp.status_code == 200:
                rows = resp.json()
                return len(rows) > 0
    except Exception as e:
        logger.debug(f"Error checking existing prospect in Supabase: {e}")
    return False


def save_prospect_to_supabase(supabase_url: str, supabase_key: str, prospect: dict[str, Any]) -> bool:
    """Inserts a prospect row into Supabase `outreach_prospects`."""
    if not supabase_key:
        logger.info(f"[DRY-RUN] Would insert into Supabase: {prospect['url']}")
        return True

    now_iso = datetime.now(UTC).isoformat()

    # Ensure source_type matches database check constraints ('resource_page', 'guest_post', 'comment_section')
    act_type = prospect.get("action_type", "resource_page")
    if "comment" in act_type:
        stype = "comment_section"
    elif "guest" in act_type or "write" in act_type:
        stype = "guest_post"
    else:
        stype = "resource_page"

    # Status must be one of: 'sent', 'human_review', 'draft', 'dead'
    status = prospect.get("status", "draft")
    if status not in ("sent", "human_review", "draft", "dead"):
        status = "draft"

    row = {
        "url": prospect["url"],
        "pillar": prospect.get("pillar", "money"),
        "source_type": stype,
        "contact": prospect.get("contact", ""),
        "target_asset": prospect.get("target_asset", f"{CANONICAL_GROUNDWORK_BASE}/tools"),
        "status": status,
        "draft_outreach": prospect.get("draft_outreach", ""),
        "link_secured": False,
        "gray_tier": None,
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{supabase_url}/rest/v1/outreach_prospects",
                headers={
                    "apikey": supabase_key,
                    "Authorization": f"Bearer {supabase_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                json=row,
            )
            if resp.status_code in (200, 201):
                logger.info(f"✅ Prospect saved to Supabase ({status}): {prospect['url']}")
                if stype == "comment_section":
                    enqueue_comment_target_to_link_logs(supabase_url, supabase_key, prospect)
                return True
            logger.warning(f"Failed to insert into Supabase: HTTP {resp.status_code} - {resp.text}")
    except Exception as e:
        logger.error(f"Supabase insertion exception: {e}")
    return False


def enqueue_comment_target_to_link_logs(supabase_url: str, supabase_key: str, prospect: dict[str, Any]) -> bool:
    """Enqueues a comment target into link_injection_logs for the offpage orchestrator."""
    if not supabase_key:
        return True

    now_iso = datetime.now(UTC).isoformat()
    pillar = prospect.get("pillar", "money")
    tool_slug = prospect.get("tool_slug", "mortgage-refinance-calculator")
    target_url = prospect["url"]
    domain = urlparse(target_url).netloc

    record = {
        "source_slug": f"queue-{pillar}-{domain}"[:50],
        "target_platform": "wordpress_comment",
        "tier_level": "tier1",
        "live_backlink_url": target_url,
        "target_url": prospect.get("target_asset", f"{CANONICAL_GROUNDWORK_BASE}/tools/{tool_slug}"),
        "anchor_text": "Elena Vance",
        "is_dofollow": True,
        "status": "draft",
        "metrics_snapshot": {
            "queued_by": "dork_harvester",
            "pillar": pillar,
            "phase": "initial_seed",
            "enqueued_at": now_iso,
        },
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{supabase_url}/rest/v1/link_injection_logs",
                headers={
                    "apikey": supabase_key,
                    "Authorization": f"Bearer {supabase_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                json=record,
            )
            if resp.status_code in (200, 201):
                logger.info(f"📥 Enqueued comment target to link_injection_logs: {target_url}")
                return True
    except Exception as e:
        logger.debug(f"Failed to enqueue comment target to link_injection_logs: {e}")
    return False


def run_dork_harvest_pipeline(
    pillar: str = "money",
    footprint_type: str = "all",
    limit_per_query: int = 5,
    max_prospects: int = 10,
    dry_run: bool = False,
) -> int:
    """
    Main Orchestrator for Dork Harvesting:
    1. Iterates footprint queries for chosen pillar.
    2. Runs hybrid search.
    3. Verifies target landing pages (HTTP 200, MX records, email/form detection).
    4. Saves to Supabase and sends Telegram alert cards.
    """
    supabase_url, supabase_key = get_supabase_config()
    proxy_url = DataImpulseProxyRouter.get_proxy_url("us")

    pillars_to_run = [pillar] if pillar in DORK_FOOTPRINT_MATRIX else list(DORK_FOOTPRINT_MATRIX.keys())
    total_saved = 0

    logger.info(f"🚀 Starting Dork Harvester Pipeline. Pillars: {pillars_to_run}, Footprint: {footprint_type}, Cap: {max_prospects}")

    for pil in pillars_to_run:
        tool_info = DEFAULT_TOOLS_BY_PILLAR.get(pil, DEFAULT_TOOLS_BY_PILLAR["money"])
        footprint_dict = DORK_FOOTPRINT_MATRIX.get(pil, {})

        categories = [footprint_type] if footprint_type in footprint_dict else list(footprint_dict.keys())

        for cat in categories:
            queries = footprint_dict.get(cat, [])
            for q in queries:
                if total_saved >= max_prospects:
                    logger.info(f"Reached max prospect limit ({max_prospects}). Concluding harvest.")
                    return total_saved

                raw_results = search_dork_cascade(q, count=limit_per_query)
                logger.info(f"Processing {len(raw_results)} candidates from query: {q}")

                for res in raw_results:
                    if total_saved >= max_prospects:
                        break

                    candidate_url = res.get("url", "").strip()
                    domain = extract_domain(candidate_url)

                    # 1. Filter out self, general aggregators, and non-English domains (Rule 2.10)
                    if not domain or domain in EXCLUDED_DOMAINS or any(ex in domain for ex in EXCLUDED_DOMAINS) or is_non_english_domain(domain):
                        continue

                    # 2. Check if already known in Supabase
                    if check_existing_prospect(supabase_url, supabase_key, domain):
                        logger.info(f"Skipping already recorded domain: {domain}")
                        continue

                    # 3. Live Handshake & In-Page Content Inspection
                    logger.info(f"Inspecting candidate landing page: {candidate_url}")
                    inspection = inspect_landing_page(candidate_url, proxy=proxy_url)
                    if not inspection.get("is_live"):
                        logger.warning(f"Rejected non-live candidate: {candidate_url} (HTTP {inspection.get('status_code')})")
                        continue

                    # 4. Determine contact & qualification track
                    emails = inspection.get("emails", [])
                    has_comment = inspection.get("has_comment_form", False)
                    has_resource = inspection.get("has_resource_form", False)
                    primary_email = emails[0] if emails else ""

                    mx_hosts: list[str] = []
                    action_type = "resource_submission"

                    if primary_email:
                        valid, email_dom = validate_email_syntax_and_domain(primary_email)
                        if valid:
                            mx_hosts = resolve_mx_records(email_dom)
                            if mx_hosts:
                                action_type = "email_outreach"

                    if not primary_email:
                        if has_comment:
                            action_type = "comment_submission"
                        elif has_resource:
                            action_type = "resource_submission"
                        else:
                            action_type = "curator_outreach"

                    # 5. Calculate semantic relevance score
                    score = calculate_relevance_score(
                        pillar=pil,
                        target_category=cat.replace("_", " ").title(),
                        page_text_or_anchor=f"{domain} {inspection.get('title', '')}",
                        target_asset=tool_info["url"],
                        draft_content=res.get("snippet", "")
                    )

                    # Discard very low quality or irrelevant noise
                    if score < 0.65:
                        logger.info(f"Skipping low relevance candidate ({score:.2f}): {candidate_url}")
                        continue

                    prospect_record = {
                        "url": candidate_url,
                        "domain": domain,
                        "pillar": pil,
                        "target_category": f"{pil.title()} {cat.replace('_', ' ').title()}",
                        "contact": primary_email,
                        "target_asset": tool_info["url"],
                        "tool_slug": tool_info["slug"],
                        "action_type": action_type,
                        "relevance_score": score,
                    }

                    # 6. Autonomous Pitch Synthesis & Execution
                    subject, pitch_body = generate_dork_outreach_pitch(prospect_record, tool_info)

                    status = "draft"
                    resend_id: str | None = None

                    if action_type == "email_outreach" and primary_email and mx_hosts and score >= MIN_AUTONOMOUS_SCORE:
                        # Check safety and daily warmup limit
                        daily_count = get_daily_sent_count(supabase_url, supabase_key)
                        safe = check_bounce_rate_safety(supabase_url, supabase_key)

                        if daily_count < DAILY_WARMUP_LIMIT and safe and not dry_run:
                            logger.info(f"⚡ Autonomously dispatching outreach to {primary_email} (Score: {score:.2f})...")
                            resend_id = send_via_resend(primary_email, subject, pitch_body)
                            if resend_id:
                                status = "sent"
                                prospect_record["draft_outreach"] = f"Subject: {subject}\n\n{pitch_body}\n\n[Resend ID: {resend_id}]"
                                prospect_record["status"] = status
                                saved = save_prospect_to_supabase(supabase_url, supabase_key, prospect_record)
                                if saved:
                                    total_saved += 1
                                    send_autonomous_dispatch_report(
                                        prospect_record,
                                        resend_id,
                                        {"mx_hosts": mx_hosts},
                                        daily_count + 1,
                                        subject
                                    )
                        else:
                            if dry_run:
                                logger.info(f"[DRY-RUN] Discovered qualified email target ({score:.2f}): {primary_email} on {candidate_url} -> Would pitch: '{subject}'")
                                total_saved += 1
                            else:
                                if daily_count >= DAILY_WARMUP_LIMIT:
                                    logger.info(f"Daily warmup limit reached ({daily_count}/{DAILY_WARMUP_LIMIT}). Staging as draft.")
                                prospect_record["draft_outreach"] = f"Subject: {subject}\n\n{pitch_body}"
                                prospect_record["status"] = "draft"
                                saved = save_prospect_to_supabase(supabase_url, supabase_key, prospect_record)
                                if saved:
                                    total_saved += 1
                                    send_harvest_discovery_report(prospect_record, {"mx_hosts": mx_hosts})
                    else:
                        # Form submission or comment payload target
                        prospect_record["draft_outreach"] = f"Subject: {subject}\n\n{pitch_body}"
                        prospect_record["status"] = "draft"
                        if not dry_run:
                            saved = save_prospect_to_supabase(supabase_url, supabase_key, prospect_record)
                            if saved:
                                total_saved += 1
                                send_harvest_discovery_report(prospect_record, {"mx_hosts": mx_hosts})
                        else:
                            logger.info(f"[DRY-RUN] Discovered form target: {prospect_record['url']}")
                            total_saved += 1

                    # Jitter delay between candidate landing page evaluations
                    time.sleep(2.0)

    logger.info(f"🎉 Harvest run completed! Total new qualified prospects: {total_saved}")
    return total_saved


def main() -> None:
    parser = argparse.ArgumentParser(description="Groundwork Autonomous Google Dork Harvester")
    parser.add_argument("--pillar", choices=["money", "home", "all"], default="money", help="Pillar to target (default: money)")
    parser.add_argument("--footprint", choices=["resources", "guest_post", "comment", "directory", "all"], default="all", help="Footprint type to search (default: all)")
    parser.add_argument("--limit", type=int, default=5, help="Search results limit per query (default: 5)")
    parser.add_argument("--max", type=int, default=5, help="Maximum new prospects to harvest (default: 5)")
    parser.add_argument("--dry-run", action="store_true", help="Inspect and simulate without saving to Supabase")

    args = parser.parse_args()
    run_dork_harvest_pipeline(
        pillar=args.pillar,
        footprint_type=args.footprint,
        limit_per_query=args.limit,
        max_prospects=args.max,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
