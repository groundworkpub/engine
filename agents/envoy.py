"""Groundwork Envoy (Agent 6, Tier 4) — Automated Digital PR pipeline.

Ingests journalist query digests (HARO/Featured.com, Source of Sources,
Qwoted free tier, Connectively, SourceBottle) via RSS feeds and/or an
email-digest text/CSV file, evaluates each query against our research
pillars, and drafts data-backed expert commentary as the *Groundwork brand*
(never a virtual persona — see docs/LINK-BUILDING-IMPLEMENTATION-PLAN.md).

All output lands in ``outreach_prospects`` with ``source_type='journalist'``
and status ``human_review`` — the Envoy NEVER sends anything. A human
approves each response in the dashboard before any message goes out.

Run:  python agents/envoy.py [--dry-run] [--feed <rss> ...] [--input <file>]
"""

import argparse
import csv
import logging
import os
import re
import urllib.request
from datetime import UTC, datetime
from typing import Any

import feedparser
import yaml

from scribe import call_llm_with_fallback

logger = logging.getLogger(__name__)

SITE_URL = "https://gworky.com"
DEFAULT_FEED_TIMEOUT_SECONDS = 10

DEFAULT_FALLBACK_CHAIN = [
    "gemini/gemini-3.1-flash-lite",
    "groq/llama-3.3-70b-versatile",
]
DEFAULT_TEMPERATURE = 0.4
DEFAULT_MAX_TOKENS = 600

# Digital PR voice — data-backed brand commentary. NEVER persona bylines,
# NEVER invented metrics, NEVER fabricated URLs.
ENVOY_SYSTEM_PROMPT = (
    "You write quotable expert commentary for the Groundwork brand — a "
    "Tier-1 evidence-based research platform covering money, health, home, "
    "life, and tech decisions. A journalist will quote this response in a "
    "news article. Rules: 2-4 sentences, plain English, answer the question "
    "directly; back every claim with data but never invent statistics or "
    "figures; never cite sources you cannot name; never reference Groundwork's "
    "own articles, tools, or URLs in the commentary; never claim credentials "
    "or expert titles for a named individual — speak as the Groundwork "
    "research team; no markdown; no filler like 'As experts say'."
)

# Pillar keyword signals for query relevance routing.
_PILLAR_PATTERNS: dict[str, re.Pattern[str]] = {
    "money": re.compile(
        r"\b(mortgage|refinanc|interest rate|savings|credit|debt|loan|insurance|retire|invest|tax|bank)\b",
        re.IGNORECASE,
    ),
    "body": re.compile(
        r"\b(health|fitness|nutrition|diet|sleep|workout|exercise|blood pressure|cholesterol|longevity|wellness)\b",
        re.IGNORECASE,
    ),
    "home": re.compile(
        r"\b(solar|hvac|heat pump|roof|insulation|security|door lock|home improvement|energy|generator|homeowner)\b",
        re.IGNORECASE,
    ),
    "life": re.compile(
        r"\b(travel|vacation|legal|estate planning|career|salary|auto|carshare|insurance)\b",
        re.IGNORECASE,
    ),
    "tech": re.compile(
        r"\b(smart home|software|ai |artificial intelligence|gadget|wifi|router|device|app|cybersecurity)\b",
        re.IGNORECASE,
    ),
}

# Source platforms allowed in the journalist outreach queue (all free tier).
_ALLOWED_SOURCES = ("haro", "featured", "source_of_sources", "qwoted", "connectively", "sourcebottle")


def _supabase() -> Any:
    from supabase import create_client  # lazy: keeps module importable offline

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are required")
    return create_client(url, key)


