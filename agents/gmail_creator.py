#!/usr/bin/env python3
"""
agents/gmail_creator.py — Automated Gmail Creator Agent
Groundwork Platform — https://gworky.com

Automates Gmail account creation leveraging:
1. YouTube Signup Flow (https://accounts.google.com/signup?service=youtube).
2. Poltergeist PRNG stealth script (Canvas/WebGL/Audio/WebRTC masking).
3. Ghost Mode: Auto-detects and triggers "Skip" button on phone verification.
4. SMS Fallback: Seamlessly integrates with TextBee Android SMS Gateway when phone is required.
5. Session Storage: Exports authenticated cookies to database/sessions/{account_id}.json.
6. Ledger Sync: Automatically appends newly created accounts to database/accounts.csv.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import string
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_root_dir = Path(__file__).resolve().parent.parent
if str(_root_dir) not in sys.path:
    sys.path.insert(0, str(_root_dir))

from agents.browser_stealth import build_stealth_script, stealth_launch_args
from agents.egress_dataimpulse import DataImpulseProxyRouter

logger = logging.getLogger("gmail_creator")

FIRST_NAMES = [
    "Liam", "Noah", "Oliver", "James", "Elijah", "William", "Henry", "Lucas",
    "Emma", "Olivia", "Amelia", "Sophia", "Charlotte", "Ava", "Mia", "Harper",
    "Alexander", "Ethan", "Benjamin", "Arthur", "Daniel", "Matthew", "Grace", "Chloe"
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Davis", "Wilson",
    "Anderson", "Taylor", "Thomas", "Moore", "Jackson", "Martin", "Lee", "Clark",
    "Lewis", "Walker", "Hall", "Allen", "Young", "Wright", "King", "Scott"
]


@dataclass
class GeneratedProfile:
    first_name: str
    last_name: str
    username: str
    email: str
    password: str
    birth_day: int
    birth_month: int
    birth_year: int
    gender: int  # 1: Female, 2: Male, 3: Rather not say


def generate_random_profile() -> GeneratedProfile:
    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)
    suffix = f"{random.randint(10, 99)}{random.choice(string.ascii_lowercase)}{random.randint(100, 999)}"
    username = f"{first.lower()}{last.lower()}{suffix}"
    
    # Generate strong password meeting Google security requirements
    letters = "".join(random.choices(string.ascii_letters, k=8))
    digits = "".join(random.choices(string.digits, k=3))
    symbols = "".join(random.choices("!@#$%", k=2))
    pwd_chars = list(letters + digits + symbols)
    random.shuffle(pwd_chars)
    password = "".join(pwd_chars)

    return GeneratedProfile(
        first_name=first,
        last_name=last,
        username=username,
        email=f"{username}@gmail.com",
        password=password,
        birth_day=random.randint(1, 28),
        birth_month=random.randint(1, 12),
        birth_year=random.randint(1988, 2002),
        gender=random.choice([1, 2, 3]),
    )


class AutomatedGmailCreator:
    """Automates Gmail creation using YouTube signup flow, stealth injection, and TextBee SMS."""

    SIGNUP_URL = "https://accounts.google.com/signup?service=youtube&continue=https%3A%2F%2Fwww.youtube.com%2F"

    def __init__(self, sessions_dir: Path | None = None):
        self.sessions_dir = sessions_dir or (_root_dir / "database" / "sessions")
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    async def _ghost_type(self, element, text: str):
        """Types text with natural human micro-delays (50ms - 180ms per keystroke)."""
        await element.click()
        await asyncio.sleep(random.uniform(0.3, 0.7))
        for char in text:
            await element.type(char, delay=random.uniform(60, 160))
            if random.random() < 0.04:
                # Occasional slight hesitation
                await asyncio.sleep(random.uniform(0.2, 0.5))

    async def _click_next_button(self, page, timeout_ms: int = 7000) -> bool:
        """Finds and clicks the active Next / Berikutnya button."""
        next_selectors = [
            "button:has-text('Next')",
            "button:has-text('Berikutnya')",
            "#collectNameNext",
            "#birthdaygenderNext",
            "#next",
            "button[type='button']:has-text('Next')",
            "div[role='button']:has-text('Next')",
        ]
        for sel in next_selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1500):
                    await btn.click()
                    await asyncio.sleep(random.uniform(1.5, 3.0))
                    return True
            except Exception:
                continue
        return False

    async def create_account(
        self,
        phone_number: str | None = None,
        ghost_only: bool = False,
        headed: bool = False,
        geo_region: str = "US",
        use_proxy: bool = False,
    ) -> dict[str, Any]:
        """Executes full automated registration session.
        
        Args:
            phone_number: Optional phone number for SMS fallback (e.g. from TextBee).
            ghost_only: If True, aborts if phone verification cannot be skipped.
            headed: If True, opens visible browser window.
            geo_region: Proxy region code (US, UK, AU).
            use_proxy: Whether to route through residential proxy (default: False for fast direct Google connectivity).
        """
        from playwright.async_api import async_playwright
        from master_orchestrator import IdentityManager

        profile = generate_random_profile()
        account_id = f"acc_{int(time.time())}_{random.randint(100, 999)}"
        session_file = self.sessions_dir / f"{account_id}.json"

        logger.info(f"🚀 Initializing automated creation for [{profile.email}] (Region: {geo_region}, Proxy: {use_proxy})...")

        id_mgr = IdentityManager()
        phone_to_use = phone_number or os.environ.get("TEXTBEE_PHONE_NUMBER", "")

        async with async_playwright() as p:
            launch_args = stealth_launch_args()
            launch_kwargs: dict[str, Any] = {
                "headless": not headed,
                "args": launch_args,
            }
            if use_proxy:
                raw_proxy = DataImpulseProxyRouter.get_proxy_url(geo_region.lower(), f"create_{account_id}")
                if raw_proxy and "@" in raw_proxy:
                    proto_auth, host_port = raw_proxy.split("@", 1)
                    proto, auth = proto_auth.split("://", 1)
                    u, pw = auth.split(":", 1)
                    launch_kwargs["proxy"] = {
                        "server": f"{proto}://{host_port}",
                        "username": u,
                        "password": pw,
                    }
                elif raw_proxy:
                    launch_kwargs["proxy"] = {"server": raw_proxy}

            browser = await p.chromium.launch(**launch_kwargs)

            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                locale="en-US",
                timezone_id="America/New_York",
            )

            stealth_js = build_stealth_script(platform="macos", session_seed=account_id)
            await context.add_init_script(stealth_js)
            page = await context.new_page()

            try:
                # ── Step 1: Open YouTube Signup Entrypoint ───────────────────
                logger.info("🌐 Step 1: Navigating to YouTube signup portal...")
                await page.goto(self.SIGNUP_URL, wait_until="domcontentloaded", timeout=45000)
                await asyncio.sleep(random.uniform(3.0, 5.0))

                # Handle cookie consent if presented
                try:
                    consent_btn = page.locator("button:has-text('Accept all'), button:has-text('I agree'), button:has-text('Reject all')").first
                    if await consent_btn.is_visible(timeout=3000):
                        await consent_btn.click()
                        await asyncio.sleep(2)
                except Exception:
                    pass

                # ── Step 2: Name Input ────────────────────────────────────────
                logger.info(f"✍️ Step 2: Typing Name ({profile.first_name} {profile.last_name})...")
                fname_input = page.locator("input[name='firstName'], input[id='firstName'], input[aria-label*='First name']").first
                await fname_input.wait_for(state="visible", timeout=15000)
                await self._ghost_type(fname_input, profile.first_name)

                lname_input = page.locator("input[name='lastName'], input[id='lastName'], input[aria-label*='Last name']").first
                if await lname_input.is_visible(timeout=2000):
                    await self._ghost_type(lname_input, profile.last_name)

                await self._click_next_button(page)
                await asyncio.sleep(random.uniform(2.0, 3.5))

                # ── Step 3: Birthday & Gender ─────────────────────────────────
                logger.info(f"🎂 Step 3: Setting Birthday ({profile.birth_month}/{profile.birth_day}/{profile.birth_year})...")
                month_names = [
                    "January", "February", "March", "April", "May", "June",
                    "July", "August", "September", "October", "November", "December"
                ]
                month_str = month_names[profile.birth_month - 1]
                gender_map = {1: "Female", 2: "Male", 3: "Rather not say"}
                gender_str = gender_map.get(profile.gender, "Rather not say")

                try:
                    # 1. Month selector (combobox or native select)
                    m_select = page.locator("select[id='month'], select[name='month']").first
                    if await m_select.is_visible(timeout=1500):
                        await m_select.select_option(value=str(profile.birth_month))
                    else:
                        m_box = page.locator("[role='combobox'][aria-label*='Month'], #month [role='combobox'], #month").first
                        if await m_box.is_visible(timeout=3000):
                            await m_box.click()
                            await asyncio.sleep(0.5)
                            opt = page.locator(f"[role='option']:has-text('{month_str}')").first
                            if await opt.is_visible(timeout=2000):
                                await opt.click()

                    # 2. Day input
                    day_input = page.locator("input[id='day'], input[name='day']").first
                    await self._ghost_type(day_input, str(profile.birth_day))

                    # 3. Year input
                    year_input = page.locator("input[id='year'], input[name='year']").first
                    await self._ghost_type(year_input, str(profile.birth_year))

                    # 4. Gender selector (combobox or native select)
                    g_select = page.locator("select[id='gender'], select[name='gender']").first
                    if await g_select.is_visible(timeout=1500):
                        await g_select.select_option(value=str(profile.gender))
                    else:
                        g_box = page.locator("[role='combobox'][aria-label*='Gender'], #gender [role='combobox'], #gender").first
                        if await g_box.is_visible(timeout=3000):
                            await g_box.click()
                            await asyncio.sleep(0.5)
                            g_opt = page.locator(f"[role='option']:has-text('{gender_str}'), [role='option']:has-text('Pria'), [role='option']:has-text('Wanita')").first
                            if await g_opt.is_visible(timeout=2000):
                                await g_opt.click()

                    await asyncio.sleep(1.0)
                    await self._click_next_button(page)
                    await asyncio.sleep(random.uniform(2.5, 4.0))
                except Exception as e:
                    logger.warning(f"Birthday/Gender step notice: {e}")

                # ── Step 4: Choose or Type Username ───────────────────────────
                logger.info("📧 Step 4: Selecting Gmail address...")
                try:
                    # If YouTube signup defaults to existing email, switch to creating a Gmail address
                    switch_btn = page.locator("button:has-text('Get a Gmail address instead'), button:has-text('Dapatkan alamat Gmail')").first
                    if await switch_btn.is_visible(timeout=2500):
                        logger.info("Detected 'Get a Gmail address instead' toggle. Switching to new Gmail address...")
                        await switch_btn.click()
                        await asyncio.sleep(1.5)

                    # Check if Google suggests radio buttons for addresses
                    radio_opt = page.locator("input[type='radio']").first
                    if await radio_opt.is_visible(timeout=2500):
                        await radio_opt.click()
                        await asyncio.sleep(1.0)
                    else:
                        user_input = page.locator("input[name='Username'], input[id='identifierId'], input[id='username']").first
                        if await user_input.is_visible(timeout=3000):
                            await self._ghost_type(user_input, profile.username)

                    await asyncio.sleep(0.5)
                    await self._click_next_button(page)
                    await asyncio.sleep(random.uniform(2.5, 4.0))
                except Exception as e:
                    logger.warning(f"Username selection notice: {e}")

                # ── Step 5: Password Entry ────────────────────────────────────
                logger.info("🔑 Step 5: Setting strong password...")
                try:
                    pwd_input = page.locator("input[name='Passwd'], input[type='password']").first
                    await pwd_input.wait_for(state="visible", timeout=8000)
                    await self._ghost_type(pwd_input, profile.password)

                    confirm_input = page.locator("input[name='PasswdAgain'], input[name='ConfirmPasswd']").first
                    if await confirm_input.is_visible(timeout=2000):
                        await self._ghost_type(confirm_input, profile.password)

                    await self._click_next_button(page)
                    await asyncio.sleep(random.uniform(4.0, 6.0))
                except Exception as e:
                    logger.error(f"Password entry failed: {e}")
                    return {"status": "error", "step": "password", "error": str(e)}

                # ── Step 6: Verification & Anti-Abuse Evaluation ───────────────
                logger.info("🛡️ Step 6: Evaluating verification challenge & anti-abuse policy...")

                # Check if Google anti-abuse policy triggered on this egress
                if "signup/error" in page.url:
                    err_text = await page.evaluate("() => document.body.innerText")
                    logger.error(f"Google anti-abuse policy triggered: {err_text[:160]}")
                    return {
                        "status": "ip_rate_limited",
                        "error": "Google anti-abuse triggered ('Sorry, we could not create your Google Account').",
                        "email": profile.email,
                        "remedy": "Rotate to residential mobile 4G/5G proxy or import aged PVA accounts directly via 'accounts --import'.",
                    }

                # 1. Check for Skip button (Ghost Mode)
                skip_btn = page.locator("button:has-text('Skip'), button:has-text('Lewati')").first
                if await skip_btn.is_visible(timeout=3000):
                    logger.info("👻 Ghost Mode Activated: 'Skip' button detected! Skipping phone verification...")
                    await skip_btn.click()
                    await asyncio.sleep(random.uniform(2.0, 4.0))
                else:
                    # 2. Check if phone input is required
                    phone_input = page.locator("input[type='tel'], input[id='phoneNumberId']").first
                    if await phone_input.is_visible(timeout=3000):
                        logger.info("📱 Phone verification challenge presented by Google.")
                        if ghost_only:
                            logger.warning("Ghost Mode Only requested and Skip button was absent. Aborting creation.")
                            return {
                                "status": "phone_required",
                                "message": "Phone verification mandatory on this proxy/IP. Ghost skip not available.",
                                "email": profile.email,
                            }

                        if not phone_to_use:
                            logger.warning("No phone number configured for SMS fallback. Aborting.")
                            return {
                                "status": "needs_phone",
                                "message": "Phone required but no TEXTBEE_PHONE_NUMBER provided.",
                                "email": profile.email,
                            }

                        logger.info(f"📲 Entering fallback phone number: {phone_to_use[:6]}****...")
                        await self._ghost_type(phone_input, phone_to_use)
                        await self._click_next_button(page)

                        # Wait for OTP code input
                        logger.info("⏳ Waiting for OTP prompt...")
                        code_input = page.locator("input[id='code'], input[name='code'], input[aria-label*='verification code']").first
                        await code_input.wait_for(state="visible", timeout=15000)

                        logger.info("📡 Polling TextBee Android SMS Gateway for 6-digit Google OTP (45s timeout)...")
                        otp_code = id_mgr.poll_textbee_otp_with_timeout(timeout_seconds=45, poll_interval=3)
                        if otp_code:
                            logger.info(f"✅ OTP Received via TextBee: [{otp_code}]! Submitting code...")
                            await self._ghost_type(code_input, otp_code)
                            await self._click_next_button(page)
                            await asyncio.sleep(random.uniform(3.0, 5.0))
                        else:
                            logger.error("❌ Timed out waiting for SMS OTP from TextBee gateway.")
                            return {"status": "otp_timeout", "email": profile.email}

                # ── Step 7: Recovery Email & Terms ────────────────────────────
                logger.info("📜 Step 7: Handling Recovery Email and Terms of Service...")
                # Skip recovery email if prompted
                rec_skip = page.locator("button:has-text('Skip'), button:has-text('Lewati')").first
                if await rec_skip.is_visible(timeout=3000):
                    await rec_skip.click()
                    await asyncio.sleep(2)

                # Review account info -> Next
                await self._click_next_button(page)
                await asyncio.sleep(2)

                # Accept terms
                agree_btn = page.locator("button:has-text('I agree'), button:has-text('Saya setuju'), button:has-text('Express personalization')").first
                if await agree_btn.is_visible(timeout=5000):
                    await agree_btn.click()
                    await asyncio.sleep(random.uniform(4.0, 7.0))

                # ── Step 8: Save Session & Sync Ledger ────────────────────────
                logger.info(f"💾 Saving authenticated session to {session_file.name}...")
                await context.storage_state(path=str(session_file))

                # Append account to accounts.csv
                from master_orchestrator import AccountRecord
                new_acc = AccountRecord(
                    account_id=account_id,
                    email=profile.email,
                    password=profile.password,
                    recovery_email="recovery@gworky.com",
                    assigned_proxy=f"residential_{geo_region.lower()}",
                    persona_name=f"{geo_region.upper()}_Chrome_Desktop",
                    status="active",
                    last_active=time.strftime("%Y-%m-%d"),
                    created_at=time.strftime("%Y-%m-%d"),
                    warmup_stage=0,
                    subscribed_to_groundwork=False,
                    session_file=str(session_file),
                    notes="created_via_automated_creator",
                )
                id_mgr.append_account(new_acc)

                logger.info(f"🎉 Account [{profile.email}] successfully created and registered into OAE ledger!")
                return {
                    "status": "success",
                    "account_id": account_id,
                    "email": profile.email,
                    "password": profile.password,
                    "session_file": str(session_file),
                }

            except Exception as e:
                logger.error(f"Creation session error: {e}")
                return {"status": "error", "error": str(e), "email": profile.email}
            finally:
                await browser.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Automated Gmail Creator")
    parser.add_argument("--phone", type=str, default=None, help="Cellular phone number for SMS verification")
    parser.add_argument("--ghost-only", action="store_true", help="Only register if phone verification can be skipped")
    parser.add_argument("--headed", action="store_true", help="Run browser in visible mode")
    parser.add_argument("--region", type=str, default="US", help="Proxy region (US, UK, AU)")
    args = parser.parse_args()

    creator = AutomatedGmailCreator()
    res = asyncio.run(
        creator.create_account(
            phone_number=args.phone,
            ghost_only=args.ghost_only,
            headed=args.headed,
            geo_region=args.region,
        )
    )
    print("\nResult:")
    import json
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
