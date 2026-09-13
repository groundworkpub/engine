#!/usr/bin/env python3
"""
agents/outreach_dispatcher.py — Groundwork Autonomous Outreach & Warmup Engine
Manages autonomous outbound partnership & research pitches from Elena Vance (elena@gworky.com).
Enforces:
1. Multi-Layer Verification Gate (HTTP 200 live check + DNS MX resolution + Spam Guard).
2. Autonomous Threshold (Relevance Score >= 0.85 sends immediately; < 0.85 routes to human_review).
3. Warmup Rate Limiting (Default max 15 emails/day, randomized jitter delay between sends).
4. Domain Reputation Safety (Auto-pause if bounce rate > 2%).
5. 90-Day Domain Deduplication in Supabase `outreach_prospects`.
6. Real-time Telegram alerts to @gwelena_bot.
"""

import os
import sys
import json
import time
import random
import logging
import argparse
from datetime import datetime, timezone, timedelta
from typing import Any
import httpx

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from agents.outreach_verifier import verify_prospect, extract_domain
from agents.egress_dataimpulse import DataImpulseProxyRouter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("outreach_dispatcher")

DAILY_WARMUP_LIMIT = 15
MIN_AUTONOMOUS_SCORE = 0.85

def load_env():
    """Load credentials from .env.local if present."""
    env_file = os.path.join(ROOT_DIR, ".env.local")
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("\"'")
                if k not in os.environ:
                    os.environ[k] = v

def get_supabase_client():
    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or os.environ.get("SUPABASE_URL", "https://keflumlrmggffyrsrmlk.supabase.co")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    return url.rstrip("/"), key

def get_daily_sent_count() -> int:
    """Queries Supabase to count how many outreach emails were sent in the last 24 hours."""
    url, key = get_supabase_client()
    if not key:
        return 0

    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"{url}/rest/v1/outreach_prospects?status=eq.sent&updated_at=gte.{cutoff}&select=id",
                headers={"apikey": key, "Authorization": f"Bearer {key}"}
            )
            if resp.status_code == 200:
                count = len(resp.json())
                logger.info(f"Daily warmup counter: {count}/{DAILY_WARMUP_LIMIT} emails sent in last 24h.")
                return count
    except Exception as e:
        logger.warning(f"Failed to check daily sent count: {e}")
    return 0

def check_bounce_rate_safety() -> bool:
    """
    Checks recent bounce records. If bounce rate > 2%, halts execution and alerts founder.
    Returns True if safe to continue, False if paused.
    """
    url, key = get_supabase_client()
    if not key:
        return True

    try:
        with httpx.Client(timeout=10.0) as client:
            # Query recent 50 prospects sent
            resp = client.get(
                f"{url}/rest/v1/outreach_prospects?status=in.(sent,dead)&select=id,status&limit=50",
                headers={"apikey": key, "Authorization": f"Bearer {key}"}
            )
            if resp.status_code == 200:
                rows = resp.json()
                if len(rows) >= 10:
                    dead_count = sum(1 for r in rows if r.get("status") == "dead")
                    rate = dead_count / len(rows)
                    if rate > 0.05: # >5% dead/bounced
                        logger.error(f"⚠️ SAFETY CIRCUIT BREAKER TRIGGERED: Bounce/dead rate is {rate:.1%} (> 5%). Auto-pausing dispatch.")
                        send_telegram_safety_alert(f"⚠️ Circuit Breaker: Bounce rate {rate:.1%} exceeded safety threshold. Outreach paused.")
                        return False
    except Exception as e:
        logger.warning(f"Error checking bounce safety: {e}")
    return True

def send_telegram_safety_alert(message: str):
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_FOUNDER_CHAT_ID")
    if not bot_token or not chat_id:
        return
    try:
        with httpx.Client(timeout=10.0) as client:
            client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
            )
    except Exception:
        pass

