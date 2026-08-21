import argparse
import logging
import os
import re
import urllib.request
from datetime import UTC, datetime
from typing import Any

import feedparser
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BRAND_DOMAIN = "gworky.com"
DEFAULT_FEED_TIMEOUT_SECONDS = 10

# Keyword signals → reclamation priority tier (T3.9: roundup > report > news).
_ROUNDUP_PATTERNS = re.compile(
    r"\b(best|top|roundup|round-up|list(ing|icle)?|greatest|must-(read|use)|favorites?)\b",
    re.IGNORECASE,
)
_REPORT_PATTERNS = re.compile(
    r"\b(report|study|survey|data|research|analysis|index|benchmark)\b",
    re.IGNORECASE,
)
_NEWS_PATTERNS = re.compile(
    r"\b(news|breaks?|announces?|reports?|updates?|coverage)\b",
    re.IGNORECASE,
)


def _supabase() -> Any:
    from supabase import create_client  # lazy: keeps module importable offline

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are required")
    return create_client(url, key)


def _fetch_feed(feed_url: str) -> list[dict[str, Any]]:
    """Fetch and parse one RSS feed into normalized mention candidates."""
    req = urllib.request.Request(
        feed_url,
        headers={"User-Agent": "Groundwork-LinkWatch/1.0 (+https://gworky.com)"},
    )
    with urllib.request.urlopen(req, timeout=DEFAULT_FEED_TIMEOUT_SECONDS) as response:
        parsed = feedparser.parse(response.read())
    if parsed.bozo and not parsed.entries:
        logger.warning("feed %s failed to parse: %s", feed_url, parsed.bozo_exception)
        return []
    out: list[dict[str, Any]] = []
    for entry in parsed.entries:
        url = str(entry.get("link") or "").strip()
        if not url:
            continue
        out.append(
            {
                "url": url,
                "title": str(entry.get("title") or "").strip(),
                "summary": str((entry.get("summary") or entry.get("description") or "").strip()),
                "source": "google_alerts",
            }
        )
    return out


def _page_links_to_brand(html: str) -> bool:
    """Does the mention page actually link to gworky.com?"""
    if not html:
        return False
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = (str(a.get("href") or "")).strip()
        if BRAND_DOMAIN in href:
            return True
    return False


def _classify_priority(title: str, summary: str) -> str:
    """Heuristic priority from page context (roundup > report > news > other)."""
    haystack = f"{title} {summary}"
    if _ROUNDUP_PATTERNS.search(haystack):
        return "roundup"
    if _REPORT_PATTERNS.search(haystack):
        return "report"
    if _NEWS_PATTERNS.search(haystack):
        return "news"
    return "other"


def _fetch_mention_html(url: str) -> str:
    """Best-effort fetch of a mention page; empty string on any failure."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Groundwork-LinkWatch/1.0 (+https://gworky.com)"},
        )
        with urllib.request.urlopen(req, timeout=DEFAULT_FEED_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8", errors="ignore")
    except Exception as exc:  # noqa: BLE001 — network/HTTP failures are non-fatal
        logger.info("could not fetch %s for link check: %s", url, exc)
        return ""


def _existing_mention_urls(supabase: Any) -> set[str]:
    result = supabase.table("brand_mentions").select("mention_url").execute()
    return {row["mention_url"] for row in (result.data or [])}


def _log_run(supabase: Any, status: str, items_processed: int, items_published: int, error_log: str | None) -> None:
    try:
        supabase.table("pipeline_runs").insert(
            {
                "agent": "link_watch",
                "status": status,
                "items_processed": items_processed,
                "items_published": items_published,
                "error_log": error_log,
                "run_at": datetime.now(UTC).isoformat(),
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to write pipeline_runs: %s", exc)


def run_link_watch(supabase: Any, feeds: list[str], dry_run: bool = False) -> tuple[int, int, int]:
    """Scan mention feeds, classify, upsert new brand mentions.

    Returns (processed, inserted, failures). Mentions whose pages we could
    not reach are still recorded (status uncontacted) — the link check is
    best-effort and can be re-run by the human in the dashboard.
    """
    existing: set[str] = set()
    if supabase is not None:
        existing = _existing_mention_urls(supabase)
    candidates: list[dict[str, Any]] = []
    for feed in feeds:
        candidates.extend(_fetch_feed(feed))

    inserted = 0
    failures = 0
    for candidate in candidates:
        url = candidate["url"]
        if url in existing:
            continue
        html = _fetch_mention_html(url)
        if not html:
            failures += 1
        linked = _page_links_to_brand(html)
        row: dict[str, Any] = {
            "mention_url": url,
            "mention_text": f"{candidate['title']}\n{candidate['summary']}".strip() or None,
            "linked": linked,
            "source": candidate["source"],
            "status": "no_link_needed" if linked else "uncontacted",
            "priority": _classify_priority(candidate["title"], candidate["summary"]),
        }
        if not dry_run:
            assert supabase is not None, "supabase client required when not dry_run"
            try:
                supabase.table("brand_mentions").upsert(row, on_conflict="mention_url").execute()
                inserted += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("failed to upsert mention %s: %s", url, exc)
                failures += 1
        else:
            inserted += 1
            logger.info("[dry-run] would insert %s (%s, linked=%s)", url, row["priority"], linked)

    processed = len(candidates)
    logger.info("link_watch: processed=%s new=%s fetch_failures=%s", processed, inserted, failures)
    return processed, inserted, failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Groundwork Link Watch — brand mention discovery & reclamation triage")
    parser.add_argument("--dry-run", action="store_true", help="Scan and classify, do not write to DB")
    parser.add_argument(
        "--feeds",
        nargs="*",
        default=None,
        help="RSS feeds to scan (default: $GOOGLE_ALERTS_RSS_URL plus $EXTRA_MENTION_FEEDS)",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()

    feeds = list(args.feeds or [])
    if not feeds:
        for key in ("GOOGLE_ALERTS_RSS_URL", "EXTRA_MENTION_FEEDS"):
            value = os.getenv(key)
            if value:
                feeds.extend(f for f in value.split(",") if f.strip())
    if not feeds:
        # Default fallback to Google News brand search for gworky.com / Groundwork Media
        feeds = ["https://news.google.com/rss/search?q=gworky.com+OR+%22Groundwork+Media%22&hl=en-US&gl=US&ceid=US:en"]

    try:
        supabase = _supabase()
    except RuntimeError as exc:
        if args.dry_run:
            print(f"[dry-run] skipping DB: {exc}")
            supabase = None
        else:
            logger.error("DB unavailable: %s", exc)
            return 1

    if supabase is None:
        processed, inserted, failures = run_link_watch(None, feeds, dry_run=True)
    else:
        processed, inserted, failures = run_link_watch(supabase, feeds, dry_run=args.dry_run)

    if args.dry_run:
        print(f"[dry-run] scanned {processed} mention(s), {inserted} new")
        return 0

    if supabase is None:
        return 1
    status = "success" if failures == 0 else "partial"
    _log_run(supabase, status, processed, inserted, None if failures == 0 else f"{failures} fetch failure(s)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
