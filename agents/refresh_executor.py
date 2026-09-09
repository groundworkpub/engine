#!/usr/bin/env python3
"""
Groundwork Refresh Executor (G4) — consumes seo_refresh_queue behind a
diff-review gate.

Flow:
  1. ``scan``   — pull queued rows, fetch the live article from Supabase,
     generate a refreshed draft via the LLM router, sanitize it through the
     EditorialHumanizer, and stage it as a JSONB ``proposal`` on the queue row
     (status → in_progress). The live article is NEVER modified here.
  2. ``apply``  — after human review of the proposal diff, write the proposed
     markdown into articles.content, bump updated_at, and close the queue row.
  3. ``reject`` — mark a proposal skipped with a review note.

Guardrails: proposals below MIN_WORDS or above 2.5x original length are refused
(hallucination bloat). LLM is instructed to add no new unsourced claims.

Usage:
    python agents/refresh_executor.py scan [--limit 5] [--dry-run]
    python agents/refresh_executor.py list [--status in_progress]
    python agents/refresh_executor.py apply <queue_id>
    python agents/refresh_executor.py reject <queue_id> [note]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("refresh_executor")

MIN_PROPOSAL_WORDS = 300
MAX_GROWTH_FACTOR = 2.5

try:
    from agents.llm_router import call_llm
except ImportError:  # direct script execution without package context
    from llm_router import call_llm  # type: ignore[no-redef]


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


@dataclass
class StagedProposal:
    queue_id: str
    slug: str
    url: str
    old_words: int
    new_words: int


# ── Pure helpers ─────────────────────────────────────────────────────────────

def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def build_refresh_messages(title: str, content: str) -> list[dict[str, str]]:
    """Prompt enforcing no-new-claims refresh semantics. Pure."""
    system = (
        "You are Groundwork's senior editor refreshing an existing article for "
        "search freshness. HARD RULES: keep every existing fact, number, source "
        "attribution, and link unchanged; invent NO new claims or statistics; "
        "tighten prose to active voice with sentence-case headings; keep the "
        "first paragraph answering the primary query directly; preserve any FAQ "
        "section structure; output English markdown only."
    )
    user = (
        f"Refresh this article titled \"{title}\". Improve flow, remove redundancy, "
        "and update framing where it references relative time (e.g. 'this year') "
        "only when the existing content already supports it.\n\n"
        f"--- ARTICLE START ---\n{content}\n--- ARTICLE END ---\n\n"
        "Return ONLY the full refreshed markdown body, no commentary."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def parse_llm_markdown(raw: str | None) -> str:
    """Strip accidental code fences / preamble. Raises ValueError when empty."""
    if not raw:
        raise ValueError("empty LLM response")
    text = raw.strip()
    fence = re.match(r"^```(?:markdown)?\s*\n(.*?)\n?```\s*$", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1)
    if not text.strip():
        raise ValueError("LLM response contained only fences")
    return text.strip()


def validate_proposal(proposal_md: str, original_md: str) -> tuple[bool, str]:
    """Length guardrails. Pure."""
    new_wc = word_count(proposal_md)
    old_wc = max(word_count(original_md), 1)
    if new_wc < MIN_PROPOSAL_WORDS:
        return False, f"proposal too short ({new_wc} < {MIN_PROPOSAL_WORDS} words)"
    if new_wc > old_wc * MAX_GROWTH_FACTOR:
        return False, f"proposal bloat ({new_wc} > {MAX_GROWTH_FACTOR}x original {old_wc})"
    return True, "ok"


def can_apply(row: dict[str, Any]) -> tuple[bool, str]:
    """A queue row is applicable only when staged and holding a proposal. Pure."""
    if row.get("status") != "in_progress":
        return False, f"status is '{row.get('status')}', expected 'in_progress'"
    proposal = row.get("proposal")
    if not isinstance(proposal, dict) or not proposal.get("content_md"):
        return False, "row has no staged proposal"
    return True, "ok"


def summarize_scan(staged: list[StagedProposal], skipped: int, dry_run: bool) -> str:
    lines = ["🔄 **Refresh Executor Scan**"]
    mode = "(dry-run)" if dry_run else ""
    lines.append(f"- Staged {len(staged)} proposals {mode}, skipped {skipped}")
    for p in staged[:6]:
        delta = p.new_words - p.old_words
        sign = "+" if delta >= 0 else ""
        lines.append(f"  • `{p.slug}` {p.old_words}→{p.new_words} words ({sign}{delta})")
    if not staged and skipped == 0:
        lines.append("✅ Queue empty.")
    return "\n".join(lines)


# ── Supabase I/O ─────────────────────────────────────────────────────────────

def _get_client() -> Any:
    from supabase import create_client

    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("Supabase credentials missing")
    return create_client(url, key)


def fetch_queued(client: Any, limit: int = 5) -> list[dict[str, Any]]:
    res = (
        client.table("seo_refresh_queue")
        .select("*")
        .eq("status", "queued")
        .order("queued_at", desc=True)
        .limit(limit)
        .execute()
    )
    return list(res.data or [])


def fetch_article_by_slug(client: Any, slug: str) -> dict[str, Any] | None:
    res = (
        client.table("articles")
        .select("id, slug, title, content, excerpt, status")
        .eq("slug", slug)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def stage_proposal(client: Any, queue_row: dict[str, Any], proposal_md: str) -> None:
    client.table("seo_refresh_queue").update(
        {
            "proposal": {"content_md": proposal_md},
            "proposed_at": "now()",
            "status": "in_progress",
            "review_note": None,
        }
    ).eq("id", queue_row["id"]).execute()


def apply_proposal(client: Any, queue_id: str) -> bool:
    res = client.table("seo_refresh_queue").select("*").eq("id", queue_id).execute()
    rows = res.data or []
    if not rows:
        logger.error("Queue id %s not found", queue_id)
        return False
    row = rows[0]
    ok, reason = can_apply(row)
    if not ok:
        logger.error("Cannot apply %s: %s", queue_id, reason)
        return False

    slug = row.get("slug") or ""
    article = fetch_article_by_slug(client, slug)
    if not article:
        logger.error("Article '%s' not found for %s", slug, queue_id)
        return False

    client.table("articles").update(
        {"content": row["proposal"]["content_md"], "updated_at": "now()"}
    ).eq("id", article["id"]).execute()
    client.table("seo_refresh_queue").update(
        {
            "status": "refreshed",
            "last_refreshed_at": "now()",
            "review_note": "applied",
        }
    ).eq("id", queue_id).execute()
    logger.info("Applied refresh → %s", slug)
    return True


def reject_proposal(client: Any, queue_id: str, note: str) -> bool:
    res = client.table("seo_refresh_queue").select("id").eq("id", queue_id).execute()
    if not res.data:
        logger.error("Queue id %s not found", queue_id)
        return False
    client.table("seo_refresh_queue").update(
        {"status": "skipped", "review_note": note[:500]}
    ).eq("id", queue_id).execute()
    logger.info("Rejected %s (%s)", queue_id, note)
    return True


def send_telegram(text: str) -> bool:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_FOUNDER_CHAT_ID", "")
    if not bot_token or not chat_id:
        return False
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=15,
        )
        return resp.status_code == 200
    except Exception as exc:  # noqa: BLE001
        logger.warning("Telegram send failed: %s", exc)
        return False


# ── Orchestration ────────────────────────────────────────────────────────────

def run_scan(limit: int = 5, dry_run: bool = False) -> str:
    client = _get_client()
    queued = fetch_queued(client, limit=limit)
    staged: list[StagedProposal] = []
    skipped = 0

    for row in queued:
        slug = row.get("slug") or ""
        if not slug:
            skipped += 1
            continue
        article = fetch_article_by_slug(client, slug)
        if not article or not article.get("content"):
            skipped += 1
            continue

        messages = build_refresh_messages(article["title"], article["content"])
        raw = call_llm(messages, response_format="text", max_tokens=8192)
        try:
            proposal_md = parse_llm_markdown(raw)
        except ValueError as exc:
            logger.warning("LLM output unusable for %s: %s", slug, exc)
            skipped += 1
            continue

        ok, reason = validate_proposal(proposal_md, article["content"])
        if not ok:
            logger.warning("Proposal rejected for %s: %s", slug, reason)
            skipped += 1
            continue

        staged.append(
            StagedProposal(
                queue_id=row["id"],
                slug=slug,
                url=row.get("url", ""),
                old_words=word_count(article["content"]),
                new_words=word_count(proposal_md),
            )
        )
        if not dry_run:
            stage_proposal(client, row, proposal_md)

    summary = summarize_scan(staged, skipped, dry_run)
    if not dry_run and staged:
        send_telegram(summary + "\n\nReview diffs then `apply <queue_id>`.")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Groundwork Refresh Executor (G4)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Stage proposals for queued pages")
    p_scan.add_argument("--limit", type=int, default=5)
    p_scan.add_argument("--dry-run", action="store_true")

    p_list = sub.add_parser("list", help="List queue rows")
    p_list.add_argument("--status", default="in_progress")

    p_apply = sub.add_parser("apply", help="Apply a reviewed proposal")
    p_apply.add_argument("queue_id")

    p_reject = sub.add_parser("reject", help="Reject a proposal")
    p_reject.add_argument("queue_id")
    p_reject.add_argument("note", nargs="?", default="rejected by operator")

    args = parser.parse_args()

    if args.command == "scan":
        print(run_scan(limit=args.limit, dry_run=args.dry_run))
    elif args.command == "list":
        client = _get_client()
        res = (
            client.table("seo_refresh_queue")
            .select("id, slug, status, delta_pct, queued_at, proposed_at")
            .eq("status", args.status)
            .order("queued_at", desc=True)
            .limit(20)
            .execute()
        )
        rows = res.data or []
        if not rows:
            print(f"No rows with status={args.status}")
        for r in rows:
            print(json.dumps(r, default=str))
    elif args.command == "apply":
        sys.exit(0 if apply_proposal(_get_client(), args.queue_id) else 1)
    elif args.command == "reject":
        sys.exit(0 if reject_proposal(_get_client(), args.queue_id, args.note) else 1)


if __name__ == "__main__":
    main()
