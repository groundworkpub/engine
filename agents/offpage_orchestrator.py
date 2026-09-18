"""Groundwork Centralized Off-Page Authority Orchestrator

Master autonomous coordinator executing the decoupled off-page authority pipeline:
1. Harvest Intake: Replenishes target queues (dork harvesting & zombie hunter).
2. Throttled Injection: Executes Two-Phase Progressive Seeding with strict pacing.
3. Lifecycle Verifier: Audits 24h-72h public DOM status & auto-triggers Phase 2 replies.
4. Buffer Syndication: Ingests open-access RSS, packages articles with sticky utility widgets.
5. Telemetry Digest: Dispatches a unified observational report to Telegram (@gwelena_bot).

Usage:
    python agents/offpage_orchestrator.py --pillar money --dry-run
    python agents/offpage_orchestrator.py --pillar all --limit 3
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

# Ensure paths
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load .env.local
_env_path = PROJECT_ROOT / ".env.local"
if _env_path.exists():
    with open(_env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("offpage_orchestrator")

# Import subsystem modules
try:
    from blogger_publisher import get_service_account_credentials, publish_post_to_blogger
    from buffer_syndicator import execute_buffer_syndication
    from comment_verifier import run_comment_verification_crawler
    from dork_harvester import run_dork_harvest_pipeline
    from wordpress_injector import execute_wordpress_injection
    from zombie_hunter import hunt_zombies
except ImportError as e:
    logger.error(f"Subsystem import failure: {e}")
    from agents.buffer_syndicator import execute_buffer_syndication
    from agents.comment_verifier import run_comment_verification_crawler
    from agents.dork_harvester import run_dork_harvest_pipeline
    from agents.wordpress_injector import execute_wordpress_injection
    from agents.zombie_hunter import hunt_zombies

ALL_PILLARS = ["money", "home", "body", "tech", "life"]


def get_supabase_config() -> tuple[str, str]:
    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or os.environ.get("SUPABASE_URL", "https://keflumlrmggffyrsrmlk.supabase.co")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    return url.rstrip("/"), key


def send_unified_telegram_digest(summary: dict[str, Any]) -> None:
    """Dispatches a single comprehensive observational telemetry card to Telegram."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_FOUNDER_CHAT_ID")
    if not bot_token or not chat_id:
        return

    duration = summary.get("duration_seconds", 0)
    intake = summary.get("intake", {})
    injections = summary.get("injections", {})
    verified = summary.get("verified", 0)
    buffer_posts = summary.get("buffer_posts", 0)

    msg = (
        f"🌐 <b>[OFF-PAGE ORCHESTRATOR] CYCLE TELEMETRY SUMMARY</b>\n\n"
        f"⏱️ <b>Cycle Execution Time:</b> <code>{duration:.1f}s</code>\n"
        f"🎯 <b>Pillars Covered:</b> <code>{', '.join(summary.get('pillars', []))}</code>\n\n"
        f"📥 <b>1. Intake & Harvesting:</b>\n"
        f"• New Prospects Enqueued: <b>{intake.get('prospects_enqueued', 0)}</b>\n"
        f"• Zombie Properties Located: <b>{intake.get('zombies_found', 0)}</b>\n\n"
        f"📝 <b>2. Two-Phase Injections:</b>\n"
        f"• Targets Processed: <b>{injections.get('attempted', 0)}</b>\n"
        f"• Submissions Queued/Moderated: <b>{injections.get('moderated', 0)}</b>\n"
        f"• Submissions Live: <b>{injections.get('live', 0)}</b>\n\n"
        f"🔍 <b>3. Public Lifecycle Verification:</b>\n"
        f"• Backlinks Audited & Verified Live: <b>{verified}</b>\n\n"
        f"📡 <b>4. Buffer Content Syndication:</b>\n"
        f"• Articles Packaged & Widgets Embedded: <b>{buffer_posts}</b>\n\n"
        f"<i>100% Autonomous Execution | Zero-Cost Infrastructure | No Human Gating</i>"
    )

    telegram_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True}

    try:
        with httpx.Client(timeout=10.0) as client:
            client.post(telegram_url, json=payload)
    except Exception as e:
        logger.warning(f"Failed to dispatch Telegram digest: {e}")


def step_harvest_intake(pillar: str, dry_run: bool = False) -> dict[str, int]:
    """Runs dork harvesting and zombie hunting to replenish queue."""
    logger.info(f"--- [STAGE 1] INTAKE & HARVESTING (Pillar: {pillar.upper()}) ---")
    intake_stats = {"prospects_enqueued": 0, "zombies_found": 0}

    try:
        # Run dork harvester
        prospects = run_dork_harvest_pipeline(
            pillar=pillar,
            footprint_type="all",
            limit_per_query=2,
            max_prospects=3,
            dry_run=dry_run,
        )
        intake_stats["prospects_enqueued"] = prospects if isinstance(prospects, int) else len(prospects)
    except Exception as e:
        logger.warning(f"Intake dork harvesting error: {e}")

    try:
        # Run zombie hunter
        zombies = hunt_zombies(pillar=pillar, limit=1)
        intake_stats["zombies_found"] = len(zombies)
    except Exception as e:
        logger.warning(f"Intake zombie hunting error: {e}")

    return intake_stats