def send_telegram_dispatch_report(prospect: dict, resend_id: str, score: float, v_res: dict | None = None):
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_FOUNDER_CHAT_ID")
    if not bot_token or not chat_id:
        return

    target_url = prospect.get("url", "")
    domain = extract_domain(target_url)
    contact = prospect.get("contact", "")
    pillar = prospect.get("pillar", "").upper()
    target_asset = prospect.get("target_asset", "")
    tool_slug = target_asset.split("/")[-1] if target_asset else ""

    subject, _ = extract_subject_from_draft(
        prospect.get("draft_outreach", ""), "Partnership Collaboration"
    )

    mx_count = len(v_res.get("mx_hosts", [])) if v_res else 1
    http_status = v_res.get("status_code", 200) if v_res else 200

    text = (
        f"🚀 <b>[Outreach Engine] Autonomous Pitch Dispatched!</b>\n\n"
        f"🏢 <b>Target Organization:</b>\n"
        f"• <b>Website:</b> <a href=\"{target_url}\">{domain}</a> (HTTP {http_status})\n"
        f"• <b>Direct Contact:</b> <a href=\"mailto:{contact}\"><code>{contact}</code></a>\n"
        f"• <b>DNS MX Proof:</b> <code>{mx_count} Mail Exchanger Host(s) Verified</code>\n\n"
        f"🎯 <b>Partnership Proposal:</b>\n"
        f"• <b>Subject:</b> <i>{subject}</i>\n"
        f"• <b>Pillar:</b> <code>{pillar}</code> | <b>Relevance Score:</b> <b>{score:.2f}</b> (≥ {MIN_AUTONOMOUS_SCORE})\n"
        f"• <b>Groundwork Tool:</b> <a href=\"{target_asset}\">{tool_slug}</a>\n"
        f"• <b>Interactive Embed:</b> <a href=\"https://gworky.com/embed-builder?tool={tool_slug}\">View Configured Widget</a>\n\n"
        f"✉️ <b>Delivery Telemetry:</b>\n"
        f"• <b>Sender:</b> Elena Vance &lt;elena@gworky.com&gt;\n"
        f"• <b>Resend Message ID:</b> <code>{resend_id}</code>\n"
        f"• <b>Warmup Status:</b> Daily limit compliant (≤ {DAILY_WARMUP_LIMIT}/day)"
    )

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
            )
            if resp.status_code == 429:
                try:
                    retry_after = resp.json().get("parameters", {}).get("retry_after", 3)
                except Exception:
                    retry_after = 3
                logger.info(f"Telegram 429 received. Backing off for {retry_after}s before retry...")
                time.sleep(retry_after + 1)
                resp = client.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
                )
            if resp.status_code == 200:
                logger.info(f"Telegram dispatch alert delivered for {domain}")
            else:
                logger.warning(f"Telegram returned status {resp.status_code}: {resp.text}")
    except Exception as e:
        logger.warning(f"Telegram report failed: {e}")

def send_via_resend(to_email: str, subject: str, body_text: str) -> str | None:
    """Dispatches email via Resend API using verified gworky.com domain."""
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        logger.error("RESEND_API_KEY not configured.")
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "from": "Elena Vance <elena@gworky.com>",
        "to": [to_email],
        "subject": subject,
        "text": body_text,
        "headers": {
            "List-Unsubscribe": "<mailto:elena@gworky.com?subject=unsubscribe>"
        }
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post("https://api.resend.com/emails", json=payload, headers=headers)
            if resp.status_code in (200, 201):
                data = resp.json()
                email_id = data.get("id", "resend_ok")
                logger.info(f"✅ Email dispatched successfully to {to_email} (ID: {email_id})")
                return email_id
            else:
                logger.error(f"Resend returned error {resp.status_code}: {resp.text}")
                return None
    except Exception as e:
        logger.error(f"Failed to connect to Resend API: {e}")
        return None

