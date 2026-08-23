#!/usr/bin/env python3
"""
Groundwork Decay Sentinel — autonomous Measure→Act growth loop.

Reads Google Search Console Search Analytics for gworky.com, compares the last
28 days against the prior 28-day window per URL, and classifies every page:

  DECAY    — meaningful prior clicks, current clicks down ≥ 25%
  EMERGING — current clicks up ≥ 50% with real volume (opportunity to expand)
  STABLE   — everything else

Decayed pages whose article body has not been refreshed within REFRESH_WINDOW
days are queued into the Supabase ``seo_refresh_queue`` table, which downstream
refresh tooling consumes. A Telegram digest reports deltas when credentials are
present; missing Telegram config is non-fatal.

All classification logic is pure and unit-tested in
``agents/tests/test_decay_sentinel.py``.

Usage:
    python agents/decay_sentinel.py                # full run, writes queue
    python agents/decay_sentinel.py --dry-run      # report only, no DB writes
    python agents/decay_sentinel.py --days 14      # shorter comparison window
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("decay_sentinel")

SITE_URL = "https://gworky.com"
GSC_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
SEARCH_ANALYTICS_ENDPOINT = (
    "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
)

# Classification thresholds (tuned for a site with modest but growing volume).
MIN_PREV_CLICKS = 20          # below this, noise dominates the delta
DECAY_THRESHOLD = -0.25       # clicks down 25%+ vs prior window
EMERGING_THRESHOLD = 0.50     # clicks up 50%+
MIN_CURR_CLICKS_EMERGING = 10

REFRESH_WINDOW_DAYS = 90      # only queue pages not refreshed within this window
ROW_LIMIT = 2500              # per-page API page size


@dataclass
class PageWindowStats:
    url: str
    clicks: int = 0
    impressions: int = 0


@dataclass
class DecayVerdict:
    url: str
    classification: str  # DECAY | EMERGING | STABLE
    clicks_prev: int
    clicks_curr: int
    impressions_prev: int
    impressions_curr: int
    delta_pct: float

    def to_row(self, window_label: str = "28d") -> dict[str, Any]:
        asset_type, slug = classify_asset(self.url)
        return {
            "url": self.url,
            "slug": slug,
            "asset_type": asset_type,
            "clicks_prev": self.clicks_prev,
            "clicks_curr": self.clicks_curr,
            "impressions_prev": self.impressions_prev,
            "impressions_curr": self.impressions_curr,
            "delta_pct": round(self.delta_pct, 4),
            "status": "queued",
            "reason": f"{self.classification} vs prior {window_label}",
        }


@dataclass
class SentinelRunResult:
    pages_compared: int = 0
    verdicts: list[DecayVerdict] = field(default_factory=list)

    @property
    def decayed(self) -> list[DecayVerdict]:
        return [v for v in self.verdicts if v.classification == "DECAY"]

    @property
    def emerging(self) -> list[DecayVerdict]:
        return [v for v in self.verdicts if v.classification == "EMERGING"]


def _load_env_local() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env.local"
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'").strip('"')
                if k and k not in os.environ:
                    os.environ[k] = v


_load_env_local()


def _window_days_label(days: int) -> str:
    return f"{days}d"


def get_gsc_access_token() -> str:
    """Exchange the GSC service account for an OAuth2 access token."""
    import jwt

    b64 = os.environ.get("GSC_SERVICE_ACCOUNT_JSON_B64")
    if not b64:
        raise ValueError("GSC_SERVICE_ACCOUNT_JSON_B64 not set")
    sa = json.loads(base64.b64decode(b64).decode("utf-8"))
    now = int(time.time())
    assertion = jwt.encode(
        {
            "iss": sa["client_email"],
            "scope": GSC_SCOPE,
            "aud": GSC_TOKEN_ENDPOINT,
            "iat": now,
            "exp": now + 3600,
        },
        sa["private_key"],
        algorithm="RS256",
    )
    resp = httpx.post(
        GSC_TOKEN_ENDPOINT,
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        },
        timeout=15,
    )
    resp.raise_for_status()
    token: str = resp.json()["access_token"]
    return token


def fetch_window_totals(
    access_token: str,
    start_date: str,
    end_date: str,
    site_url: str = SITE_URL,
) -> dict[str, PageWindowStats]:
    """Fetch per-URL click/impression totals for one date window (paginated)."""
    endpoint = SEARCH_ANALYTICS_ENDPOINT.format(site=_encode_site(site_url))
    headers = {"Authorization": f"Bearer {access_token}"}
    stats: dict[str, PageWindowStats] = {}
    start_row = 0
    while True:
        resp = httpx.post(
            endpoint,
            headers=headers,
            json={
                "startDate": start_date,
                "endDate": end_date,
                "dimensions": ["page"],
                "rowLimit": ROW_LIMIT,
                "startRow": start_row,
            },
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json().get("rows", [])
        if not rows:
            break
        for row in rows:
            keys = row.get("keys", [])
            url = keys[0] if keys else ""
            if not url:
                continue
            entry = stats.setdefault(url, PageWindowStats(url=url))
            entry.clicks += int(row.get("clicks", 0))
            entry.impressions += int(row.get("impressions", 0))
        if len(rows) < ROW_LIMIT:
            break
        start_row += ROW_LIMIT
    return stats


def _encode_site(site_url: str) -> str:
    from urllib.parse import quote

    return quote(site_url, safe="")


def classify_delta(
    prev_clicks: int,
    curr_clicks: int,
    min_prev_clicks: int = MIN_PREV_CLICKS,
    decay_threshold: float = DECAY_THRESHOLD,
    emerging_threshold: float = EMERGING_THRESHOLD,
    min_curr_clicks_emerging: int = MIN_CURR_CLICKS_EMERGING,
) -> str:
    """Pure classification of a click delta between two windows."""
    if curr_clicks <= 0:
        return "STABLE" if prev_clicks < min_prev_clicks else "DECAY"
    if prev_clicks <= 0:
        # New entrant in the current window (e.g. freshly indexed page).
        return "EMERGING" if curr_clicks >= min_curr_clicks_emerging else "STABLE"
    delta = (curr_clicks - prev_clicks) / prev_clicks
    if delta <= decay_threshold:
        return "DECAY"
    if delta >= emerging_threshold and curr_clicks >= min_curr_clicks_emerging:
        return "EMERGING"
    return "STABLE"


def classify_asset(url: str) -> tuple[str, str | None]:
    """Map a live URL to (asset_type, slug). Pure."""
    path = url.replace("https://", "").replace("http://", "")
    parts = [p for p in path.split("/") if p]
    # Drop domain segment
    segs = parts[1:] if parts else []
    if len(segs) >= 1 and segs[0] in ("article", "tools"):
        asset = "tool" if segs[0] == "tools" else "article"
        slug = segs[1].split("?")[0].rstrip("/") if len(segs) >= 2 else None
        return asset, slug
    if len(segs) >= 1 and segs[0] in ("wire",):
        return "wire", segs[1].split("?")[0].rstrip("/") if len(segs) >= 2 else None
    return "other", None


def build_verdicts(
    current: dict[str, PageWindowStats],
    previous: dict[str, PageWindowStats],
) -> list[DecayVerdict]:
    """Join both windows on URL and produce classified verdicts. Pure."""
    verdicts: list[DecayVerdict] = []
    for url, cur in current.items():
        prev = previous.get(url, PageWindowStats(url=url))
        classification = classify_delta(prev.clicks, cur.clicks)
        prev_c = max(prev.clicks, 0)
        delta = (
            (cur.clicks - prev_c) / prev_c if prev_c > 0 else (1.0 if cur.clicks > 0 else 0.0)
        )
        verdicts.append(
            DecayVerdict(
                url=url,
                classification=classification,
                clicks_prev=prev_c,
                clicks_curr=cur.clicks,
                impressions_prev=max(prev.impressions, 0),
                impressions_curr=cur.impressions,
                delta_pct=delta,
            )
        )
    return verdicts


def is_stale_for_refresh(last_refreshed_at: str | None, now: datetime) -> bool:
    """A page qualifies for the refresh queue when never refreshed or older
    than REFRESH_WINDOW_DAYS. Pure given explicit ``now``."""
    if not last_refreshed_at:
        return True
    try:
        ts = datetime.fromisoformat(last_refreshed_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return (now - ts) > timedelta(days=REFRESH_WINDOW_DAYS)


def filter_refresh_candidates(
    verdicts: list[DecayVerdict],
    updated_at_by_slug: dict[str, str | None],
    now: datetime,
) -> list[DecayVerdict]:
    """Keep only DECAY pages that have gone stale since last refresh."""
    candidates: list[DecayVerdict] = []
    for v in verdicts:
        if v.classification != "DECAY":
            continue
        _, slug = classify_asset(v.url)
        updated_at = updated_at_by_slug.get(slug or "")
        if is_stale_for_refresh(updated_at, now):
            candidates.append(v)
    return candidates


def fetch_article_updated_at(supabase_client: Any, slug: str) -> str | None:
    """Look up articles.updated_at for a slug; returns None on any failure."""
    try:
        res = (
            supabase_client.table("articles")
            .select("updated_at")
            .eq("slug", slug)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0].get("updated_at") if rows else None
    except Exception as exc:  # noqa: BLE001 — resilience over strictness here
        logger.warning("updated_at lookup failed for %s: %s", slug, exc)
        return None


def upsert_queue(rows: list[dict[str, Any]]) -> int:
    """Upsert refresh-queue rows keyed by unique ``url``. Returns count."""
    from supabase import create_client

    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        logger.error("Supabase credentials missing; cannot write seo_refresh_queue.")
        return 0
    client = create_client(url, key)
    resp = client.table("seo_refresh_queue").upsert(rows, on_conflict="url").execute()
    count = len(resp.data) if resp.data else 0
    logger.info("Upserted %d rows → seo_refresh_queue", count)
    return count


def send_telegram_digest(result: SentinelRunResult) -> bool:
    """Best-effort Telegram delta report; False when unconfigured or failed."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_FOUNDER_CHAT_ID", "")
    if not bot_token or not chat_id:
        logger.info("Telegram not configured; skipping digest.")
        return False
    text = build_digest_text(result)
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=15,
        )
        return resp.status_code == 200
    except Exception as exc:  # noqa: BLE001
        logger.warning("Telegram digest failed: %s", exc)
        return False


