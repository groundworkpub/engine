"""Backfill: migrate existing published articles onto the media pipeline.

Processes articles that still reference a third-party source image (or no
image) and routes them through the same 4-tier pipeline as new content
(``media_uploader.process_image``):

  * Tier 1 source image (>= 600px)  -> WebP 1200x675 + credit overlay -> R2
  * Tier 2 Unsplash hotlink          -> stays external, attribution stored
  * Tier 3 dynamic OG banner         -> R2
  * Tier 4 Pollinations AI visual    -> R2

Articles already self-hosted (``image_url`` starts with ``MEDIA_BASE_URL``)
or already marked ``image_source`` are skipped. Run once per migration wave:

    python backfill_images.py --limit 50
"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Any

from supabase import create_client

from media_uploader import MEDIA_BASE_URL, R2Uploader, process_image

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def fetch_candidates(supabase: Any, limit: int) -> list[dict[str, Any]]:
    cols = "id,slug,title,image_url,image_source,source_url"
    query = supabase.table("articles").select(cols).eq("status", "published")
    # Re-process only rows that are not yet self-hosted and not yet tagged.
    query = query.or_(f"image_url.is.null,image_url.not.like.{MEDIA_BASE_URL}%,image_source.is.null")
    result = query.limit(limit).execute()
    return list(result.data)


def backfill(supabase: Any, limit: int, uploader: R2Uploader | None = None) -> dict[str, int]:
    candidates = fetch_candidates(supabase, limit)
    logger.info("Found %s article(s) to backfill", len(candidates))

    counts = {"processed": 0, "updated": 0, "failed": 0}
    for article in candidates:
        counts["processed"] += 1
        try:
            media = process_image(
                source_url=article.get("source_url") or article.get("image_url"),
                title=article["title"],
                slug=article["slug"],
                uploader=uploader,
            )
            if not media.image_url:
                counts["failed"] += 1
                logger.warning("No image produced for %s: %s", article["slug"], media.errors)
                continue
            if media.image_url == article.get("image_url"):
                logger.info("Unchanged: %s", article["slug"])
                continue
            supabase.table("articles").update(
                {
                    "image_url": media.image_url,
                    "image_source": media.image_source,
                    "image_credit": media.image_credit,
                }
            ).eq("id", article["id"]).execute()
            counts["updated"] += 1
            logger.info("Updated: %s -> %s", article["slug"], media.image_url)
        except Exception:
            counts["failed"] += 1
            logger.exception("Backfill failed for %s", article.get("slug", "?"))

    logger.info("Backfill done: %s", counts)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill article images onto the media pipeline")
    parser.add_argument("--limit", type=int, default=50, help="Max articles per run")
    args = parser.parse_args()

    supabase = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )
    backfill(supabase, args.limit)


if __name__ == "__main__":
    main()
