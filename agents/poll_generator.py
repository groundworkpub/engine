"""Groundwork Weekly Community Poll Auto-Generator.

SSOT: AGENTS.md §5, docs/research/GROUNDWORK — MASTER CONTEXT.md
Automatically generates 1 high-intent community decision poll per pillar per week
from trending keyword graph nodes and editorial topics.
Upserts into `public.community_polls`.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("poll_generator")

PILLARS = ["money", "body", "home", "life", "tech"]

CANONICAL_FALLBACK_POLLS = {
    "money": {
        "question": "What is your primary financial decision focus for this quarter?",
        "options": [
            {"id": "opt_refinance", "text": "Refinancing mortgage / debt reduction", "votes": 0},
            {"id": "opt_hysa", "text": "Maximizing cash yields in HYSA & CDs", "votes": 0},
            {"id": "opt_invest", "text": "Dollar-cost averaging into index funds", "votes": 0},
            {"id": "opt_tax", "text": "Roth IRA conversion & tax harvesting", "votes": 0},
        ],
    },
    "body": {
        "question": "Which health longevity protocol has the highest priority in your routine?",
        "options": [
            {"id": "opt_zone2", "text": "Zone 2 aerobic base cardio (150+ min/wk)", "votes": 0},
            {"id": "opt_strength", "text": "Heavy resistance training & lean muscle mass", "votes": 0},
            {"id": "opt_sleep", "text": "Optimizing deep & REM sleep stages", "votes": 0},
            {"id": "opt_nutrition", "text": "Protein targeting & metabolic timing", "votes": 0},
        ],
    },
    "home": {
        "question": "Which residential efficiency upgrade are you evaluating this year?",
        "options": [
            {"id": "opt_heatpump", "text": "Cold-climate heat pump vs gas furnace", "votes": 0},
            {"id": "opt_solar", "text": "Rooftop solar + battery storage ROI", "votes": 0},
            {"id": "opt_insulation", "text": "Air sealing and attic insulation upgrade", "votes": 0},
            {"id": "opt_smarthome", "text": "Smart thermostat and energy monitoring", "votes": 0},
        ],
    },
    "life": {
        "question": "What is your main estate and legal planning milestone for 2026?",
        "options": [
            {"id": "opt_trust", "text": "Setting up a revocable living trust", "votes": 0},
            {"id": "opt_term_life", "text": "Laddering 20-30 year term life policies", "votes": 0},
            {"id": "opt_will", "text": "Drafting or updating a living will & POA", "votes": 0},
            {"id": "opt_emergency", "text": "Documenting digital executor instructions", "votes": 0},
        ],
    },
    "tech": {
        "question": "Which AI and smart technology adoption represents your biggest workflow shift?",
        "options": [
            {"id": "opt_local_llm", "text": "Running private local LLMs on-device", "votes": 0},
            {"id": "opt_ai_code", "text": "Autonomous AI coding assistants in pair-dev", "votes": 0},
            {"id": "opt_matter", "text": "Migrating smart home to Matter/Thread", "votes": 0},
            {"id": "opt_security", "text": "Hardware security keys (FIDO2/Passkeys)", "votes": 0},
        ],
    },
}


def get_supabase() -> Client:
    load_dotenv(".env.local")
    url = os.getenv("NEXT_PUBLIC_SUPABASE_URL") or os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        logger.error("Missing Supabase credentials.")
        sys.exit(1)
    return create_client(url, key)


def generate_polls_for_pillars(
    supabase: Client,
    target_pillar: str | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    pillars_to_run = [target_pillar] if target_pillar and target_pillar in PILLARS else PILLARS
    now = datetime.now(UTC)
    week_str = now.strftime("%Y-w%W")
    expires_at = (now + timedelta(days=7)).isoformat()

    results: list[dict[str, Any]] = []

    for pillar in pillars_to_run:
        slug = f"poll-{pillar}-{week_str}"
        poll_def = CANONICAL_FALLBACK_POLLS.get(pillar)
        if not poll_def:
            continue

        poll_question = poll_def["question"]
        poll_options = poll_def["options"]

        # Check if keyword_rankings or telemetry has trending topic for custom question
        try:
            kw_res = (
                supabase.table("keyword_rankings")
                .select("keyword, position")
                .order("position", asc=True)
                .limit(20)
                .execute()
            )
            if kw_res.data and len(kw_res.data) > 0:
                pillar_keywords = {
                    "money": ["mortgage", "refinance", "rate", "debt", "invest", "hysa"],
                    "body": ["cardio", "tdee", "calorie", "heart", "health", "fat"],
                    "home": ["solar", "heat pump", "furnace", "payback", "energy"],
                    "life": ["retir", "delaware", "florida", "cost", "travel"],
                    "tech": ["ai", "cursor", "software", "verbatim", "origin"],
                }
                valid_terms = pillar_keywords.get(pillar, [pillar])
                for row in kw_res.data:
                    kw_candidate = row.get("keyword", "")
                    if any(term in kw_candidate.lower() for term in valid_terms) and len(kw_candidate) > 5:
                        top_kw = kw_candidate
                        break
                if top_kw and len(top_kw) > 5:
                    logger.info("Found top trending keyword for %s: '%s'. Synthesizing poll...", pillar, top_kw)
                    try:
                        from core.llm_router import get_llm_router
                        router = get_llm_router()
                        poll_prompt = f"""You are the Community Engagement Editor at Groundwork.
