"""Groundwork Multi-Source News & Open Research Harvester.

Aggregates trending high-intent news from Google News RSS feeds across
key domains (Business, Health, Technology, Science) and matches them for
syndicated whitepaper digests with contextual backlinks to gworky.com.
"""

from __future__ import annotations

import logging
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from agents.authority_injector import _load_env_local, get_supabase_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("news_harvester")

TOPICS = {
    "money": "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en",
    "body": "https://news.google.com/rss/headlines/section/topic/HEALTH?hl=en-US&gl=US&ceid=US:en",
    "tech": "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en",
    "home": "https://news.google.com/rss/headlines/section/topic/SCIENCE?hl=en-US&gl=US&ceid=US:en",
}

SEARCH_FEEDS = {
    "money": [
        "https://news.google.com/rss/search?q=mortgage+rates+OR+treasury+yields&hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=personal+finance+OR+index+funds&hl=en-US&gl=US&ceid=US:en",
    ],
    "body": [
        "https://news.google.com/rss/search?q=cardiovascular+health+OR+metabolic+syndrome&hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=preventive+nutrition+OR+longevity+research&hl=en-US&gl=US&ceid=US:en",
    ],
    "home": [
        "https://news.google.com/rss/search?q=heat+pump+efficiency+OR+solar+energy+tax+credit&hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=home+renovation+costs+OR+energy+rebates&hl=en-US&gl=US&ceid=US:en",
    ],
    "tech": [
        "https://news.google.com/rss/search?q=artificial+intelligence+productivity+OR+cybersecurity&hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=smart+home+devices+matter+standard&hl=en-US&gl=US&ceid=US:en",
    ],
}


@dataclass
class NewsItem:
    title: str
    link: str
    published: str
    source: str
    pillar: str
    description: str
    slug: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:80].strip("-")


def clean_html_tags(raw_html: str) -> str:
    clean = re.sub(r"<[^<]+?>", "", raw_html)
    return clean.strip()


def harvest_feed(url: str, pillar: str, limit: int = 5) -> list[NewsItem]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/133.0.0.0 Safari/537.36"
        )
    }

    try:
        with httpx.Client(timeout=15.0, follow_redirects=True, headers=headers) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                logger.warning("Feed fetch failed %s: %d", url, resp.status_code)
                return []

            root = ET.fromstring(resp.content)
            channel = root.find("channel")
            if channel is None:
                return []

            items: list[NewsItem] = []
            for item in channel.findall("item")[:limit]:
                title_elem = item.find("title")
                link_elem = item.find("link")
                pub_date_elem = item.find("pubDate")
                desc_elem = item.find("description")
                source_elem = item.find("source")

                title = title_elem.text if title_elem is not None and title_elem.text else "Untitled"
                link = link_elem.text if link_elem is not None and link_elem.text else ""
                published = pub_date_elem.text if pub_date_elem is not None and pub_date_elem.text else ""
                desc = clean_html_tags(desc_elem.text) if desc_elem is not None and desc_elem.text else ""
                source_name = source_elem.text if source_elem is not None and source_elem.text else "Open Wire"

                # Strip trailing " - Publisher Name" from Google News titles
                if " - " in title:
                    title_clean = title.rsplit(" - ", 1)[0]
                else:
                    title_clean = title

                slug = slugify(title_clean)
                if not slug:
                    continue

                items.append(
                    NewsItem(
                        title=title_clean,
                        link=link,
                        published=published,
                        source=source_name,
                        pillar=pillar,
                        description=desc or f"Analytical research synthesis regarding {title_clean}.",
                        slug=slug,
                    )
                )

            return items
    except Exception as exc:
        logger.warning("Failed to parse feed %s: %s", url, exc)
        return []


def harvest_all_sources(max_per_pillar: int = 5) -> list[NewsItem]:
    """Harvests Google News across all 4 pillars and returns deduplicated items."""
    _load_env_local()
    all_items: list[NewsItem] = []
    seen_slugs: set[str] = set()

    for pillar, topic_url in TOPICS.items():
        logger.info("Harvesting topic feed for pillar '%s'...", pillar)
        feed_items = harvest_feed(topic_url, pillar, limit=max_per_pillar)
        for item in feed_items:
            if item.slug not in seen_slugs:
                seen_slugs.add(item.slug)
                all_items.append(item)

        # Harvest specific queries for deeper coverage
        for search_url in SEARCH_FEEDS.get(pillar, []):
            search_items = harvest_feed(search_url, pillar, limit=2)
            for item in search_items:
                if item.slug not in seen_slugs:
                    seen_slugs.add(item.slug)
                    all_items.append(item)

    logger.info("Total harvested news items across all pillars: %d", len(all_items))
    return all_items


def trigger_wire_revalidation() -> None:
    """Triggers ISR on-demand revalidation for /wire and sitemaps."""
    _load_env_local()
    secret = os.environ.get("REVALIDATE_SECRET")
    site_url = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://gworky.com")

    if not secret:
        logger.info("REVALIDATE_SECRET not configured, skipping on-demand revalidation webhook.")
        return

    try:
        url = f"{site_url}/api/revalidate"
        headers = {"x-revalidate-secret": secret, "Content-Type": "application/json"}
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, headers=headers, json={"path": "/wire"})
            if resp.status_code == 200:
                logger.info("Successfully revalidated /wire cache on edge!")
            else:
                logger.warning("Revalidate endpoint responded with %d: %s", resp.status_code, resp.text)
    except Exception as exc:
        logger.warning("Failed to trigger ISR revalidation: %s", exc)


if __name__ == "__main__":
    items = harvest_all_sources(max_per_pillar=3)
    for i, it in enumerate(items[:5], 1):
        print(f"{i}. [{it.pillar.upper()}] {it.title} (Source: {it.source}) -> slug: {it.slug}")
