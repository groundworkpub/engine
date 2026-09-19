#!/usr/bin/env python3
"""
agents/awin_join_merchants.py — Programmatic Awin Merchant Application Automator

Submits professional, high-approval partnership applications ("Join Programme")
to target solar and home battery manufacturers (EcoFlow, BLUETTI, Jackery, BougeRV, ALLPOWERS)
using authenticated headless Chrome session.
"""

import asyncio
import json
import logging
from pathlib import Path
from playwright.async_api import async_playwright
import os
import httpx
from dotenv import load_dotenv

# Load local environment if present
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env.local")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("awin_joiner")

PROFILE_DIR = Path(__file__).resolve().parent / ".affiliate_profile"
OAUTH_TOKEN = os.getenv("AWIN_OAUTH_TOKEN", os.getenv("AWIN_API_TOKEN", ""))
PUBLISHER_ID = os.getenv("AWIN_PUBLISHER_ID", "")
AWIN_EMAIL = os.getenv("AWIN_EMAIL", os.getenv("AWIN_USER", ""))
AWIN_PASSWORD = os.getenv("AWIN_PASSWORD", os.getenv("AWIN_PASS", ""))

TARGET_MERCHANTS = [
    {"mid": "59181", "name": "EcoFlow", "niche": "Solar & Battery Storage"},
    {"mid": "59271", "name": "BLUETTI US", "niche": "Solar & Battery Storage"},
    {"mid": "59183", "name": "Jackery US", "niche": "Portable Power & Solar"},
    {"mid": "52765", "name": "BougeRV", "niche": "Off-grid Solar & Portable Fridges"},
    {"mid": "40342", "name": "ALLPOWERS (US & CA)", "niche": "Solar Generators & Panels"},
]

SOLAR_PITCH_MESSAGE = """Groundwork (https://gworky.com) is an independent digital research and consumer utility platform serving homeowners and educated professionals aged 35–48 across the United States, United Kingdom, and Australia.

We produce comprehensive residential energy evaluations, interactive solar battery payback calculators, and off-grid power comparisons. We plan to feature your brand contextually within our flagship Solar Battery & Energy Storage research guides and interactive simulation tools, connecting high-intent homeowners directly with vetted hardware solutions.

All promotional placements adhere strictly to high editorial standards, transparent FTC/ASA-compliant disclosures, and zero-spam practices. We look forward to a productive, long-term partnership with your team."""


