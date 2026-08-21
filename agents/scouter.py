import logging
import time
from datetime import UTC, datetime
from typing import Any

import feedparser
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_FEED_TIMEOUT_SECONDS = 10
DEFAULT_FEED_RETRIES = 2
RETRY_BACKOFF_SECONDS = 0.5


def html_to_text(html: str, max_chars: int = 6000) -> str:
    """Strip HTML tags, remove boilerplate, and compress text via Headroom."""
    if not html:
        return ""
    try:
        from agents.headroom_compressor import HeadroomCompressor
        return HeadroomCompressor.compress_html(html, target_chars=max_chars)
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(
            ["script", "style", "nav", "header", "footer", "aside", "figure", "svg", "noscript", "iframe", "form", "button"]
        ):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        cleaned = "\n".join(lines)
        if len(cleaned) > max_chars:
            cleaned = cleaned[:max_chars].rsplit("\n", 1)[0]
        return cleaned


def extract_image_url(entry: Any, content_html: str) -> str | None:
    """Extract a featured image URL from a feed entry, best-effort.

    Resolution order:
      1. Media RSS namespace (``media_content`` / ``media_thumbnail``).
      2. First ``<img src>`` in the entry's HTML content/summary.
      3. First ``<enclosure>`` with an ``image/*`` (or missing) MIME type.

    Returns ``None`` when no image is present — feeds without images are
    handled gracefully downstream (no fabricated URLs).
    """
    # 1. Media RSS namespace
    for attr in ("media_content", "media_thumbnail"):
        media = getattr(entry, attr, None) or []
        for item in media:
            url = item.get("url")
            if url:
                return str(url)

    # 2. First <img src> in content/summary HTML
    if content_html:
        soup = BeautifulSoup(content_html, "html.parser")
        img = soup.find("img")
        if img:
            src = img.get("src")
            if src:
                return str(src)

    # 3. Image enclosure
    for enc in getattr(entry, "enclosures", None) or []:
        url = enc.get("url")
        enc_type = enc.get("type", "") or ""
        if url and (enc_type.startswith("image/") or not enc_type):
            return str(url)

    return None


def extract_og_image(html: str) -> str | None:
    """Extract the og:image meta tag from a full article page, best-effort."""
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    node = soup.find("meta", attrs={"property": "og:image"}) or soup.find("meta", attrs={"name": "og:image"})
    if node:
        src = node.get("content")
        if src:
            return str(src).strip()
    return None


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, text/html, */*",
}


def fetch_article_page(url: str, timeout: int = 10) -> tuple[str, str | None]:
    """Fetch an article page and return (main_text, og_image).

    Many feeds expose only 100-200 char summaries; fetching the underlying
    page gives the scribe real content to rewrite. Best-effort: any failure
    returns an empty tuple so the scouter falls back to the feed text.
    """
    try:
        import httpx

        with httpx.Client(timeout=float(timeout), follow_redirects=True, headers=DEFAULT_HEADERS) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                html = resp.text
                return html_to_text(html), extract_og_image(html)
    except Exception:
        pass
    return "", None


def get_existing_urls(supabase: Any) -> set[str]:
    """Fetch all known source_urls to avoid re-processing."""
    try:
        result = supabase.table("articles").select("source_url").execute()
        return {row["source_url"] for row in result.data if row.get("source_url")}
    except Exception:
        return set()


def fetch_feed(feed_url: str, timeout: int, retries: int) -> Any:
    """Fetch and parse an RSS feed with bounded network work."""
    import httpx

    attempts = retries + 1

    for attempt in range(attempts):
        try:
            with httpx.Client(timeout=float(timeout), follow_redirects=True, headers=DEFAULT_HEADERS) as client:
                resp = client.get(feed_url)
                if resp.status_code == 200:
                    return feedparser.parse(resp.content)
                elif resp.status_code in (403, 404, 410):
                    # Permanent/WAF blocked — do not retry uselessly
                    raise RuntimeError(f"HTTP {resp.status_code}")
        except Exception:
            if attempt == attempts - 1:
                raise
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))

    raise RuntimeError(f"Unable to fetch feed: {feed_url}")


def run_scouter(config: dict, supabase: Any) -> list[dict[str, Any]]:
    """Agent 1: Harvest raw content from RSS feeds."""
    existing_urls = get_existing_urls(supabase)
    raw_payload: list[dict[str, Any]] = []

    for source in config.get("sources", []):
        feed_url = source.get("feed_url")
        if not feed_url:
            continue
        try:
            timeout = source.get("timeout_seconds", DEFAULT_FEED_TIMEOUT_SECONDS)
            retries = source.get("retries", DEFAULT_FEED_RETRIES)
            feed = fetch_feed(feed_url, timeout, retries)
            if getattr(feed, "bozo", False) and not getattr(feed, "entries", None):
                logger.warning("Skipping unparseable feed: %s", feed_url)
                continue
            logger.info("Scouted: %s (%d entries)", source["name"], len(feed.entries))
            for entry in feed.entries[: source.get("max_items", 10)]:
                url = getattr(entry, "link", None)
                if not url or url in existing_urls:
                    continue

                # Extract content (prefer full content over summary)
                content_html = ""
                if hasattr(entry, "content") and entry.content:
                    content_html = entry.content[0].value
                elif hasattr(entry, "summary"):
                    content_html = entry.summary

                raw_text = html_to_text(content_html)
                image_url = extract_image_url(entry, content_html)

                # Summary-only feeds: enrich with the full article page.
                if len(raw_text) < 200:
                    page_text, page_image = fetch_article_page(
                        url, source.get("timeout_seconds", DEFAULT_FEED_TIMEOUT_SECONDS)
                    )
                    if len(page_text) > len(raw_text):
                        raw_text = page_text
                    if not image_url and page_image:
                        image_url = page_image

                if len(raw_text) < 200:
                    logger.debug(f"Skipping short content ({len(raw_text)} chars): {url}")
                    continue

                raw_payload.append(
                    {
                        "url": url,
                        "title": getattr(entry, "title", "").strip(),
                        "raw_content": raw_text,
                        "source_name": source["name"],
                        "pillar": source["pillar"],
                        "image_url": image_url,
                        "published_at": datetime.now(UTC).isoformat(),
                    }
                )
                existing_urls.add(url)  # Prevent intra-run duplicates

        except Exception as e:
            logger.warning("Feed skipped for %s: %s", source.get("name", "Unknown"), e)

    logger.info(f"Scouter harvested {len(raw_payload)} new items")
    return raw_payload