def step_throttled_injection(
    pillar: str,
    limit: int = 2,
    pacing_seconds: int = 5,
    dry_run: bool = False,
) -> dict[str, int]:
    """Fetches queued targets from Supabase and executes Two-Phase Progressive Seeding."""
    logger.info(f"--- [STAGE 2] THROTTLED INJECTION (Pillar: {pillar.upper()}) ---")
    stats = {"attempted": 0, "live": 0, "moderated": 0, "failed": 0}

    supabase_url, supabase_key = get_supabase_config()
    if not supabase_key and not dry_run:
        logger.warning("Supabase key missing. Skipping injection queue poll.")
        return stats

    # Fetch pending queue targets
    targets_to_inject = []
    if not dry_run and supabase_key:
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(
                    f"{supabase_url}/rest/v1/link_injection_logs?status=eq.draft&target_platform=eq.wordpress_comment&metrics_snapshot->>pillar=eq.{pillar}&select=*&limit={limit}",
                    headers={"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"},
                )
                if resp.status_code == 200:
                    targets_to_inject = resp.json()
        except Exception as e:
            logger.warning(f"Failed to fetch queue from Supabase: {e}")

    # Fallback default target for demonstration/dry-run
    if not targets_to_inject:
        targets_to_inject = [{
            "live_backlink_url": "https://cleantechnica.com/2024/05/20/ev-sales-boom/",
            "pillar": pillar,
            "id": None,
        }]

    for item in targets_to_inject[:limit]:
        target_url = item.get("live_backlink_url") or item.get("target_url")
        if not target_url:
            continue

        stats["attempted"] += 1
        logger.info(f"Executing throttled injection on target: {target_url}")

        res = execute_wordpress_injection(
            target_url=target_url,
            pillar=pillar,
            preferred_method="auto",
            phase="initial_seed",
            dry_run=dry_run,
            use_proxy=True,
        )

        status = res.get("status", "failed")
        if status == "live":
            stats["live"] += 1
        elif status in ("moderated", "desktop_browser_task"):
            stats["moderated"] += 1
        else:
            stats["failed"] += 1

        # Pacing sleep
        if pacing_seconds > 0:
            time.sleep(pacing_seconds)

    return stats


def step_lifecycle_verification(dry_run: bool = False) -> int:
    """Executes the public DOM verifier to confirm approved comments and trigger Phase 2."""
    logger.info("--- [STAGE 3] PUBLIC LIFECYCLE VERIFICATION ---")
    try:
        return run_comment_verification_crawler(dry_run=dry_run)
    except Exception as e:
        logger.warning(f"Lifecycle verification encountered error: {e}")
        return 0


def step_buffer_syndication(pillar: str, dry_run: bool = False) -> int:
    """Ingests RSS, synthesizes buffer articles with sticky utility widgets."""
    logger.info(f"--- [STAGE 4] BUFFER CONTENT SYNDICATION (Pillar: {pillar.upper()}) ---")
    try:
        articles = execute_buffer_syndication(pillar=pillar, limit=1, dry_run=dry_run)
        return len(articles)
    except Exception as e:
        logger.warning(f"Buffer syndication error: {e}")
        return 0


def run_orchestrator(
    pillar: str = "money",
    limit_per_pillar: int = 1,
    pacing_seconds: int = 2,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Runs the complete off-page authority engine cycle."""
    start_time = time.time()
    pillars = ALL_PILLARS if pillar == "all" else [pillar]

    logger.info("=" * 65)
    logger.info("STARTING GROUNDWORK OFF-PAGE AUTHORITY ORCHESTRATOR")
    logger.info(f" Pillars  : {', '.join(pillars)}")
    logger.info(f" Dry Run  : {dry_run}")
    logger.info(f" Pacing   : {pacing_seconds}s per action")
    logger.info("=" * 65)

    total_intake = {"prospects_enqueued": 0, "zombies_found": 0}
    total_injections = {"attempted": 0, "live": 0, "moderated": 0, "failed": 0}
    total_buffer_posts = 0

    for p in pillars:
        # Step 1: Intake & Harvesting
        intake_res = step_harvest_intake(p, dry_run=dry_run)
        total_intake["prospects_enqueued"] += intake_res.get("prospects_enqueued", 0)
        total_intake["zombies_found"] += intake_res.get("zombies_found", 0)

        # Step 2: Throttled Injection
        inj_res = step_throttled_injection(p, limit=limit_per_pillar, pacing_seconds=pacing_seconds, dry_run=dry_run)
        for k in total_injections:
            total_injections[k] += inj_res.get(k, 0)

        # Step 4: Buffer Syndication
        total_buffer_posts += step_buffer_syndication(p, dry_run=dry_run)

    # Step 3: Global Lifecycle Verification
    verified_live = step_lifecycle_verification(dry_run=dry_run)

    duration = time.time() - start_time
    summary = {
        "status": "completed",
        "pillars": pillars,
        "duration_seconds": duration,
        "intake": total_intake,
        "injections": total_injections,
        "verified": verified_live,
        "buffer_posts": total_buffer_posts,
    }

    logger.info("=" * 65)
    logger.info(f"ORCHESTRATOR CYCLE FINISHED in {duration:.1f}s")
    logger.info(json.dumps(summary, indent=2))
    logger.info("=" * 65)

    if not dry_run:
        send_unified_telegram_digest(summary)

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Groundwork Off-Page Authority Orchestrator")
    parser.add_argument("--pillar", default="money", choices=["money", "body", "home", "life", "tech", "all"])
    parser.add_argument("--limit", type=int, default=1, help="Max targets to inject per pillar")
    parser.add_argument("--pacing", type=int, default=2, help="Pacing delay in seconds between injection actions")
    parser.add_argument("--dry-run", action="store_true", help="Simulate orchestrator without network mutations")
    args = parser.parse_args()

    summary = run_orchestrator(
        pillar=args.pillar,
        limit_per_pillar=args.limit,
        pacing_seconds=args.pacing,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
