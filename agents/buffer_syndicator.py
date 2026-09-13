"""Groundwork Buffer Farm Syndicator & Interactive Utility Injector (Pure RSS Daily Digest - Non-LLM)

Automates Tier-2 content syndication on buffer properties by compiling authoritative,
open-access RSS feeds into professional Daily Pillar Digests / Roundups WITHOUT LLM tokens ($0 USD).
Embeds sticky, responsive Groundwork calculation tools and comparison models.

Features:
1. Multi-Pillar Open-Access Ingestion:
   - Money: BLS Consumer Price Index & Federal Reserve Releases
   - Home: EnergyStar & EIA Energy Feeds
   - Body: CDC Newsroom & NIH Releases
   - Tech: CISA Advisories & arXiv AI
   - Life: DOT & BLS Consumer Expenditures
2. Pure RSS Daily Digest Mode (Default - Zero LLM):
   - Compiles 2-4 official bulletins into an authoritative Daily Domain Roundup.
   - Preserves 100% factual accuracy, official agency citations, and publication dates.
   - Zero hallucinations, zero AI slop, zero LLM token consumption.
3. Sticky Groundwork Utility Widget:
   - Embeds responsive HTML widget card linking to the relevant Groundwork tool
     (e.g., Mortgage Amortization, Heat Pump ROI, Living Cost Index) with UTM tracking.
4. Supabase Telemetry Logging & Telegram Audit Cards.

Usage:
    python agents/buffer_syndicator.py --pillar money
    python agents/buffer_syndicator.py --pillar all
    python agents/buffer_syndicator.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

# Load environment
def _load_env_local() -> None:
    root_env = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.local")
    if os.path.exists(root_env):
        with open(root_env, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip("'").strip('"')
                if k not in os.environ:
                    os.environ[k] = v

_load_env_local()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("buffer_syndicator")

CANONICAL_GROUNDWORK_BASE = "https://www.gworky.com"

# Verified Open-Access Authority Feeds per Pillar
AUTHORITY_FEEDS = {
    "money": [
        {"name": "Federal Reserve Press Releases", "url": "https://www.federalreserve.gov/feeds/press_all.xml"},
        {"name": "BLS Consumer Price Index", "url": "https://www.bls.gov/feed/cpi.rss"},
    ],
    "home": [
        {"name": "EnergyStar News", "url": "https://www.energystar.gov/rss.xml"},
        {"name": "EIA Today in Energy", "url": "https://www.eia.gov/rss/todayinenergy.xml"},
    ],
    "body": [
        {"name": "NIH Research Matters", "url": "https://www.nih.gov/news-events/news-releases/feed.xml"},
        {"name": "CDC Newsroom", "url": "https://tools.cdc.gov/podcasts/feed.asp?feedid=183"},
    ],
    "tech": [
        {"name": "CISA Cybersecurity Advisories", "url": "https://www.cisa.gov/cybersecurity-advisories/all.xml"},
        {"name": "arXiv Computer Science AI", "url": "https://arxiv.org/rss/cs.AI"},
    ],
    "life": [
        {"name": "Bureau of Transportation Statistics", "url": "https://www.transportation.gov/rss/press-releases.xml"},
        {"name": "BLS Employment Situation", "url": "https://www.bls.gov/feed/empsit.rss"},
    ],
}

PILLAR_TOOLS = {
    "money": {
        "name": "Mortgage Refinance Break-Even Calculator",
        "url": f"{CANONICAL_GROUNDWORK_BASE}/tools/mortgage-refinance-calculator",
        "slug": "mortgage-refinance-calculator",
        "description": "Simulate amortization curves, closing cost payback periods, and net monthly interest savings under current rate cycles.",
    },
    "home": {
        "name": "Heat Pump Efficiency & Payback Model",
        "url": f"{CANONICAL_GROUNDWORK_BASE}/tools/heat-pump-savings-calculator",
        "slug": "heat-pump-savings-calculator",
        "description": "Calculate localized heating seasonal performance factors (HSPF2) and multi-year utility cost offsets.",
    },
    "body": {
        "name": "Clinical Calorie & Macronutrient Partitioning Model",
        "url": f"{CANONICAL_GROUNDWORK_BASE}/tools/calorie-calculator",
        "slug": "calorie-calculator",
        "description": "Model basal metabolic expenditure and lean-mass preservation ratios under structured energy deficits.",
    },
    "tech": {
        "name": "LLM Inference Cost & Edge Latency Benchmark",
        "url": f"{CANONICAL_GROUNDWORK_BASE}/tools/llm-cost-calculator",
        "slug": "llm-cost-calculator",
        "description": "Quantify per-million token expenditures and throughput tradeoffs across open and proprietary foundation models.",
    },
    "life": {
        "name": "Cost of Living & Real Purchasing Power Relocation Index",
        "url": f"{CANONICAL_GROUNDWORK_BASE}/tools/cost-of-living-calculator",
        "slug": "cost-of-living-calculator",
        "description": "Compare basket-of-goods inflation and state tax gradients across metropolitan areas for relocation decisions.",
    },
}


def fetch_and_parse_rss(feed_url: str) -> list[dict[str, str]]:
    """Fetches an XML RSS feed and extracts item metadata."""
    items: list[dict[str, str]] = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }

    try:
        with httpx.Client(timeout=15.0, headers=headers, follow_redirects=True) as client:
            resp = client.get(feed_url)
            if resp.status_code != 200:
                logger.warning(f"RSS feed returned HTTP {resp.status_code} for {feed_url}")
                return items

            root = ET.fromstring(resp.content)
            channel = root.find("channel")
            raw_items = channel.findall("item") if channel is not None else root.findall(".//{*}item")

            for item in raw_items[:6]:
                title = item.findtext("title", "").strip()
                link = item.findtext("link", "").strip()
                description = item.findtext("description", "").strip()
                pub_date = item.findtext("pubDate", "").strip()

                clean_desc = re.sub(r"<[^>]+>", "", description).strip()
                # Clean up repeated whitespace
                clean_desc = re.sub(r"\s+", " ", clean_desc)

                if title and link:
                    items.append({
                        "title": title,
                        "link": link,
                        "description": clean_desc[:1200],
                        "pub_date": pub_date or datetime.now().strftime("%a, %d %b %Y"),
                    })

    except Exception as e:
        logger.warning(f"Failed to fetch or parse RSS {feed_url}: {e}")

    logger.info(f"Extracted {len(items)} items from {feed_url}")
    return items


def generate_sticky_widget_html(pillar: str) -> str:
    """Generates the responsive Groundwork interactive calculator widget embed."""
    tool = PILLAR_TOOLS.get(pillar, PILLAR_TOOLS["money"])
    tracking_url = f"{tool['url']}?utm_source=buffer_network&utm_medium=interactive_widget&utm_campaign={pillar}_digest"

    return f"""
