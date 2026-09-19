#!/usr/bin/env python3
"""
agents/awin_session_crawler.py — In-Session Awin Crawler & Advertiser Explorer

Logs in to Awin using verified credentials and immediately explores:
1. My Programmes (Active & Pending)
2. Join Programmes (Directory Search for Renogy, EcoFlow, Jackery, Bluetti, TradingView, etc.)
Captures full DOM data and screenshots without session drop.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from playwright.async_api import async_playwright
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env.local")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("awin_crawler")

PROFILE_DIR = Path(__file__).resolve().parent / ".affiliate_profile"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "affiliate"
OUTPUT_FILE = OUTPUT_DIR / "awin_live_catalog.json"
AWIN_EMAIL = os.getenv("AWIN_EMAIL", os.getenv("AWIN_USER", ""))
AWIN_PASSWORD = os.getenv("AWIN_PASSWORD", os.getenv("AWIN_PASS", ""))
AWIN_PUBLISHER_ID = os.getenv("AWIN_PUBLISHER_ID", "3081079")


async def main():
    logger.info("Starting Awin In-Session Crawler...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    Path("scratch").mkdir(exist_ok=True)

    catalog = {
        "active_programmes": [],
        "pending_programmes": [],
        "searched_brands": {},
    }

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=True,
            viewport={"width": 1366, "height": 768},
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # Step 1: Navigate to login
        logger.info("1. Navigating to login...")
        await page.goto("https://ui.awin.com/login", timeout=40000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        # Cookie consent
        try:
            btn = page.locator("#accept, button:has-text('Accept all')").first
            if await btn.is_visible():
                await btn.click()
                logger.info("✓ Cookie banner accepted.")
        except Exception:
            pass

        # Check if login needed
        if "id.awin.com" in page.url or "login" in page.url or "prelogin" in page.url:
            logger.info("Authenticating via Auth0...")
            # Email step
            try:
                email_input = page.locator('#email, #username, input[name="username"], input[type="email"]').first
                if await email_input.is_visible():
                    logger.info("Entering email...")
                    if not AWIN_EMAIL:
                        raise ValueError("AWIN_EMAIL or AWIN_USER environment variable missing")
                    await email_input.fill(AWIN_EMAIL)
                    await page.wait_for_timeout(500)
                    submit_email = page.locator('button[type="submit"], #login, button:has-text("Continue")').first
                    await submit_email.click()
                    await page.wait_for_timeout(3000)
            except Exception as e:
                logger.warning("Email step note: %s", e)

            # Password step
            try:
                pwd_input = page.locator('#password, input[name="password"], input[type="password"]:not([hidden])').first
                await pwd_input.wait_for(state="visible", timeout=12000)
                logger.info("Entering password...")
                if not AWIN_PASSWORD:
                    raise ValueError("AWIN_PASSWORD or AWIN_PASS environment variable missing")
                await pwd_input.fill(AWIN_PASSWORD)
                await page.wait_for_timeout(500)
                submit_pwd = page.locator('button[type="submit"], button:has-text("Continue"), button:has-text("Log in")').first
                await submit_pwd.click()
                logger.info("Password submitted. Waiting for dashboard...")
                await page.wait_for_timeout(6000)
            except Exception as e:
                logger.warning("Password step note: %s", e)

        logger.info("Current URL: %s", page.url)
        logger.info("Page Title: %s", await page.title())

        # Verify we are on dashboard
        if "ui.awin.com" not in page.url or "id.awin.com" in page.url:
            logger.error("Failed to reach authenticated dashboard. Current: %s", page.url)
            await page.screenshot(path="scratch/awin_fail_snap.png")
            await ctx.close()
            return

        logger.info("✓ Authenticated session active on dashboard!")

        # Step 2: Navigate to My Programmes (Active)
        logger.info("2. Navigating to My Programmes (Active)...")
        active_url = f"https://ui.awin.com/awin/affiliate/{AWIN_PUBLISHER_ID}/merchant-directory/index/tab/active/page/1"
        await page.goto(active_url, timeout=30000, wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)
        await page.screenshot(path="scratch/awin_active_programmes.png")

        # Scrape all merchant links
        active_links = await page.locator("a[href*='merchant-profile']").all()
        logger.info("Active merchant profile links found: %d", len(active_links))
        for l in active_links:
            name = (await l.inner_text()).strip()
            href = await l.get_attribute("href") or ""
            if name and href:
                mid = href.split("/")[-1]
                catalog["active_programmes"].append({"mid": mid, "name": name, "href": href})

        # Step 3: Search for target brands in Directory
        target_brands = ["renogy", "ecoflow", "bluetti", "jackery", "tradingview", "nordvpn", "athletic greens"]
        for brand in target_brands:
            logger.info("3. Searching for '%s' in Directory...", brand)
            search_url = f"https://ui.awin.com/awin/affiliate/{AWIN_PUBLISHER_ID}/merchant-directory/index/search/{brand}"
            await page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3500)

            found = []
            links = await page.locator("a[href*='merchant-profile']").all()
            seen_mids = set()
            for l in links:
                name = (await l.inner_text()).strip()
                href = await l.get_attribute("href") or ""
                mid = href.split("/")[-1] if href else ""
                if name and mid and mid not in seen_mids:
                    seen_mids.add(mid)
                    found.append({"mid": mid, "name": name, "href": href})

            logger.info("Found %d results for '%s'", len(found), brand)
            catalog["searched_brands"][brand] = found

        await ctx.close()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2)

    print("\n" + "=" * 65)
    print("AWIN LIVE IN-SESSION CRAWL REPORT")
    print("=" * 65)
    print(f"Active Joined Programmes: {len(catalog['active_programmes'])}")
    for a in catalog["active_programmes"]:
        print(f"  - [{a['mid']}] {a['name']}")

    print("\nTarget Brand Matches in Directory:")
    for b, items in catalog["searched_brands"].items():
        print(f"  Brand: {b.upper()} ({len(items)} found)")
        for it in items:
            print(f"    -> MID {it['mid']}: {it['name']} ({it['href']})")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
