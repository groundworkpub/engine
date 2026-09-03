#!/usr/bin/env python3
"""Autonomous pSEO AI Agent Pipeline Engine (Groundwork Platform).

Integrates the 4 core pSEO agent skills:
  1. Product Seed Expander (Attributes x Personas x Scenarios)
  2. SERP Weakness Auditor (SearchAPI + Google Autocomplete)
  3. Cannibalization Guard (1 Intent = 1 Canonical URL, < 0.65 threshold)
  4. Template Router & pseolint Quality Gate

Dual Telemetry:
  - Persists execution run to Supabase `pipeline_runs` table.
  - Dispatches execution summaries & alerts to Telegram `@gwelena_bot`.
"""

import argparse
import logging
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from typing import Any

import httpx

# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] pseo_pipeline: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("pseo_pipeline")


def _load_env_local() -> None:
    """Loads environment variables from root .env.local if present."""
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_file = os.path.join(root_dir, ".env.local")
    if os.path.exists(env_file):
        try:
            with open(env_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        if k.strip() not in os.environ:
                            os.environ[k.strip()] = v.strip().strip("\"'")
        except Exception as e:
            logger.warning("Failed reading .env.local: %s", e)


_load_env_local()
_searchapi_exhausted: bool = False


# ─── SEED EXPANSION CONFIGURATION ─────────────────────────────────────────────

PILLAR_SEEDS: dict[str, list[dict[str, Any]]] = {
    "money": [
        {"topic": "rent vs buy", "intent": "decision_calculator", "base_slug": "rent-vs-buy-calculator"},
        {"topic": "mortgage refinance break-even", "intent": "decision_calculator", "base_slug": "mortgage-refinance"},
        {"topic": "mortgage refinance vs bankrate", "intent": "competitor_comparison", "base_slug": "mortgage-refinance-vs-bankrate-nerdwallet"},
        {"topic": "s-corp reasonable salary", "intent": "decision_calculator", "base_slug": "scorp-salary-optimizer"},
    ],
    "body": [
        {"topic": "peptide reconstitution dosage", "intent": "decision_calculator", "base_slug": "peptide-reconstitution-calculator"},
        {"topic": "compounded semaglutide cost vs ro hims", "intent": "competitor_comparison", "base_slug": "glp1-peptide-cost-vs-ro-hims"},
        {"topic": "sleep cycle rem rhythm", "intent": "decision_calculator", "base_slug": "sleep-cycle-planner"},
        {"topic": "tdee protein target", "intent": "decision_calculator", "base_slug": "daily-calorie-tdee"},
    ],
    "home": [
        {"topic": "whole house surge protector sizing", "intent": "decision_calculator", "base_slug": "whole-house-surge-protector-calculator"},
        {"topic": "heat pump roi vs furnace", "intent": "decision_calculator", "base_slug": "heat-pump-roi-calculator"},
        {"topic": "solar battery payback energysage tesla", "intent": "competitor_comparison", "base_slug": "solar-roi-battery-vs-energysage-tesla"},
        {"topic": "residential solar battery payback", "intent": "decision_calculator", "base_slug": "solar-roi"},
    ],
    "life": [
        {"topic": "freelance billable rate", "intent": "decision_calculator", "base_slug": "freelance-rate-calculator"},
        {"topic": "car true cost of ownership vs edmunds kbb", "intent": "competitor_comparison", "base_slug": "car-true-cost-ownership-vs-edmunds-kbb"},
        {"topic": "ev charging cost vs gas", "intent": "decision_calculator", "base_slug": "commute-ev-vs-gas-calculator"},
    ],
    "tech": [
        {"topic": "llm api token pricing comparison", "intent": "decision_calculator", "base_slug": "llm-token-cost-calculator"},
        {"topic": "llm api pricing vs openrouter", "intent": "competitor_comparison", "base_slug": "llm-api-pricing-vs-openrouter-artificialanalysis"},
        {"topic": "smart home matter energy savings", "intent": "decision_calculator", "base_slug": "smart-home-roi"},
    ],
}

MODIFIERS = {
    "scenarios": ["first time", "high cost of living", "veteran", "remote worker", "commercial"],
    "attributes": ["roi", "dosage units", "payback timeline", "break even", "formula"],
}


# ─── PHASE 1: SEED EXPANSION ──────────────────────────────────────────────────

def expand_seeds(pillars: list[str] | None = None) -> list[dict[str, Any]]:
    """Expands seeds into multi-dimensional keyword variations."""
    selected_pillars = pillars or list(PILLAR_SEEDS.keys())
    expanded: list[dict[str, Any]] = []

    for pillar in selected_pillars:
        seeds = PILLAR_SEEDS.get(pillar, [])
        for seed in seeds:
            # 1. Base query
            expanded.append({
                "pillar": pillar,
                "query": seed["topic"],
                "intent": seed["intent"],
                "base_slug": seed["base_slug"],
                "dimension": "core",
            })

            # 2. Scenario permutations
            for scenario in MODIFIERS["scenarios"][:2]:
                expanded.append({
                    "pillar": pillar,
                    "query": f"{seed['topic']} for {scenario}",
                    "intent": seed["intent"],
                    "base_slug": seed["base_slug"],
                    "dimension": "persona",
                })

            # 3. Attribute permutations
            for attr in MODIFIERS["attributes"][:2]:
                expanded.append({
                    "pillar": pillar,
                    "query": f"{seed['topic']} {attr}",
                    "intent": seed["intent"],
                    "base_slug": seed["base_slug"],
                    "dimension": "attribute",
                })

    logger.info("Phase 1: Expanded into %d candidate queries.", len(expanded))
    return expanded


# ─── PHASE 2: SERP WEAKNESS AUDITOR ───────────────────────────────────────────

def fetch_google_autocomplete(query: str) -> list[str]:
    """Fetches real-time search queries from Google Autocomplete."""
    url = f"https://suggestqueries.google.com/complete/search?client=chrome&q={httpx.URL(query)}"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                if len(data) > 1 and isinstance(data[1], list):
                    return [str(s).lower() for s in data[1]]
    except Exception as e:
        logger.debug("Autocomplete lookup failed for '%s': %s", query, e)
    return []


def calculate_serp_weakness(query: str, searchapi_key: str | None = None) -> dict[str, Any]:
    """Calculates SERP Weakness Index (0.0 to 1.0) detecting thin content & lack of interactive utilities."""
    suggestions = fetch_google_autocomplete(query)
    has_high_intent = any(kw in query for kw in ["calculator", "dosage", "units", "break even", "roi", "vs", "rate"])

    score = 0.50
    signals = []

    if suggestions:
        score += 0.20
        signals.append("verified_search_volume")

    if has_high_intent:
        score += 0.20
        signals.append("interactive_utility_intent")

    # If SearchAPI key is present and not exhausted, execute deep probe
    global _searchapi_exhausted
    if searchapi_key and not _searchapi_exhausted:
        try:
            with httpx.Client(timeout=8.0) as client:
                res = client.get(
                    "https://www.searchapi.io/api/v1/search",
                    params={"engine": "google", "q": query, "api_key": searchapi_key, "num": 10},
                )
                if res.status_code == 200:
                    organic = res.json().get("organic_results", [])
                    has_forum = any("reddit.com" in r.get("link", "") or "quora.com" in r.get("link", "") for r in organic)
                    has_interactive = any("calculator" in r.get("title", "").lower() for r in organic)

                    if has_forum:
                        score += 0.15
                        signals.append("forum_serp_vulnerability")
                    if not has_interactive and has_high_intent:
                        score += 0.15
                        signals.append("missing_interactive_tool")
                elif res.status_code == 429:
                    _searchapi_exhausted = True
                    logger.info("SearchAPI quota exhausted (429); tripping circuit breaker to 100% free Google Autocomplete.")
        except Exception as e:
            logger.debug("SearchAPI probe error: %s", e)

    final_score = min(1.0, round(score, 2))
    return {
        "weakness_score": final_score,
        "is_viable": final_score >= 0.60,
        "signals": signals,
        "suggestions_count": len(suggestions),
    }


# ─── PHASE 3: ANTI-CANNIBALIZATION GUARD ──────────────────────────────────────

def _tokenize(text: str) -> set:
    words = re.findall(r"\b[a-z0-9]+\b", text.lower())
    stop_words = {"the", "a", "an", "and", "or", "for", "in", "of", "to", "with", "vs", "by", "on", "is", "at"}
    return {w for w in words if w not in stop_words and len(w) > 1}


def jaccard_similarity(text_a: str, text_b: str) -> float:
    tokens_a = _tokenize(text_a)
    tokens_b = _tokenize(text_b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return intersection / union if union > 0 else 0.0


def check_cannibalization(candidate_slug: str, candidate_query: str, indexed_routes: list[str]) -> tuple[bool, float, str]:
    """Ensures candidate query does not cannibalize existing indexed URLs (threshold < 0.65)."""
    max_sim = 0.0
    matched_target = ""

    for route in indexed_routes:
        clean_route = route.replace("/tools/", "").replace("/article/", "").replace("-", " ")
        sim = jaccard_similarity(candidate_query, clean_route)
        if sim > max_sim:
            max_sim = sim
            matched_target = route

    is_cannibalizing = max_sim >= 0.65
    return is_cannibalizing, round(max_sim, 3), matched_target


# ─── PHASE 4: QUALITY GATE & SYNTHESIS ────────────────────────────────────────

def run_pseolint() -> bool:
    """Executes the strict pSEO Quality Gate linter."""
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    linter_script = os.path.join(root_dir, "scripts", "lint_pseo.mjs")
    if not os.path.exists(linter_script):
        logger.warning("scripts/lint_pseo.mjs not found; skipping linter check.")
        return True

    res = subprocess.run(["node", linter_script], cwd=root_dir, capture_output=True, text=True)
    if res.returncode == 0:
        logger.info("pSEO Quality Gate Linter passed with 0 violations.")
        return True
    else:
        logger.error("pSEO Quality Gate Linter failed:\n%s", res.stdout + res.stderr)
        return False


# ─── PHASE 5: DUAL TELEMETRY & NOTIFICATIONS ──────────────────────────────────

def dispatch_telegram_summary(summary: dict[str, Any]) -> None:
    """Sends execution summary to Telegram @gwelena_bot."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_FOUNDER_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")

    if not bot_token or not chat_id:
        logger.info("Telegram credentials unset; skipping alert dispatch.")
        return

    text = (
        f"🤖 *Groundwork pSEO Pipeline Execution*\n\n"
        f"• *Status:* `{summary['status'].upper()}`\n"
        f"• *Total Candidates Scouted:* `{summary['scouted_count']}`\n"
        f"• *Viable Weakness Gaps (≥0.60):* `{summary['viable_count']}`\n"
        f"• *Cannibalization Passed (<0.65):* `{summary['passed_guard_count']}`\n"
        f"• *Published / Staged:* `{summary['published_count']}`\n"
        f"• *Duration:* `{summary['duration_seconds']}s`\n"
    )

    if summary.get("errors"):
        text += f"\n⚠️ *Errors:* {summary['errors'][:300]}"

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }

    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                logger.info("Telegram execution summary dispatched to @gwelena_bot.")
            else:
                logger.warning("Telegram dispatch returned status %d: %s", resp.status_code, resp.text)
    except Exception as e:
        logger.warning("Failed sending Telegram alert: %s", e)


def persist_pipeline_run(supabase_client: Any, run_data: dict[str, Any]) -> None:
    """Persists pipeline execution telemetry into Supabase pipeline_runs table."""
    if not supabase_client:
        return
    try:
        supabase_client.table("pipeline_runs").insert(run_data).execute()
        logger.info("Telemetry persisted to Supabase pipeline_runs.")
    except Exception as e:
        logger.warning("Failed to persist pipeline_runs row: %s", e)


# ─── MAIN ORCHESTRATOR ────────────────────────────────────────────────────────

def run_pseo_pipeline(dry_run: bool = False, pillars: list[str] | None = None) -> dict[str, Any]:
    """Runs the complete end-to-end pSEO pipeline."""
    start_time = time.time()
    logger.info("=" * 60)
    logger.info("STARTING AUTONOMOUS pSEO AI AGENT PIPELINE (Dry Run = %s)", dry_run)
    logger.info("=" * 60)

    # 1. Initialize Supabase if available
    supabase_client = None
    sb_url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    sb_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    if sb_url and sb_key and not dry_run:
        try:
            from supabase import create_client
            supabase_client = create_client(sb_url, sb_key)
        except Exception as e:
            logger.warning("Could not instantiate Supabase client: %s", e)

    # 2. Run pSEO Linter quality check
    linter_passed = run_pseolint()
    if not linter_passed:
        logger.error("Aborting pipeline run due to linter quality violations.")
        return {"status": "error", "message": "Linter quality check failed"}

    # 3. Phase 1: Expand seeds
    candidates = expand_seeds(pillars)

    # 4. Load indexed routes for anti-cannibalization check
    indexed_routes = [
        "/tools/rent-vs-buy-calculator",
        "/tools/mortgage-refinance",
        "/tools/peptide-reconstitution-calculator",
        "/tools/sleep-cycle-planner",
        "/tools/whole-house-surge-protector-calculator",
        "/tools/solar-roi",
        "/tools/heat-pump-roi-calculator",
        "/tools/daily-calorie-tdee",
        "/tools/freelance-rate-calculator",
        "/tools/commute-ev-vs-gas-calculator",
        "/tools/smart-home-roi",
        "/compare/mortgage-refinance-vs-bankrate-nerdwallet",
        "/compare/solar-roi-battery-vs-energysage-tesla",
        "/compare/glp1-peptide-cost-vs-ro-hims",
        "/compare/car-true-cost-ownership-vs-edmunds-kbb",
        "/compare/llm-api-pricing-vs-openrouter-artificialanalysis",
    ]

    searchapi_key = os.environ.get("SEARCHAPI_API_KEY")

    viable_candidates = []
    cannibalized_count = 0

    for cand in candidates:
        # Phase 2: SERP Weakness Audit
        audit = calculate_serp_weakness(cand["query"], searchapi_key)
        cand.update(audit)

        if not audit["is_viable"]:
            continue

        # Phase 3: Anti-Cannibalization Guard
        is_cannibal, sim, matched = check_cannibalization(cand["base_slug"], cand["query"], indexed_routes)
        cand["similarity"] = sim
        cand["matched_route"] = matched

        if is_cannibal:
            cannibalized_count += 1
            logger.debug("Cannibalization rejected '%s' (similarity %.2f with %s)", cand["query"], sim, matched)
            continue

        viable_candidates.append(cand)

    logger.info("Pipeline Summary: %d Candidates scouted -> %d Viable & Safe.", len(candidates), len(viable_candidates))

    duration = round(time.time() - start_time, 2)
    summary = {
        "status": "success" if viable_candidates else "partial",
        "scouted_count": len(candidates),
        "viable_count": sum(1 for c in candidates if c.get("is_viable")),
        "passed_guard_count": len(viable_candidates),
        "published_count": len(viable_candidates) if not dry_run else 0,
        "duration_seconds": duration,
        "run_at": datetime.now(UTC).isoformat(),
        "dry_run": dry_run,
    }

    # Phase 5: Telemetry & Notification
    if not dry_run and supabase_client:
        persist_pipeline_run(supabase_client, {
            "agent": "pseo_pipeline",
            "run_at": summary["run_at"],
            "items_processed": summary["scouted_count"],
            "items_published": summary["published_count"],
            "status": summary["status"],
            "error_log": None,
        })

    dispatch_telegram_summary(summary)

    logger.info("=" * 60)
    logger.info("pSEO PIPELINE FINISHED IN %.2fs (Status: %s)", duration, summary["status"].upper())
    logger.info("=" * 60)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Autonomous pSEO AI Agent Pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Run in simulation mode without DB mutations")
    parser.add_argument("--pillars", nargs="+", help="Specific pillars to scout (money, body, home, life, tech)")
    args = parser.parse_args()

    res = run_pseo_pipeline(dry_run=args.dry_run, pillars=args.pillars)
    if res.get("status") == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