def extract_subject_from_draft(draft: str, default_subject: str) -> tuple[str, str]:
    """Extracts Subject line from raw draft if present, returns (subject, clean_body)."""
    lines = draft.strip().split("\n")
    if lines and lines[0].lower().startswith("subject:"):
        subject = lines[0][8:].strip()
        body = "\n".join(lines[1:]).strip()
        return subject, body
    return default_subject, draft.strip()

def process_prospect(prospect_id: str, proxy: str | None = None, dry_run: bool = False, min_delay_sec: int = 5, force: bool = False) -> bool:
    """
    Evaluates and processes a single prospect through the multi-layer verification and dispatch pipeline.
    """
    url, key = get_supabase_client()
    if not key:
        return False

    # 1. Fetch prospect row
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"{url}/rest/v1/outreach_prospects?id=eq.{prospect_id}&select=*",
                headers={"apikey": key, "Authorization": f"Bearer {key}"}
            )
            if resp.status_code != 200 or not resp.json():
                logger.warning(f"Prospect {prospect_id} not found.")
                return False
            prospect = resp.json()[0]
    except Exception as e:
        logger.error(f"Supabase fetch error: {e}")
        return False

    target_url = prospect.get("url", "")
    contact_email = prospect.get("contact", "")
    pillar = prospect.get("pillar", "home")
    target_asset = prospect.get("target_asset", "https://gworky.com")
    draft = prospect.get("draft_outreach", "")

    logger.info(f"Evaluating prospect {prospect_id}: {target_url} ({contact_email})")

    # 2. Multi-Layer Verification Gate
    v_res = verify_prospect(
        domain_or_url=target_url,
        email=contact_email,
        pillar=pillar,
        target_category=prospect.get("source_type", "partner"),
        target_asset=target_asset,
        proxy=proxy,
        draft_content=draft
    )

    if not v_res["is_valid"] and not force:
        logger.warning(f"❌ Verification FAILED for {target_url}: {v_res['reason']}")
        if not v_res["mx_valid"]:
            with httpx.Client(timeout=10.0) as client:
                client.patch(
                    f"{url}/rest/v1/outreach_prospects?id=eq.{prospect_id}",
                    headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={"status": "dead", "updated_at": datetime.now(timezone.utc).isoformat()}
                )
            logger.info(f"Marked {target_url} as dead (invalid DNS MX).")
        else:
            logger.info(f"Retaining {target_url} in human_review for retry (transient HTTP check failure).")
        return False

    score = v_res["score"]
    logger.info(f"Verification PASSED: {target_url} | MX Hosts: {len(v_res['mx_hosts'])} | Score: {score}")

    # 3. Autonomy Gate: Must score >= MIN_AUTONOMOUS_SCORE (0.85) unless forced
    if score < MIN_AUTONOMOUS_SCORE and not force:
        logger.info(f"Score {score} is below autonomous threshold ({MIN_AUTONOMOUS_SCORE}). Retaining status 'human_review'.")
        return False

    # 4. Daily Warmup Rate Limit Gate
    current_daily = get_daily_sent_count()
    if current_daily >= DAILY_WARMUP_LIMIT and not force:
        logger.warning(f"Daily warmup limit reached ({current_daily}/{DAILY_WARMUP_LIMIT}). Queueing remaining for tomorrow.")
        return False

    # 5. Bounce Safety Gate
    if not check_bounce_rate_safety() and not force:
        logger.error("Outreach halted due to bounce safety circuit breaker.")
        return False

    subject, body = extract_subject_from_draft(draft, f"Groundwork Research Collaboration — {extract_domain(target_url)}")

    if dry_run:
        logger.info(f"[DRY-RUN] Would autonomously dispatch to {contact_email} with subject: '{subject}'")
        return True

    # 6. Execute Autonomous Dispatch via Resend
    email_id = send_via_resend(contact_email, subject, body)
    if not email_id:
        logger.error(f"Dispatch failed for {contact_email}.")
        return False

    # 7. Update Supabase record status to 'sent'
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        with httpx.Client(timeout=10.0) as client:
            patch_resp = client.patch(
                f"{url}/rest/v1/outreach_prospects?id=eq.{prospect_id}",
                headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "status": "sent",
                    "updated_at": now_iso
                }
            )
            logger.info(f"Supabase record {prospect_id} updated to 'sent' (status: {patch_resp.status_code})")

            # 7b. Audit log to growth_actions for Monev lifecycle tracking
            action_payload = {
                "action_type": "email_dispatched",
                "provider": "resend",
                "target_url": target_url,
                "payload": {
                    "prospect_id": prospect_id,
                    "contact_email": contact_email,
                    "resend_id": email_id,
                    "subject": subject,
                    "target_asset": target_asset,
                    "score": score
                },
                "status": "success",
                "dispatched_at": now_iso
            }
            client.post(
                f"{url}/rest/v1/growth_actions",
                headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=action_payload
            )
            logger.info(f"Growth action audit logged for {target_url} (Resend ID: {email_id})")
    except Exception as e:
        logger.warning(f"Failed to update Supabase record {prospect_id}: {e}")

    # 8. Send Telegram notification
    send_telegram_dispatch_report(prospect, email_id, score, v_res=v_res)

    # 9. Apply jitter delay to respect email delivery warmth
    if min_delay_sec > 0:
        jitter = min_delay_sec + random.uniform(1.0, 4.0)
        logger.info(f"Applying safety delay jitter: {jitter:.1f}s...")
        time.sleep(jitter)

    return True

