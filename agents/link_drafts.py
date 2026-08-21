import argparse
import logging
import os
import re
import urllib.request
from datetime import UTC, datetime
from typing import Any

import yaml
from bs4 import BeautifulSoup

from scribe import call_llm_with_fallback

logger = logging.getLogger(__name__)

DEFAULT_FALLBACK_CHAIN = [
    "gemini/gemini-3.1-flash-lite",
    "groq/llama-3.3-70b-versatile",
]
DEFAULT_TEMPERATURE = 0.6
DEFAULT_MAX_TOKENS = 700
SITE_URL = "https://gworky.com"

DRAFTS_SYSTEM_PROMPT = (
    "You draft short, human-sounding outreach emails for the Groundwork editorial team. "
    "Rules: plain text only (no markdown, no bullet lists); under 140 words; one clear ask; "
    "never invent facts, metrics, or URLs; never mention that AI drafted the message; "
    "no templates — each draft must reference the actual target page and asset."
)

# Personalized outreach sources; never auto-sends.
_SOURCE_TYPES = {
    "resource_page": "a curated resource page",
    "broken_link": "a page with a broken outbound link",
    "mention": "a page that already mentions or references our content",
    "guest_post": "a blog that accepts guest contributions",
    "journalist": "a journalist query platform",
    "link_exchange": "a relevant site in our trusted exchange tribe",
    "niche_edit": "a topical article that could cite our research",
}


def _supabase() -> Any:
    from supabase import create_client  # lazy: keeps module importable offline

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are required")
    return create_client(url, key)


def _fetch_page_context(url: str) -> str:
    """Best-effort: page title + meta description (outreach needs real context)."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Groundwork-LinkDrafts/1.0 (+https://gworky.com)"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode("utf-8", errors="ignore")
        soup = BeautifulSoup(html, "html.parser")
        title_tag = soup.find("title")
        title = (title_tag.get_text(strip=True) if title_tag else "") or ""
        meta = soup.find("meta", attrs={"name": "description"}) or soup.find(
            "meta", attrs={"property": "og:description"}
        )
        content = meta.get("content") if meta else None
        if isinstance(content, list):
            content = content[0] if content else None
        description = (str(content or "").strip()) or ""
        context = (title + " — " + description) if description else title
        return context[:400]
    except Exception as exc:  # noqa: BLE001 — network failure degrades to generic context
        logger.info("could not fetch %s for context: %s", url, exc)
        return ""


def _asset_label(target_asset: str | None) -> str:
    """Human label for the asset we pitch (fall back to the site itself)."""
    if not target_asset:
        return "one of our flagship guides or calculators"
    slug = re.sub(r"[^a-z0-9-]+", "-", target_asset.lower()).strip("-")
    return f"{SITE_URL}/{slug}" if slug else "one of our flagship guides"


def _draft_prompt(prospect: dict[str, Any], page_context: str) -> str:
    asset = _asset_label(prospect.get("target_asset"))
    source = _SOURCE_TYPES.get(prospect.get("source_type", ""), "a relevant page")
    pillar = prospect.get("pillar") or "personal finance, home, health, life, or tech"
    return (
        f"Target page: {prospect.get('url')}\n"
        f"Page context: {page_context or '(unavailable)'}\n"
        f"Source type: {source}\n"
        f"Asset we are pitching: {asset}\n"
        f"Pillar/topic: {pillar}\n\n"
        "Write the outreach email now."
    )


def _log_run(supabase: Any, status: str, items_processed: int, items_published: int, error_log: str | None) -> None:
    try:
        supabase.table("pipeline_runs").insert(
            {
                "agent": "link_drafts",
                "status": status,
                "items_processed": items_processed,
                "items_published": items_published,
                "error_log": error_log,
                "run_at": datetime.now(UTC).isoformat(),
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to write pipeline_runs: %s", exc)


def _fetch_pending_prospects(supabase: Any, limit: int, source_type: str | None) -> list[dict[str, Any]]:
    query = supabase.table("outreach_prospects").select("id,url,contact,source_type,pillar,target_asset,status")
    if source_type:
        query = query.eq("source_type", source_type)
    result = query.eq("status", "todo").order("created_at", asc=True).limit(limit).execute()
    return result.data or []


def _save_draft(supabase: Any, prospect_id: str, draft: str) -> None:
    supabase.table("outreach_prospects").update({"status": "human_review", "draft_outreach": draft}).eq(
        "id", prospect_id
    ).execute()


def run_link_drafts(
    supabase: Any,
    fallback_chain: list[str],
    temperature: float,
    max_tokens: int,
    limit: int,
    source_type: str | None,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Draft outreach for pending prospects; status → human_review. Never sends."""
    prospects = _fetch_pending_prospects(supabase, limit, source_type)
    drafted = 0
    failed = 0
    for prospect in prospects:
        pid = prospect.get("id", "")
        page_context = _fetch_page_context(prospect.get("url", ""))
        try:
            raw = call_llm_with_fallback(
                _draft_prompt(prospect, page_context),
                fallback_chain,
                temperature,
                max_tokens,
                supabase=supabase,
                source_url=prospect.get("url", ""),
            )
            draft = re.sub(r"\s+", " ", raw).strip().strip('"')
            if len(draft) < 20:
                raise RuntimeError("draft too short after cleaning")
            if dry_run:
                logger.info("[dry-run] draft for %s: %.120s…", prospect.get("url", ""), draft)
            else:
                _save_draft(supabase, pid, draft)
            drafted += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.warning("draft failed for %s: %s", prospect.get("url", ""), exc)
    logger.info("link_drafts: drafted=%s failed=%s", drafted, failed)
    return drafted, failed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Groundwork Link Drafts — AI outreach drafting (human-gated, never auto-sends)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Draft but do not write to DB")
    parser.add_argument("--limit", type=int, default=10, help="Max prospects to draft (default 10)")
    parser.add_argument("--source-type", default=None, help="Draft only this source_type")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()

    config_path = os.path.join(os.path.dirname(__file__), "config.yml")
    with open(config_path) as f:
        config = yaml.safe_load(f)
    llm_cfg = config.get("llm", {})
    fallback_chain = llm_cfg.get("fallback_chain", DEFAULT_FALLBACK_CHAIN)
    temperature = llm_cfg.get("temperature", DEFAULT_TEMPERATURE)
    max_tokens = min(llm_cfg.get("max_tokens", DEFAULT_MAX_TOKENS), DEFAULT_MAX_TOKENS)

    try:
        supabase = _supabase()
    except RuntimeError as exc:
        logger.error("DB unavailable: %s", exc)
        return 1

    drafted, failed = run_link_drafts(
        supabase,
        fallback_chain,
        temperature,
        max_tokens,
        args.limit,
        args.source_type,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        print(f"[dry-run] would draft {drafted} prospect(s)")
        return 0

    status = "success" if failed == 0 else "partial"
    _log_run(supabase, status, drafted + failed, drafted, None if failed == 0 else f"{failed} draft(s) failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