def _normalize_source(raw: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", (raw or "").lower()).strip("_")
    return value if value in _ALLOWED_SOURCES else "haro"


def _classify_pillar(title: str, summary: str) -> str | None:
    """First matching pillar signal, or None if not relevant to Groundwork."""
    haystack = f"{title}\n{summary}"
    for pillar, pattern in _PILLAR_PATTERNS.items():
        if pattern.search(haystack):
            return pillar
    return None


def _extract_queries_feed(feed_url: str, source: str) -> list[dict[str, Any]]:
    """Parse an RSS feed of journalist queries into normalized candidates."""
    req = urllib.request.Request(
        feed_url,
        headers={"User-Agent": "Groundwork-Envoy/1.0 (+https://gworky.com)"},
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
                "deadline": str(entry.get("published") or entry.get("updated") or "").strip(),
                "source": source,
            }
        )
    return out


def _extract_queries_input(path: str, source: str) -> list[dict[str, Any]]:
    """Parse a pasted digest file (CSV or plain text) into normalized candidates.

    CSV columns: url,title,summary,deadline (header optional). Plain text: one
    query per block separated by blank lines; first line = title, rest = body.
    """
    with open(path, encoding="utf-8") as f:
        content = f.read()
    candidates: list[dict[str, Any]] = []

    if path.endswith(".csv"):
        reader = csv.DictReader(line for line in content.splitlines() if line.strip())
        for row in reader:
            title = (row.get("title") or row.get("query") or "").strip()
            summary = (row.get("summary") or row.get("description") or "").strip()
            url = (row.get("url") or "").strip()
            if not title and not summary:
                continue
            candidates.append(
                {
                    "url": url or f"{SITE_URL}/#envoy-{abs(hash(title))}",
                    "title": title,
                    "summary": summary,
                    "deadline": (row.get("deadline") or "").strip(),
                    "source": source,
                }
            )
        return candidates

    for block in re.split(r"\n\s*\n", content):
        lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
        if not lines:
            continue
        candidates.append(
            {
                "url": f"{SITE_URL}/#envoy-{abs(hash(lines[0]))}",
                "title": lines[0],
                "summary": " ".join(lines[1:]),
                "deadline": "",
                "source": source,
            }
        )
    return candidates


def _existing_query_urls(supabase: Any) -> set[str]:
    result = supabase.table("outreach_prospects").select("url").eq("source_type", "journalist").execute()
    return {row["url"] for row in (result.data or [])}


def _target_asset(pillar: str | None) -> str | None:
    """Map a pillar to our flagship research asset for follow-up context."""
    assets = {
        "money": "research/money-index",
        "body": "research/body-index",
        "home": "research/home-index",
        "life": "research/life-index",
        "tech": "research/tech-index",
    }
    return assets.get(pillar or "")


def _commentary_prompt(query: dict[str, Any], pillar: str) -> str:
    return (
        f"Journalist query: {query['title']}\n"
        f"Details: {query['summary'][:1200]}\n"
        f"Relevant Groundwork research pillar: {pillar}\n\n"
        "Write the expert commentary for the Groundwork research team now."
    )