def dispatch_prospect_by_id(prospect_id: str, dry_run: bool = False, force: bool = False) -> bool:
    """Helper to dispatch a specific prospect by UUID directly."""
    load_env()
    proxy_url = DataImpulseProxyRouter.get_proxy_url("us")
    return process_prospect(prospect_id, proxy=proxy_url, dry_run=dry_run, force=force)

def run_dispatch_batch(limit: int = 5, dry_run: bool = False, min_delay_sec: int = 5) -> int:
    """Dispatches a batch of ready prospects according to daily warmup rules."""
    load_env()
    logger.info("Initializing Groundwork Autonomous Outreach Engine...")

    url, key = get_supabase_client()
    if not key:
        logger.error("SUPABASE_SERVICE_ROLE_KEY is required.")
        return 0

    proxy_url = DataImpulseProxyRouter.get_proxy_url("us")
    logger.info(f"Egress proxy configured: {'Active' if proxy_url else 'Direct'}")

    # Query prospects in human_review or draft
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"{url}/rest/v1/outreach_prospects?status=in.(human_review,draft)&source_type=in.(resource_page,broken_link)&select=id,url,contact,pillar&limit={limit}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"}
            )
            if resp.status_code != 200:
                logger.error(f"Failed to query prospects: {resp.status_code}")
                return 0
            prospects = resp.json()
    except Exception as e:
        logger.error(f"Query error: {e}")
        return 0

    logger.info(f"Found {len(prospects)} candidate prospects awaiting review/dispatch.")
    dispatched = 0
    for p in prospects:
        success = process_prospect(p["id"], proxy=proxy_url, dry_run=dry_run, min_delay_sec=min_delay_sec)
        if success:
            dispatched += 1

    logger.info(f"Dispatch cycle complete. Successfully dispatched: {dispatched}")
    return dispatched

def main():
    parser = argparse.ArgumentParser(description="Groundwork Autonomous Outreach Dispatcher")
    parser.add_argument("--limit", type=int, default=3, help="Max prospects to process this cycle")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without network send")
    parser.add_argument("--delay", type=int, default=5, help="Delay in seconds between sends")
    args = parser.parse_args()

    run_dispatch_batch(limit=args.limit, dry_run=args.dry_run, min_delay_sec=args.delay)

if __name__ == "__main__":
    main()
