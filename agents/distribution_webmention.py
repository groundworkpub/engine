"""Groundwork W3C Webmention Sender (Distribution Layer).

Discovers webmention endpoints on cited domains and sends W3C-compliant
Webmention notifications.  This is the **sender** side — the receiver
endpoint already exists at ``/api/webmention/route.ts``.

Usage:
    uv run python agents/distribution_webmention.py --slug [slug]
    uv run python agents/distribution_webmention.py --batch-all --limit 20
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
SITE_URL = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://gworky.com").rstrip("/")


def _load_env_local() -> None:
    env_file = _ROOT / ".env.local"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


def _get_supabase():
    try:
        from supabase import create_client

        url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if url and key:
            return create_client(url, key)
    except ImportError:
        pass
    return None


class WebmentionSender:
    """Sends W3C Webmentions to cited domains in article content."""

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self._session = None

    def _get_session(self):
        if self._session is None:
            import httpx

            self._session = httpx.Client(
                timeout=15,
                follow_redirects=True,
                headers={"User-Agent": "Groundwork-Webmention/1.0 (+https://gworky.com)"},
            )
        return self._session

    def extract_urls(self, content: str) -> list[str]:
        """Extract external URLs from article markdown/HTML content."""
        # Match markdown links [text](url) and bare URLs
        url_pattern = re.compile(
            r'(?:\[.*?\]\((https?://[^\s)]+)\))|(?:(?:^|\s)(https?://[^\s<>"]+))',
            re.MULTILINE,
        )
        urls: list[str] = []
        for match in url_pattern.finditer(content):
            url = match.group(1) or match.group(2)
            if url and not url.startswith(SITE_URL):
                # Skip internal links and common non-webmention targets
                parsed = urlparse(url)
                skip_domains = {"github.com", "youtube.com", "twitter.com", "x.com", "google.com"}
                if parsed.hostname and parsed.hostname not in skip_domains:
                    urls.append(url)
        return list(dict.fromkeys(urls))  # dedupe preserving order

    def discover_endpoint(self, target_url: str) -> str | None:
        """Discover the webmention endpoint for a target URL.

        Checks HTTP Link header first, then HTML <link> tags.
        """
        try:
            session = self._get_session()
            resp = session.get(target_url)

            # 1. Check HTTP Link header
            link_header = resp.headers.get("link", "")
            wm_match = re.search(r'<([^>]+)>;\s*rel="?webmention"?', link_header)
            if wm_match:
                return wm_match.group(1)

            # 2. Check HTML <link> or <a> tags
            html_match = re.search(
                r'<(?:link|a)[^>]+rel="?webmention"?[^>]+href="([^"]+)"',
                resp.text[:5000],
            )
            if html_match:
                endpoint = html_match.group(1)
                # Handle relative URLs
                if endpoint.startswith("/"):
                    parsed = urlparse(target_url)
                    endpoint = f"{parsed.scheme}://{parsed.hostname}{endpoint}"
                return endpoint

            # Also check reverse attribute order
            html_match2 = re.search(
                r'<(?:link|a)[^>]+href="([^"]+)"[^>]+rel="?webmention"?',
                resp.text[:5000],
            )
            if html_match2:
                endpoint = html_match2.group(1)
                if endpoint.startswith("/"):
                    parsed = urlparse(target_url)
                    endpoint = f"{parsed.scheme}://{parsed.hostname}{endpoint}"
                return endpoint

        except Exception as exc:
            logger.debug("Failed to discover webmention endpoint for %s: %s", target_url, exc)

        return None

    def send(self, source_url: str, target_url: str) -> dict[str, Any]:
        """Send a W3C Webmention to the target's endpoint."""
        endpoint = self.discover_endpoint(target_url)
        if not endpoint:
            return {"target": target_url, "status": "no_endpoint", "sent": False}

        if self.dry_run:
            logger.info("[DRY-RUN] Would send webmention: %s → %s (endpoint: %s)", source_url, target_url, endpoint)
            return {"target": target_url, "endpoint": endpoint, "status": "dry_run", "sent": False}

        try:
            session = self._get_session()
            resp = session.post(
                endpoint,
                data={"source": source_url, "target": target_url},
            )
            success = resp.status_code in (200, 201, 202)
            return {
                "target": target_url,
                "endpoint": endpoint,
                "status_code": resp.status_code,
                "sent": success,
                "status": "sent" if success else "rejected",
            }
        except Exception as exc:
            return {"target": target_url, "status": "error", "error": str(exc)[:200], "sent": False}

    def process_article(self, article: dict[str, Any]) -> dict[str, Any]:
        """Extract cited URLs from an article and send webmentions.

        Returns dict with 'sent' and 'skipped' lists for pipeline reporting.
        Deduplicates against the webmention_sent table.
        """
        slug = article.get("slug", "")
        content = article.get("content", "")
        source_url = f"{SITE_URL}/article/{slug}"

        urls = self.extract_urls(content)
        logger.info("Found %d external URLs in article '%s'", len(urls), slug)

        # Load already-sent targets for deduplication
        already_sent: set[str] = set()
        try:
            supabase = _get_supabase()
            if supabase:
                res = (
                    supabase.table("webmention_sent")
                    .select("target_url")
                    .eq("source_url", source_url)
                    .execute()
                )
                if res.data:
                    already_sent = {r["target_url"] for r in res.data}
        except Exception as dedup_err:
            logger.warning("Dedup lookup failed (proceeding without dedup): %s", dedup_err)

        sent: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        for url in urls[:50]:  # Cap at 50 per article
            if url in already_sent:
                skipped.append({"target": url, "reason": "already_sent"})
                continue

            result = self.send(source_url, url)
            if result.get("sent"):
                sent.append(result)
                # Record in webmention_sent table
                self._record_sent(source_url, url, result)
            else:
                skipped.append(result)

        logger.info(
            "Sent %d / %d webmentions for '%s' (%d skipped)",
            len(sent),
            len(urls[:50]),
            slug,
            len(skipped),
        )
        return {"sent": sent, "skipped": skipped}

    @staticmethod
    def _record_sent(source_url: str, target_url: str, result: dict[str, Any]) -> None:
        """Persist an outbound webmention to the webmention_sent tracking table."""
        try:
            supabase = _get_supabase()
            if supabase:
                supabase.table("webmention_sent").upsert(
                    {
                        "source_url": source_url,
                        "target_url": target_url,
                        "target_endpoint": result.get("endpoint", ""),
                        "status": "sent" if result.get("sent") else "error",
                        "http_status": result.get("status_code"),
                    },
                    on_conflict="source_url,target_url",
                )
        except Exception as rec_err:
            logger.warning("Failed to record sent webmention: %s", rec_err)