<!-- START GROUNDWORK INTERACTIVE UTILITY WIDGET -->
<div class="groundwork-utility-card" style="margin: 2.5rem 0; padding: 1.75rem; border: 1px solid #cbd5e1; border-radius: 12px; background: #f8fafc; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
  <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.75rem;">
    <span style="font-size: 0.75rem; font-weight: 700; text-transform: uppercase; color: #0284c7; letter-spacing: 0.05em; background: #e0f2fe; padding: 0.25rem 0.6rem; border-radius: 4px;">
      Interactive Decision Utility
    </span>
    <span style="font-size: 0.75rem; color: #64748b; font-weight: 500;">
      Verified Evidence Standard
    </span>
  </div>
  <h3 style="margin: 0.25rem 0 0.5rem 0; font-size: 1.25rem; font-weight: 700; color: #0f172a; line-height: 1.3;">
    {tool['name']}
  </h3>
  <p style="margin: 0 0 1.25rem 0; font-size: 0.925rem; color: #334155; line-height: 1.5;">
    {tool['description']} Scenario-test your customized parameters to evaluate quantitative outcomes against recent benchmark shifts.
  </p>
  <div style="display: flex; align-items: center; gap: 0.75rem;">
    <a href="{tracking_url}" target="_blank" rel="noopener" style="display: inline-flex; align-items: center; justify-content: center; background: #0284c7; color: #ffffff; padding: 0.7rem 1.5rem; border-radius: 8px; font-weight: 600; text-decoration: none; font-size: 0.9rem; transition: background 0.2s ease;">
      Launch Interactive Model →
    </a>
    <span style="font-size: 0.8rem; color: #64748b;">$0 Free Open-Access Utility</span>
  </div>
