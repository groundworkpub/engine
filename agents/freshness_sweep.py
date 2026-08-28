#!/usr/bin/env python3
"""T2.1 — Content freshness sweep.

Picks the highest-impression articles from Google Search Console whose
``updated_at`` is stale, refreshes statistics and dates via the shared LLM
router, writes the result back to Supabase, and pings IndexNow so search
engines re-crawl immediately.

Usage:
    python agents/freshness_sweep.py --dry-run          # plan only, no writes
    python agents/freshness_sweep.py --limit 5          # refresh top 5
    python agents/freshness_sweep.py --stale-days 90    # staleness threshold
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gsc_manager import get_gsc_access_token  # noqa: E402
from indexer_dispatcher import (  # noqa: E402
    _load_env_local,
    get_supabase_client,
    submit_indexnow,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("freshness_sweep")

SEARCH_ANALYTICS_ENDPOINT = "https://searchconsole.googleapis.com/webmasters/v3/sites"
SITE_URL = os.getenv("GSC_PROPERTY", "https://gworky.com/")
ARTICLE_PATH = "/article/"
REFRESH_SYSTEM_PROMPT = (
    "You are Groundwork's content refresh editor. You update existing research "
    "guides so their statistics, prices, dates, and references reflect the most "
    "recent widely-reported figures. You never invent precise numbers you cannot "
    "attribute; where a figure cannot be verified you phrase it conservatively "
    "(for example 'recently reported data'). You preserve the document's exact "
    "heading structure, internal links, tables, and FAQ blocks. You write in "
    "clear, active English with no promotional fluff."
)


def fetch_top_gsc_pages(
    access_token: str, days: int = 28, limit: int = 50
) -> list[dict[str, Any]]:
    """Return top article pages by impressions from GSC search analytics."""
    end = datetime.now(UTC).date()
    start = end - timedelta(days=days)
    resp = httpx.post(
        f"{SEARCH_ANALYTICS_ENDPOINT}/{_encode_property(SITE_URL)}/searchAnalytics/query",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": ["page"],
            "rowLimit": limit,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    rows = resp.json().get("rows", [])
    pages: list[dict[str, Any]] = []
    for row in rows:
        url = (row.get("keys") or [""])[0]
        if ARTICLE_PATH in url:
            pages.append(
                {
                    "url": url,
                    "slug": url.split(ARTICLE_PATH)[1].split("?")[0].split("#")[0].rstrip("/"),
                    "clicks": int(row.get("clicks", 0)),
                    "impressions": int(row.get("impressions", 0)),
                }
            )
    logger.info(f"GSC returned {len(pages)} article pages over {days}d")
    return pages


def _encode_property(site_url: str) -> str:
    from urllib.parse import quote

    return quote(site_url, safe="")


def load_published_articles(supabase: Any, slugs: list[str]) -> list[dict[str, Any]]:
    """Fetch published articles matching the given slugs."""
    if not slugs:
        return []
    resp = (
        supabase.table("articles")
        .select("id,slug,title,content,updated_at,published_at")
        .in_("slug", slugs)
        .eq("status", "published")
        .execute()
    )
    return resp.data or []


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def rank_stale_candidates(
    pages: list[dict[str, Any]],
    articles: list[dict[str, Any]],
    stale_days: int,
    top_n: int,
) -> list[dict[str, Any]]:
    """Join GSC pages with articles, keep stale ones ranked by impressions."""
    by_slug = {a["slug"]: a for a in articles}
    cutoff = datetime.now(UTC) - timedelta(days=stale_days)
    candidates: list[dict[str, Any]] = []
    for page in pages:
        article = by_slug.get(page["slug"])
        if not article:
            continue
        updated = parse_ts(article.get("updated_at"))
        if updated is not None and updated >= cutoff:
            continue
        candidates.append({**article, "impressions": page["impressions"], "clicks": page["clicks"]})
    candidates.sort(key=lambda c: c["impressions"], reverse=True)
    return candidates[:top_n]


def _h2_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.startswith("## "))


def refresh_content(title: str, content: str) -> str | None:
    """Ask the shared LLM router to refresh statistics and dates."""
    from llm_router import call_llm

    messages = [
        {"role": "system", "content": REFRESH_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Refresh this published guide titled \"{title}\".\n\n"
                "Rules:\n"
                "1. Update dated statistics, dollar figures, model years, and time "
                "references to the latest widely-reported values as of August 2026.\n"
                "2. If a specific figure cannot be verified, replace it with a "
                "conservative range or attribute it loosely instead of guessing.\n"
                "3. Keep every heading, list, table, link, and FAQ block intact.\n"
                "4. Do not add commentary about the update itself.\n"
                "5. Return ONLY the complete refreshed markdown document.\n\n"
                f"{content}"
            ),
        },
    ]
    try:
        refreshed = call_llm(messages, max_tokens=8000)
    except Exception as exc:
        logger.error(f"LLM refresh failed for '{title}': {exc}")
        return None
    if not refreshed or "## " not in refreshed:
        return None
    return refreshed.strip()


def quality_gate(original: str, refreshed: str) -> bool:
    """Reject refreshes that truncate, lose structure, or balloon size."""
    if len(refreshed) < 0.7 * len(original):
        logger.warning("Rejected: refreshed content too short (possible truncation)")
        return False
    if len(refreshed) > 1.5 * len(original):
        logger.warning("Rejected: refreshed content ballooned >1.5x")
        return False
    if _h2_count(refreshed) < _h2_count(original):
        logger.warning("Rejected: lost H2 sections")
        return False
    return True


def update_article(supabase: Any, article_id: str, new_content: str) -> bool:
    now = datetime.now(UTC).isoformat()
    resp = (
        supabase.table("articles")
        .update({"content": new_content, "updated_at": now})
        .eq("id", article_id)
        .execute()
    )
    return bool(resp.data)


def notify_telegram(message: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_FOUNDER_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=15.0,
        )
    except Exception as exc:
        logger.warning(f"Telegram notify failed: {exc}")


def log_pipeline_run(
    supabase: Any, status: str, processed: int, published: int, error: str = ""
) -> None:
    try:
        supabase.table("pipeline_runs").insert(
            {
                "agent": "freshness_sweep",
                "status": status,
                "items_processed": processed,
                "items_published": published,
                "error_log": error or None,
            }
        ).execute()
    except Exception as exc:
        logger.warning(f"pipeline_runs insert failed: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Content freshness sweep (T2.1)")
    parser.add_argument("--dry-run", action="store_true", help="Plan only, no writes")
    parser.add_argument("--limit", type=int, default=20, help="Max articles to refresh")
    parser.add_argument("--days", type=int, default=28, help="GSC lookback window")
    parser.add_argument("--stale-days", type=int, default=90, help="Staleness threshold")
    args = parser.parse_args()

    _load_env_local()
    site_base = SITE_URL.rstrip("/")

    access_token, _ = get_gsc_access_token()
    pages = fetch_top_gsc_pages(access_token, days=args.days, limit=50)
    supabase = get_supabase_client()
    articles = load_published_articles(supabase, [p["slug"] for p in pages])
    candidates = rank_stale_candidates(pages, articles, args.stale_days, args.limit)

    logger.info(
        f"{len(candidates)} stale candidates (of {len(articles)} matched, "
        f"threshold {args.stale_days}d):"
    )
    for c in candidates:
        logger.info(f"  - {c['slug']} (impressions={c['impressions']})")

    if args.dry_run:
        logger.info("Dry run — no changes written.")
        return 0

    refreshed_urls: list[str] = []
    errors: list[str] = []
    for c in candidates:
        logger.info(f"Refreshing: {c['title']}")
        new_content = refresh_content(c["title"], c["content"])
        if not new_content:
            errors.append(f"{c['slug']}: LLM refresh failed")
            continue
        if not quality_gate(c["content"], new_content):
            errors.append(f"{c['slug']}: quality gate rejected")
            continue
        if update_article(supabase, c["id"], new_content):
            refreshed_urls.append(f"{site_base}/article/{c['slug']}")
            logger.info(f"Updated: {c['slug']}")
        else:
            errors.append(f"{c['slug']}: Supabase update failed")

    if refreshed_urls:
        key = os.getenv("INDEXNOW_KEY", "")
        host = site_base.split("//")[1]
        submit_indexnow(refreshed_urls, key, host=host)

    status = "success" if not errors else ("partial" if refreshed_urls else "error")
    log_pipeline_run(supabase, status, len(candidates), len(refreshed_urls), "; ".join(errors))

    summary = (
        f"🔄 Freshness sweep: {len(refreshed_urls)} refreshed / "
        f"{len(candidates)} candidates ({status})."
    )
    if errors:
        summary += f"\nErrors: {'; '.join(errors[:5])}"
    notify_telegram(summary)
    logger.info(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
