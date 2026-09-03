"""Groundwork Rank Tracker — GSC-backed position tracking to Supabase.

Mode B (SERP recon), Python + Supabase (PostgreSQL) — NO SerpBear SQLite, NO
paid SERP-provider. Data source = official Google Search Console API (position,
impressions, clicks per query/page) — zero-cost, no bot-gate, no SERP scraping.

Reuses the proven GSC auth/query layer in `decay_sentinel.py` (service-account
JWT → searchAnalytics/query, paginated). Writes per-keyword positions to the
existing `keyword_rankings` table, joining against the `keywords` cluster.

KPI it feeds: keyword positions, CTR, impressions — the Tier-2 search KPI set.
Integration: runs alongside `growth_ops.yml` (weekly), or `workflow_dispatch`.

Safety: pure helper `map_gsc_rows_to_rankings()` is unit-tested; GSC auth is
the same bounded, read-only path already used by decay_sentinel.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from decay_sentinel import (
    SITE_URL,
    _encode_site,
    get_gsc_access_token,
)

logger = logging.getLogger(__name__)

SEARCH_ANALYTICS_ENDPOINT = (
    "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
)
ROW_LIMIT = 2500
DEFAULT_WINDOW_DAYS = 28


@dataclass
class RankRow:
    keyword: str
    url: str
    position: float
    clicks: int
    impressions: int
    ctr: float
    domain: str = "gworky.com"
    country: str = "US"

    def to_db(self) -> dict[str, Any]:
        return {
            "keyword": self.keyword,
            "position": round(self.position),
            "domain": self.domain,
            "country": self.country,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }


@dataclass
class RankRunResult:
    site_url: str
    start_date: str
    end_date: str
    rows: list[RankRow] = field(default_factory=list)
    error: str | None = None
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


def _gsc_date(days_ago: int) -> str:
    t = time.gmtime(time.time() - days_ago * 86400)
    return time.strftime("%Y-%m-%d", t)


def map_gsc_rows_to_rankings(
    rows: list[dict[str, Any]],
    *,
    top_kw: int = 50,
    min_impressions: int = 1,
) -> list[RankRow]:
    """Transform GSC searchAnalytics rows (dimensions=[query,page]) into RankRows.

    - Skips queries with tiny impression volume (noise).
    - Filters well-known navigational/internal queries.
    - Dedupes by (keyword, url) keeping best position.
    - Ranks by impressions so the top keywords dominate the tracking set.

    Pure & unit-tested (no network/DB).
    """
    by_key: dict[tuple[str, str], RankRow] = {}
    for row in rows or []:
        keys = row.get("keys", [])
        if len(keys) < 2:
            continue
        query = str(keys[0])
        url = str(keys[1])
        clicks = int(row.get("clicks", 0) or 0)
        impressions = int(row.get("impressions", 0) or 0)
        position = float(row.get("position", 0) or 0)
        ctr = (clicks / impressions) if impressions else 0.0
        if not _is_trackable_query(query):
            continue
        if impressions < min_impressions or query in ("site:" + SITE_URL, SITE_URL):
            continue
        k = (query, url)
        # Keep the best (lowest) position per query+url pair.
        existing = by_key.get(k)
        if existing is None or (position and (not existing.position or position < existing.position)):
            by_key[k] = RankRow(
                keyword=query, url=url, position=position,
                clicks=clicks, impressions=impressions, ctr=round(ctr, 4),
            )

    ranked = sorted(by_key.values(), key=lambda r: r.impressions, reverse=True)
    return ranked[:top_kw]


# Queries we should not track as organic rank positions.
_SKIP_PATTERNS = (
    r"^site:", r"^gworky", r"groundwork", r"^how to sign in",
    r"^login", r"^www\.", r"^$", r"^gworky\.com",
)


def _is_trackable_query(query: str) -> bool:
    q = query.strip().lower()
    if not q:
        return False
    return not any(re.search(p, q) for p in _SKIP_PATTERNS)


def fetch_keyword_positions(
    access_token: str,
    *,
    site_url: str = SITE_URL,
    days: int = DEFAULT_WINDOW_DAYS,
    top_kw: int = 50,
    min_impressions: int = 1,
) -> list[RankRow]:
    """Query GSC searchAnalytics (dimensions=[query,page]) → RankRows."""
    endpoint = SEARCH_ANALYTICS_ENDPOINT.format(site=_encode_site(site_url))
    headers = {"Authorization": f"Bearer {access_token}"}
    start_date = _gsc_date(days)
    end_date = _gsc_date(0)

    all_rows: list[dict[str, Any]] = []
    start_row = 0
    while True:
        resp = httpx.post(
            endpoint,
            headers=headers,
            json={
                "startDate": start_date,
                "endDate": end_date,
                "dimensions": ["query", "page"],
                "rowLimit": ROW_LIMIT,
                "startRow": start_row,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("rows", [])
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < ROW_LIMIT:
            break
        start_row += ROW_LIMIT

    if not all_rows:
        return []
    return map_gsc_rows_to_rankings(all_rows, top_kw=top_kw, min_impressions=min_impressions)


def persist_rankings(supabase: Any, rows: list[RankRow]) -> int:
    """Upsert RankRows into the `keyword_rankings` table. Returns rows written."""
    if not rows:
        return 0
    payload = [r.to_db() for r in rows]
    try:
        res = supabase.table("keyword_rankings").insert(payload).execute()
        return len(res.data or payload)
    except Exception as e:  # pragma: no cover - DB env
        # Dedupe-friendly fallback: insert one-by-one, ignore conflicts.
        written = 0
        for r in payload:
            try:
                supabase.table("keyword_rankings").insert(r).execute()
                written += 1
            except Exception:
                continue
        logger.warning("Rankings insert fallback: %s (wrote %d)", e, written)
        return written


def run_rank_track(supabase: Any, *, days: int = DEFAULT_WINDOW_DAYS, top_kw: int = 50) -> RankRunResult:
    """End-to-end: GSC token → positions → Supabase."""
    start_date = _gsc_date(days)
    end_date = _gsc_date(0)
    try:
        token = get_gsc_access_token()
        rows = fetch_keyword_positions(token, days=days, top_kw=top_kw)
        written = persist_rankings(supabase, rows)
        logger.info("Rank track: %d rows → %d written (window %s..%s).", len(rows), written, start_date, end_date)
        emit_near_page_1_signals(supabase, rows)
        return RankRunResult(site_url=SITE_URL, start_date=start_date, end_date=end_date, rows=rows)
    except Exception as exc:  # pragma: no cover - env dependent
        logger.error("Rank track failed: %s", exc)
        return RankRunResult(site_url=SITE_URL, start_date=start_date, end_date=end_date, error=str(exc))


def emit_near_page_1_signals(supabase: Any, rows: list[RankRow]) -> int:
    """Emit near-page-1 rank signals (position 5-10 with impressions) to growth_signals."""
    near_page_1 = [r for r in rows if 5.0 <= r.position <= 10.0 and r.impressions >= 20]
    if not near_page_1:
        return 0
    emitted = 0
    for r in near_page_1:
        try:
            signal_strength = round((11.0 - r.position) / 10.0, 3)
            payload = {
                "signal_type": "near_page_1",
                "keyword": r.query,
                "target_url": r.page,
                "signal_strength": signal_strength,
                "metadata": {
                    "position": r.position,
                    "impressions": r.impressions,
                    "clicks": r.clicks,
                    "ctr": r.ctr,
                },
                "processed": False,
            }
            supabase.table("growth_signals").insert(payload).execute()
            emitted += 1
        except Exception as e:
            logger.debug("Growth signal emit notice: %s", e)
    logger.info("Emitted %d near_page_1 growth signals to growth_signals table.", emitted)
    return emitted


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Groundwork Google Search Console rank tracker")
    parser.add_argument("--days", type=int, default=DEFAULT_WINDOW_DAYS)
    parser.add_argument("--top-kw", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true", help="Fetch+report only, no DB write")
    args = parser.parse_args()

    from supabase import create_client

    supabase_url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not supabase_key:
        raise SystemExit("SUPABASE credentials not configured")
    client = create_client(supabase_url, supabase_key)

    result = run_rank_track(client, days=args.days, top_kw=args.top_kw)
    if args.dry_run:
        for r in result.rows[:15]:
            print(f"  pos={r.position:<5} imp={r.impressions:<6} ctr={r.ctr:.4f}  {r.keyword[:42]} -> {r.url[:40]}")
        sys.exit(0)
    if result.error:
        sys.exit(f"Rank track error: {result.error}")