def _log_run(supabase: Any, status: str, items_processed: int, items_published: int, error_log: str | None) -> None:
    try:
        supabase.table("pipeline_runs").insert(
            {
                "agent": "envoy",
                "status": status,
                "items_processed": items_processed,
                "items_published": items_published,
                "error_log": error_log,
                "run_at": datetime.now(UTC).isoformat(),
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to write pipeline_runs: %s", exc)


def _save_prospect(
    supabase: Any,
    query: dict[str, Any],
    pillar: str,
    source: str,
    draft: str | None,
) -> None:
    row: dict[str, Any] = {
        "url": query["url"],
        "source_type": "journalist",
        "pillar": pillar,
        "target_asset": _target_asset(pillar),
        "status": "human_review" if draft else "todo",
        "contact": query.get("deadline") or None,
        "gray_tier": None,  # Digital PR is white-hat (ARCHITECTURE.md §13 Tier 4).
    }
    if draft:
        row["draft_outreach"] = draft
    supabase.table("outreach_prospects").upsert(row, on_conflict="url").execute()


def run_envoy(
    supabase: Any,
    feeds: list[str],
    input_files: list[str],
    fallback_chain: list[str],
    temperature: float,
    max_tokens: int,
    draft_commentary: bool,
    dry_run: bool = False,
) -> tuple[int, int, int]:
    """Ingest journalist queries; draft brand commentary for relevant ones.

    Returns (processed, drafted, failed). Never sends. Relevant queries
    without a successful draft are still recorded (status todo) so the human
    can respond manually.
    """
    candidates: list[dict[str, Any]] = []
    for feed_url in feeds:
        source = _normalize_source(feed_url.split("/")[2] if "//" in feed_url else "haro")
        candidates.extend(_extract_queries_feed(feed_url, source))
    for path in input_files:
        source = _normalize_source(os.path.splitext(os.path.basename(path))[0])
        candidates.extend(_extract_queries_input(path, source))

    existing = _existing_query_urls(supabase) if supabase is not None else set()

    drafted = 0
    failed = 0
    processed = 0
    for query in candidates:
        url = query["url"]
        if url in existing:
            continue
        pillar = _classify_pillar(query["title"], query["summary"])
        if not pillar:
            logger.info("skip (not relevant): %s", query["title"][:80])
            continue

        draft: str | None = None
        if draft_commentary:
            try:
                raw = call_llm_with_fallback(
                    _commentary_prompt(query, pillar),
                    fallback_chain,
                    temperature,
                    max_tokens,
                    supabase=supabase,
                    source_url=url,
                    system_prompt=ENVOY_SYSTEM_PROMPT,
                )
                draft = re.sub(r"\s+", " ", raw).strip().strip('"')
                if len(draft) < 20:
                    raise RuntimeError("commentary too short after cleaning")
                drafted += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.warning("commentary draft failed for %s: %s", url, exc)

        processed += 1
        if dry_run:
            logger.info("[dry-run] would queue %s (%s, drafted=%s)", url, pillar, draft is not None)
        else:
            try:
                _save_prospect(supabase, query, pillar, query["source"], draft)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.warning("failed to save prospect %s: %s", url, exc)

    logger.info("envoy: processed=%s drafted=%s failed=%s", processed, drafted, failed)
    return processed, drafted, failed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Groundwork Envoy (Agent 6) — journalist query ingestion & brand commentary (human-gated)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Ingest and draft, do not write to DB")
    parser.add_argument("--feed", action="append", default=None, help="RSS feed of journalist queries (repeatable)")
    parser.add_argument("--input", action="append", default=None, help="Path to digest text/CSV file (repeatable)")
    parser.add_argument("--no-draft", action="store_true", help="Queue relevant queries without LLM commentary")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()

    config_path = os.path.join(os.path.dirname(__file__), "config.yml")
    with open(config_path) as f:
        config = yaml.safe_load(f)
    envoy_cfg = config.get("envoy", {})
    llm_cfg = config.get("llm", {})
    fallback_chain = llm_cfg.get("fallback_chain", DEFAULT_FALLBACK_CHAIN)
    temperature = llm_cfg.get("temperature", DEFAULT_TEMPERATURE)
    max_tokens = min(llm_cfg.get("max_tokens", DEFAULT_MAX_TOKENS), DEFAULT_MAX_TOKENS)

    feeds = list(args.feed or [])
    if not feeds:
        feeds = [url for url in (envoy_cfg.get("feeds") or []) if url]
    input_files = list(args.input or [])

    if not feeds and not input_files:
        logger.error("no query sources configured — pass --feed/--input or add `envoy.feeds` to config.yml")
        return 1

    try:
        supabase = _supabase()
    except RuntimeError as exc:
        if args.dry_run:
            print(f"[dry-run] skipping DB: {exc}")
            supabase = None
        else:
            logger.error("DB unavailable: %s", exc)
            return 1

    processed, drafted, failed = run_envoy(
        supabase,
        feeds,
        input_files,
        fallback_chain,
        temperature,
        max_tokens,
        draft_commentary=not args.no_draft,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        print(f"[dry-run] would queue {processed} query(s), {drafted} with commentary")
        return 0

    if supabase is None:
        return 1
    status = "success" if failed == 0 else "partial"
    _log_run(supabase, status, processed, drafted, None if failed == 0 else f"{failed} failure(s)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
