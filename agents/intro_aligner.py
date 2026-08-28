#!/usr/bin/env python3
"""T2.4 retro-fit — query-answer alignment for published articles.

Finds published articles whose opening paragraph fails to address the title
query (keyword-overlap heuristic), surgically rewrites only that paragraph via
the shared LLM router, and pings IndexNow.

Usage:
    python agents/intro_aligner.py --dry-run            # report only
    python agents/intro_aligner.py --limit 10           # fix worst 10
    python agents/intro_aligner.py --threshold 0.34     # overlap cutoff
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import UTC, datetime
from typing import Any

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from indexer_dispatcher import (  # noqa: E402
    _load_env_local,
    get_supabase_client,
    submit_indexnow,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("intro_aligner")

SITE_BASE = "https://gworky.com"
STOP_WORDS = {
    "the", "a", "an", "to", "of", "in", "for", "on", "and", "is", "are",
    "what", "why", "how", "when", "who", "do", "does", "did", "you", "your",
    "it", "its", "with", "that", "this", "about", "at", "by", "from", "as",
    "be", "can", "will", "should", "know", "need", "use", "best", "top", "vs",
}


def title_keywords(title: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z0-9']+", title.lower())
        if w not in STOP_WORDS and len(w) > 2
    }


def split_intro(content: str) -> tuple[str, str, str] | None:
    """Return (prefix, first_paragraph, suffix) or None if no paragraph found."""
    lines = content.splitlines()
    para_start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            para_start = i
            break
    if para_start is None:
        return None
    para_end = para_start
    while para_end < len(lines) and lines[para_end].strip():
        para_end += 1
    prefix = "\n".join(lines[:para_start])
    paragraph = "\n".join(lines[para_start:para_end])
    suffix = "\n".join(lines[para_end:])
    return prefix, paragraph, suffix


def overlap_ratio(keywords: set[str], text: str) -> float:
    if not keywords:
        return 1.0
    lowered = text.lower()
    hits = sum(1 for k in keywords if k in lowered)
    return hits / len(keywords)


def find_weak_articles(supabase: Any, threshold: float, limit: int) -> list[dict[str, Any]]:
    rows = (
        supabase.table("articles")
        .select("id,slug,title,content")
        .eq("status", "published")
        .execute()
        .data
        or []
    )
    weak: list[dict[str, Any]] = []
    for row in rows:
        split = split_intro(row.get("content") or "")
        if not split:
            continue
        keywords = title_keywords(row["title"] or "")
        ratio = overlap_ratio(keywords, split[1])
        if ratio < threshold:
            weak.append({**row, "ratio": ratio, "keywords": keywords})
    weak.sort(key=lambda w: w["ratio"])
    return weak[:limit]


def rewrite_intro(title: str, paragraph: str, keywords: set[str]) -> str | None:
    from llm_router import call_llm

    kw_list = ", ".join(sorted(keywords))
    messages = [
        {
            "role": "system",
            "content": (
                "You are Groundwork's lead editor. You rewrite opening paragraphs "
                "so they answer the article's core search query directly in the "
                "first two sentences. You keep every fact, figure, and link from "
                "the original paragraph. You write clear, active English with no "
                "promotional fluff and no meta-commentary."
            ),
        },
        {
            "role": "user",
            "content": (
                f'Article title: "{title}"\n'
                f"Key terms the opening must address: {kw_list}\n\n"
                f"Current opening paragraph:\n{paragraph}\n\n"
                "Rewrite this single paragraph so it answers the title's question "
                "directly and naturally weaves in the key terms. Keep it between 40 "
                "and 90 words. Preserve any links and verifiable figures. Return "
                "ONLY the rewritten paragraph — no headings, no quotes around it."
            ),
        },
    ]
    try:
        result = call_llm(messages, max_tokens=400)
    except Exception as exc:
        logger.error(f"LLM rewrite failed for '{title}': {exc}")
        return None
    if not result:
        return None
    cleaned = result.strip().strip('"')
    if "\n\n" in cleaned or cleaned.startswith("#"):
        return None
    return cleaned


def quality_gate(
    original_para: str, new_para: str, keywords: set[str]
) -> bool:
    word_new = len(new_para.split())
    if not (30 <= word_new <= 140):
        logger.warning(f"Rejected: new intro length {word_new} words")
        return False
    if overlap_ratio(keywords, new_para) < 0.5:
        logger.warning("Rejected: new intro still misses key terms")
        return False
    # Links present in the original must survive.
    orig_links = re.findall(r"\[[^\]]+\]\([^)]+\)", original_para)
    for link in orig_links:
        if link not in new_para:
            logger.warning(f"Rejected: dropped link {link[:60]}")
            return False
    return True


def update_article(supabase: Any, article_id: str, new_content: str) -> bool:
    resp = (
        supabase.table("articles")
        .update({"content": new_content, "updated_at": datetime.now(UTC).isoformat()})
        .eq("id", article_id)
        .execute()
    )
    return bool(resp.data)


def notify_telegram(message: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_FOUNDER_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=15.0,
        )
    except Exception as exc:
        logger.warning(f"Telegram notify failed: {exc}")


def log_pipeline_run(
    supabase: Any, status: str, processed: int, published: int, error: str = ""
) -> None:
    try:
        supabase.table("pipeline_runs").insert(
            {
                "agent": "intro_aligner",
                "status": status,
                "items_processed": processed,
                "items_published": published,
                "error_log": error or None,
            }
        ).execute()
    except Exception as exc:
        logger.warning(f"pipeline_runs insert failed: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Query-answer intro alignment (T2.4)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--threshold", type=float, default=0.34)
    args = parser.parse_args()

    _load_env_local()
    supabase = get_supabase_client()
    weak = find_weak_articles(supabase, args.threshold, args.limit)

    logger.info(f"{len(weak)} articles below {args.threshold:.0%} keyword overlap:")
    for w in weak:
        logger.info(f"  - {w['ratio']:.0%} {w['slug']}")

    if args.dry_run:
        logger.info("Dry run — no changes written.")
        return 0

    fixed_urls: list[str] = []
    errors: list[str] = []
    for w in weak:
        split = split_intro(w["content"])
        if not split:
            continue
        _, paragraph, _ = split
        logger.info(f"Rewriting intro: {w['slug']} ({w['ratio']:.0%})")
        new_para = rewrite_intro(w["title"], paragraph, w["keywords"])
        if not new_para or not quality_gate(paragraph, new_para, w["keywords"]):
            errors.append(f"{w['slug']}: gate rejected")
            continue
        new_content = split[0] + "\n" + new_para + split[2]
        if update_article(supabase, w["id"], new_content):
            fixed_urls.append(f"{SITE_BASE}/article/{w['slug']}")
            logger.info(f"Fixed: {w['slug']}")
        else:
            errors.append(f"{w['slug']}: Supabase update failed")

    if fixed_urls:
        submit_indexnow(fixed_urls, os.getenv("INDEXNOW_KEY", ""), host="gworky.com")

    status = "success" if not errors else ("partial" if fixed_urls else "error")
    log_pipeline_run(supabase, status, len(weak), len(fixed_urls), "; ".join(errors))

    summary = (
        f"🎯 Intro alignment: {len(fixed_urls)} fixed / {len(weak)} flagged ({status})."
    )
    if errors:
        summary += f"\nErrors: {'; '.join(errors[:5])}"
    notify_telegram(summary)
    logger.info(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
