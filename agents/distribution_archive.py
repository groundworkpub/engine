"""Groundwork Wayback Machine Archiver (Distribution Layer).

Archives published pages permanently to the Internet Archive via
the Save Page Now (SPN) API.  Supports both public (unauthenticated)
and SPN 2.0 (authenticated) modes.

Usage:
    uv run python agents/distribution_archive.py --slug [slug]
    uv run python agents/distribution_archive.py --batch-all --limit 20
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
SITE_URL = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://gworky.com").rstrip("/")

# Rate limit: 5 requests/minute for unauthenticated, 25 for authenticated
PUBLIC_DELAY = 13  # seconds between requests (5 req/min)
AUTH_DELAY = 3  # seconds between authenticated requests


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


class WaybackArchiver:
    """Archives pages to the Internet Archive via SPN API."""

    PUBLIC_SAVE_URL = "https://web.archive.org/save/"
    SPN2_URL = "https://web.archive.org/save"

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.s3_access = os.environ.get("WAYBACK_S3_ACCESS_KEY", "")
        self.s3_secret = os.environ.get("WAYBACK_S3_SECRET_KEY", "")

    def is_authenticated(self) -> bool:
        """Check if SPN 2.0 credentials are configured."""
        return bool(self.s3_access and self.s3_secret)

    def archive_url(self, url: str) -> dict[str, Any]:
        """Archive a single URL to the Wayback Machine."""
        if self.dry_run:
            logger.info("[DRY-RUN] Would archive: %s", url)
            return {"url": url, "status": "dry_run", "archived_url": None}

        if self.is_authenticated():
            return self._archive_spn2(url)
        return self._archive_public(url)

    def _archive_public(self, url: str) -> dict[str, Any]:
        """Archive via public SPN API (unauthenticated, rate-limited)."""
        import httpx

        try:
            resp = httpx.get(
                f"{self.PUBLIC_SAVE_URL}{url}",
                follow_redirects=True,
                timeout=30,
                headers={"User-Agent": "Groundwork-Archiver/1.0 (+https://gworky.com)"},
            )

            # SPN returns a redirect to the archived URL
            archived_url = str(resp.url) if resp.status_code == 200 else None
            # Also check the Content-Location header
            if not archived_url or "web.archive.org" not in archived_url:
                content_loc = resp.headers.get("content-location", "")
                if content_loc:
                    archived_url = f"https://web.archive.org{content_loc}"

            return {
                "url": url,
                "archived_url": archived_url,
                "status": "archived" if archived_url else "unknown",
                "status_code": resp.status_code,
            }
        except Exception as exc:
            return {"url": url, "status": "error", "error": str(exc)[:200]}

    def _archive_spn2(self, url: str) -> dict[str, Any]:
        """Archive via SPN 2.0 API (authenticated, faster)."""
        import httpx

        try:
            resp = httpx.post(
                self.SPN2_URL,
                headers={
                    "Authorization": f"LOW {self.s3_access}:{self.s3_secret}",
                    "Accept": "application/json",
                },
                data={
                    "url": url,
                    "capture_all": "1",
                    "capture_outlinks": "0",
                },
                timeout=60,
            )

            if resp.status_code == 200:
                data = resp.json()
                job_id = data.get("job_id")
                return {
                    "url": url,
                    "job_id": job_id,
                    "status": "submitted",
                    "archived_url": f"https://web.archive.org/web/{url}" if job_id else None,
                }
            return {
                "url": url,
                "status": "failed",
                "status_code": resp.status_code,
                "error": resp.text[:200],
            }
        except Exception as exc:
            return {"url": url, "status": "error", "error": str(exc)[:200]}

    def batch_archive(self, urls: list[str], delay: float | None = None) -> list[dict[str, Any]]:
        """Archive multiple URLs with rate limiting."""
        if delay is None:
            delay = AUTH_DELAY if self.is_authenticated() else PUBLIC_DELAY

        results: list[dict[str, Any]] = []
        for i, url in enumerate(urls):
            result = self.archive_url(url)
            results.append(result)
            logger.info("Archived [%d/%d]: %s → %s", i + 1, len(urls), url[:60], result.get("status"))
            if i < len(urls) - 1 and not self.dry_run:
                time.sleep(delay)
        return results


# ── CLI ──────────────────────────────────────────────────────────────


def main() -> None:
    _load_env_local()
    parser = argparse.ArgumentParser(description="Groundwork Wayback Machine Archiver")
    parser.add_argument("--slug", help="Archive a single article by slug")
    parser.add_argument("--batch-all", action="store_true", help="Archive all published articles")
    parser.add_argument("--limit", type=int, default=20, help="Max articles for batch (default 20)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without archiving")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    archiver = WaybackArchiver(dry_run=args.dry_run)
    supabase = _get_supabase()

    mode = "SPN 2.0 (authenticated)" if archiver.is_authenticated() else "Public API (rate-limited)"
    print(f"🏛️  Wayback Machine Archiver — Mode: {mode}")

    if args.slug:
        url = f"{SITE_URL}/article/{args.slug}"
        result = archiver.archive_url(url)
        print(json.dumps(result, indent=2))
    elif args.batch_all:
        if not supabase:
            print("❌ Supabase not configured")
            sys.exit(1)
        res = (
            supabase.table("articles")
            .select("slug")
            .eq("status", "published")
            .order("published_at", desc=True)
            .limit(args.limit)
            .execute()
        )
        slugs = [a["slug"] for a in (res.data or [])]
        urls = [f"{SITE_URL}/article/{slug}" for slug in slugs]
        print(f"🏛️  Archiving {len(urls)} articles...")
        results = archiver.batch_archive(urls)
        for r in results:
            icon = "✅" if r.get("status") in ("archived", "submitted") else "⚠️"
            print(f"  {icon} {r['url'][-50:]} → {r.get('status')}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
