"""Groundwork Expired Domain Semantic Bridge & Scribe (Agent 4f).

Modernizes historical archived articles into 2026 E-E-A-T guides,
maps them to Groundwork's 5 pillars (money, body, home, life, tech),
and weaves natural contextual anchor links targeting gworky.com tools & guides.

Usage:
    python agents/expired_scribe.py --limit 10
    python agents/expired_scribe.py --domain emailforums.biz --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent

try:
    from agents.llm_router import call_llm, call_llm_json
except ImportError:
    from llm_router import call_llm, call_llm_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("expired_scribe")

PILLARS = ["money", "body", "home", "life", "tech"]

ANCHOR_TEMPLATES = {
    "money": [
        ("the comprehensive financial analysis", "https://gworky.com/money"),
        ("real-time mortgage rate benchmarks", "https://gworky.com/tools/mortgage-calculator"),
        ("debt payoff strategies on Groundwork", "https://gworky.com/tools/debt-payoff-calculator"),
        ("Groundwork Money Research Hub", "https://gworky.com/money"),
    ],
    "body": [
        ("clinical evidence and wellness benchmarks", "https://gworky.com/body"),
        ("the daily protein and calorie calculator", "https://gworky.com/tools/calorie-calculator"),
        ("Groundwork Body Health Index", "https://gworky.com/body"),
    ],
    "home": [
        ("the 2026 solar payback calculator", "https://gworky.com/tools/solar-roi-calculator"),
        ("home improvement energy benchmarks", "https://gworky.com/home"),
        ("Groundwork Home Efficiency Hub", "https://gworky.com/home"),
    ],
    "life": [
        ("the travel insurance and lifestyle guide", "https://gworky.com/life"),
        ("salary negotiation calculator", "https://gworky.com/tools/salary-negotiation-calculator"),
        ("Groundwork Life Decisions Guide", "https://gworky.com/life"),
    ],
    "tech": [
        ("interactive software and tech benchmarks", "https://gworky.com/tech"),
        ("the universal citation generator", "https://gworky.com/tools/citation-generator"),
        ("Groundwork Tech & AI Directory", "https://gworky.com/tech"),
    ],
}


def _load_env_local() -> None:
    env_file = _ROOT / ".env.local"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip("'").strip('"')
            if k not in os.environ:
                os.environ[k] = v


def get_supabase_client() -> Any:
    _load_env_local()
    from supabase import create_client

    url = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
    return create_client(url, key)


def match_pillar_and_anchor(title: str, text: str) -> tuple[str, str, str]:
    """Determines the best matching pillar and target anchor based on text keywords."""
    combined = f"{title} {text}".lower()
    scores = {
        "money": len(re.findall(r"\b(mortgage|loan|interest|debt|invest|stock|finance|money|bank|credit|tax|retirement|crypto)\b", combined)),
        "body": len(re.findall(r"\b(health|diet|fitness|workout|protein|sleep|weight|muscle|doctor|medicine|cardio)\b", combined)),
        "home": len(re.findall(r"\b(home|house|solar|roof|hvac|energy|kitchen|renovation|remodel|plumbing|garden)\b", combined)),
        "life": len(re.findall(r"\b(travel|career|job|salary|resume|insurance|legal|estate|auto|car|relocation)\b", combined)),
        "tech": len(re.findall(r"\b(ai|software|app|code|computer|laptop|cloud|tool|gadget|hardware|router|wifi)\b", combined)),
    }

    best_pillar = max(scores, key=scores.get)
    if scores[best_pillar] == 0:
        best_pillar = "money"  # Default fallback

    anchors = ANCHOR_TEMPLATES.get(best_pillar, ANCHOR_TEMPLATES["money"])
    chosen_anchor_text, chosen_target_url = random.choice(anchors)
    return best_pillar, chosen_anchor_text, chosen_target_url


def rewrite_article_agentic(
    title: str,
    raw_content: str,
    pillar: str,
    anchor_text: str,
    target_url: str,
) -> tuple[str, str]:
    """Modernizes historical content with LiteLLM and weaves natural co-citations."""
    system_prompt = (
        "You are an expert editorial writer and researcher at Groundwork Media. "
        "Your task is to take a legacy article excerpt, modernize it to the year 2026 with accurate, authoritative, "
        "and data-backed insights, and structure it cleanly in Markdown (with H2, H3, and bullet points). "
        "Strict rules:\n"
        "1. Never use motivational fluff, guru clichés, or fake jargon.\n"
        f"2. Naturally integrate this exact contextual hyperlink into one of the key analytical paragraphs: [{anchor_text}]({target_url})\n"
        "3. Produce a complete 600-900 word informative guide."
    )

    user_prompt = f"Original Historical Title: {title}\n\nArchived Raw Content Snippet:\n{raw_content[:2000]}\n\nTarget Pillar: {pillar}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    rewritten = call_llm(messages, max_tokens=2500)

    if not rewritten or len(rewritten.strip()) < 200:
        logger.warning("LLM generation returned empty/short text. Using deterministic rule-based generator.")
        rewritten = (
            f"## Overview: Modernizing {title}\n\n"
            f"In this updated 2026 analysis, we evaluate key decision factors regarding {title.lower()}.\n\n"
            f"{raw_content[:1000]}\n\n"
            f"### Critical Benchmarks and Key Takeaways\n\n"
            f"- Data-driven verification is essential for evaluating long-term outcomes.\n"
            f"- For real-time metrics and comparative tools, explore [{anchor_text}]({target_url}).\n"
            f"- Independent research replaces guesswork across modern lifestyle decisions.\n"
        )

    # Ensure link is present if model missed it
    if target_url not in rewritten:
        rewritten += f"\n\n*Reference Resource:* For deeper analysis, explore [{anchor_text}]({target_url})."

    new_title = f"{title} (2026 Analysis & Guide)"
    return new_title, rewritten


def process_pending_rewrites(limit: int = 10, domain: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
    """Processes routes in ARCHIVED status and produces modernized content."""
    supabase = get_supabase_client()

    query = supabase.table("expired_routes").select("id,domain_id,original_url,original_path,historical_title,historical_content,strategy").eq("status", "ARCHIVED").limit(limit)
    res = query.execute()
    routes = res.data or []

    logger.info(f"Found {len(routes)} routes awaiting AI Scribe modernization.")
    processed = 0

    for item in routes:
        route_id = item["id"]
        orig_title = item.get("historical_title") or "Archived Research Topic"
        orig_content = item.get("historical_content") or ""

        pillar, anchor_text, target_url = match_pillar_and_anchor(orig_title, orig_content)
        new_title, modern_content = rewrite_article_agentic(orig_title, orig_content, pillar, anchor_text, target_url)

        if dry_run:
            logger.info(f"[DRY-RUN] Modernized: {new_title[:50]} | Anchor: {anchor_text} -> {target_url}")
            processed += 1
        else:
            try:
                supabase.table("expired_routes").update({
                    "historical_title": new_title,
                    "historical_content": modern_content,
                    "target_pillar": pillar,
                    "target_gworky_url": target_url,
                    "anchor_text": anchor_text,
                    "status": "AI_REWRITING",
                }).eq("id", route_id).execute()
                processed += 1
                logger.info(f"✅ Route [{route_id}] updated to AI_REWRITING.")
            except Exception as db_err:
                logger.error(f"DB update failed for route {route_id}: {db_err}")

    return {"processed": processed, "total_found": len(routes), "status": "completed"}


def main() -> None:
    _load_env_local()
    parser = argparse.ArgumentParser(description="Groundwork Expired Domain Scribe")
    parser.add_argument("--limit", type=int, default=10, help="Max routes to rewrite")
    parser.add_argument("--domain", type=str, help="Filter by specific domain")
    parser.add_argument("--dry-run", action="store_true", help="Preview without updating DB")
    args = parser.parse_args()

    result = process_pending_rewrites(limit=args.limit, domain=args.domain, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