async def ensure_authenticated(page):
    logger.info("Verifying authentication...")
    await page.goto("https://ui.awin.com/login", timeout=40000, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    # Accept cookie banner
    try:
        accept_btn = page.locator("#accept, button:has-text('Accept all')").first
        if await accept_btn.is_visible():
            await accept_btn.click()
            await page.wait_for_timeout(1000)
    except Exception:
        pass

    # If redirected to Auth0 login
    if "id.awin.com" in page.url or "login" in page.url or "prelogin" in page.url:
        logger.info("Session expired. Authenticating via Auth0...")
        try:
            email_field = page.locator('#email, #username, input[name="username"], input[type="email"]').first
            if await email_field.is_visible():
                if not AWIN_EMAIL:
                    raise ValueError("AWIN_EMAIL or AWIN_USER environment variable is missing")
                await email_field.fill(AWIN_EMAIL)
                await page.locator('button[type="submit"], #login, button:has-text("Continue")').first.click()
                await page.wait_for_timeout(3000)
        except Exception as e:
            logger.warning("Email step note: %s", e)

        try:
            pwd_field = page.locator('#password, input[name="password"], input[type="password"]:not([hidden])').first
            await pwd_field.wait_for(state="visible", timeout=12000)
            if not AWIN_PASSWORD:
                raise ValueError("AWIN_PASSWORD or AWIN_PASS environment variable is missing")
            await pwd_field.fill(AWIN_PASSWORD)
            await page.locator('button[type="submit"], button:has-text("Continue"), button:has-text("Log in")').first.click()
            await page.wait_for_timeout(6000)
        except Exception as e:
            logger.warning("Password step note: %s", e)

    logger.info("Landed on URL: %s | Title: %s", page.url, await page.title())


async def apply_to_merchant(page, merchant):
    mid = merchant["mid"]
    name = merchant["name"]
    logger.info("--------------------------------------------------")
    logger.info("Processing application for: [%s] %s", mid, name)

    profile_url = f"https://ui.awin.com/awin/affiliate/{PUBLISHER_ID}/merchant-profile/{mid}"
    await page.goto(profile_url, timeout=35000, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    # Check status on profile
    page_text = await page.locator("body").inner_text()
    if "(Joined)" in page_text:
        logger.info("✓ Already JOINED with [%s] %s!", mid, name)
        return {"mid": mid, "name": name, "status": "already_joined"}

    if "(Pending)" in page_text:
        logger.info("⏳ Application already PENDING for [%s] %s.", mid, name)
        return {"mid": mid, "name": name, "status": "already_pending"}

    # Find the Join Programme trigger button (can be span, div, a, or button)
    join_trigger = page.locator('text="Join Programme"').first
    try:
        await join_trigger.wait_for(state="visible", timeout=6000)
    except Exception:
        logger.warning("Join trigger button not found for [%s] %s. Current URL: %s", mid, name, page.url)
        await page.screenshot(path=f"scratch/awin_join_{mid}_not_found.png")
        return {"mid": mid, "name": name, "status": "trigger_not_found"}

    logger.info("Clicking 'Join Programme' for [%s] %s...", mid, name)
    await join_trigger.click()
    await page.wait_for_timeout(2500)

    # Modal should now be open
    modal_locator = page.locator('div[role="dialog"], .modal, div:has-text("Join Programme")').first
    await page.screenshot(path=f"scratch/awin_join_modal_{mid}.png")

    # 1. Verify/Select Promotion URL (should default to https://gworky.com)
    try:
        promo_select = page.locator('select, [role="combobox"]').first
        if await promo_select.is_visible():
            logger.info("Promotion dropdown detected.")
    except Exception:
        pass

    # 2. Fill Message Textarea
    try:
        msg_textarea = page.locator('textarea, textarea[name="message"], div[contenteditable="true"]').first
        await msg_textarea.wait_for(state="visible", timeout=6000)
        logger.info("Filling pitch message...")
        await msg_textarea.fill(SOLAR_PITCH_MESSAGE)
        await page.wait_for_timeout(500)
    except Exception as e:
        logger.error("Failed to fill message textarea: %s", e)
        return {"mid": mid, "name": name, "status": "textarea_error"}

    # 3. Check Terms and Conditions Checkbox
    try:
        checkbox = page.locator('input[type="checkbox"]').first
        await checkbox.wait_for(state="visible", timeout=6000)
        if not await checkbox.is_checked():
            logger.info("Checking Terms & Conditions checkbox...")
            await checkbox.check()
            await page.wait_for_timeout(500)
    except Exception as e:
        logger.warning("Checkbox check note: %s", e)

    # 4. Click Submit / Join Button inside modal
    try:
        submit_btn = page.locator('button:has-text("Join"), input[type="submit"][value*="Join"], button:has-text("Apply")').first
        logger.info("Clicking Submit/Join button...")
        await submit_btn.click()
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"scratch/awin_join_submitted_{mid}.png")
        logger.info("✓ Application submitted for [%s] %s!", mid, name)
        return {"mid": mid, "name": name, "status": "submitted"}
    except Exception as e:
        logger.error("Failed to click submit button: %s", e)
        return {"mid": mid, "name": name, "status": "submit_error"}


async def main():
    logger.info("Starting Batch Solar Merchant Applications on Awin...")
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-gpu",
                "--no-sandbox",
            ],
            viewport={"width": 1366, "height": 768},
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()

        await ensure_authenticated(page)

        for merchant in TARGET_MERCHANTS:
            res = await apply_to_merchant(page, merchant)
            results.append(res)
            await page.wait_for_timeout(2000)

        await browser.close()

    # Verify pending status via official REST API
    logger.info("==================================================")
    logger.info("Verifying updated pending status via Awin REST API...")
    url = f"https://api.awin.com/publishers/{PUBLISHER_ID}/programmes?relationship=pending"
    headers = {"Authorization": f"Bearer {OAUTH_TOKEN}", "Accept": "application/json"}
    
    try:
        resp = httpx.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            pending_programmes = resp.json()
            logger.info("Total Pending Programmes on Awin: %d", len(pending_programmes))
            for p in pending_programmes:
                logger.info("  ⏳ [%s] %s", p.get("id"), p.get("name"))
    except Exception as e:
        logger.warning("API check exception: %s", e)

    output_path = Path("scratch/awin_join_results.json")
    output_path.write_text(json.dumps(results, indent=2))
    logger.info("Application batch complete. Results saved to %s", output_path)


if __name__ == "__main__":
    asyncio.run(main())
