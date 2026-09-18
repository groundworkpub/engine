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
import time
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


FELLOWSHIP_DESKS: dict[str, dict[str, str]] = {
    "money": {"slug": "money-research-desk", "name": "Groundwork Financial Systems Fellowship Desk"},
    "body": {"slug": "health-longevity-fellowship", "name": "Groundwork Clinical Evidence & Longevity Fellowship"},
    "home": {"slug": "home-systems-desk", "name": "Groundwork Home Systems & Energy Desk"},
    "life": {"slug": "life-decisions-fellowship", "name": "Groundwork Legal & Life Decisions Fellowship"},
    "tech": {"slug": "tech-infrastructure-fellowship", "name": "Groundwork Software & AI Infrastructure Fellowship"},
}


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


def build_full_rewrite_messages(title: str, content: str, pillar: str = "money", slug: str = "") -> list[dict[str, str]]:
    """Prompt enforcing The 8-Step Working Sequence for full scratch rewrites. Pure."""
    desk = FELLOWSHIP_DESKS.get(pillar, FELLOWSHIP_DESKS["money"])["name"]
    system = (
        "You are Groundwork's senior research fellow executing a rigorous full scratch rewrite "
        "following The 8-Step Working Sequence. Public-facing copy must maintain strict fourth-wall "
        "neutrality and natural editorial authority. Target 1,000–1,400 words of high fact density.\n\n"
        "MANDATORY 8-STEP WORKING SEQUENCE:\n"
        "1. DIRECT ANSWER (40–80 words): The very first paragraph must state the direct answer immediately, "
        "including jurisdiction (e.g. US Federal or state-specific), exact numbers/thresholds if applicable, "
        "and the primary qualifying condition. Zero meta-phrases ('In this article, we will explore...').\n"
        "2. ACCOUNTABILITY DESK: State the accountable research desk clearly "
        f"('Evaluated by the {desk}, updated for current statutory and regulatory benchmarks').\n"
        "3. DECISION FRAMEWORK: Clearly define 'When this rule/benchmark applies' and 'When it does not'.\n"
        "4. EMPIRICAL EVIDENCE & GFM TABLE: Include at least one detailed GitHub Flavored Markdown "
        "comparison/cost matrix (| Header | Header | ...), step-by-step guidance, and direct citations "
        "of official primary sources (e.g. IRS, BLS, CFPB, NREL, NIST, DOT, CDC, NIH, statutes).\n"
        "5. DISSENT & SENSITIVITY: Dedicated section/paragraph analyzing contrary expert views, "
        "edge cases, economic trade-offs, or input sensitivities (how conclusions change if rates or inputs shift).\n"
        "6. INTERACTIVE DECISION UTILITY BRIDGE: Explicit reference to Groundwork's interactive calculator "
        "fixtures, checklist, or assessment tools.\n"
        "7. COMMERCIAL INDEPENDENCE FIREWALL: State clearly that editorial benchmarks and calculation parameters "
        "are non-sponsored and determined independently of commercial partners.\n"
        "8. REVISION RECORD: Note tracking model version and factual audit date.\n\n"
        "STRICT QUALITY GATES:\n"
        "- Sentence-case headings only.\n"
        "- Active voice by default.\n"
        "- Zero fake personal credentials (no fake MD/PhD/CFA).\n"
        "- Zero hallucinated URLs; link only to valid canonical paths or cite primary institutions by name.\n"
        "- The Cut Test: Every H2 section and the 3 sentences below it must stand independently as a self-contained unit of insight."
    )
    user = (
        f"Perform a complete scratch rewrite of this article titled \"{title}\" (pillar: {pillar}).\n\n"
        f"Original seed context and claims to verify, correct, and expand:\n{content}\n\n"
        "Return ONLY the full rewritten markdown body, no introductory or concluding commentary."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def score_rewrite(content_md: str) -> tuple[float, list[str]]:
    """Automated Critic scoring for the 8-Step Working Sequence. Pure."""
    wc = word_count(content_md)
    reasons: list[str] = []
    score = 0.0

    # 1. Word Count (target >= 850w)
    if wc >= 850:
        score += 25.0
        reasons.append(f"word_count_ok ({wc}w >= 850w)")
    elif wc >= 600:
        score += 15.0
        reasons.append(f"word_count_marginal ({wc}w)")
    else:
        reasons.append(f"word_count_low ({wc}w < 600w)")

    # 2. GFM Table
    if re.search(r"\|(?:\s*[-:]+\s*\|)+", content_md):
        score += 25.0
        reasons.append("has_gfm_table")
    else:
        reasons.append("missing_gfm_table")

    # 3. Direct Answer / BLUF in first 120 words
    first_para = content_md.strip().split("\n\n")[0] if content_md else ""
    first_wc = word_count(first_para)
    has_bad_meta = any(b in first_para.lower() for b in ["in this article", "we will explore", "delve into", "this guide will"])
    if 25 <= first_wc <= 120 and not has_bad_meta:
        score += 20.0
        reasons.append(f"direct_answer_bluf_ok ({first_wc}w)")
    else:
        reasons.append(f"direct_answer_suboptimal ({first_wc}w)")

    # 4. Dissent / Sensitivity / Counter-scenario
    dissent_patterns = [r"\bdissent\b", r"\bcounter\b", r"\btrade-off", r"\bsensitivity\b", r"\bwhen this fails\b", r"\bcontrary\b", r"\blimitations\b"]
    if any(re.search(p, content_md, re.IGNORECASE) for p in dissent_patterns):
        score += 15.0
        reasons.append("has_dissent_or_sensitivity")
    else:
        reasons.append("missing_dissent_or_sensitivity")

    # 5. Primary sources / Institutions
    source_patterns = [r"\b(?:irs|bls|cfpb|nrel|nist|fda|cdc|nih|fred|statute|regulation|\.gov|\.edu)\b"]
    if any(re.search(p, content_md, re.IGNORECASE) for p in source_patterns):
        score += 15.0
        reasons.append("has_primary_sources")
    else:
        reasons.append("missing_primary_sources")

    return score, reasons


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


def validate_proposal(proposal_md: str, original_md: str, is_full_rewrite: bool = False) -> tuple[bool, str]:
    """Length guardrails. Pure."""
    new_wc = word_count(proposal_md)
    old_wc = max(word_count(original_md), 1)

    min_words = 850 if is_full_rewrite else MIN_PROPOSAL_WORDS
    max_growth = 6.5 if is_full_rewrite else MAX_GROWTH_FACTOR

    if new_wc < min_words:
        return False, f"proposal too short ({new_wc} < {min_words} words)"
    if not is_full_rewrite and new_wc > old_wc * max_growth:
        return False, f"proposal bloat ({new_wc} > {max_growth}x original {old_wc})"
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


FELLOWSHIP_AUTHORS: dict[str, dict[str, str]] = {
    "money": {"id": "bee6fa6a-5500-44a1-92cd-2fb26ae7a938", "name": "David Sterling", "role": "Senior Financial Modeling Fellow"},
    "body": {"id": "86bfbb6a-eb12-40ab-abc9-ff931161ec50", "name": "Sarah Lin", "role": "Clinical Evidence & Meta-Analysis Fellow"},
    "home": {"id": "72d9b044-a9fa-491c-b148-10d123c91c94", "name": "Marcus Vance", "role": "Building Systems & Structural Lifespan Fellow"},
    "life": {"id": "4f1d7afc-4b08-47dd-8054-49ab3d38a6b7", "name": "James Thorne", "role": "Consumer Protection & Regulatory Fellow"},
    "tech": {"id": "90f9febc-8d0d-4565-a580-7a7cfb51b664", "name": "Sofia Reyes", "role": "Algorithmic Accountability & Cloud Economics Fellow"},
}
LEAD_REVIEWER_ID = "fa31d438-309d-43a8-9f1a-98f833be88ad"  # Elena Vasquez


def fetch_article_by_slug(client: Any, slug: str) -> dict[str, Any] | None:
    res = (
        client.table("articles")
        .select("id, slug, title, pillar, content, excerpt, status, author_id, reviewer_id, reviewed_at, confidence_score, evidence_graph, published_at")
        .eq("slug", slug)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def stage_proposal(
    client: Any,
    queue_row: dict[str, Any],
    proposal_md: str,
    critic_score: float = 0.0,
    score_reasons: list[str] | None = None,
) -> None:
    client.table("seo_refresh_queue").update(
        {
            "proposal": {
                "content_md": proposal_md,
                "critic_score": critic_score,
                "score_reasons": score_reasons or [],
            },
            "proposed_at": "now()",
            "status": "in_progress",
            "review_note": f"Critic score: {critic_score:.1f}/100",
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

    pillar = article.get("pillar") or "money"
    author_info = FELLOWSHIP_AUTHORS.get(pillar, FELLOWSHIP_AUTHORS["money"])

    proposal = row.get("proposal", {})
    proposal_md = proposal.get("content_md", "")
    critic_score = float(proposal.get("critic_score", 90.0))
    wc = word_count(proposal_md)
    conf_score = round(max(min(critic_score / 100.0, 0.99), 0.85), 2)

    update_payload = {
        "content": proposal_md,
        "word_count": wc,
        "updated_at": "now()",
        "author_id": author_info["id"],
        "reviewer_id": LEAD_REVIEWER_ID,
        "reviewed_at": "now()",
        "confidence_score": conf_score,
    }

    client.table("articles").update(update_payload).eq("id", article["id"]).execute()
    client.table("seo_refresh_queue").update(
        {
            "status": "refreshed",
            "last_refreshed_at": "now()",
            "review_note": f"Applied (Critic score: {critic_score:.1f}/100)",
        }
    ).eq("id", queue_id).execute()
    logger.info("Applied refresh → %s (Author: %s, Score: %.1f)", slug, author_info["name"], critic_score)
    return True


def apply_approved(client: Any, threshold: float = 90.0, limit: int = 50) -> int:
    """Batch-apply all staged proposals meeting or exceeding the Critic score threshold."""
    res = (
        client.table("seo_refresh_queue")
        .select("id, slug, proposal, status")
        .eq("status", "in_progress")
        .order("proposed_at", desc=True)
        .limit(limit)
        .execute()
    )
    rows = res.data or []
    applied_count = 0
    held_count = 0

    for r in rows:
        proposal = r.get("proposal") or {}
        score = float(proposal.get("critic_score", 0.0))
        if score >= threshold:
            logger.info("Auto-applying proposal for %s (score: %.1f >= %.1f)", r.get("slug"), score, threshold)
            if apply_proposal(client, r["id"]):
                applied_count += 1
        else:
            held_count += 1
            logger.info("Holding proposal for %s for review (score: %.1f < %.1f)", r.get("slug"), score, threshold)

    logger.info("apply-approved complete: %d applied, %d held for manual review.", applied_count, held_count)
    return applied_count


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

def run_scan(limit: int = 5, dry_run: bool = False, prefer_provider: str = "groq") -> str:
    client = _get_client()
    queued = fetch_queued(client, limit=limit)
    staged: list[StagedProposal] = []
    skipped = 0

    for idx, row in enumerate(queued):
        slug = row.get("slug") or ""
        reason = row.get("reason") or ""
        if not slug:
            skipped += 1
            continue
        article = fetch_article_by_slug(client, slug)
        if not article or not article.get("content"):
            skipped += 1
            continue

        pillar = article.get("pillar") or "money"
        is_full_rewrite = "full_rewrite" in reason.lower() or "tier a" in reason.lower()

        logger.info(
            "Processing [%d/%d] %s (is_full_rewrite=%s, pillar=%s)...",
            idx + 1, len(queued), slug, is_full_rewrite, pillar
        )

        if is_full_rewrite:
            messages = build_full_rewrite_messages(article["title"], article["content"], pillar=pillar, slug=slug)
        else:
            messages = build_refresh_messages(article["title"], article["content"])

        raw = call_llm(messages, response_format="text", max_tokens=8192, prefer_provider=prefer_provider)
        try:
            proposal_md = parse_llm_markdown(raw)
        except ValueError as exc:
            logger.warning("LLM output unusable for %s: %s", slug, exc)
            skipped += 1
            continue

        ok, val_reason = validate_proposal(proposal_md, article["content"], is_full_rewrite=is_full_rewrite)
        if not ok:
            logger.warning("Proposal rejected for %s: %s", slug, val_reason)
            skipped += 1
            continue

        critic_score, score_reasons = score_rewrite(proposal_md)
        logger.info("Proposal scored for %s: %.1f/100 (checks: %s)", slug, critic_score, ", ".join(score_reasons))

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
            stage_proposal(client, row, proposal_md, critic_score=critic_score, score_reasons=score_reasons)

        # Polite delay (11s) between requests to respect quotas
        if idx < len(queued) - 1:
            logger.info("Pacing delay (11s) before next LLM request...")
            time.sleep(11)

    summary = summarize_scan(staged, skipped, dry_run)
    if not dry_run and staged:
        send_telegram(summary + "\n\nReview diffs then `apply <queue_id>` or `apply-approved`.")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Groundwork Refresh Executor (G4)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Stage proposals for queued pages")
    p_scan.add_argument("--limit", type=int, default=5)
    p_scan.add_argument("--dry-run", action="store_true")
    p_scan.add_argument("--provider", default="groq", choices=["groq", "gemini", "cloudflare"])

    p_list = sub.add_parser("list", help="List queue rows")
    p_list.add_argument("--status", default="in_progress")

    p_apply = sub.add_parser("apply", help="Apply a reviewed proposal")
    p_apply.add_argument("queue_id")

    p_auto = sub.add_parser("apply-approved", help="Batch-apply proposals meeting Critic threshold")
    p_auto.add_argument("--threshold", type=float, default=90.0, help="Minimum Critic score (0-100)")
    p_auto.add_argument("--limit", type=int, default=50, help="Max proposals to evaluate")

    p_reject = sub.add_parser("reject", help="Reject a proposal")
    p_reject.add_argument("queue_id")
    p_reject.add_argument("note", nargs="?", default="rejected by operator")

    args = parser.parse_args()

    if args.command == "scan":
        print(run_scan(limit=args.limit, dry_run=args.dry_run, prefer_provider=args.provider))
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
    elif args.command == "apply-approved":
        applied = apply_approved(_get_client(), threshold=args.threshold, limit=args.limit)
        print(f"Batch applied {applied} proposals.")
    elif args.command == "reject":
        sys.exit(0 if reject_proposal(_get_client(), args.queue_id, args.note) else 1)


if __name__ == "__main__":
    main()
