"""Groundwork Zenodo DOI Engine (Distribution Layer).

CLI wrapper for the Zenodo API — deposits flagship articles as citable
open-science publications with permanent DOIs from CERN.

Complements the Next.js endpoint ``/api/zenodo/deposit`` with batch
processing, sandbox mode, and CLI interface.

Usage:
    uv run python agents/distribution_zenodo.py --slug [slug]
    uv run python agents/distribution_zenodo.py --batch-all --limit 10
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

logger = logging.getLogger(__name__)

# ── Environment helpers ──────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parent.parent


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


# ── Zenodo API ───────────────────────────────────────────────────────

ZENODO_SANDBOX = "https://sandbox.zenodo.org/api"
ZENODO_PRODUCTION = "https://zenodo.org/api"
SITE_URL = "https://gworky.com"


class ZenodoEngine:
    """Deposits articles to Zenodo and mints permanent DOIs."""

    def __init__(self, sandbox: bool = False, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.token = os.environ.get("ZENODO_SANDBOX_TOKEN" if sandbox else "ZENODO_TOKEN", "")
        self.base_url = ZENODO_SANDBOX if sandbox else ZENODO_PRODUCTION
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def is_available(self) -> bool:
        return bool(self.token)

    def deposit_article(self, article: dict[str, Any]) -> dict[str, Any]:
        """Create a Zenodo deposit for a single article."""
        import httpx

        if self.dry_run:
            logger.info("[DRY-RUN] Would deposit: %s", article.get("title"))
            return {"dry_run": True, "title": article.get("title")}

        if not self.is_available():
            return {"error": "ZENODO_TOKEN not configured"}

        # Create deposition with metadata
        pub_date = article.get("published_at")
        if pub_date:
            try:
                pub_date = datetime.fromisoformat(pub_date.replace("Z", "+00:00")).strftime("%Y-%m-%d")
            except Exception:
                pub_date = datetime.now(UTC).strftime("%Y-%m-%d")
        else:
            pub_date = datetime.now(UTC).strftime("%Y-%m-%d")

        metadata_payload = {
            "metadata": {
                "title": article.get("title", "Untitled"),
                "upload_type": "publication",
                "publication_type": "article",
                "description": article.get("takeaway") or article.get("excerpt") or article.get("title", ""),
                "creators": [{"name": article.get("author_name", "Groundwork Editorial"), "affiliation": "Groundwork Research"}],
                "keywords": [article.get("pillar", "research"), "groundwork", "evidence-based"],
                "license": "cc-by-4.0",
                "related_identifiers": [
                    {
                        "identifier": f"{SITE_URL}/article/{article.get('slug', '')}",
                        "relation": "isSupplementedBy",
                        "scheme": "url",
                    }
                ],
                "publication_date": pub_date,
            }
        }

        r = httpx.post(
            f"{self.base_url}/deposit/depositions",
            headers={**self.headers, "Content-Type": "application/json"},
            json=metadata_payload,
            timeout=30,
        )
        if r.status_code not in (200, 201):
            return {"error": f"Create deposit failed: {r.status_code} {r.text[:200]}"}

        deposit = r.json()
        deposit_id = deposit["id"]
        bucket_url = deposit.get("links", {}).get("bucket")

        # 2. Upload preprint markdown artifact to bucket
        if bucket_url:
            slug = article.get("slug", f"record-{deposit_id}")
            file_name = f"groundwork-{slug}.md"
            preprint_md = f"""# {article.get('title', 'Groundwork Report')}

**Author:** {article.get('author_name', 'Groundwork Editorial')}
**Publisher:** Groundwork Research Platform
**Date:** {pub_date}
**URL:** {SITE_URL}/article/{slug}

## Findings
{article.get('takeaway') or article.get('excerpt') or ''}

## Content
{article.get('content', '')}
""".encode()

            try:
                r_file = httpx.put(
                    f"{bucket_url}/{file_name}",
                    headers={**self.headers, "Content-Type": "application/octet-stream"},
                    content=preprint_md,
                    timeout=30,
                )
                if r_file.status_code not in (200, 201):
                    logger.warning("Preprint upload returned %s: %s", r_file.status_code, r_file.text[:150])
            except Exception as file_err:
                logger.warning("Preprint upload failed: %s", file_err)

        # 3. Publish deposition and mint final DOI
        pub_result = self.publish(deposit_id)
        final_doi = pub_result.get("doi") or deposit.get("metadata", {}).get("prereserve_doi", {}).get("doi") or f"10.5281/zenodo.{deposit_id}"

        return {
            "deposit_id": deposit_id,
            "doi": final_doi,
            "record_url": pub_result.get("record_url") or deposit.get("links", {}).get("html"),
            "status": "published" if pub_result.get("status") == "published" else "draft",
            "title": article.get("title"),
        }

    def publish(self, deposit_id: int) -> dict[str, Any]:
        """Publish a deposit and mint the final DOI."""
        import httpx

        if self.dry_run:
            return {"dry_run": True, "deposit_id": deposit_id}

        r = httpx.post(
            f"{self.base_url}/deposit/depositions/{deposit_id}/actions/publish",
            headers=self.headers,
            timeout=30,
        )
        if r.status_code not in (200, 201, 202):
            return {"error": f"Publish failed: {r.status_code} {r.text[:200]}"}

        data = r.json()
        return {
            "deposit_id": deposit_id,
            "doi": data.get("doi"),
            "record_url": data.get("links", {}).get("record_html") or data.get("links", {}).get("html"),
            "status": "published",
        }

    def batch_deposit(self, articles: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
        """Deposit multiple articles in batch."""
        results = []
        for article in articles[:limit]:
            result = self.deposit_article(article)
            results.append(result)
            logger.info("Deposited: %s → %s", article.get("title", "?")[:50], result.get("status", "?"))
        return results


# ── CLI ──────────────────────────────────────────────────────────────


def main() -> None:
    _load_env_local()
    parser = argparse.ArgumentParser(description="Groundwork Zenodo DOI Engine")
    parser.add_argument("--slug", help="Deposit a single article by slug")
    parser.add_argument("--batch-all", action="store_true", help="Deposit all flagship articles")
    parser.add_argument("--limit", type=int, default=10, help="Max articles for batch (default 10)")
    parser.add_argument("--sandbox", action="store_true", help="Use Zenodo Sandbox (testing)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without depositing")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    engine = ZenodoEngine(sandbox=args.sandbox, dry_run=args.dry_run)

    if not engine.is_available() and not args.dry_run:
        print("❌ ZENODO_TOKEN not configured in .env.local")
        sys.exit(1)

    supabase = _get_supabase()
    if not supabase:
        print("❌ Supabase not configured — cannot fetch articles")
        sys.exit(1)

    if args.slug:
        res = supabase.table("articles").select("*").eq("slug", args.slug).maybe_single().execute()
        if not res.data:
            print(f"❌ Article not found: {args.slug}")
            sys.exit(1)
        result = engine.deposit_article(res.data)
        print(json.dumps(result, indent=2))
    elif args.batch_all:
        res = (
            supabase.table("articles")
            .select("*")
            .eq("status", "published")
            .eq("is_flagship", True)
            .is_("doi", "null")
            .limit(args.limit)
            .execute()
        )
        articles = res.data or []
        print(f"📜 Depositing {len(articles)} flagship articles to Zenodo...")
        results = engine.batch_deposit(articles, limit=args.limit)
        for r in results:
            icon = "✅" if r.get("status") == "deposited" else "⚠️"
            print(f"  {icon} {r.get('title', '?')[:60]} → {r.get('status', '?')}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
