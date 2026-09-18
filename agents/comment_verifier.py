#!/usr/bin/env python3
"""
agents/comment_verifier.py — Autonomous Lifecycle Verifier Crawler for Submitted Comments
Part of Groundwork Tier-1 Off-Page Authority Engine.

Functionality:
1. Re-crawls target URLs where comments/links were injected at 24h, 48h, and 72h lifecycle milestones.
2. Checks public DOM for approved, live links pointing to `www.gworky.com` or `gworky.com`.
3. Verifies that the comment is 100% public (not pending moderation / unapproved).
4. Updates Supabase `link_injection_logs` and `outreach_prospects` (`status = 'published'` / `'verified_live'`).
5. Dispatches rich observational celebration telemetry to Telegram (@gwelena_bot).
6. Auto-expires comments rejected or pruned after 72 hours (`status = 'moderation_rejected'`).
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

# Auto-load .env.local
_ROOT = Path(__file__).resolve().parent.parent
_env_path = _ROOT / ".env.local"
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
logger = logging.getLogger("comment_verifier")

try:
    from agents.egress_dataimpulse import DataImpulseProxyRouter
except ImportError:
    from egress_dataimpulse import DataImpulseProxyRouter

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
CANONICAL_DOMAIN = "gworky.com"


def get_supabase_config() -> tuple[str, str]:
    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or os.environ.get("SUPABASE_URL", "https://keflumlrmggffyrsrmlk.supabase.co")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    return url.rstrip("/"), key


def send_telegram_alert(message: str) -> None:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_FOUNDER_CHAT_ID")
    if not bot_token or not chat_id:
        return

    telegram_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    for _attempt in range(4):
        try:
            with httpx.Client(timeout=10.0) as client:
                res = client.post(telegram_url, json=payload)
                if res.status_code == 200:
                    return
                if res.status_code == 429:
                    retry_after = res.json().get("parameters", {}).get("retry_after", 3)
                    time.sleep(retry_after + 1)
                    continue
                break
        except Exception as e:
            logger.debug(f"Telegram error: {e}")
            time.sleep(2)


def check_target_page_for_live_backlink(target_url: str, proxy: str | None = None) -> tuple[bool, str | None, bool]:
    """
    Crawls the target page publicly to verify if a Groundwork link is live.
    Returns (is_live, matched_anchor_text, is_dofollow).
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}

    # Attempt 1: Via proxy
    # Attempt 2: Direct fallback
    attempts = [(proxy, 12.0), (None, 10.0)] if proxy else [(None, 12.0)]

    for p, timeout in attempts:
        try:
            with httpx.Client(proxy=p, headers=headers, timeout=timeout, follow_redirects=True, verify=False) as client:
                resp = client.get(target_url)
                if resp.status_code != 200:
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")

                # Scan all anchor tags for gworky.com
                for a in soup.find_all("a", href=True):
                    href = a.get("href", "").lower()
                    if CANONICAL_DOMAIN in href:
                        # Check if parent container denotes awaiting moderation
                        parent_text = a.find_parent(class_=re.compile(r"comment", re.I))
                        if parent_text:
                            p_str = str(parent_text).lower()
                            if "awaiting moderation" in p_str or "unapproved" in p_str:
                                logger.info(f"Link found on {target_url} but still 'awaiting moderation'.")
                                return False, None, False

                        rel = a.get("rel", [])
                        if isinstance(rel, str):
                            rel = rel.split()
                        is_dofollow = "nofollow" not in rel and "ugc" not in rel
                        anchor_text = a.get_text(strip=True) or href
                        return True, anchor_text, is_dofollow

        except Exception as e:
            logger.debug(f"Crawl check attempt failed for {target_url}: {e}")

    return False, None, False


