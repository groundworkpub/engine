#!/usr/bin/env python3
"""
Image Cold-Transform Warmer — Groundwork media.gworky.com
==========================================================
Warms Cloudflare Image Resizing transforms (/cdn-cgi/image/width=N) so the
FIRST real visitor never pays the ~1.5s cold-transform penalty (measured:
RUM LCP p90 7.4s on cold variants). Called post-publish by herald.py and
safe to run standalone:

    python3 agents/image_warmup.py --slug my-article
    python3 agents/image_warmup.py --limit 10   # newest published articles

Zero-cost: HEAD requests only, 3 variants/image, failures never raise.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

import httpx

logger = logging.getLogger("image_warmup")

MEDIA_PREFIX = "https://media.gworky.com/"
WIDTHS = (640, 750, 1200)
UA = "GroundworkWarmBot/1.0 (+https://gworky.com)"


def warm_variants(image_url: str, client: httpx.Client) -> int:
    """Warm one image's transform variants. Returns count of warmed URLs."""
    if not image_url or "media.gworky.com" not in image_url:
        return 0
    path = image_url.split("?")[0].replace(MEDIA_PREFIX, "").replace(
        "cdn-cgi/image/", ""
    ).split("/", 2)[-1] if "/cdn-cgi/" in image_url else image_url.split("?")[0].replace(MEDIA_PREFIX, "")
    warmed = 0
    for width in WIDTHS:
        url = f"{MEDIA_PREFIX}cdn-cgi/image/width={width},quality=80,format=auto/{path}"
        try:
            resp = client.head(url, timeout=15.0)
            ok = resp.status_code == 200
        except Exception as exc:  # noqa: BLE001 — warming must never break pipeline
            logger.warning("warm fail w=%s %s: %s", width, path[:60], exc)
            continue
        logger.info("warm %s w=%s -> %s", "OK" if ok else f"HTTP {resp.status_code}", width, path[:60])
        if ok:
            warmed += 1
    return warmed


def warm_recent(supabase: Any, limit: int = 5, slug: str | None = None) -> dict[str, int]:
    """Warm images for latest published articles. Returns summary counts."""
    query = supabase.table("articles").select("slug,image_url").eq("status", "published")
    if slug:
        query = query.eq("slug", slug)
    rows = query.order("published_at", desc=True).limit(limit).execute().data or []

    images: list[str] = []
    for row in rows:
        if row.get("image_url"):
            images.append(row["image_url"])

    stats = {"articles": len(rows), "images": len(images), "variants_ok": 0}
    if not images:
        return stats

    with httpx.Client(headers={"User-Agent": UA}, follow_redirects=True) as client:
        for image_url in images:
            stats["variants_ok"] += warm_variants(image_url, client)
    return stats


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Warm media.gworky.com transform cache")
    parser.add_argument("--slug", help="Warm a single article's hero image")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get(
        "NEXT_PUBLIC_SUPABASE_ANON_KEY", ""
    )
    if not url or not key:
        logger.error("SUPABASE env tidak lengkap — load .env.local dahulu")
        return 1
    from supabase import create_client  # deferred: module usable without DB for tests

    supabase = create_client(url, key)
    stats = warm_recent(supabase, limit=args.limit, slug=args.slug)
    logger.info("SUMMARY %s", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
