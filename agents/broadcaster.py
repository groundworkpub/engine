"""Groundwork Real-Time Omnichannel Syndication & Hub Broadcaster.

Orchestrates automated real-time pings and distribution across:
1. Google Search Console API (gworky.com & groundworkpub.github.io)
2. IndexNow Protocol (Bing, Yandex, Naver, Seznam)
3. W3C WebSub / PubSubHubbub Hubs (Google AppSpot, Superfeedr)
4. PodcastIndex.org Podcasting 2.0 Hub
5. W3C Outbound Webmentions (citations & scholarly nodes)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.authority_injector import _load_env_local, get_supabase_client
from agents.gsc_manager import submit_gsc_sitemap
from agents.podcast_syndicator import ping_podcast_index_hub

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("broadcaster")

INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"
WEBSUB_HUBS = [
    "https://pubsubhubbub.appspot.com/",
    "https://superfeedr.com/hubbub",
]

DEFAULT_FEEDS = [
    "https://gworky.com/rss.xml",
    "https://gworky.com/feed.xml",
    "https://gworky.com/news-sitemap.xml",
    "https://groundworkpub.github.io/feed.xml",
]


def ping_websub_hubs(feed_urls: list[str] | None = None) -> dict[str, int]:
    """Pings W3C WebSub hubs to push instant RSS updates to real-time subscribers."""
    urls = feed_urls or DEFAULT_FEEDS
    results = {}

    with httpx.Client(timeout=10.0) as client:
        for hub in WEBSUB_HUBS:
            for feed_url in urls:
                key = f"{hub} -> {feed_url}"
                try:
                    data = {
                        "hub.mode": "publish",
                        "hub.url": feed_url,
                    }
                    resp = client.post(hub, data=data)
                    results[key] = resp.status_code
                    if resp.status_code in (200, 204):
                        logger.info("WebSub ping success: %s (HTTP %d)", key, resp.status_code)
                    else:
                        logger.warning("WebSub ping returned %d for %s", resp.status_code, key)
                except Exception as exc:
                    logger.warning("Failed WebSub ping for %s: %s", key, exc)
                    results[key] = 0

    return results


def ping_indexnow(urls: list[str], host: str = "gworky.com") -> bool:
    """Submits URLs directly to IndexNow for instantaneous crawling by Bing & Yandex."""
    _load_env_local()
    key = os.environ.get("INDEXNOW_KEY", "")

    if not urls:
        logger.info("No URLs provided for IndexNow ping.")
        return False

    if not key:
        logger.warning("INDEXNOW_KEY not configured — skipping ping")
        return False

    payload = {
        "host": host,
        "key": key,
        "keyLocation": f"https://{host}/{key}.txt",
        "urlList": urls[:1000],
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(INDEXNOW_ENDPOINT, json=payload)
            if resp.status_code in (200, 202):
                logger.info("IndexNow ping success for %d URLs on %s (HTTP %d)", len(urls), host, resp.status_code)
                return True
            logger.warning("IndexNow ping responded with %d: %s", resp.status_code, resp.text)
            return False
    except Exception as exc:
        logger.warning("IndexNow ping failed for %s: %s", host, exc)
        return False


def broadcast_all_sitemaps() -> dict[str, Any]:
    """Submits sitemaps across all verified Google Search Console properties."""
    logger.info("Broadcasting sitemaps to Google Search Console fleet...")
    sitemap_matrix = [
        ("https://gworky.com/", "https://gworky.com/sitemap.xml"),
        ("https://groundworkpub.github.io/", "https://groundworkpub.github.io/sitemap.xml"),
        ("https://emailforums.biz/", "https://emailforums.biz/sitemap_index.xml"),
    ]

    results = {}
    for site_url, sm_url in sitemap_matrix:
        res = submit_gsc_sitemap(site_url, sm_url)
        results[site_url] = res
        logger.info("GSC submission for %s: Status %d", site_url, res["status_code"])

    return results


def broadcast_omnidirectional(limit: int = 10) -> dict[str, Any]:
    """Runs full-spectrum syndication across GSC, IndexNow, WebSub, and PodcastIndex."""
    _load_env_local()
    supabase = get_supabase_client()
    now_iso = datetime.now(UTC).isoformat()

    logger.info("=== STARTING OMNIDIRECTIONAL SYNDICATION BROADCAST ===")

    # 1. Fetch recently published articles
    res = (
        supabase.table("articles")
        .select("slug, pillar, published_at")
        .eq("status", "published")
        .order("published_at", desc=True)
        .limit(limit)
        .execute()
    )
    articles = res.data or []
    gworky_urls = [f"https://gworky.com/article/{a['slug']}" for a in articles]
    gh_pages_urls = [f"https://groundworkpub.github.io/{a['slug']}/" for a in articles]

    # 2. Google Search Console Sitemaps
    gsc_results = broadcast_all_sitemaps()

    # 3. IndexNow Pings (Bing / Yandex / Naver)
    indexnow_gworky = ping_indexnow(gworky_urls, host="gworky.com")
    indexnow_gh = ping_indexnow(gh_pages_urls, host="groundworkpub.github.io")

    # 4. WebSub / PubSubHubbub Real-Time Hubs
    websub_results = ping_websub_hubs()

    # 5. PodcastIndex 2.0 Hub
    podcast_result = ping_podcast_index_hub("https://gworky.com/podcast/feed.xml")

    # 6. Outbound Webmentions (Optional)
    wm_sent = 0
    try:
        from agents.distribution_webmention import WebmentionSender
        sender = WebmentionSender()
        for art in articles[:3]:
            # Fetch full article for webmention scanning
            full_art = supabase.table("articles").select("*").eq("slug", art["slug"]).single().execute()
            if full_art.data:
                r = sender.process_article(full_art.data)
                wm_sent += len(r.get("sent", []))
    except Exception as exc:
        logger.warning("Webmention step notice: %s", exc)

    logger.info("=== OMNIDIRECTIONAL BROADCAST COMPLETED ===")
    return {
        "timestamp": now_iso,
        "gsc": gsc_results,
        "indexnow_gworky": indexnow_gworky,
        "indexnow_gh_pages": indexnow_gh,
        "websub": websub_results,
        "podcast_hub": podcast_result,
        "webmentions_sent": wm_sent,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Groundwork Omnichannel Broadcaster")
    parser.add_argument("--limit", type=int, default=10, help="Number of recent articles to ping")
    args = parser.parse_args()

    result = broadcast_omnidirectional(limit=args.limit)
    print(json.dumps(result, indent=2))