Create a sharp, single-choice decision poll for our audience (adults 35-48) centered on the trending topic: "{top_kw}".
Pillar: {pillar}

Format strictly as JSON:
{{
  "question": "Clear, engaging question under 15 words?",
  "options": [
    {{"id": "opt_1", "text": "Concrete stance / action 1", "votes": 0}},
    {{"id": "opt_2", "text": "Concrete stance / action 2", "votes": 0}},
    {{"id": "opt_3", "text": "Concrete stance / action 3", "votes": 0}}
  ]
}}
"""
                        res = router.call_llm_json(prompt=poll_prompt, system_prompt="You generate high-conversion decision polls.")
                        if res and "question" in res and "options" in res and len(res["options"]) >= 2:
                            poll_question = res["question"]
                            poll_options = res["options"]
                    except Exception as llm_err:
                        logger.warning(f"LLM poll synthesis fallback: {llm_err}")
        except Exception:
            pass  # Fall back cleanly to canonical question

        poll_record = {
            "slug": slug,
            "pillar": pillar,
            "question": poll_question,
            "description": f"Groundwork Community Consensus Benchmark — {pillar.capitalize()} Pillar ({week_str})",
            "options": poll_options,
            "total_votes": 0,
            "is_active": True,
            "expires_at": expires_at,
        }

        logger.info("Poll Candidate [%s]: '%s' (Slug: %s)", pillar, poll_def["question"], slug)

        if not dry_run:
            try:
                supabase.table("community_polls").upsert(poll_record, on_conflict="slug").execute()
                logger.info("  Successfully upserted poll '%s'", slug)
            except Exception as e:
                logger.warning("  Failed to upsert poll '%s': %s", slug, e)
        else:
            logger.info("  [DRY-RUN] Would upsert poll '%s'", slug)

        results.append(poll_record)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Groundwork Community Poll Auto-Generator")
    parser.add_argument("--pillar", choices=[*PILLARS, "all"], default="all", help="Target pillar")
    parser.add_argument("--dry-run", action="store_true", help="Generate without writing to database")
    args = parser.parse_args()

    supabase = get_supabase()
    pillar_arg = None if args.pillar == "all" else args.pillar
    polls = generate_polls_for_pillars(supabase, target_pillar=pillar_arg, dry_run=args.dry_run)
    logger.info("Poll generation complete. Created/Verified %d polls.", len(polls))


if __name__ == "__main__":
    main()
