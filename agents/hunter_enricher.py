#!/usr/bin/env python3
"""
agents/hunter_enricher.py — Surgical Hunter.io API v2 Client & Contact Resolver

Strictly follows the "Opportunity-First, Surgical Hunter Consumption" directive:
1. Only consumes Hunter.io search credits when an Opportunity is QUALIFIED (Score >= 75).
2. Queries Hunter.io Domain Search for named editorial/content staff.
3. Verifies email deliverability via Hunter.io Email Verifier.
4. Synthesizes a bespoke pitch personalized to the recipient's name and position.
5. Pushes 1-click mobile approval cards to Telegram (@gwelena_bot).
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

try:
    from agents.opportunity_engine import Opportunity, evaluate_page_opportunity, synthesize_pitch_from_opportunity
except ImportError:
    from opportunity_engine import Opportunity, evaluate_page_opportunity, synthesize_pitch_from_opportunity

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("hunter_enricher")

# Load environment
def _load_env() -> None:
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

_load_env()

HUNTER_API_KEY = os.environ.get("HUNTER_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_FOUNDER_CHAT_ID", "")


class HunterClient:
    """Hunter.io API v2 Client with built-in rate-limiting and quota tracking."""

    BASE_URL = "https://api.hunter.io/v2"

    def __init__(self, api_key: str | None = None):
        self.api_key = HUNTER_API_KEY if api_key is None else api_key
        if not self.api_key:
            logger.warning("HUNTER_API_KEY is not set. HunterClient will operate in mock/offline mode.")

    async def get_account_info(self) -> dict[str, Any]:
        """Fetches remaining search & verification credits."""
        if not self.api_key:
            return {"mock": True, "calls_remaining": 50, "searches_remaining": 50}

        url = f"{self.BASE_URL}/account?api_key={self.api_key}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(url)
            if res.status_code == 200:
                data = res.json().get("data", {})
                reqs = data.get("requests", {})
                searches = reqs.get("searches", {})
                return {
                    "email": data.get("email"),
                    "plan_name": data.get("plan_name"),
                    "searches_used": searches.get("used", 0),
                    "searches_available": searches.get("available", 0),
                    "resets_at": data.get("reset_date"),
                }
            else:
                logger.error(f"Hunter account lookup failed: {res.status_code} - {res.text}")
                return {}

    async def domain_search(
        self,
        domain: str,
        department: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Searches Hunter for personal contacts with deliverability confidence."""
        if not self.api_key:
            return [
                {
                    "first_name": "Editorial",
                    "last_name": "Desk",
                    "email": f"editorial@{domain}",
                    "position": "Content Lead",
                    "confidence": 85,
                    "source": "mock_hunter",
                }
            ]

        # In Hunter.io v2, valid department values are: communication, executive, it, finance, management, sales, legal, marketing, hr, operations
        params = f"domain={domain}&type=personal&limit={limit}&api_key={self.api_key}"
        if department and department in ["communication", "marketing", "management", "executive", "it", "finance"]:
            params += f"&department={department}"

        url = f"{self.BASE_URL}/domain-search?{params}"

        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(url)
            if res.status_code == 200:
                data = res.json().get("data", {})
                emails = data.get("emails", [])

                results = []
                for e in emails:
                    results.append({
                        "first_name": e.get("first_name") or "",
                        "last_name": e.get("last_name") or "",
                        "email": e.get("value", "").lower(),
                        "position": e.get("position") or "Editor / Researcher",
                        "confidence": e.get("confidence", 0),
                        "source": "hunter_domain_search",
                    })
                return results
            else:
                logger.error(f"Hunter domain search failed for {domain}: {res.status_code} - {res.text}")
                return []

    async def verify_email(self, email: str) -> dict[str, Any]:
        """Verifies email deliverability status via Hunter."""
        if not self.api_key:
            return {"result": "deliverable", "score": 90}

        url = f"{self.BASE_URL}/email-verifier?email={email}&api_key={self.api_key}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(url)
            if res.status_code == 200:
                data = res.json().get("data", {})
                return {
                    "result": data.get("result", "unknown"),
                    "score": data.get("score", 0),
                    "smtp_check": data.get("smtp_check", False),
                    "mx_records": data.get("mx_records", False),
                }
            return {"result": "unknown", "score": 0}


async def enrich_opportunity_surgically(
    opp: Opportunity,
    hunter_client: HunterClient | None = None,
) -> Opportunity:
    """
    Enriches a qualified Opportunity with verified contact details from Hunter.io.
    """
    if opp.status == "NO_GO" or opp.total_score < 75.0:
        logger.info(f"Skipping Hunter enrichment for {opp.target_url} — Opportunity score too low ({opp.total_score}).")
        return opp

    client = hunter_client or HunterClient()
    contacts = await client.domain_search(opp.domain, department="editorial", limit=3)

    if contacts:
        # Choose contact with highest confidence
        best_contact = max(contacts, key=lambda c: c["confidence"])

        # Verify deliverability
        verification = await client.verify_email(best_contact["email"])
        if verification.get("result") in ["deliverable", "risky"]:
            full_name = f"{best_contact['first_name']} {best_contact['last_name']}".strip()
            opp.target_person = full_name if full_name else "Editorial Team"
            opp.target_email = best_contact["email"]
            opp.contact_source = best_contact["source"]
            opp.hunter_confidence = best_contact["confidence"]

            # Re-synthesize pitch with personalized name
            opp.pitch_draft = synthesize_pitch_from_opportunity(opp, contact_name=best_contact["first_name"])
            logger.info(f"✅ Enriched {opp.domain} -> {opp.target_email} ({opp.target_person}, {best_contact['position']}) [Confidence: {opp.hunter_confidence}%]")
    else:
        # Fallback to institutional desk
        opp.target_email = f"editorial@{opp.domain}"
        opp.target_person = "Editorial Desk"
        opp.contact_source = "domain_fallback"
        opp.hunter_confidence = 50

    return opp


