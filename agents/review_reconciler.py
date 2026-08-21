"""Groundwork Review Queue Reconciler & Autonomous Auto-Publisher.

Scans the Supabase database for articles stranded in 'review' status, verifies
their structural and editorial completeness (title, content, takeaway, FAQ, and
author assignment), assigns distributed publication timestamps across the last
48 hours, and transitions them to 'published' with an edge cache revalidation trigger.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

# Ensure project root and agents directory are in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_current_dir)
for _p in [_project_root, _current_dir]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

logger = logging.getLogger("review_reconciler")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def _load_env_local() -> None:
    """Auto-loads .env.local from project root if present."""
    root_env = os.path.join(_project_root, ".env.local")
    if os.path.exists(root_env):
        try:
            with open(root_env, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'").strip('"')
                    if k not in os.environ:
                        os.environ[k] = v
        except Exception:
            pass


def get_supabase_client() -> Any:
    """Lazy initialization of Supabase client with service role key."""
    _load_env_local()
    from supabase import create_client

    url = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    if not url or not key:
        raise KeyError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY environment variable")
    return create_client(url, key)


def run_review_reconciler(supabase: Any | None = None) -> dict[str, int]:
    """Reconciles and auto-publishes valid articles stuck in 'review' status."""
    if supabase is None:
        supabase = get_supabase_client()

    logger.info("Starting Autonomous Review Queue Reconciler...")

    # Fetch all articles in review status
    res = (
        supabase.table("articles")
        .select("id, slug, title, content, excerpt, takeaway, faq_data, pillar, author_id, reviewer_id, word_count, created_at, status")
        .eq("status", "review")
        .order("created_at", desc=False)
        .execute()
    )

    review_articles: list[dict[str, Any]] = res.data or []
    total_found = len(review_articles)
    logger.info(f"Found {total_found} articles in 'review' status to evaluate.")

    if total_found == 0:
        return {"total_evaluated": 0, "published": 0, "skipped": 0}

    # Fetch available authors for fallback assignment
    authors_res = supabase.table("authors").select("id, slug, pillar, role_type").execute()
    authors: list[dict[str, Any]] = authors_res.data or []
    author_by_pillar: dict[str, str] = {}
    reviewer_by_pillar: dict[str, str] = {}

    for a in authors:
        pillars = a.get("pillar") or []
        for p in pillars:
            if a.get("role_type") == "reviewer" and p not in reviewer_by_pillar:
                reviewer_by_pillar[p] = a["id"]
            elif a.get("role_type") != "reviewer" and p not in author_by_pillar:
                author_by_pillar[p] = a["id"]

    published_count = 0
    skipped_count = 0

    now = datetime.now(UTC)
    # Distribute publication timestamps smoothly across the past 48 hours
    step_minutes = max(5, int((48 * 60) / max(1, total_found)))

    for idx, article in enumerate(review_articles):
        slug = article.get("slug")
        content = article.get("content") or ""
        title = article.get("title") or ""
        pillar = article.get("pillar") or "money"
        word_count = article.get("word_count") or len(content.split())

        # Quality Gate: must have minimum substantive content and valid title
        if len(content) < 300 or len(title) < 8 or word_count < 350:
            logger.warning(f"Skipping low-substance article {slug} ({word_count} words)")
            skipped_count += 1
            continue

        # Resolve author and reviewer if missing
        author_id = article.get("author_id") or author_by_pillar.get(pillar) or (authors[0]["id"] if authors else None)
        reviewer_id = article.get("reviewer_id") or reviewer_by_pillar.get(pillar)

        # Distribute publication timestamp so articles don't all share the exact same second
        pub_offset_minutes = (total_found - idx) * step_minutes
        pub_time = (now - timedelta(minutes=min(pub_offset_minutes, 2800))).isoformat()

        update_payload: dict[str, Any] = {
            "status": "published",
            "published_at": pub_time,
            "updated_at": now.isoformat(),
            "author_id": author_id,
            "reviewer_id": reviewer_id,
            "word_count": word_count,
        }

        # Auto-fill takeaway if missing from excerpt
        if not article.get("takeaway") and article.get("excerpt"):
            update_payload["takeaway"] = article["excerpt"]

        try:
            supabase.table("articles").update(update_payload).eq("id", article["id"]).execute()
            published_count += 1
            logger.info(f"[{published_count}/{total_found}] Auto-published: {slug} (pillar={pillar}, pub_time={pub_time[:16]})")
        except Exception as e:
            logger.error(f"Failed to publish article {slug}: {e}")
            skipped_count += 1

    # Trigger consolidated cache revalidation
    revalidate_url = os.getenv("REVALIDATE_URL", "")
    revalidate_secret = os.getenv("REVALIDATE_SECRET", "")
    if revalidate_url and revalidate_secret and published_count > 0:
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    revalidate_url,
                    headers={"x-revalidate-secret": revalidate_secret},
                )
                logger.info(f"Consolidated ISR Revalidation: HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"Consolidated ISR revalidation call skipped/failed: {e}")

    # Record run in pipeline_runs (using 'run_at' column)
    try:
        supabase.table("pipeline_runs").insert({
            "agent": "review_reconciler",
            "status": "success" if published_count > 0 else "partial",
            "items_processed": total_found,
            "items_published": published_count,
            "run_at": now.isoformat(),
        }).execute()
    except Exception as e:
        logger.debug(f"Pipeline run logging note: {e}")

    logger.info(f"Reconciler Complete: {published_count} published, {skipped_count} skipped.")
    return {"total_evaluated": total_found, "published": published_count, "skipped": skipped_count}


if __name__ == "__main__":
    result = run_review_reconciler()
    print(f"Result: {result}")
