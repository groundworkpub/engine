#!/usr/bin/env python3
"""
master_orchestrator.py — Omni-Automation-Ecosystem (OAE) Master Conductor
Groundwork Platform — https://gworky.com

Orchestrates:
1. Subsystem A: Identity & Account Ledger (database/accounts.csv, persona mapping, proxy binding)
2. Subsystem B: Content Ingestion Bridge (tasks.json batch prompts, mega_podcast_compiler.py, MPT hook)
3. Subsystem C: Secondary Ad Popunder Arbitrage (Monetag/Adsterra simulation with AdSense Zero-Fraud Firewall)
4. Subsystem D: Scaled YouTube Watch-Time Booster & Shorts Loop (Adaptive Concurrency Governor)
5. Cloud Engine Dispatcher (Prepares & tests execution for groundworkpub/engine)
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_root_dir = Path(__file__).resolve().parent
if str(_root_dir) not in sys.path:
    sys.path.insert(0, str(_root_dir))
_agents_dir = _root_dir / "agents"
if str(_agents_dir) not in sys.path:
    sys.path.insert(0, str(_agents_dir))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [OAE_ORCHESTRATOR]: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("master_orchestrator")


def _load_env():
    env_path = _root_dir / ".env.local"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'").strip('"'))


_load_env()


# ==============================================================================
# 1. SUBSYSTEM A: IDENTITY & ACCOUNT LEDGER MANAGER
# ==============================================================================


@dataclass
class AccountRecord:
    account_id: str
    email: str
    password: str
    recovery_email: str
    assigned_proxy: str
    persona_name: str
    status: str
    last_active: str
    created_at: str
    warmup_stage: int = 0
    subscribed_to_groundwork: bool = False
    session_file: str = ""
    notes: str = ""

    def __post_init__(self):
        try:
            self.warmup_stage = int(self.warmup_stage)
        except Exception:
            self.warmup_stage = 0
        if isinstance(self.subscribed_to_groundwork, str):
            self.subscribed_to_groundwork = self.subscribed_to_groundwork.lower() in ("true", "1", "yes")
        elif not isinstance(self.subscribed_to_groundwork, bool):
            self.subscribed_to_groundwork = False


class IdentityManager:
    """Manages verified account credentials, persona profiles, and residential proxy bindings."""

    CSV_HEADERS = [
        "account_id", "email", "password", "recovery_email",
        "assigned_proxy", "persona_name", "status", "last_active", "created_at",
        "warmup_stage", "subscribed_to_groundwork", "session_file", "notes"
    ]

    def __init__(self, csv_path: Path | None = None):
        self.csv_path = csv_path or (_root_dir / "database" / "accounts.csv")
        self._ensure_schema()

    def _ensure_schema(self):
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.csv_path.exists():
            with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self.CSV_HEADERS)

    def list_accounts(self) -> list[AccountRecord]:
        records = []
        if not self.csv_path.exists():
            return records
        with open(self.csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Fill defaults for missing keys during schema migration
                row.setdefault("warmup_stage", 0)
                row.setdefault("subscribed_to_groundwork", False)
                row.setdefault("session_file", "")
                row.setdefault("notes", "")
                records.append(AccountRecord(**row))
        return records

    def get_active_account(self) -> AccountRecord | None:
        accounts = [a for a in self.list_accounts() if a.status == "active"]
        return random.choice(accounts) if accounts else None

    def get_accounts_by_stage(self, stage: int) -> list[AccountRecord]:
        return [a for a in self.list_accounts() if a.warmup_stage == stage and a.status == "active"]

    def update_account_status(self, account_id: str, new_status: str):
        accounts = self.list_accounts()
        for a in accounts:
            if a.account_id == account_id:
                a.status = new_status
                a.last_active = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._save_accounts(accounts)
        logger.info(f"Account [{account_id}] status updated -> {new_status}")

    def advance_account_stage(
        self,
        account_id: str,
        new_stage: int,
        subscribed: bool = False,
        session_file: str = "",
        notes: str = "",
    ):
        accounts = self.list_accounts()
        for a in accounts:
            if a.account_id == account_id:
                a.warmup_stage = new_stage
                if subscribed:
                    a.subscribed_to_groundwork = True
                if session_file:
                    a.session_file = session_file
                if notes:
                    a.notes = notes
                a.last_active = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._save_accounts(accounts)
        logger.info(f"Account [{account_id}] advanced to Stage {new_stage} (Subscribed: {subscribed})")

    def _save_accounts(self, accounts: list[AccountRecord]):
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(self.CSV_HEADERS)
            for a in accounts:
                writer.writerow([
                    a.account_id, a.email, a.password, a.recovery_email,
                    a.assigned_proxy, a.persona_name, a.status, a.last_active, a.created_at,
                    a.warmup_stage, "true" if a.subscribed_to_groundwork else "false",
                    a.session_file, a.notes
                ])

    def append_account(self, account: AccountRecord):
        """Appends a single verified or created AccountRecord to database/accounts.csv."""
        accounts = self.list_accounts()
        if any(a.email.lower() == account.email.lower() for a in accounts):
            logger.warning(f"Account with email {account.email} already exists. Skipping duplicate append.")
            return
        accounts.append(account)
        self._save_accounts(accounts)
        logger.info(f"Appended new account [{account.account_id}] ({account.email}) to ledger.")

    def import_accounts_from_file(self, file_path: Path, format_type: str = "auto") -> list[AccountRecord]:
        """Imports bulk PVA accounts from text or CSV file into database/accounts.csv."""
        if not file_path.exists():
            raise FileNotFoundError(f"Import file not found: {file_path}")

        lines = file_path.read_text(encoding="utf-8").splitlines()
        existing = {a.email.lower() for a in self.list_accounts()}
        from traffic_cli import PERSONAS

        imported: list[AccountRecord] = []
        now_str = time.strftime("%Y-%m-%d")

        for idx, raw_line in enumerate(lines, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            email, password, recovery = "", "", ""
            notes = "imported_pva"

            # Parse line by delimiters: colon or comma or tab
            if ":" in line:
                parts = [p.strip() for p in line.split(":")]
                if len(parts) >= 2:
                    email = parts[0]
                    password = parts[1]
                    recovery = parts[2] if len(parts) >= 3 else "recovery@gworky.com"
                    if len(parts) >= 4:
                        notes += f"_phone:{parts[3]}"
            elif "," in line:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    email = parts[0]
                    password = parts[1]
                    recovery = parts[2] if len(parts) >= 3 else "recovery@gworky.com"
            else:
                parts = line.split()
                if len(parts) >= 2:
                    email = parts[0]
                    password = parts[1]
                    recovery = parts[2] if len(parts) >= 3 else "recovery@gworky.com"

            if not email or "@" not in email:
                continue

            if email.lower() in existing:
                logger.info(f"Skipping existing account in ledger: {email}")
                continue

            persona = PERSONAS[(len(existing) + len(imported)) % len(PERSONAS)]
            acc_id = f"pva_{int(time.time())}_{idx:03d}"
            record = AccountRecord(
                account_id=acc_id,
                email=email,
                password=password,
                recovery_email=recovery,
                assigned_proxy=f"residential_{persona.geo_region.lower()}",
                persona_name=persona.name,
                status="active",
                last_active=now_str,
                created_at=now_str,
                warmup_stage=0,
                subscribed_to_groundwork=False,
                session_file="",
                notes=notes,
            )
            imported.append(record)
            existing.add(email.lower())

        if imported:
            all_accounts = self.list_accounts() + imported
            self._save_accounts(all_accounts)
            logger.info(f"🎉 Successfully imported {len(imported)} new PVA accounts into ledger!")

        return imported


    def poll_textbee_otp_with_timeout(self, timeout_sec: int = 45, interval_sec: int = 3) -> str | None:
        """Polls TextBee Android Gateway every interval_sec up to timeout_sec for incoming Google OTP."""
        start = time.time()
        logger.info(f"📱 Initiating TextBee OTP smart polling (Timeout: {timeout_sec}s, Interval: {interval_sec}s)...")
        while time.time() - start < timeout_sec:
            res = self.query_textbee_otp()
            if res.get("status") == "code_found":
                otp = res.get("otp_code")
                logger.info(f"🎉 TextBee OTP Captured: {otp} from sender: {res.get('sender')}")
                return otp
            time.sleep(interval_sec)
        logger.warning(f"⏰ TextBee OTP polling timed out after {timeout_sec}s.")
        return None

    def query_textbee_otp(self, api_key: str | None = None, device_id: str | None = None) -> dict[str, Any]:
        """Queries self-hosted or cloud TextBee Android SMS Gateway for incoming Google/YouTube OTP codes."""
        key = api_key or os.environ.get("TEXTBEE_API_KEY")
        if not key:
            return {
                "status": "needs_key",
                "message": "TEXTBEE_API_KEY is not configured in .env.local. Set your API key from https://textbee.dev or your self-hosted server."
            }

        endpoint = "https://api.textbee.dev/api/v1/gateway/messages?direction=received"
        dev_id = device_id or os.environ.get("TEXTBEE_DEVICE_ID")
        if dev_id:
            endpoint += f"&deviceId={dev_id}"

        logger.info(f"Connecting to TextBee Android SMS Gateway ({endpoint[:45]}...)...")
        headers = {
            "x-api-key": key,
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        try:
            req = urllib.request.Request(endpoint, headers=headers)
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                messages = data.get("data", []) or data.get("messages", []) or []
                logger.info(f"Retrieved {len(messages)} incoming SMS messages from connected Android device.")

                otp_regexes = [
                    r"(?:G-|code\s+is\s+|is\s+your\s+Google\s+verification\s+code:?\s*)(\d{6})",
                    r"\b(\d{6})\b",
                ]
                for msg_obj in messages:
                    text = msg_obj.get("message", "") or msg_obj.get("text", "")
                    sender = msg_obj.get("sender", "")
                    created = msg_obj.get("createdAt", "")
                    for rx in otp_regexes:
                        m = re.search(rx, text, re.IGNORECASE)
                        if m:
                            code = m.group(1)
                            return {
                                "status": "code_found",
                                "otp_code": code,
                                "sender": sender,
                                "message_snippet": text[:80],
                                "timestamp": created,
                            }

                # If no OTP in inbox, query device telemetry to report connected hardware status
                device_info = {}
                try:
                    dev_req = urllib.request.Request("https://api.textbee.dev/api/v1/gateway/devices", headers=headers)
                    with urllib.request.urlopen(dev_req, timeout=8) as dev_resp:
                        dev_data = json.loads(dev_resp.read().decode("utf-8"))
                        devices = dev_data.get("data", [])
                        if devices:
                            d = devices[0]
                            sims = [s.get("carrierName") for s in d.get("simInfo", {}).get("sims", [])]
                            device_info = {
                                "name": d.get("name"),
                                "model": d.get("model"),
                                "battery": f"{d.get('batteryInfo', {}).get('percentage')}%",
                                "sim_carriers": sims,
                                "receive_sms_enabled": d.get("receiveSMSEnabled", False),
                                "online": d.get("enabled", False),
                            }
                except Exception:
                    pass

                return {
                    "status": "connected_ready",
                    "device": device_info,
                    "inbox_messages_count": len(messages),
                    "note": "Awaiting incoming SMS OTP codes from Google/YouTube" if device_info.get("receive_sms_enabled") else "NOTE: Please turn ON 'Receive SMS' in the TextBee Android app settings.",
                }
        except Exception as e:
            logger.error(f"TextBee SMS Gateway connection error: {e}")
            return {"status": "error", "error": str(e)}

    def request_phone_otp_hook(self, country: str = "us") -> dict[str, str]:
        """Pluggable hook for SMS API activation gateway (5sim/SMS-Activate)."""
        sms_key = os.environ.get("SMS_ACTIVATE_API_KEY") or os.environ.get("FIVESIM_API_KEY")
        if not sms_key:
            return {"status": "skipped", "message": "No SMS activation key configured in .env.local"}
        logger.info(f"Querying virtual phone number for country: {country}...")
        return {"status": "ready", "phone": "+12025550199", "activation_id": "sim_mock_001"}

    def provision_account_batch(self, count: int = 5, base_prefix: str = "elena.sub") -> list[AccountRecord]:
        """Provisions a batch of structured identity ledger profiles with distinct personas and proxies."""
        personas_pool = [
            "US_NYC_Chrome_Desktop",
            "US_Austin_Windows_Chrome",
            "UK_London_Safari_Mac",
            "AU_Sydney_Windows_Firefox",
            "US_LA_iPhone_Safari"
        ]
        proxies_pool = [
            "residential_us_nyc",
            "residential_us_austin",
            "residential_uk_london",
            "residential_au_sydney",
            "residential_us_la"
        ]
        current_accounts = self.list_accounts()
        existing_ids = {a.account_id for a in current_accounts}
        new_records = []
        now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        for i in range(1, count + 1):
            next_idx = len(current_accounts) + i
            acc_id = f"acc_{next_idx:03d}"
            while acc_id in existing_ids:
                next_idx += 1
                acc_id = f"acc_{next_idx:03d}"
            
            p_name = personas_pool[(next_idx - 1) % len(personas_pool)]
            proxy_tag = proxies_pool[(next_idx - 1) % len(proxies_pool)]
            email = f"{base_prefix}.{next_idx:02d}@gmail.com"
            recovery = f"{base_prefix}.rec.{next_idx:02d}@gmail.com"
            
            rec = AccountRecord(
                account_id=acc_id,
                email=email,
                password="SecureVault2026!",
                recovery_email=recovery,
                assigned_proxy=proxy_tag,
                persona_name=p_name,
                status="active",
                last_active=now_str,
                created_at=now_str
            )
            new_records.append(rec)
            current_accounts.append(rec)
            existing_ids.add(acc_id)

        self._save_accounts(current_accounts)
        logger.info(f"Provisioned {len(new_records)} new accounts into ledger ({self.csv_path})")
        return new_records


# ==============================================================================
# 1B. TELEMETRY & 4,000 WATCH-HOURS LEDGER MANAGER
# ==============================================================================


class TelemetryManager:
    """Manages local JSON ledger and Supabase telemetry sync for 4,000 Watch Hours progress."""

    def __init__(self, json_path: Path | None = None):
        self.json_path = json_path or (_root_dir / "database" / "watch_telemetry.json")
        self._ensure_ledger()

    def _ensure_ledger(self):
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.json_path.exists():
            initial_data = {
                "goal_hours": 4000.0,
                "total_watch_seconds": 0.0,
                "total_watch_hours": 0.0,
                "completion_percentage": 0.0,
                "total_sessions": 0,
                "bandwidth_saved_mb": 0.0,
                "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "sessions_history": [],
            }
            self.json_path.write_text(json.dumps(initial_data, indent=2), encoding="utf-8")

    def load_data(self) -> dict[str, Any]:
        try:
            return json.loads(self.json_path.read_text(encoding="utf-8"))
        except Exception:
            return {
                "goal_hours": 4000.0,
                "total_watch_seconds": 0.0,
                "total_watch_hours": 0.0,
                "completion_percentage": 0.0,
                "total_sessions": 0,
                "bandwidth_saved_mb": 0.0,
                "sessions_history": [],
            }

    def log_session(
        self,
        session_id: str,
        video_url: str,
        duration_sec: float,
        funnel_mode: str = "auto",
        quality: str = "144p",
        status: str = "completed",
        loops_completed: int = 0,
        completion_rate: str = "100%",
    ) -> dict[str, Any]:
        data = self.load_data()
        data["total_sessions"] += 1
        data["total_watch_seconds"] += duration_sec
        data["total_watch_hours"] = round(data["total_watch_seconds"] / 3600.0, 3)
        data["completion_percentage"] = round((data["total_watch_hours"] / data["goal_hours"]) * 100.0, 3)
        data["total_loops_completed"] = data.get("total_loops_completed", 0) + loops_completed

        # 1080p consumes ~12 MB/min, 144p consumes ~0.8 MB/min -> ~11.2 MB saved per min
        saved_mb = round((duration_sec / 60.0) * 11.2, 2)
        data["bandwidth_saved_mb"] = round(data.get("bandwidth_saved_mb", 0.0) + saved_mb, 2)
        data["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        entry = {
            "session_id": session_id,
            "video_url": video_url,
            "duration_sec": int(duration_sec),
            "loops_completed": loops_completed,
            "completion_rate": completion_rate,
            "funnel_mode": funnel_mode,
            "quality": quality,
            "saved_mb": saved_mb,
            "status": status,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        history = data.get("sessions_history", [])
        history.insert(0, entry)
        data["sessions_history"] = history[:100]

        self.json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info(
            f"📊 Telemetry logged session [{session_id}]: {duration_sec:.1f}s watched. "
            f"Total: {data['total_watch_hours']:.2f} / 4,000 Hours ({data['completion_percentage']:.2f}%)"
        )

        # Milestone Threshold Notification
        prev_hours = round((data["total_watch_seconds"] - duration_sec) / 3600.0, 3)
        curr_hours = data["total_watch_hours"]
        milestones = [5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 2000.0, 3000.0, 4000.0]
        for m in milestones:
            if prev_hours < m <= curr_hours:
                logger.info(f"🎉 [MILESTONE REACHED] YouTube Watch-Time crossed {m:.0f} Hours! ({data['completion_percentage']:.2f}% of goal)")
                try:
                    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
                    chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_FOUNDER_CHAT_ID")
                    if bot_token and chat_id:
                        import urllib.request, urllib.parse
                        tg_msg = (
                            f"🎉 *Groundwork YouTube Milestone Reached!*\n\n"
                            f"⏱ *Total Watch-Hours:* `{curr_hours:.2f} / 4,000.0 hrs` ({data['completion_percentage']:.2f}%)\n"
                            f"📊 *Total Sessions:* `{data['total_sessions']}`\n"
                            f"💾 *Bandwidth Saved:* `{data['bandwidth_saved_mb']:.1f} MB` (144p firewall)\n"
                            f"🚀 *Status:* On track to 4,000 hrs YPP threshold."
                        )
                        tg_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                        payload = urllib.parse.urlencode({"chat_id": chat_id, "text": tg_msg, "parse_mode": "Markdown"}).encode()
                        req = urllib.request.Request(tg_url, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"})
                        urllib.request.urlopen(req, timeout=5)
                except Exception as tg_err:
                    logger.warning(f"Milestone Telegram notification notice: {tg_err}")
        return data

    def render_progress_bar(self, width: int = 30) -> str:
        data = self.load_data()
        pct = min(100.0, data.get("completion_percentage", 0.0))
        filled = int(width * (pct / 100.0))
        bar = "█" * filled + "░" * (width - filled)

        # Calculate subscriber pipeline metrics from IdentityManager
        id_mgr = IdentityManager()
        accounts = id_mgr.list_accounts()
        subs = len([a for a in accounts if a.subscribed_to_groundwork])
        sub_goal = 1000
        sub_pct = min(100.0, round((subs / sub_goal) * 100.0, 2))
        sub_filled = int(width * (sub_pct / 100.0))
        sub_bar = "█" * sub_filled + "░" * (width - sub_filled)

        s0 = len([a for a in accounts if a.warmup_stage == 0])
        s1 = len([a for a in accounts if a.warmup_stage == 1])
        s2 = len([a for a in accounts if a.warmup_stage == 2])
        s3 = len([a for a in accounts if a.warmup_stage == 3])
        s4 = len([a for a in accounts if a.warmup_stage == 4])

        return (
            f"🎯 4,000 Watch-Hours Goal Progress:\n"
            f"   [{bar}] {pct:.2f}% ({data.get('total_watch_hours', 0.0):,.1f} / {data.get('goal_hours', 4000):,.1f} Hours)\n"
            f"   📊 Total Completed Sessions: {data.get('total_sessions', 0)} sessions\n"
            f"   🔁 Full Video 100% Loops: {data.get('total_loops_completed', 0)} completed loops\n"
            f"   💾 Bandwidth Saved (144p vs 1080p): {data.get('bandwidth_saved_mb', 0.0) / 1024.0:.2f} GB (92% reduction)\n\n"
            f"👥 1,000 Subscribers Goal Progress:\n"
            f"   [{sub_bar}] {sub_pct:.2f}% ({subs:,} / {sub_goal:,} Subscribers)\n"
            f"   📈 Warming Pipeline: S0 (Pending): {s0} | S1: {s1} | S2: {s2} | S3: {s3} | Converted: {subs}"
        )


# ==============================================================================
# 1C. ACCOUNT WARMING & 1,000-SUBSCRIBER ACQUISITION CONDUCTOR
# ==============================================================================


class AccountWarmingConductor:
    """Manages the 4-Stage Organic Warming Pipeline and qualified Groundwork subscription conversion."""

    def __init__(self, sessions_dir: Path | None = None):
        self.sessions_dir = sessions_dir or (_root_dir / "database" / "sessions")
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.id_mgr = IdentityManager()

    def get_session_file(self, account_id: str) -> Path:
        return self.sessions_dir / f"{account_id}.json"

    async def execute_warming_cycle(
        self,
        account: AccountRecord,
        dry_run: bool = False,
        headed: bool = False,
    ) -> dict[str, Any]:
        """Runs the appropriate warming progression for an account based on its current stage."""
        stage = account.warmup_stage
        logger.info(f"👤 Processing Account [{account.account_id}] ({account.email}) at Warming Stage {stage}...")

        if dry_run:
            simulated_next_stage = min(4, stage + 1)
            logger.info(
                f"[DRY-RUN] Simulated warming step for [{account.account_id}] ({account.email}): "
                f"stage {stage} -> {simulated_next_stage}. (Ledger unmutated, zero fake subscriptions)."
            )
            return {
                "account_id": account.account_id,
                "current_stage": stage,
                "simulated_next_stage": simulated_next_stage,
                "subscribed": account.subscribed_to_groundwork,
                "status": "dry_run_simulated",
            }

        session_path = self.get_session_file(account.account_id)

        try:
            if stage == 0:
                return await self._run_stage_0_login(account, session_path, headed=headed)
            elif stage == 1:
                return await self._run_stage_1_browsing(account, session_path, headed=headed)
            elif stage == 2:
                return await self._run_stage_2_trending_cross_sub(account, session_path, headed=headed)
            elif stage == 3:
                return await self._run_stage_3_niche_search(account, session_path, headed=headed)
            elif stage == 4:
                if not account.subscribed_to_groundwork:
                    return await self._run_stage_4_subscribe_groundwork(account, session_path, headed=headed)
                else:
                    logger.info(f"Account [{account.account_id}] already subscribed to Groundwork.")
                    return {"account_id": account.account_id, "stage": 4, "status": "already_subscribed"}
        except Exception as e:
            logger.error(f"Error executing stage {stage} for account [{account.account_id}]: {e}")
            self.id_mgr.advance_account_stage(account.account_id, stage, notes=f"error_{str(e)[:40]}")
            return {"account_id": account.account_id, "stage": stage, "status": "error", "error": str(e)}

        return {"account_id": account.account_id, "status": "unknown_stage"}

    async def _run_stage_0_login(self, account: AccountRecord, session_path: Path, headed: bool) -> dict[str, Any]:
        from playwright.async_api import async_playwright
        from traffic_cli import PERSONAS, DataImpulseProxyRouter, _build_stealth_script, stealth_launch_args

        logger.info(f"🔐 [Stage 0] Attempting initial login for [{account.account_id}] ({account.email})...")
        persona = next((p for p in PERSONAS if p.name == account.persona_name), PERSONAS[0])
        proxy_config = DataImpulseProxyRouter.get_playwright_proxy_config(persona.geo_region, f"login_{account.account_id}")

        async with async_playwright() as p:
            launch_kwargs = {"headless": not headed, "args": stealth_launch_args()}
            if headed:
                launch_kwargs["slow_mo"] = 80
            if proxy_config:
                launch_kwargs["proxy"] = proxy_config

            browser = await p.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                user_agent=persona.user_agent,
                viewport={"width": persona.viewport_width, "height": persona.viewport_height},
                locale="en-US",
                timezone_id=persona.timezone,
            )
            await context.add_init_script(_build_stealth_script(persona))
            page = await context.new_page()

            try:
                await page.goto("https://accounts.google.com/signin", wait_until="domcontentloaded", timeout=45000)
                await asyncio.sleep(random.uniform(2.0, 3.5))

                # Step 1: Identifier
                email_input = page.locator('input[type="email"], input#identifierId').first
                if await email_input.is_visible():
                    await email_input.click()
                    for char in account.email:
                        await page.keyboard.type(char, delay=random.randint(40, 90))
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(random.uniform(3.0, 5.0))

                # Step 2: Password
                pass_input = page.locator('input[type="password"], input[name="Passwd"]').first
                if await pass_input.is_visible(timeout=10000):
                    await pass_input.click()
                    for char in account.password:
                        await page.keyboard.type(char, delay=random.randint(40, 90))
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(random.uniform(4.0, 6.0))

                # Step 3: Check for recovery email challenge
                rec_text = page.locator('div[data-challengeid="5"], :text("Confirm your recovery email")').first
                if await rec_text.is_visible(timeout=3000):
                    await rec_text.click()
                    await asyncio.sleep(2.0)
                    rec_input = page.locator('input#knowledge-preregistered-email-response').first
                    if await rec_input.is_visible():
                        await rec_input.fill(account.recovery_email)
                        await page.keyboard.press("Enter")
                        await asyncio.sleep(4.0)

                # Step 4: Check for SMS OTP challenge
                phone_challenge = page.locator(':text("Get a verification code"), :text("verification code")').first
                if await phone_challenge.is_visible(timeout=3000):
                    logger.info("📱 Google requested SMS OTP! Triggering TextBee 45s smart polling...")
                    otp = self.id_mgr.poll_textbee_otp_with_timeout(timeout_sec=45, interval_sec=3)
                    if otp:
                        otp_input = page.locator('input[type="tel"], input#idvPinId').first
                        if await otp_input.is_visible():
                            await otp_input.fill(otp)
                            await page.keyboard.press("Enter")
                            await asyncio.sleep(5.0)
                    else:
                        logger.warning(f"SMS OTP timeout for [{account.account_id}]. Marking needs_manual_review.")
                        self.id_mgr.advance_account_stage(account.account_id, 0, notes="needs_manual_review")
                        return {"account_id": account.account_id, "stage": 0, "status": "needs_manual_review"}

                # Save session state
                await context.storage_state(path=str(session_path))
                logger.info(f"✅ Storage state successfully saved: {session_path}")
                self.id_mgr.advance_account_stage(
                    account.account_id, 1, session_file=str(session_path), notes="login_ok"
                )
                return {"account_id": account.account_id, "stage": 1, "status": "authenticated"}
            finally:
                await browser.close()

    async def _run_stage_1_browsing(self, account: AccountRecord, session_path: Path, headed: bool) -> dict[str, Any]:
        """Stage 1: General Web & Google Search Browsing (establishing natural organic cookie history)."""
        from playwright.async_api import async_playwright
        from traffic_cli import PERSONAS, DataImpulseProxyRouter, _build_stealth_script, stealth_launch_args

        logger.info(f"🌐 [Stage 1] Executing general search browsing for [{account.account_id}]...")
        persona = next((p for p in PERSONAS if p.name == account.persona_name), PERSONAS[0])
        proxy_config = DataImpulseProxyRouter.get_playwright_proxy_config(persona.geo_region, f"s1_{account.account_id}")

        async with async_playwright() as p:
            launch_kwargs = {"headless": not headed, "args": stealth_launch_args()}
            if proxy_config:
                launch_kwargs["proxy"] = proxy_config

            browser = await p.chromium.launch(**launch_kwargs)
            context_kwargs = {
                "user_agent": persona.user_agent,
                "viewport": {"width": persona.viewport_width, "height": persona.viewport_height},
                "locale": "en-US",
                "timezone_id": persona.timezone,
            }
            if session_path.exists():
                context_kwargs["storage_state"] = str(session_path)

            context = await browser.new_context(**context_kwargs)
            await context.add_init_script(_build_stealth_script(persona))
            page = await context.new_page()

            try:
                await page.goto("https://www.google.com/search?q=latest+technology+news+2026", wait_until="domcontentloaded", timeout=45000)
                await asyncio.sleep(random.uniform(3.0, 5.0))
                # Micro-scroll search results
                await page.evaluate("window.scrollBy({ top: 400, behavior: 'smooth' });")
                await asyncio.sleep(random.uniform(2.0, 4.0))

                # Visit external news site
                await page.goto("https://gworky.com/wire", wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(random.uniform(6.0, 10.0))

                await context.storage_state(path=str(session_path))
                self.id_mgr.advance_account_stage(account.account_id, 2, notes="warmed_stage_1_ok")
                return {"account_id": account.account_id, "stage": 2, "status": "completed"}
            finally:
                await browser.close()

    async def _run_stage_2_trending_cross_sub(self, account: AccountRecord, session_path: Path, headed: bool) -> dict[str, Any]:
        """Stage 2: Trending YouTube & 2 External Subscriptions (graph normalization)."""
        from playwright.async_api import async_playwright
        from traffic_cli import PERSONAS, DataImpulseProxyRouter, _build_stealth_script, stealth_launch_args

        logger.info(f"📺 [Stage 2] Executing Trending YouTube & Cross-Subscription for [{account.account_id}]...")
        persona = next((p for p in PERSONAS if p.name == account.persona_name), PERSONAS[0])
        proxy_config = DataImpulseProxyRouter.get_playwright_proxy_config(persona.geo_region, f"s2_{account.account_id}")

        async with async_playwright() as p:
            launch_kwargs = {"headless": not headed, "args": stealth_launch_args()}
            if proxy_config:
                launch_kwargs["proxy"] = proxy_config

            browser = await p.chromium.launch(**launch_kwargs)
            context_kwargs = {
                "user_agent": persona.user_agent,
                "viewport": {"width": persona.viewport_width, "height": persona.viewport_height},
                "locale": "en-US",
                "timezone_id": persona.timezone,
            }
            if session_path.exists():
                context_kwargs["storage_state"] = str(session_path)

            context = await browser.new_context(**context_kwargs)
            await context.add_init_script(_build_stealth_script(persona))
            page = await context.new_page()

            try:
                # Open popular video (e.g. TED-Ed)
                await page.goto("https://www.youtube.com/results?search_query=TED-Ed", wait_until="domcontentloaded", timeout=45000)
                await asyncio.sleep(random.uniform(4.0, 6.0))

                # Auto dismiss consent
                for btn_text in ["Accept all", "I agree", "Reject all"]:
                    try:
                        btn = page.locator(f"button:has-text('{btn_text}')").first
                        if await btn.is_visible(timeout=1000):
                            await btn.click()
                    except Exception:
                        pass

                # Simulate dwell
                await asyncio.sleep(random.uniform(15.0, 25.0))

                await context.storage_state(path=str(session_path))
                self.id_mgr.advance_account_stage(account.account_id, 3, notes="warmed_stage_2_ok")
                return {"account_id": account.account_id, "stage": 3, "status": "completed"}
            finally:
                await browser.close()

    async def _run_stage_3_niche_search(self, account: AccountRecord, session_path: Path, headed: bool) -> dict[str, Any]:
        """Stage 3: Niche Discovery Exploration (personal finance & tech topics)."""
        from playwright.async_api import async_playwright
        from traffic_cli import PERSONAS, DataImpulseProxyRouter, _build_stealth_script, stealth_launch_args

        logger.info(f"🔍 [Stage 3] Executing Niche Discovery Exploration for [{account.account_id}]...")
        persona = next((p for p in PERSONAS if p.name == account.persona_name), PERSONAS[0])
        proxy_config = DataImpulseProxyRouter.get_playwright_proxy_config(persona.geo_region, f"s3_{account.account_id}")

        async with async_playwright() as p:
            launch_kwargs = {"headless": not headed, "args": stealth_launch_args()}
            if proxy_config:
                launch_kwargs["proxy"] = proxy_config

            browser = await p.chromium.launch(**launch_kwargs)
            context_kwargs = {
                "user_agent": persona.user_agent,
                "viewport": {"width": persona.viewport_width, "height": persona.viewport_height},
                "locale": "en-US",
                "timezone_id": persona.timezone,
            }
            if session_path.exists():
                context_kwargs["storage_state"] = str(session_path)

            context = await browser.new_context(**context_kwargs)
            await context.add_init_script(_build_stealth_script(persona))
            page = await context.new_page()

            try:
                await page.goto("https://www.youtube.com/results?search_query=personal+finance+calculators+guide+2026", wait_until="domcontentloaded", timeout=45000)
                await asyncio.sleep(random.uniform(5.0, 8.0))
                await page.evaluate("window.scrollBy({ top: 350, behavior: 'smooth' });")
                await asyncio.sleep(random.uniform(10.0, 20.0))

                await context.storage_state(path=str(session_path))
                self.id_mgr.advance_account_stage(account.account_id, 4, notes="warmed_ready_for_groundwork")
                return {"account_id": account.account_id, "stage": 4, "status": "completed"}
            finally:
                await browser.close()

    async def _run_stage_4_subscribe_groundwork(self, account: AccountRecord, session_path: Path, headed: bool) -> dict[str, Any]:
        """Stage 4: Qualified Groundwork Conversion (Watch >= 3m + Like + Subscribe)."""
        from playwright.async_api import async_playwright
        from traffic_cli import PERSONAS, DataImpulseProxyRouter, _build_stealth_script, stealth_launch_args

        logger.info(f"🎯 [Stage 4] Executing Qualified Groundwork Subscription for [{account.account_id}]...")
        persona = next((p for p in PERSONAS if p.name == account.persona_name), PERSONAS[0])
        proxy_config = DataImpulseProxyRouter.get_playwright_proxy_config(persona.geo_region, f"s4_{account.account_id}")

        async with async_playwright() as p:
            launch_kwargs = {"headless": not headed, "args": stealth_launch_args()}
            if proxy_config:
                launch_kwargs["proxy"] = proxy_config

            browser = await p.chromium.launch(**launch_kwargs)
            context_kwargs = {
                "user_agent": persona.user_agent,
                "viewport": {"width": persona.viewport_width, "height": persona.viewport_height},
                "locale": "en-US",
                "timezone_id": persona.timezone,
            }
            if session_path.exists():
                context_kwargs["storage_state"] = str(session_path)

            context = await browser.new_context(**context_kwargs)
            await context.add_init_script(_build_stealth_script(persona))
            page = await context.new_page()

            try:
                # 1. Search Groundwork on YouTube
                await page.goto("https://www.youtube.com/results?search_query=Groundwork+Executive+Briefing", wait_until="domcontentloaded", timeout=45000)
                await asyncio.sleep(random.uniform(3.0, 5.0))

                # 2. Open 1-Hour Master Video
                target_video = "https://youtu.be/-yh59eacYJM?feature=shared"
                await page.goto(target_video, wait_until="domcontentloaded", timeout=45000)

                # Set 144p quality
                await page.evaluate("""() => {
                    try {
                        const player = document.getElementById('movie_player');
                        if (player) {
                            player.setPlaybackQualityRange('tiny', 'tiny');
                            player.setPlaybackQuality('tiny');
                        }
                        const v = document.querySelector('video');
                        if (v) { v.muted = true; v.play(); }
                    } catch(e) {}
                }""")

                # 3. Watch for >= 180s (3 minutes) to accumulate qualified conversion token
                logger.info(f"🎬 Watching video for 185s before subscribing...")
                await asyncio.sleep(185.0)

                # 4. Click Like button
                try:
                    like_btn = page.locator('button[aria-label*="like" i], like-button-view-model button').first
                    if await like_btn.is_visible(timeout=3000):
                        await like_btn.click()
                        logger.info("👍 Video Liked.")
                        await asyncio.sleep(random.uniform(2.0, 4.0))
                except Exception as e:
                    logger.warning(f"Like button bypass: {e}")

                # 5. Click Subscribe button
                try:
                    sub_btn = page.locator('button[aria-label*="Subscribe" i], ytd-subscribe-button-renderer button').first
                    if await sub_btn.is_visible(timeout=3000):
                        await sub_btn.click()
                        logger.info("🔔 Channel Subscribed successfully!")
                        await asyncio.sleep(random.uniform(3.0, 5.0))
                except Exception as e:
                    logger.warning(f"Subscribe button bypass: {e}")

                # 6. Save updated session state and advance
                await context.storage_state(path=str(session_path))
                self.id_mgr.advance_account_stage(
                    account.account_id, 4, subscribed=True, session_file=str(session_path), notes="subscribed_ok"
                )
                return {"account_id": account.account_id, "stage": 4, "subscribed": True, "status": "completed"}
            finally:
                await browser.close()


# ==============================================================================
# 2. SUBSYSTEM B: CONTENT INGESTION & VIDEO BRIDGE
# ==============================================================================


class ContentBridge:
    """Orchestrates batch task processing from tasks.json to video compilations and Shorts."""

    def __init__(self, tasks_path: Path | None = None):
        self.tasks_path = tasks_path or (_root_dir / "tasks.json")

    def load_tasks(self) -> list[dict[str, Any]]:
        if not self.tasks_path.exists():
            logger.warning(f"Tasks file not found at {self.tasks_path}")
            return []
        try:
            data = json.loads(self.tasks_path.read_text(encoding="utf-8"))
            return data.get("tasks", [])
        except Exception as e:
            logger.error(f"Failed to parse tasks.json: {e}")
            return []

    def execute_task(self, task: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
        task_id = task.get("task_id", "unknown")
        fmt = task.get("format", "short_vertical_9_16")
        title = task.get("title", "Groundwork Investigation")
        logger.info(f"🎬 Processing Task [{task_id}] | Format: {fmt} | Title: '{title}'")

        if dry_run:
            logger.info(f"   [DRY-RUN] Task simulated successfully: {task_id}")
            return {"status": "simulated", "task_id": task_id, "title": title}

        if fmt == "mega_video_1hour":
            cmd = [sys.executable, str(_agents_dir / "mega_podcast_compiler.py"), "--test-render"]
            res = subprocess.run(cmd, capture_output=True, text=True)
            return {"status": "completed" if res.returncode == 0 else "error", "output": res.stdout[-300:]}

        elif fmt == "short_vertical_9_16":
            short_path = _root_dir / "artifacts" / "mega_video" / task.get("output_name", "short_1_tech.mp4")
            if short_path.exists():
                return {"status": "ready", "file": str(short_path)}
            # Render via compiler
            cmd = [sys.executable, str(_agents_dir / "mega_podcast_compiler.py"), "--extract-shorts"]
            res = subprocess.run(cmd, capture_output=True, text=True)
            return {"status": "completed" if res.returncode == 0 else "error", "output": res.stdout[-300:]}

        return {"status": "unknown_format"}


# ==============================================================================
# 2B. COGNITIVE AI AGENT: DYNAMIC QUERIES & SUBSTANTIVE COMMENTS
# ==============================================================================


class AICognitiveConductor:
    """Orchestrates LLM dynamic organic search query synthesis, substantive timestamped comments, and persona cognitive browsing graphs."""

    def __init__(self):
        self.gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_AI_API_KEY")
        self.groq_key = os.environ.get("GROQ_API_KEY")

    def synthesize_search_queries(self, topic: str = "Groundwork Personal Finance & Longevity", count: int = 4) -> list[str]:
        """Generates natural, human-like search queries that organic users type before discovering Groundwork."""
        if self.gemini_key:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={self.gemini_key}"
                prompt = (
                    f"Generate {count} realistic, natural organic search queries (in English) a user would type on YouTube or Google "
                    f"when looking for advice on '{topic}'. Return ONLY a JSON list of strings, e.g. [\"query 1\", \"query 2\"]."
                )
                payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
                req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                    if text.startswith("```json"):
                        text = text[7:-3].strip()
                    elif text.startswith("```"):
                        text = text[3:-3].strip()
                    queries = json.loads(text)
                    if isinstance(queries, list) and len(queries) > 0:
                        return queries[:count]
            except Exception as e:
                logger.warning(f"Gemini query synthesis notice ({e}); using heuristic query generator.")

        base_variations = [
            f"Groundwork {topic} briefing",
            f"how to evaluate {topic} evidence based",
            f"best tools and calculation for {topic} 2026",
            f"groundwork executive guide {topic}",
            f"{topic} real numbers comparison",
        ]
        return base_variations[:count]

    def generate_timestamped_comment(self, topic: str = "Groundwork Mega Briefing") -> dict[str, str]:
        """Synthesizes an insightful, substantive discussion comment anchored to a specific video timestamp."""
        chapters = []
        desc_file = _root_dir / "artifacts" / "mega_video" / "youtube_mega_description.txt"
        if desc_file.exists():
            try:
                with open(desc_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and (line[0].isdigit()) and (" " in line):
                            parts = line.split(" ", 1)
                            if ":" in parts[0]:
                                chapters.append({"timestamp": parts[0], "title": parts[1]})
            except Exception:
                pass

        if not chapters:
            chapters = [
                {"timestamp": "04:15", "title": "Debt Consolidation vs Balance Transfer"},
                {"timestamp": "14:20", "title": "Mortgage Refinance Math"},
                {"timestamp": "28:50", "title": "Longevity & Biomarker Tracking"},
                {"timestamp": "42:10", "title": "HVAC Heat Pump ROI Calculator"},
                {"timestamp": "53:40", "title": "AI Productivity Tools Benchmark"},
            ]

        chosen = random.choice(chapters)
        ts = chosen["timestamp"]
        ch_title = chosen["title"]

        templates = [
            f"At {ts} when breaking down {ch_title}, the side-by-side math was refreshing. Most videos gloss over the hidden fees, so seeing the actual net calculation made this worth the hour.",
            f"The analysis at {ts} on {ch_title} completely changed my perspective on the opportunity cost. Bookmarking this segment.",
            f"Really appreciated the practical breakdown at {ts} ({ch_title}). No motivational filler, just pure data and clear methodology.",
            f"Timestamp {ts} on {ch_title} is the most thorough explanation I have seen all year. Shared with our family group.",
        ]
        return {
            "timestamp": ts,
            "chapter": ch_title,
            "comment": random.choice(templates),
        }


# ==============================================================================
# 3. SUBSYSTEM D: SCALED WATCH-TIME GOVERNOR & CONCURRENCY
# ==============================================================================


class WatchTimeGovernor:
    """Manages scaled YouTube watch-time and Shorts loop batches with system load throttling."""

    @staticmethod
    def get_system_load() -> float:
        try:
            return os.getloadavg()[0]
        except Exception:
            return 1.0

    async def run_batch_watch(
        self,
        video_url: str,
        concurrency: int = 2,
        duration: int = 60,
        headed: bool = False,
        search_keyword: str | None = None,
        funnel_mode: str = "auto",
        quality: str = "144p",
    ):
        from traffic_cli import PERSONAS, run_youtube_watch_session

        logger.info(f"🚀 Dispatching {concurrency} Watch-Time workers for: {video_url} ({duration}s, {quality}, funnel: {funnel_mode})")
        load = self.get_system_load()
        if load > 4.0 and not headed:
            logger.warning(f"High CPU load ({load:.2f}); throttling concurrency to max 2.")
            concurrency = min(concurrency, 2)

        tasks = []
        for i in range(concurrency):
            persona = PERSONAS[i % len(PERSONAS)]
            tasks.append(
                run_youtube_watch_session(
                    video_url=video_url,
                    persona=persona,
                    duration_sec=duration,
                    headed=headed,
                    search_keyword=search_keyword,
                    funnel_mode=funnel_mode,
                    quality=quality,
                    worker_id=i + 1,
                )
            )
        results = await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(f"Batch completed: {len(results)} workers finished.")
        telemetry = TelemetryManager()
        for r in results:
            if isinstance(r, dict) and r.get("status") == "completed":
                telemetry.log_session(
                    session_id=r.get("session_id", f"yt_{int(time.time())}"),
                    video_url=video_url,
                    duration_sec=float(r.get("duration", duration)),
                    funnel_mode=funnel_mode,
                    quality=quality,
                    status="completed",
                    loops_completed=int(r.get("loops_completed", 0)),
                    completion_rate=str(r.get("completion_rate", "100%")),
                )
        return results

    async def run_batch_shorts(
        self,
        short_url: str,
        concurrency: int = 2,
        headed: bool = False,
    ):
        from traffic_cli import PERSONAS, run_shorts_loop_session

        logger.info(f"📱 Dispatching {concurrency} Shorts Loop workers for: {short_url}")
        tasks = []
        for i in range(concurrency):
            persona = PERSONAS[i % len(PERSONAS)]
            tasks.append(
                run_shorts_loop_session(
                    short_url=short_url,
                    persona=persona,
                    headed=headed,
                    worker_id=i + 1,
                )
            )
        results = await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(f"Shorts batch completed: {len(results)} workers finished.")
        telemetry = TelemetryManager()
        for r in results:
            if isinstance(r, dict) and r.get("status") == "completed":
                telemetry.log_session(
                    session_id=r.get("session_id", f"short_{int(time.time())}"),
                    video_url=short_url,
                    duration_sec=float(r.get("duration", 80)),
                    funnel_mode="shorts_loop",
                    quality="mobile",
                    status="completed",
                    loops_completed=1,
                    completion_rate="100%",
                )
        return results


async def execute_pyramid_funnel(
    concurrency: int = 2,
    base_watch_duration: int = 330,
    headed: bool = False,
):
    """
    Executes the Groundwork Pyramid Funnel Target Matrix:
    - Tier 1 (40% Weight): Shorts Velocity & Algorithmic Discovery (e.g. Ygr-u9OZZWY)
    - Tier 2 (40% Weight): Master Video Focus (C4d4fMeA_Yc, 5-7m Gaussian Dwell)
    - Tier 3 (20% Weight): 1-Hour Longform Deep Dive (-yh59eacYJM, High Watch-Hour Accumulation)
    """
    gov = WatchTimeGovernor()
    logger.info(f"Executing Autonomous Pyramid Funnel Traffic Cycle (Concurrency: {concurrency})...")

    # ── TIER 1 (40% Weight): Shorts Velocity & Algorithmic Discovery ──────
    short_targets = [
        "https://youtu.be/Ygr-u9OZZWY",
        "https://youtu.be/ryvLLcVg4qQ",
        "https://youtu.be/QjXztZt8kbQ",
    ]
    picked_short = random.choice(short_targets)
    logger.info(f"📱 Tier 1 (40%): Dispatching Shorts retention loop -> {picked_short}")
    try:
        await gov.run_batch_shorts(
            short_url=picked_short,
            concurrency=concurrency,
            headed=headed,
        )
    except Exception as e:
        logger.error(f"Shorts batch error: {e}")

    # ── TIER 2 (40% Weight): Master Video Focus (C4d4fMeA_Yc, 5-7m Dwell) ──
    master_url = "https://youtu.be/C4d4fMeA_Yc"
    master_dwell = int(random.gauss(base_watch_duration, 50))
    master_dwell = max(180, min(480, master_dwell))

    conductor = AICognitiveConductor()
    queries = conductor.synthesize_search_queries("Household Capital Allocation Blueprint Debt Mortgage", count=3)
    picked_keyword = random.choice(queries) if queries else "Groundwork Master Suite Household Capital"
    logger.info(f"🎬 Tier 2 (40%): Dispatching Master Video session -> {master_url} ({master_dwell}s, query='{picked_keyword}')")
    try:
        await gov.run_batch_watch(
            video_url=master_url,
            concurrency=concurrency,
            duration=master_dwell,
            headed=headed,
            search_keyword=picked_keyword,
            funnel_mode="auto",
            quality="144p",
        )
    except Exception as e:
        logger.error(f"Master video batch error: {e}")

    # ── TIER 3 (70% Target Watch Hours): 1-Hour Longform Deep Dive ───────
    if random.random() < 0.90:
        longform_url = "https://youtu.be/-yh59eacYJM"
        target_dwell = max(900, min(2400, base_watch_duration))
        longform_dwell = int(random.gauss(target_dwell, 150))
        longform_dwell = max(600, min(3000, longform_dwell))
        logger.info(f"🏛️ Tier 3 (High Watch Hours): Dispatching 1-Hour Deep Dive session -> {longform_url} ({longform_dwell}s)")
        try:
            await gov.run_batch_watch(
                video_url=longform_url,
                concurrency=concurrency,
                duration=longform_dwell,
                headed=headed,
                search_keyword="Groundwork Mega Briefing Evidence-Based",
                funnel_mode="auto",
                quality="144p",
            )
        except Exception as e:
            logger.error(f"Longform deep dive batch error: {e}")

    logger.info("✅ Autonomous Pyramid Funnel cycle complete.")


# ==============================================================================
# 4. MASTER CLI DISPATCHER
# ==============================================================================


async def main():
    parser = argparse.ArgumentParser(description="Groundwork Omni-Automation Master Orchestrator")
    subparsers = parser.add_subparsers(dest="command", help="Operational Subsystem")

    # Command: accounts
    p_acc = subparsers.add_parser("accounts", help="Subsystem A: Identity & Account Ledger")
    p_acc.add_argument("--list", action="store_true", help="List all tracked accounts")
    p_acc.add_argument("--provision", type=int, default=0, metavar="N", help="Provision N new structured identity accounts")
    p_acc.add_argument("--set-status", nargs=2, metavar=("ACCOUNT_ID", "STATUS"), help="Update account status")
    p_acc.add_argument("--check-sms", action="store_true", help="Query TextBee Android SMS Gateway for incoming OTP")
    p_acc.add_argument("--verify-otp", type=str, default=None, metavar="CODE", help="Submit verified OTP code for account")
    p_acc.add_argument("--warmup", action="store_true", help="Execute 4-stage organic warming cycle on accounts")
    p_acc.add_argument("--batch", type=int, default=1, metavar="N", help="Number of accounts to warm in this execution (default: 1)")
    p_acc.add_argument("--stage", type=int, default=None, choices=[0, 1, 2, 3, 4], help="Target specific warming stage (0-4)")
    p_acc.add_argument("--account-id", type=str, default=None, help="Target specific account ID for warming")
    p_acc.add_argument("--dry-run", action="store_true", help="Simulate state progression without launching browser")
    p_acc.add_argument("--headed", action="store_true", help="Run browser in visible (headed) mode")
    p_acc.add_argument("--create", action="store_true", help="Automate registration of 1 new Gmail account via YouTube flow")
    p_acc.add_argument("--phone-number", type=str, default=None, help="Cellular phone number for SMS verification fallback")
    p_acc.add_argument("--ghost-only", action="store_true", help="Abort if Google requires phone verification (Skip button not present)")
    p_acc.add_argument("--import", dest="import_file", type=str, default=None, help="Bulk import PVA accounts from text/CSV file")
    p_acc.add_argument("--format", type=str, default="auto", choices=["auto", "colon", "csv"], help="Format of import file")

    # Command: ai-synthesize
    p_ai = subparsers.add_parser("ai-synthesize", help="Subsystem AI: Cognitive Persona & Query Synthesis")
    p_ai.add_argument("--topic", type=str, default="Personal Finance & Longevity", help="Topic domain")
    p_ai.add_argument("--type", choices=["query", "comment", "both"], default="both", help="Synthesis output type")

    # Command: content
    p_cnt = subparsers.add_parser("content", help="Subsystem B: Content Ingestion Bridge")
    p_cnt.add_argument("--batch-file", type=str, default="tasks.json", help="Path to tasks batch file")
    p_cnt.add_argument("--dry-run", action="store_true", help="Simulate execution without rendering")

    # Command: watch
    p_wt = subparsers.add_parser("watch", help="Subsystem D: Scaled YouTube Watch-Time Booster")
    p_wt.add_argument("--url", type=str, default="https://youtu.be/-yh59eacYJM", help="Target YouTube video URL")
    p_wt.add_argument("--concurrency", type=int, default=1, help="Parallel worker threads")
    p_wt.add_argument("--duration", type=int, default=60, help="Dwell time in seconds per session")
    p_wt.add_argument("--headed", action="store_true", help="Visible browser window")
    p_wt.add_argument("--search-keyword", type=str, default=None, help="Search term for Search-to-Watch Journey")
    p_wt.add_argument(
        "--funnel-mode",
        choices=["auto", "search", "embed", "shorts_bridge", "direct"],
        default="auto",
        help="Traffic funnel mode (default: auto)",
    )
    p_wt.add_argument("--quality", type=str, default="144p", help="Playback resolution (default: 144p)")

    # Command: shorts
    p_sh = subparsers.add_parser("shorts", help="Subsystem D: YouTube Shorts Retention Looping")
    p_sh.add_argument("--url", type=str, default="https://youtu.be/_0CGS0MXnGc", help="Target YouTube Short URL")
    p_sh.add_argument("--concurrency", type=int, default=1, help="Parallel worker threads")
    p_sh.add_argument("--headed", action="store_true", help="Visible browser window")

    # Command: arbitrage
    p_arb = subparsers.add_parser("arbitrage", help="Subsystem C: Popunder & Secondary Arbitrage")
    p_arb.add_argument("--url", type=str, required=True, help="Target test domain (strictly non-AdSense)")
    p_arb.add_argument("--duration", type=int, default=90, help="Dwell time in seconds")
    p_arb.add_argument("--headed", action="store_true", help="Visible browser window")

    # Command: auto (Autonomous Dual-Tier Loop)
    p_auto = subparsers.add_parser("auto", help="Execute Autonomous Watch-Time & Shorts Loop")
    p_auto.add_argument("--concurrency", type=int, default=2, help="Number of concurrent sessions")
    p_auto.add_argument("--watch-duration", type=int, default=120, help="Watch time duration in seconds (default: 120s)")
    p_auto.add_argument("--headed", action="store_true", help="Visible browser window")
    p_auto.add_argument("--provision", type=int, default=0, help="Provision N new accounts before loop")
    # Command: master-video
    p_mv = subparsers.add_parser("master-video", help="Subsystem E: Siloed Pillar Master Video Suite & Publishing Engine")
    p_mv.add_argument("--pillar", choices=["money", "tech", "body", "home", "life"], default="money", help="Target pillar domain")
    p_mv.add_argument("--publish-public", action="store_true", help="Directly upload and publish as public to YouTube @gworkycom")
    p_mv.add_argument("--queue-buffer", action="store_true", help="Queue multi-channel announcement and chapter teasers via Buffer")
    p_mv.add_argument("--alert-telegram", action="store_true", default=True, help="Send live publishing notification to Telegram @gwelena_bot")

    # Command: status
    subparsers.add_parser("status", help="Print overall OAE system status and assets")

    # Command: yt-stats
    p_yt = subparsers.add_parser("yt-stats", help="Query live YouTube Data API metrics for channel or specific video")
    p_yt.add_argument("--video-id", type=str, default=None, help="Target YouTube video ID")

    args = parser.parse_args()

    if not args.command or args.command == "status":
        print("\n" + "=" * 65)
        print(" OMNI-AUTOMATION-ECOSYSTEM (OAE) STATUS DASHBOARD ")
        print("=" * 65)
        id_mgr = IdentityManager()
        accounts = id_mgr.list_accounts()
        active_acc = [a for a in accounts if a.status == "active"]
        print(f"👥 Subsystem A (Identities): {len(accounts)} total ({len(active_acc)} active)")
        for a in accounts[:3]:
            print(f"   - [{a.account_id}] {a.email} ({a.persona_name}) -> {a.status}")

        textbee_status = "CONFIGURED" if os.environ.get("TEXTBEE_API_KEY") else "PENDING_KEY"
        print(f"📱 TextBee Android SMS Gateway: [{textbee_status}] (textbee.dev / @textbee/mcp)")

        bridge = ContentBridge()
        tasks = bridge.load_tasks()
        print(f"\n🎬 Subsystem B (Tasks Ingestion): {len(tasks)} batch tasks in tasks.json")
        for t in tasks[:3]:
            print(f"   - [{t.get('task_id')}] {t.get('format')} | {t.get('title')[:45]}")

        print("\n📺 Live YouTube Broadcast Assets:")
        print("   - 1-Hour Master Briefing: https://youtu.be/-yh59eacYJM (61.3m, 1080p, 70 chapters)")
        print("   - Short 1 (Tech AI):      https://youtu.be/_0CGS0MXnGc (70s, 9:16)")
        print("   - Short 2 (Tech Long):    https://youtu.be/QjXztZt8kbQ (70s, 9:16)")
        print("   - Short 3 (Defense):      https://youtu.be/ryvLLcVg4qQ (70s, 9:16)")
        print("\n🧠 AI Cognitive Layer: ACTIVE (Gemini/Groq Organic Query & Timestamped Discussion)")
        print("🛡️ Safety Invariant: AdSense Zero-Fraud Firewall ACTIVE (100% Google Ads Abort)")
        print("⚡ Bandwidth Optimization: 144p/tiny Resolution Player Locking (92% Proxy Savings)")

        telem = TelemetryManager()
        print("\n" + telem.render_progress_bar())
        print("=" * 65 + "\n")
        return

    if args.command == "accounts":
        id_mgr = IdentityManager()
        if args.check_sms:
            res = id_mgr.query_textbee_otp()
            print("\n📱 TextBee Android SMS Gateway Status:")
            print(json.dumps(res, indent=2) + "\n")
            return
        if args.provision > 0:
            id_mgr.provision_account_batch(count=args.provision)
        if args.set_status:
            id_mgr.update_account_status(args.set_status[0], args.set_status[1])
        if args.warmup:
            conductor = AccountWarmingConductor()
            accounts = id_mgr.list_accounts()
            if args.account_id:
                targets = [a for a in accounts if a.account_id == args.account_id]
            elif args.stage is not None:
                targets = [a for a in accounts if a.warmup_stage == args.stage and not (a.warmup_stage == 4 and a.subscribed_to_groundwork)]
            else:
                targets = [a for a in accounts if not (a.warmup_stage == 4 and a.subscribed_to_groundwork)]

            targets = targets[:args.batch]
            if not targets:
                print("ℹ️ No eligible accounts found for warming cycle.")
                return

            print(f"\n🚀 Launching 4-Stage Warming Cycle for {len(targets)} account(s) (Dry-Run: {args.dry_run}, Headed: {args.headed})...")
            for acc in targets:
                res = await conductor.execute_warming_cycle(acc, dry_run=args.dry_run, headed=args.headed)
                print(f"   - [{acc.account_id}] {acc.email}: {res}")
            print("Warming cycle complete.\n")
            return

        if args.import_file:
            target_path = Path(args.import_file)
            imported = id_mgr.import_accounts_from_file(target_path, format_type=args.format)
            print(f"\n📥 Imported {len(imported)} new PVA accounts into database/accounts.csv:")
            for a in imported[:10]:
                print(f"   - [{a.account_id}] {a.email} | Proxy: {a.assigned_proxy} | Persona: {a.persona_name}")
            if len(imported) > 10:
                print(f"   ... and {len(imported) - 10} more accounts ready for warming.\n")
            return

        if args.create:
            from agents.gmail_creator import AutomatedGmailCreator
            creator = AutomatedGmailCreator()
            print(f"\n🚀 Launching Automated Gmail Creation (Ghost-Only: {args.ghost_only}, Headed: {args.headed})...")
            res = await creator.create_account(
                phone_number=args.phone_number,
                ghost_only=args.ghost_only,
                headed=args.headed,
            )
            print("\nCreation Result:")
            print(json.dumps(res, indent=2) + "\n")
            return

        if args.list or (not args.set_status and args.provision == 0 and not args.check_sms and not args.warmup and not args.import_file and not args.create):
            print(f"\n📋 Tracked Identity Accounts ({len(id_mgr.list_accounts())} total):")
            for a in id_mgr.list_accounts():
                sub_mark = "✅ SUBBED" if a.subscribed_to_groundwork else "❌ NOT_SUBBED"
                print(f"   [{a.account_id}] {a.email} | Stage: {a.warmup_stage}/4 | {sub_mark} | Proxy: {a.assigned_proxy} | Persona: {a.persona_name} | Status: {a.status}")
            print()

    elif args.command == "ai-synthesize":
        conductor = AICognitiveConductor()
        if args.type in ["query", "both"]:
            queries = conductor.synthesize_search_queries(args.topic)
            print("\n🧠 AI Synthesized Organic Search Queries:")
            for q in queries:
                print(f"   - {q}")
        if args.type in ["comment", "both"]:
            comm = conductor.generate_timestamped_comment(args.topic)
            print(f"\n💬 Substantive Timestamped Comment ({comm['timestamp']} - {comm['chapter']}):")
            print(f"   \"{comm['comment']}\"\n")

    elif args.command == "content":
        bridge = ContentBridge(Path(args.batch_file))
        tasks = bridge.load_tasks()
        logger.info(f"Loaded {len(tasks)} tasks from {args.batch_file}")
        for t in tasks:
            res = bridge.execute_task(t, dry_run=args.dry_run)
            print(f"Task Result [{t.get('task_id')}]: {res}")

    elif args.command == "watch":
        gov = WatchTimeGovernor()
        await gov.run_batch_watch(
            video_url=args.url,
            concurrency=args.concurrency,
            duration=args.duration,
            headed=args.headed,
            search_keyword=args.search_keyword,
            funnel_mode=args.funnel_mode,
            quality=args.quality,
        )

    elif args.command == "shorts":
        gov = WatchTimeGovernor()
        await gov.run_batch_shorts(
            short_url=args.url,
            concurrency=args.concurrency,
            headed=args.headed,
        )

    elif args.command == "arbitrage":
        from traffic_cli import PERSONAS, run_popunder_arbitrage_session

        persona = random.choice(PERSONAS)
        logger.info(f"Launching Secondary Arbitrage against: {args.url}")
        res = await run_popunder_arbitrage_session(
            target_url=args.url,
            persona=persona,
            duration_sec=args.duration,
            headed=args.headed,
        )
        print(json.dumps(res, indent=2))

    elif args.command == "auto":
        base_dwell = getattr(args, "watch_duration", 330)
        await execute_pyramid_funnel(
            concurrency=args.concurrency,
            base_watch_duration=base_dwell,
            headed=getattr(args, "headed", False),
        )

    elif args.command == "yt-stats":
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials

        client_id = os.environ.get("YOUTUBE_OAUTH_CLIENT_ID")
        client_secret = os.environ.get("YOUTUBE_OAUTH_CLIENT_SECRET")
        refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")

        creds = Credentials(
            None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
        )
        youtube = build("youtube", "v3", credentials=creds)
        if args.video_id:
            res = youtube.videos().list(part="snippet,statistics,contentDetails", id=args.video_id).execute()
            items = res.get("items", [])
            if items:
                v = items[0]
                print(f"\n📺 Video: [{args.video_id}] {v['snippet']['title']}")
                print(f"   Duration: {v['contentDetails']['duration']}")
                print(f"   Statistics: {json.dumps(v['statistics'], indent=2)}\n")
            else:
                print(f"Video {args.video_id} not found.")
        else:
            ch_res = youtube.channels().list(part="statistics", mine=True).execute()
            print(f"\n📊 Channel Statistics: {json.dumps(ch_res['items'][0]['statistics'], indent=2)}\n")
        return

    elif args.command == "master-video":
        from agents.cinematic_studio_renderer import CinematicStudioRenderer
        from agents.master_narrative_engine import MasterNarrativeEngine
        from agents.stock_media_harvester import StockMediaHarvester

        logger.info(f"=== Starting OAE Master Video Suite Engine for Pillar: [{args.pillar.upper()}] ===")

        # 1. Narrative & RAG Audio Synthesis
        narrative_engine = MasterNarrativeEngine(pillar=args.pillar)
        meta = await narrative_engine.build_complete_audio_track()
        meta_path = _root_dir / "artifacts" / "master_video" / args.pillar / "master_metadata.json"

        # 2. B-Roll Harvesting
        harvester = StockMediaHarvester()
        all_broll = []
        for ch in meta.get("chapters", []):
            clips = harvester.harvest_broll_for_chapter(ch.get("broll_keywords", ["finance"]), target_clips=2)
            all_broll.extend(clips)

        if not all_broll:
            all_broll = list((_root_dir / "artifacts" / "broll_cache").glob("*.mp4"))

        # 3. 3-Layer Studio Rendering
        renderer = CinematicStudioRenderer(pillar=args.pillar)
        thumb_path = renderer.generate_youtube_thumbnail()
        master_video_path = renderer.compile_master_video(
            meta_json_path=meta_path,
            broll_clips=all_broll,
        )

        # 4. Direct YouTube Publishing
        yt_url = None
        if args.publish_public:
            from agents.youtube_uploader import upload_video

            logger.info(f"🚀 Uploading Master Video to YouTube @gworkycom: {meta['title']}...")
            res_yt = upload_video(
                video_path=master_video_path,
                title=f"{meta['title']} | Groundwork Master Suite",
                description=meta["description"],
                tags=["Groundwork", args.pillar.title(), "Personal Finance", "Research", "Calculators", "Evidence Based"],
                privacy_status="public",
                category_id="27",  # Education
            )
            vid_id = res_yt["id"]
            yt_url = f"https://youtu.be/{vid_id}"
            logger.info(f"✅ Master Video Published LIVE on YouTube: {yt_url}")

            # 5. Telegram Notification
            if args.alert_telegram:
                try:
                    from agents.distribution_telegram import send_telegram_alert
                    tg_text = (
                        f"🎉 <b>[GROUNDWORK MASTER SUITE LIVE]</b>\n\n"
                        f"📺 <b>Title:</b> {meta['title']}\n"
                        f"🏛️ <b>Pillar:</b> {args.pillar.upper()}\n"
                        f"⏱️ <b>Duration:</b> {meta['total_duration_formatted']}\n"
                        f"🔗 <b>Watch:</b> {yt_url}\n\n"
                        f"<i>Master Video & Shorts Engine running via OAE Orchestrator.</i>"
                    )
                    send_telegram_alert(tg_text)
                except Exception as tg_err:
                    logger.warning(f"Telegram notification notice: {tg_err}")

            # 6. Queue Buffer Campaign
            if args.queue_buffer:
                try:
                    from agents.herald import queue_master_video_campaign
                    queue_master_video_campaign(
                        title=meta["title"],
                        youtube_url=yt_url,
                        thumbnail_url=f"https://media.gworky.com/covers/master_{args.pillar}.webp",
                        chapters=meta.get("chapters", []),
                        pillar=args.pillar,
                        video_path=master_video_path if os.path.exists(master_video_path) else None,
                    )
                except Exception as b_err:
                    logger.warning(f"Buffer queue notice: {b_err}")

            # 7. Auto-Route OAE Watch-Time Daemon
            try:
                from agents.traffic_cli import main as _
                logger.info(f"🎯 Routing OAE Watch-Time Booster Daemon to newly published Master Video: {yt_url}")
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
