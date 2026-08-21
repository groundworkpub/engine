"""Groundwork RSS & Atom Syndication Feed Generator.

Generates standards-compliant RSS 2.0 (feed.xml) and Atom (atom.xml) feeds
with media enclosures (1200x630 vector cards) for Google Discover, Perplexity,
and AI crawler ingestion.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Any

from agents.news_harvester import NewsItem

GH_PAGES_URL = "https://groundworkpub.github.io"
SITE_URL = "https://gworky.com"


def format_rfc822_date(date_str: str | None = None) -> str:
    """Formats datetime as RFC 822 for RSS 2.0."""
    dt = datetime.now(UTC)
    if date_str:
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            dt = datetime.now(UTC)
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def generate_rss_feed(articles: list[dict[str, Any]], digests: list[NewsItem]) -> str:
    """Generates an RSS 2.0 feed with rich media enclosures."""
    now_rfc822 = format_rfc822_date()
    items_xml = ""

    # Include recent digests first
    for dig in digests[:20]:
        title = html.escape(dig.title)
        desc = html.escape(dig.description)
        pillar = html.escape(dig.pillar.upper())
        link = f"{GH_PAGES_URL}/digest/{dig.slug}/"
        image_url = f"{GH_PAGES_URL}/digest/{dig.slug}/og.svg"
        guid = f"digest-{dig.slug}"

        items_xml += f"""    <item>
      <title>{title}</title>
      <link>{link}</link>
      <guid isPermaLink="true">{link}</guid>
      <pubDate>{now_rfc822}</pubDate>
      <category>{pillar}</category>
      <description>{desc}</description>
      <enclosure url="{image_url}" type="image/svg+xml" length="1200" />
      <source url="{html.escape(dig.link)}">{html.escape(dig.source)}</source>
    </item>
"""

    # Include flagship articles
    for art in articles[:30]:
        title = html.escape(art["title"])
        excerpt = html.escape(art.get("excerpt") or art["title"])
        pillar = html.escape(art.get("pillar", "general").upper())
        slug = art["slug"]
        link = f"{GH_PAGES_URL}/{slug}/"
        image_url = f"{GH_PAGES_URL}/{slug}/og.svg"
        pub_date = format_rfc822_date(art.get("published_at"))

        items_xml += f"""    <item>
      <title>{title}</title>
      <link>{link}</link>
      <guid isPermaLink="true">{link}</guid>
      <pubDate>{pub_date}</pubDate>
      <category>{pillar}</category>
      <description>{excerpt}</description>
      <enclosure url="{image_url}" type="image/svg+xml" length="1200" />
    </item>
"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Groundwork — Evidence-Based Guides &amp; Decision Utilities</title>
    <link>{GH_PAGES_URL}</link>
    <description>Clear, evidence-backed guides and decision utilities across money, health, home, and modern technology.</description>
    <language>en-us</language>
    <lastBuildDate>{now_rfc822}</lastBuildDate>
    <atom:link href="{GH_PAGES_URL}/feed.xml" rel="self" type="application/rss+xml" />
    <atom:link href="https://pubsubhubbub.appspot.com/" rel="hub" />
    <atom:link href="https://superfeedr.com/hubbub" rel="hub" />
    <image>
      <url>{GH_PAGES_URL}/og.svg</url>
      <title>Groundwork</title>
      <link>{GH_PAGES_URL}</link>
    </image>
{items_xml}  </channel>
</rss>
"""
