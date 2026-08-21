"""Jobs pipeline orchestrator — run in GitHub Actions on a cron schedule.

Usage:
    python jobs_pipeline.py [--dry-run] [--source arbeitnow jobicy]

Env required: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY.
Exit code 0 on success/partial, 1 on total failure.
"""

import argparse
import contextlib
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

from supabase import create_client

from job_critic import (
    build_rows,
    deactivate_stale,
    get_existing_hashes,
    upsert_jobs,
)
from job_scouter import run_job_scouter
from scribe import ping_bing, ping_indexnow, trigger_gsc_indexing

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Groundwork jobs pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Fetch + validate, do not write")
    parser.add_argument("--source", nargs="*", default=None, help="Job sources to enable")
    return parser.parse_args()


def log_run(  # noqa: PLR0913
    supabase: Any,
    status: str,
    items_processed: int,
    items_published: int,
    error_log: str | None,
) -> None:
    try:
        supabase.table("pipeline_runs").insert(
            {
                "agent": "jobs",
                "status": status,
                "items_processed": items_processed,
                "items_published": items_published,
                "error_log": error_log,
                "run_at": datetime.now(UTC).isoformat(),
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to write pipeline_runs: %s", exc)


def main() -> int:
    from pathlib import Path

    env_file = Path(__file__).resolve().parent.parent / ".env.local"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()

    supabase_url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not supabase_key:
        logger.error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
        return 1

    try:
        supabase = create_client(supabase_url, supabase_key)
        raw_items = run_job_scouter(args.source)
        if not raw_items:
            logger.error("No jobs harvested from any source")
            log_run(supabase, "error", 0, 0, "No jobs harvested from any source")
            return 1

        existing_hashes = set() if args.dry_run else get_existing_hashes(supabase)
        rows, new_count, skipped = build_rows(raw_items, existing_hashes)

        logger.info("Harvested=%d  new=%d  dedup/skipped=%d", len(raw_items), new_count, skipped)

        if args.dry_run:
            for row in rows[:5]:
                logger.info("DRY-RUN job: %s — %s (%s)", row["title"], row["company"], row["pillar"])
            logger.info("DRY-RUN complete: %d ready to upsert", len(rows))
            return 0

        published, error = upsert_jobs(supabase, rows)
        if error:
            logger.error("Upsert failed: %s", error)
            log_run(supabase, "error", len(raw_items), 0, error)
            return 1

        seen = {row["source_hash"] for row in rows} | existing_hashes
        deactivated = deactivate_stale(supabase, seen)

        if published:
            site_url = os.environ.get("SITE_URL", "")
            if site_url:
                urls = [f"{site_url.rstrip('/')}/jobs/{row['slug']}" for row in rows]
                ping_indexnow(site_url, urls)
                ping_bing(site_url, urls)
                trigger_gsc_indexing(urls)

        logger.info("Published=%d  deactivated=%d", published, deactivated)
        log_run(supabase, "success", len(raw_items), published, None)
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.exception("Jobs pipeline crashed")
        with contextlib.suppress(Exception):
            log_run(supabase, "error", 0, 0, str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