# ── CLI ──────────────────────────────────────────────────────────────


def main() -> None:
    _load_env_local()
    parser = argparse.ArgumentParser(description="Groundwork W3C Webmention Sender")
    parser.add_argument("--slug", help="Process a single article by slug")
    parser.add_argument("--batch-all", action="store_true", help="Process all published articles")
    parser.add_argument("--limit", type=int, default=20, help="Max articles for batch (default 20)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without sending")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    sender = WebmentionSender(dry_run=args.dry_run)
    supabase = _get_supabase()

    if not supabase:
        print("❌ Supabase not configured")
        sys.exit(1)

    if args.slug:
        res = supabase.table("articles").select("slug, content").eq("slug", args.slug).maybe_single().execute()
        if not res.data:
            print(f"❌ Article not found: {args.slug}")
            sys.exit(1)
        result = sender.process_article(res.data)
        for r in result.get("sent", []):
            print(f"  ✅ {r['target'][:60]} → {r.get('status')}")
        for r in result.get("skipped", []):
            print(f"  ⏭️  {r['target'][:60]} → {r.get('reason', 'skip')}")
    elif args.batch_all:
        res = (
            supabase.table("articles")
            .select("slug, content")
            .eq("status", "published")
            .order("published_at", desc=True)
            .limit(args.limit)
            .execute()
        )
        articles = res.data or []
        print(f"🌐 Sending webmentions for {len(articles)} articles...")
        total_sent = 0
        total_skipped = 0
        for article in articles:
            result = sender.process_article(article)
            sent = len(result.get("sent", []))
            skipped = len(result.get("skipped", []))
            total_sent += sent
            total_skipped += skipped
            print(f"  📤 {article['slug'][:50]} — {sent}/{sent+skipped} sent ({skipped} dedup/skip)")
        print(f"\n  Total webmentions sent: {total_sent} ({total_skipped} skipped)")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