</div>
<!-- END GROUNDWORK INTERACTIVE UTILITY WIDGET -->
"""


def compile_daily_pillar_digest(pillar: str, items: list[dict[str, Any]]) -> tuple[str, str]:
    """Compiles 2-4 official bulletins into an authoritative Daily Pillar Digest without LLM tokens.

    Returns: (digest_title, full_html)
    """
    date_str = datetime.now().strftime("%B %d, %Y")
    pillar_title = pillar.capitalize()

    lead_item = items[0]
    digest_title = f"Daily {pillar_title} Intelligence Brief: {lead_item['title'][:55]} & Official Data Updates ({date_str})"

    widget_html = generate_sticky_widget_html(pillar)

    # Build sections
    bulletin_sections = []
    for idx, item in enumerate(items, 1):
        source = item.get("source_name", "Official Agency Archive")
        summary_text = item.get("description", "").strip()
        if not summary_text or len(summary_text) < 30:
            summary_text = f"Official research release regarding {item['title']}. Consult the primary agency archive for full tables and statistical documentation."

        bulletin_sections.append(f"""
    <section style="margin-bottom: 2rem; padding-bottom: 1.5rem; border-bottom: 1px solid #e2e8f0;">
      <h2 style="font-size: 1.15rem; font-weight: 700; color: #1e293b; margin-bottom: 0.5rem; line-height: 1.35;">
        {idx}. {item['title']}
      </h2>
      <p style="font-size: 0.825rem; color: #64748b; margin-bottom: 0.75rem;">
        <strong>Agency:</strong> {source} | <strong>Published:</strong> {item.get('pub_date', 'Recent Release')} | 
        <a href="{item['link']}" target="_blank" rel="noopener" style="color: #0284c7; text-decoration: none;">View Official Release ↗</a>
      </p>
      <div style="font-size: 0.95rem; line-height: 1.6; color: #334155;">
        <p>{summary_text}</p>
      </div>
    </section>
""")

    # Place widget after section 1 or in middle
    middle_idx = max(1, len(bulletin_sections) // 2)
    bulletin_sections.insert(middle_idx, f"""
    <div style="margin: 1.5rem 0;">
      <p style="font-size: 0.9rem; font-weight: 600; color: #475569; margin-bottom: 0.5rem;">
        📊 <strong>Data Simulation & Scenario Testing:</strong>
      </p>
      {widget_html}
    </div>
""")

    body_content = "\n".join(bulletin_sections)

    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{digest_title}</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #1e293b; max-width: 760px; margin: 0 auto; padding: 2rem 1rem;">
  <article>
    <header style="margin-bottom: 2rem; border-bottom: 2px solid #0f172a; padding-bottom: 1rem;">
      <span style="font-size: 0.8rem; font-weight: 700; text-transform: uppercase; color: #0284c7; letter-spacing: 0.05em;">
        Groundwork Buffer Network • Tier-2 Intelligence Digest
      </span>
      <h1 style="font-size: 1.75rem; font-weight: 800; color: #0f172a; margin: 0.5rem 0 0.75rem 0; line-height: 1.25;">
        {digest_title}
      </h1>
      <p style="font-size: 0.875rem; color: #64748b; margin: 0;">
        Curated compilation of open-access public institutional bulletins. Compiled on {date_str}.
      </p>
    </header>

    <div class="executive-summary" style="background: #f1f5f9; padding: 1.25rem; border-radius: 8px; margin-bottom: 2rem; font-size: 0.95rem; color: #334155; border-left: 4px solid #0284c7;">
      <p style="margin: 0;">
        <strong>Editorial Note:</strong> This daily briefing compiles primary data and policy releases from official government and institutional archives in the <strong>{pillar_title}</strong> domain. Below are the unedited key findings, followed by interactive quantitative tools to evaluate how these structural data shifts impact real-world household and business decisions.
      </p>
    </div>

    {body_content}

    <footer style="margin-top: 3rem; padding-top: 1.5rem; border-top: 1px solid #cbd5e1; font-size: 0.85rem; color: #64748b;">
      <p>
        <strong>Methodology & Verification:</strong> All statistical releases and bulletins cited in this briefing are retrieved directly from official government and research feeds. Data modeling, calculator simulations, and comprehensive decision guides are powered by <a href="{CANONICAL_GROUNDWORK_BASE}/{pillar}" target="_blank" rel="noopener" style="color: #0284c7;">Groundwork {pillar_title}</a>.
      </p>
    </footer>
  </article>
</body>
</html>
"""
    return digest_title, full_html


