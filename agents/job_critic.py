"""Job Critic — dedup, validate, slugify, upsert, and deactivate stale jobs."""

import hashlib
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

MIN_DESCRIPTION_LENGTH = 120
STALE_AFTER_DAYS = 14

slugify_re = re.compile(r"[^a-z0-9]+")


def compute_hash(source: str, company: str, title: str, source_url: str) -> str:
    payload = f"{source}:{company}:{title}:{source_url}".strip().lower().encode()
    return hashlib.md5(payload).hexdigest()


def make_slug(company: str, title: str, source_hash: str) -> str:
    base = slugify_re.sub("-", f"{company}-{title}".lower()).strip("-")
    return f"{base[:70].rstrip('-')}-{source_hash[:8]}"


def validate(item: dict[str, Any]) -> tuple[bool, str]:
    title = item.get("title", "").strip()
    company = item.get("company", "").strip()
    source_url = item.get("source_url", "").strip()
    description = item.get("description", "").strip()
    if len(title) < 4:
        return False, "Title too short"
    if len(company) < 2:
        return False, "Missing company"
    if not source_url:
        return False, "Missing source_url"
    if len(description) < MIN_DESCRIPTION_LENGTH:
        return False, f"Description too short ({len(description)} chars)"
    return True, "OK"


def get_existing_hashes(supabase: Any) -> set[str]:
    result = supabase.table("jobs").select("source_hash").execute()
    return {row["source_hash"] for row in result.data if row.get("source_hash")}


def build_rows(
    raw_payload: list[dict[str, Any]],
    existing_hashes: set[str],
) -> tuple[list[dict[str, Any]], int, int]:
    """Normalize → dedup → validate. Returns (rows, new_count, skipped_count)."""
    rows: list[dict[str, Any]] = []
    new_count = 0
    skipped = 0
    seen_in_batch: set[str] = set()
    for item in raw_payload:
        source = item.get("source", "unknown")
        company = item.get("company", "")
        title = item.get("title", "")
        source_url = item.get("source_url", "")
        source_hash = compute_hash(source, company, title, source_url)

        ok, reason = validate(item)
        if not ok:
            skipped += 1
            logger.info("Job skipped (%s): %s — %s", source, title, reason)
            continue
        if source_hash in existing_hashes or source_hash in seen_in_batch:
            skipped += 1
            continue
        seen_in_batch.add(source_hash)

        rows.append(
            {
                "slug": make_slug(company, title, source_hash),
                "title": title.strip(),
                "company": company.strip(),
                "company_url": item.get("company_url"),
                "company_logo": item.get("company_logo"),
                "location": item.get("location") or "Remote",
                "location_type": item.get("location_type") or "remote",
                "employment_type": item.get("employment_type") or "full_time",
                "salary_min": item.get("salary_min"),
                "salary_max": item.get("salary_max"),
                "salary_currency": item.get("salary_currency") or "USD",
                "salary_period": item.get("salary_period") or "yearly",
                "experience_level": item.get("experience_level"),
                "tech_stack": item.get("tech_stack") or [],
                "work_mode": item.get("work_mode"),
                "description": item.get("description") or "",
                "tags": item.get("tags") or [],
                "pillar": item.get("pillar") or "life",
                "source": source,
                "source_url": source_url,
                "source_hash": source_hash,
                "is_active": True,
            }
        )
        new_count += 1
    return rows, new_count, skipped


def deactivate_stale(supabase: Any, seen_hashes: set[str]) -> int:
    """Soft-delete jobs no longer listed by any source, after a stale window."""
    stale_cutoff = (datetime.now(UTC) - timedelta(days=STALE_AFTER_DAYS)).isoformat()

    result = supabase.table("jobs").select("id, source_hash, updated_at").eq("is_active", True).execute()
    to_deactivate = []
    for row in result.data:
        if row["source_hash"] in seen_hashes:
            continue
        # Deactivate only genuinely stale listings (older than the full stale
        # window) — protects fresh jobs during a single source hiccup.
        updated_at = row.get("updated_at") or row.get("published_at")
        if updated_at and updated_at < stale_cutoff:
            to_deactivate.append(row["id"])
    if not to_deactivate:
        return 0
    supabase.table("jobs").update({"is_active": False}).in_("id", to_deactivate).execute()
    logger.info("Deactivated %d stale jobs", len(to_deactivate))
    return len(to_deactivate)


def upsert_jobs(supabase: Any, rows: list[dict[str, Any]]) -> tuple[int, str | None]:
    if not rows:
        return 0, None
    result = supabase.table("jobs").upsert(rows, on_conflict="source_hash").execute()
    if getattr(result, "error", None):
        return 0, str(result.error)
    return len(rows), None