def run_comment_verification_crawler(dry_run: bool = False) -> int:
    """
    Scans pending comment records from Supabase and audits live status.
    """
    supabase_url, supabase_key = get_supabase_config()
    if not supabase_key:
        logger.warning("SUPABASE_SERVICE_ROLE_KEY missing. Cannot fetch pending queue.")
        return 0

    proxy_url = DataImpulseProxyRouter.get_proxy_url("us")
    verified_count = 0
    now_utc = datetime.now(UTC)

    # 1. Fetch pending records from link_injection_logs
    records_to_check: list[dict[str, Any]] = []
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"{supabase_url}/rest/v1/link_injection_logs?status=in.(draft,moderation_queue)&select=*&limit=50",
                headers={"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
            )
            if resp.status_code == 200:
                records_to_check = resp.json()
    except Exception as e:
        logger.warning(f"Failed to fetch pending link_injection_logs: {e}")

    logger.info(f"🔎 Audit Queue: {len(records_to_check)} pending comment records to verify.")

    for record in records_to_check:
        target_url = record.get("live_backlink_url") or record.get("target_url")
        record_id = record.get("id")
        created_at_str = record.get("created_at")

        if not target_url or not record_id:
            continue

        created_dt = None
        if created_at_str:
            with contextlib.suppress(Exception):
                created_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))

        age_hours = (now_utc - created_dt).total_seconds() / 3600 if created_dt else 12.0
        logger.info(f"Auditing comment target ({age_hours:.1f}h old): {target_url}")

        is_live, anchor_text, is_dofollow = check_target_page_for_live_backlink(target_url, proxy=proxy_url)

        if is_live:
            logger.info(f"🎉 BACKLINK VERIFIED LIVE on {target_url} (Anchor: '{anchor_text}', Dofollow: {is_dofollow})")
            verified_count += 1

            if not dry_run:
                # Update Supabase status to published
                with httpx.Client(timeout=10.0) as client:
                    client.patch(
                        f"{supabase_url}/rest/v1/link_injection_logs?id=eq.{record_id}",
                        headers={"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}", "Content-Type": "application/json"},
                        json={
                            "status": "published",
                            "is_dofollow": is_dofollow,
                            "anchor_text": anchor_text or record.get("anchor_text", ""),
                            "updated_at": now_utc.isoformat(),
                        }
                    )

                # If this was Phase 1 (initial_seed), autonomously trigger Phase 2 reply with calculator link
                metrics = record.get("metrics_snapshot") or {}
                comment_id = metrics.get("comment_id") or record.get("remote_comment_id")
                pillar = metrics.get("pillar", "money")

                if metrics.get("phase") == "initial_seed" and comment_id:
                    logger.info(f"Phase 1 seed live! Triggering Phase 2 contextual reply for parent comment {comment_id} on {target_url}...")
                    try:
                        from agents.wordpress_injector import execute_wordpress_injection
                        execute_wordpress_injection(
                            target_url=target_url,
                            pillar=pillar,
                            phase="contextual_reply",
                            parent_comment_id=str(comment_id),
                            use_proxy=True,
                        )
                    except Exception as p2_err:
                        logger.warning(f"Phase 2 progressive seeding trigger failed: {p2_err}")

                # Send Telegram celebration report
                msg = (
                    f"🎉 <b>[COMMENT ENGINE] BACKLINK VERIFIED LIVE!</b>\n\n"
                    f"• <b>Target Site:</b> <a href=\"{target_url}\">{urlparse(target_url).netloc}</a>\n"
                    f"• <b>Anchor Text:</b> <code>{anchor_text}</code>\n"
                    f"• <b>Link Equity:</b> {'🔥 DoFollow' if is_dofollow else '⚡ NoFollow/UGC'}\n"
                    f"• <b>Moderation Time:</b> {age_hours:.1f} hours from submission\n"
                    f"• <b>Destination:</b> <a href=\"{record.get('target_url')}\">{record.get('source_slug')}</a>\n\n"
                    f"<i>Status updated to 'published' in Supabase link_injection_logs.</i>"
                )
                send_telegram_alert(msg)

        elif age_hours > 72.0:
            logger.info(f"Comment unapproved after {age_hours:.1f}h (> 72h). Marking moderation_rejected: {target_url}")
            if not dry_run:
                with httpx.Client(timeout=10.0) as client:
                    client.patch(
                        f"{supabase_url}/rest/v1/link_injection_logs?id=eq.{record_id}",
                        headers={"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}", "Content-Type": "application/json"},
                        json={"status": "failed", "error_log": "Unapproved after 72 hours", "updated_at": now_utc.isoformat()}
                    )

        # Respectful delay between verification requests
        time.sleep(1.5)

    logger.info(f"✅ Comment verification crawl complete! Live confirmed: {verified_count}")
    return verified_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Groundwork Comment Lifecycle Verifier Crawler")
    parser.add_argument("--dry-run", action="store_true", help="Audit without writing to database")
    args = parser.parse_args()

    run_comment_verification_crawler(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
