"""Groundwork Fediverse / Mastodon Publisher & Interactive Curator Engine.

Publishes article summaries to Mastodon via the ActivityPub-compatible
REST API, manages bi-directional curator engagement with Newsmast Community
channels across all 5 Groundwork pillars, and synchronizes boost/favorite telemetry.

Usage:
    uv run python agents/distribution_fediverse.py --slug [slug]
    uv run python agents/distribution_fediverse.py --batch-all --limit 5
    uv run python agents/distribution_fediverse.py --follow-curators
    uv run python agents/distribution_fediverse.py --sync-curators
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
SITE_URL = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://gworky.com").rstrip("/")

# Pillar → Canonical Newsmast & Fediverse curation hashtags
PILLAR_TAGS: dict[str, list[str]] = {
    "money": ["personalfinance", "investing", "finance", "economics", "moneytips"],
    "body": ["health", "science", "wellness", "fitness", "medicine"],
    "home": ["climate", "homeimprovement", "smarthome", "solarenergy", "diy"],
    "life": ["society", "career", "education", "lifestyle", "travel"],
    "tech": ["technology", "ai", "programming", "software", "gadgets"],
}

# Newsmast Community Curated Channels per Pillar
NEWSMAST_CURATORS: dict[str, list[str]] = {
    "tech": ["technology@newsmast.community", "ai@newsmast.community", "programming@newsmast.community"],
    "money": ["finance@newsmast.community", "economics@newsmast.community"],
    "body": ["science@newsmast.community", "health@newsmast.community"],
    "home": ["climate@newsmast.community", "green@newsmast.community"],
    "life": ["society@newsmast.community", "education@newsmast.community"],
}


def _load_env_local() -> None:
    env_file = _ROOT / ".env.local"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


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


class FediversePublisher:
    """Publishes article summaries & automates curator interaction on Mastodon."""

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.instance_url = os.environ.get("MASTODON_INSTANCE_URL", "https://mastodon.social").rstrip("/")
        self.access_token = os.environ.get("MASTODON_ACCESS_TOKEN", "")

    def is_available(self) -> bool:
        return bool(self.instance_url and self.access_token)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": "Groundwork-Fediverse-Syndicator/1.0",
        }

    def format_toot(self, article: dict[str, Any]) -> str:
        """Format an article as a Mastodon toot (max 500 chars) with canonical tags."""
        title = article.get("title", "")
        excerpt = article.get("excerpt", "")
        slug = article.get("slug", "")
        pillar = article.get("pillar", "tech").lower()

        url = f"{SITE_URL}/article/{slug}"
        tags = PILLAR_TAGS.get(pillar, ["Groundwork"])
        # Format lowercase hashtags to match Fediverse discovery
        hashtags = " ".join(f"#{t.lower()}" for t in tags[:4])

        # Compose toot
        toot = f"📰 {title}\n\n{excerpt}\n\n🔗 {url}\n\n{hashtags} #groundwork"

        # Truncate if over 500 chars
        if len(toot) > 500:
            available = 500 - len(f"📰 {title}\n\n\n\n🔗 {url}\n\n{hashtags} #groundwork") - 3
            toot = f"📰 {title}\n\n{excerpt[:available]}...\n\n🔗 {url}\n\n{hashtags} #groundwork"

        return toot

    def post(self, article: dict[str, Any]) -> dict[str, Any]:
        """Post an article summary to Mastodon."""
        toot = self.format_toot(article)

        if self.dry_run:
            logger.info("[DRY-RUN] Would post to Mastodon:\n%s", toot)
            return {
                "dry_run": True,
                "title": article.get("title"),
                "toot_length": len(toot),
                "content_preview": toot[:100],
            }

        if not self.is_available():
            return {"error": "MASTODON_INSTANCE_URL or MASTODON_ACCESS_TOKEN not configured"}

        try:
            resp = httpx.post(
                f"{self.instance_url}/api/v1/statuses",
                headers={
                    **self._headers(),
                    "Idempotency-Key": f"gw-{article.get('slug', 'unknown')}",
                },
                json={
                    "status": toot,
                    "visibility": "public",
                    "language": "en",
                },
                timeout=20,
            )

            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    "title": article.get("title"),
                    "toot_id": data.get("id"),
                    "toot_url": data.get("url"),
                    "status": "posted",
                }
            return {
                "title": article.get("title"),
                "status": "failed",
                "status_code": resp.status_code,
                "error": resp.text[:200],
            }
        except Exception as exc:
            return {"title": article.get("title"), "status": "error", "error": str(exc)[:200]}

    def follow_curators(self) -> dict[str, Any]:
        """Discover and follow Newsmast community curators for all 5 pillars."""
        if not self.is_available() and not self.dry_run:
            return {"error": "Credentials missing"}

        results = []
        for pillar, handles in NEWSMAST_CURATORS.items():
            for handle in handles:
                if self.dry_run:
                    logger.info("[DRY-RUN] Would search and follow curator: @%s (Pillar: %s)", handle, pillar)
                    results.append({"handle": handle, "pillar": pillar, "status": "dry_run_followed"})
                    continue

                try:
                    # Search account by handle
                    resp = httpx.get(
                        f"{self.instance_url}/api/v1/accounts/search",
                        headers=self._headers(),
                        params={"q": handle, "resolve": "true", "limit": 1},
                        timeout=15,
                    )
                    if resp.status_code == 200:
                        accounts = resp.json()
                        if accounts:
                            acc_id = accounts[0]["id"]
                            # Follow the account
                            f_resp = httpx.post(
                                f"{self.instance_url}/api/v1/accounts/{acc_id}/follow",
                                headers=self._headers(),
                                timeout=15,
                            )
                            results.append({
                                "handle": handle,
                                "pillar": pillar,
                                "account_id": acc_id,
                                "status": "followed" if f_resp.status_code == 200 else "failed_follow",
                            })
                        else:
                            results.append({"handle": handle, "pillar": pillar, "status": "not_found"})
                except Exception as exc:
                    results.append({"handle": handle, "pillar": pillar, "status": "error", "error": str(exc)})

        return {"curators_processed": len(results), "details": results}

    def sync_curator_interactions(self) -> dict[str, Any]:
        """Poll incoming notifications and auto-favorite/boost curator interactions."""
        if not self.is_available() and not self.dry_run:
            return {"error": "Credentials missing"}

        if self.dry_run:
            logger.info("[DRY-RUN] Would poll /api/v1/notifications and auto-reciprocate curator boosts.")
            return {"dry_run": True, "status": "synced"}

        try:
            resp = httpx.get(
                f"{self.instance_url}/api/v1/notifications",
                headers=self._headers(),
                params={"types": ["reblog", "favourite", "mention"], "limit": 20},
                timeout=15,
            )
            if resp.status_code != 200:
                return {"error": f"Failed to fetch notifications: {resp.status_code}"}

            notifications = resp.json()
            reciprocated = 0

            for notif in notifications:
                notif_type = notif.get("type")
                status = notif.get("status")
                account = notif.get("account", {})
                acct_name = account.get("acct", "")

                # If boosted or favorited by any community curator, favorite their status back
                if notif_type in ("reblog", "favourite") and status:
                    status_id = status.get("id")
                    if status_id:
                        httpx.post(
                            f"{self.instance_url}/api/v1/statuses/{status_id}/favourite",
                            headers=self._headers(),
                            timeout=10,
                        )
                        reciprocated += 1
                        logger.info("Reciprocated favorite to %s for status #%s", acct_name, status_id)

            return {"notifications_scanned": len(notifications), "reciprocated": reciprocated}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}


# ── CLI ──────────────────────────────────────────────────────────────


def main() -> None:
    _load_env_local()
    parser = argparse.ArgumentParser(description="Groundwork Fediverse / Mastodon Publisher & Curator Engine")
    parser.add_argument("--slug", help="Post a single article by slug")
    parser.add_argument("--batch-all", action="store_true", help="Post recent published articles")
    parser.add_argument("--limit", type=int, default=5, help="Max articles for batch (default 5)")
    parser.add_argument("--follow-curators", action="store_true", help="Follow Newsmast pillar curation accounts")
    parser.add_argument("--sync-curators", action="store_true", help="Poll notifications and reciprocate interactions")
    parser.add_argument("--dry-run", action="store_true", help="Preview without modifying live Mastodon state")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    publisher = FediversePublisher(dry_run=args.dry_run)

    if not publisher.is_available() and not args.dry_run:
        print("❌ MASTODON_INSTANCE_URL / MASTODON_ACCESS_TOKEN not configured")
        sys.exit(1)

    if args.follow_curators:
        print("🐘 Discovering and following Newsmast pillar curators...")
        res = publisher.follow_curators()
        print(json.dumps(res, indent=2))
        return

    if args.sync_curators:
        print("🐘 Synchronizing curator notifications and reciprocations...")
        res = publisher.sync_curator_interactions()
        print(json.dumps(res, indent=2))
        return

    supabase = _get_supabase()
    if not supabase:
        print("❌ Supabase not configured")
        sys.exit(1)

    if args.slug:
        res = (
            supabase.table("articles")
            .select("slug, title, excerpt, pillar")
            .eq("slug", args.slug)
            .maybe_single()
            .execute()
        )
        if not res.data:
            print(f"❌ Article not found: {args.slug}")
            sys.exit(1)
        result = publisher.post(res.data)
        print(json.dumps(result, indent=2))
    elif args.batch_all:
        res = (
            supabase.table("articles")
            .select("slug, title, excerpt, pillar")
            .eq("status", "published")
            .order("published_at", desc=True)
            .limit(args.limit)
            .execute()
        )
        articles = res.data or []
        print(f"🐘 Posting {len(articles)} articles to Mastodon with Newsmast tags...")
        for article in articles:
            result = publisher.post(article)
            icon = "✅" if result.get("status") == "posted" else "⏭️"
            print(f"  {icon} {article['title'][:50]} → {result.get('status')}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