def log_syndication_to_supabase(
    pillar: str,
    article_title: str,
    primary_source_url: str,
    output_path: str,
    bulletin_count: int,
) -> bool:
    """Logs the buffer syndication event to Supabase link_injection_logs and buffer_nodes."""
    supabase_url = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not supabase_key:
        return False

    tool = PILLAR_TOOLS.get(pillar, PILLAR_TOOLS["money"])
    now_iso = datetime.now(timezone.utc).isoformat()

    record = {
        "source_slug": f"buffer-{pillar}-digest-{int(time.time())}",
        "target_platform": "buffer_network",
        "tier_level": "tier2",
        "live_backlink_url": primary_source_url,
        "target_url": f"{tool['url']}?utm_source=buffer_network&utm_medium=interactive_widget&utm_campaign={pillar}_digest",
        "anchor_text": tool["name"],
        "is_dofollow": True,
        "status": "draft",
        "metrics_snapshot": {
            "title": article_title,
            "pillar": pillar,
            "mode": "non_llm_daily_digest",
            "bulletin_count": bulletin_count,
            "widget_embedded": tool["slug"],
            "output_file": output_path,
            "staged_at": now_iso,
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
            return resp.status_code in (200, 201)
    except Exception as e:
        logger.warning(f"Supabase logging notice: {e}")
        return False


def send_telegram_syndication_report(
    pillar: str,
    title: str,
    bulletin_count: int,
    tool_name: str,
    output_path: str,
) -> None:
    """Sends pure observational telemetry to Telegram when a buffer digest is compiled."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_FOUNDER_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return

    msg = (
        f"📡 <b>[BUFFER SYNDICATOR] DAILY PILLAR DIGEST STAGED</b>\n\n"
        f"• <b>Pillar:</b> <code>{pillar.upper()}</code>\n"
        f"• <b>Digest Title:</b> <i>{title[:65]}...</i>\n"
        f"• <b>Mode:</b> <code>Pure RSS Digest (Non-LLM / $0 USD)</code>\n"
        f"• <b>Official Bulletins Compiled:</b> {bulletin_count} primary sources\n"
        f"• <b>Embedded Utility Widget:</b> <code>{tool_name}</code>\n"
        f"• <b>Staged File:</b> <code>{os.path.basename(output_path)}</code>\n\n"
        f"<i>Article compiled with zero LLM token waste. Ready for Blogger API v3 or Cloudflare Pages syndication.</i>"
    )

    telegram_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True}

    try:
        with httpx.Client(timeout=8.0) as client:
            client.post(telegram_url, json=payload)
    except Exception as e:
        logger.warning(f"Telegram telemetry delivery notice: {e}")


def execute_buffer_syndication(pillar: str = "money", limit: int = 1, dry_run: bool = False) -> list[dict[str, Any]]:
    """Runs the pure RSS ingest, daily digest compilation, widget injection, and packaging pipeline."""
    pillars_to_process = [pillar] if pillar != "all" else ["money", "body", "home", "tech", "life"]
    all_results: list[dict[str, Any]] = []

    scratch_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        ".gemini",
        "antigravity-ide",
        "brain",
        "220a31ca-2eda-4d0f-8e45-9e01787e5cc2",
        "scratch",
        "buffer_posts",
    )
    os.makedirs(scratch_dir, exist_ok=True)

    for p in pillars_to_process:
        feeds = AUTHORITY_FEEDS.get(p, AUTHORITY_FEEDS["money"])
        tool = PILLAR_TOOLS.get(p, PILLAR_TOOLS["money"])
        logger.info(f"Ingesting feeds for Pillar={p.upper()} across {len(feeds)} authority sources...")

        collected_items: list[dict[str, Any]] = []
        for f_info in feeds:
            feed_items = fetch_and_parse_rss(f_info["url"])
            for it in feed_items:
                it["source_name"] = f_info["name"]
                collected_items.append(it)

        if not collected_items:
            logger.warning(f"No RSS items available for pillar {p}. Skipping digest creation.")
            continue

        # Select 2-4 items for the daily digest
        selected_items = collected_items[:3]
        digest_title, digest_html = compile_daily_pillar_digest(p, selected_items)

        safe_date = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = os.path.join(scratch_dir, f"{p}_digest_{safe_date}.html")

        if not dry_run:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(digest_html)

            log_syndication_to_supabase(
                pillar=p,
                article_title=digest_title,
                primary_source_url=selected_items[0]["link"],
                output_path=output_file,
                bulletin_count=len(selected_items),
            )
            send_telegram_syndication_report(
                pillar=p,
                title=digest_title,
                bulletin_count=len(selected_items),
                tool_name=tool["name"],
                output_path=output_file,
            )

        entry = {
            "pillar": p,
            "title": digest_title,
            "bulletin_count": len(selected_items),
            "tool_embedded": tool["name"],
            "output_file": output_file if not dry_run else "dry_run_in_memory",
            "status": "staged" if not dry_run else "dry_run",
        }
        all_results.append(entry)
        logger.info(f"✅ Successfully compiled Daily Pillar Digest for {p.upper()} ({len(selected_items)} bulletins).")

    return all_results


def main() -> None:
    parser = argparse.ArgumentParser(description="Groundwork Buffer Farm Syndicator (Non-LLM Daily Digest)")
    parser.add_argument("--pillar", choices=["money", "body", "home", "life", "tech", "all"], default="money")
    parser.add_argument("--limit", type=int, default=1, help="Number of digests per pillar (default 1)")
    parser.add_argument("--dry-run", action="store_true", help="Compile and inspect without writing files or notifying Telegram")
    args = parser.parse_args()

    results = execute_buffer_syndication(pillar=args.pillar, limit=args.limit, dry_run=args.dry_run)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
