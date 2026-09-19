#!/usr/bin/env python3
"""
agents/awin_auto_login.py — Native Google Chrome Awin Authenticated Session Handshake

Uses macOS Native Google Chrome (channel="chrome") to prevent blank-screen rendering,
handles Auth0 two-step flow (identifier -> password), and preserves authenticated profile.
"""

import asyncio
import logging
import os
from pathlib import Path
from playwright.async_api import async_playwright
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env.local")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("awin_chrome_login")

PROFILE_DIR = Path(__file__).resolve().parent / ".affiliate_profile"
AWIN_EMAIL = os.getenv("AWIN_EMAIL", os.getenv("AWIN_USER", ""))
AWIN_PASSWORD = os.getenv("AWIN_PASSWORD", os.getenv("AWIN_PASS", ""))


async def main():
    logger.info("Opening Native Google Chrome with persistent profile: %s", PROFILE_DIR)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",  # Uses native macOS Google Chrome (never blank)
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--start-maximized",
            ],
            viewport=None,  # Use full native window
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        logger.info("1. Navigating to https://ui.awin.com/login...")
        await page.goto("https://ui.awin.com/login", timeout=40000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        # Handle cookie banner if present
        try:
            accept_btn = page.locator("#accept, button:has-text('Accept all')").first
            if await accept_btn.is_visible():
                await accept_btn.click()
                logger.info("✓ Cookie banner accepted.")
        except Exception:
            pass

        # STEP 1: Email / Identifier
        logger.info("Current URL: %s", page.url)
        email_selector = '#email, #username, input[name="username"], input[type="email"]'
        try:
            logger.info("Waiting for email/identifier field...")
            email_field = page.locator(email_selector).first
            await email_field.wait_for(state="visible", timeout=8000)
            logger.info("2. Filling email...")
            if not AWIN_EMAIL:
                raise ValueError("AWIN_EMAIL or AWIN_USER is not set in environment")
            await email_field.fill(AWIN_EMAIL)
            await page.wait_for_timeout(500)
            
            submit_email = page.locator('button[type="submit"], #login, button:has-text("Continue")').first
            await submit_email.click()
            logger.info("✓ Email submitted. Moving to password step...")
            await page.wait_for_timeout(3000)
        except Exception as e:
            logger.warning("Email step note (might already be on password): %s", e)

        # STEP 2: Password
        logger.info("Current URL: %s", page.url)
        pwd_selector = '#password, input[name="password"], input[type="password"]'
        try:
            logger.info("Waiting for password field...")
            pwd_field = page.locator(pwd_selector).first
            await pwd_field.wait_for(state="visible", timeout=12000)
            logger.info("3. Filling password...")
            if not AWIN_PASSWORD:
                raise ValueError("AWIN_PASSWORD or AWIN_PASS is not set in environment")
            await pwd_field.fill(AWIN_PASSWORD)
            await page.wait_for_timeout(500)

            submit_pwd = page.locator('button[type="submit"], button:has-text("Continue"), button:has-text("Log in")').first
            logger.info("4. Submitting password...")
            await submit_pwd.click()
            await page.wait_for_timeout(4000)
        except Exception as e:
            logger.warning("Password step note: %s", e)

        logger.info("5. Landed on URL: %s", page.url)
        logger.info("=========================================================")
        logger.info("Jendela Google Chrome sekarang terbuka.")
        logger.info("Jika ada kode 2FA / OTP, silakan masukkan langsung di Chrome.")
        logger.info("=========================================================")

        for i in range(150):  # 5 minutes
            await page.wait_for_timeout(2000)
            cur = page.url
            if "ui.awin.com" in cur and any(k in cur for k in ["dashboard", "publisher", "merchant"]):
                if "id.awin.com" not in cur and "login" not in cur:
                    logger.info("🎉 SUKSES! Berhasil masuk ke Dashboard Awin: %s", cur)
                    await page.wait_for_timeout(5000)
                    break

        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