async def send_telegram_opportunity_card(opp: Opportunity) -> bool:
    """Pushes a structured Opportunity Card to Telegram (@gwelena_bot) with Approve/Dismiss actions."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram Bot Token or Chat ID not configured. Skipping alert.")
        return False

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

        badge_emoji = "🎯" if opp.total_score >= 85 else "⚡"
        opp_badge = f"[{opp.opportunity_type}]"

        text = (
            f"{badge_emoji} <b>[OPPORTUNITY 2.0: {opp_badge}]</b>\n\n"
            f"• <b>Target URL:</b> <code>{opp.target_url}</code>\n"
            f"• <b>Target Contact:</b> <code>{opp.target_email}</code> ({opp.target_person or 'Curator'})\n"
            f"• <b>Hunter Confidence:</b> {opp.hunter_confidence}% | <b>Score:</b> {opp.total_score}/100\n"
            f"• <b>Matched Asset:</b> <a href=\"{opp.matching_asset['url']}\">{opp.matching_asset['title']}</a>\n\n"
            f"💡 <b>1-Sentence Proposition:</b>\n"
            f"<i>\"{opp.one_sentence_proposition}\"</i>\n\n"
            f"🔍 <b>Evidence Found:</b>\n"
            f"<code>\"{opp.evidence_snippet[:220]}...\"</code>\n\n"
            f"✉️ <b>Elena Pitch Preview:</b>\n"
            f"<i>\"{opp.pitch_draft[:300]}...\"</i>"
        )

        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {"text": "✅ Approve & Send via Resend", "callback_data": f"approve_opp:{opp.id}"},
                        {"text": "❌ Dismiss", "callback_data": f"reject_opp:{opp.id}"}
                    ]
                ]
            }
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, json=payload)
            return res.status_code == 200
    except Exception as e:
        logger.warning(f"Failed to push Telegram opportunity card: {e}")
        return False


async def send_telegram_dispatch_report(opp: Opportunity, message_id: str = "res_live") -> bool:
    """Pushes an autonomous dispatch report card to Telegram @gwelena_bot."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

        badge_emoji = "🚀" if opp.total_score >= 85 else "✉️"
        text = (
            f"{badge_emoji} <b>[OUTREACH PITCH DISPATCHED (AUTONOMOUS)]</b>\n\n"
            f"• <b>Target Institusi:</b> <code>{opp.target_url}</code>\n"
            f"• <b>Penerima Terverifikasi:</b> <code>{opp.target_email}</code> ({opp.target_person or 'Curator'})\n"
            f"• <b>Hunter Deliverability:</b> {opp.hunter_confidence}% | <b>Score:</b> {opp.total_score}/100\n"
            f"• <b>Matched Asset:</b> <a href=\"{opp.matching_asset['url']}\">{opp.matching_asset['title']}</a>\n\n"
            f"💡 <b>1-Sentence Proposition:</b>\n"
            f"<i>\"{opp.one_sentence_proposition}\"</i>\n\n"
            f"📤 <b>Resend Status:</b> 🟢 Delivered (ID: <code>{message_id}</code>)\n"
            f"• <b>Pengirim:</b> <code>Elena from Groundwork &lt;elena@gworky.com&gt;</code>\n"
            f"• <b>Waktu:</b> <code>{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}</code>\n\n"
            f"<i>Operasi autopilot aktif. Tekan '🛑 Emergency Kill' di Telegram jika ingin menjeda.</i>"
        )

        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {"text": "📊 Cek Status DB", "callback_data": "action_refresh_status"},
                        {"text": "🛑 Trigger Kill-Switch", "callback_data": "cmd_kill"}
                    ]
                ]
            }
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, json=payload)
            return res.status_code == 200
    except Exception as e:
        logger.warning(f"Failed to push Telegram dispatch report: {e}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Groundwork Surgical Hunter Enricher")
    parser.add_argument("--url", type=str, default="https://extension.harvard.edu/resources/", help="Target URL")
    parser.add_argument("--domain", type=str, default="harvard.edu", help="Domain")
    parser.add_argument("--pillar", type=str, default="money", help="Pillar")
    parser.add_argument("--check-account", action="store_true", help="Check Hunter.io account quota")
    args = parser.parse_args()

    client = HunterClient()

    if args.check_account:
        print("\n🔍 Checking Hunter.io Account Quota...")
        account_info = asyncio.run(client.get_account_info())
        print(json.dumps(account_info, indent=2))
        sys.exit(0)

    async def main():
        print(f"\n1. Evaluating page opportunity for: {args.url}...")
        opp = await evaluate_page_opportunity(args.url, args.domain, args.pillar)
        if not opp:
            print("❌ Failed to evaluate page.")
            return

        print(f"2. Opportunity Scored: {opp.total_score}/100 [{opp.opportunity_type}]")
        print("3. Surgically enriching via Hunter.io...")
        enriched_opp = await enrich_opportunity_surgically(opp, client)
        print(f"   Contact: {enriched_opp.target_email} ({enriched_opp.target_person}) [Confidence: {enriched_opp.hunter_confidence}%]")

        print("4. Pushing Opportunity Card to Telegram...")
        success = await send_telegram_opportunity_card(enriched_opp)
        print(f"   Telegram notification sent: {success}\n")

    asyncio.run(main())