def build_digest_text(result: SentinelRunResult) -> str:
    lines = [
        "📉 **Decay Sentinel**",
        f"- Pages compared: {result.pages_compared}",
        f"- Decaying: {len(result.decayed)}",
        f"- Emerging: {len(result.emerging)}",
    ]
    for v in result.decayed[:6]:
        lines.append(f"  • ↓ {round(v.delta_pct * 100)}% `{v.url}` ({v.clicks_prev}→{v.clicks_curr})")
    for v in result.emerging[:4]:
        lines.append(f"  • ↑ {round(v.delta_pct * 100)}% `{v.url}` ({v.clicks_prev}→{v.clicks_curr})")
    if not result.decayed and not result.emerging:
        lines.append("✅ No significant movement.")
    return "\n".join(lines)


WINDOW_DAYS_LABEL = "28d"


def main() -> None:
    global WINDOW_DAYS_LABEL
    parser = argparse.ArgumentParser(description="Groundwork Decay Sentinel (GSC → refresh queue)")
    parser.add_argument("--dry-run", action="store_true", help="Report only, no DB writes")
    parser.add_argument("--site", default=SITE_URL, help="GSC property URL")
    args = parser.parse_args()

    days = 28
    WINDOW_DAYS_LABEL = _window_days_label(days)
    end = datetime.now(UTC).date()
    curr_start = end - timedelta(days=days)
    prev_end = curr_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days)

    token = get_gsc_access_token()
    logger.info("Fetching current window %s..%s", curr_start.isoformat(), end.isoformat())
    current = fetch_window_totals(token, curr_start.isoformat(), end.isoformat(), args.site)
    logger.info("Fetching previous window %s..%s", prev_start.isoformat(), prev_end.isoformat())
    previous = fetch_window_totals(token, prev_start.isoformat(), prev_end.isoformat(), args.site)

    result = SentinelRunResult(pages_compared=len(current))
    result.verdicts = build_verdicts(current, previous)
    logger.info(
        "Compared %d pages: %d decaying, %d emerging",
        result.pages_compared,
        len(result.decayed),
        len(result.emerging),
    )

    from supabase import create_client

    supabase_client = None
    s_url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    s_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if s_url and s_key:
        supabase_client = create_client(s_url, s_key)

    candidates = filter_refresh_candidates(result.verdicts, {}, datetime.now(UTC))
    if supabase_client is not None:
        enriched: dict[str, str | None] = {}
        for v in candidates:
            _, slug = classify_asset(v.url)
            if slug and slug not in enriched:
                enriched[slug] = fetch_article_updated_at(supabase_client, slug)
        candidates = filter_refresh_candidates(result.verdicts, enriched, datetime.now(UTC))

    rows = [v.to_row(WINDOW_DAYS_LABEL) for v in candidates]
    if args.dry_run:
        logger.info("DRY-RUN: would queue %d refresh candidates.", len(rows))
    elif rows:
        upsert_queue(rows)

    send_telegram_digest(result)


if __name__ == "__main__":
    main()
