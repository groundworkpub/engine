#!/usr/bin/env python3
"""
agents/awin_inspector.py — Comprehensive Awin Advertiser & Programme Scanner

Scans active programmes, pending applications, and searches for strategic target brands
(Renogy, EcoFlow, Jackery, Bluetti, TradingView, AG1) using the authenticated Google Chrome session.
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
logger = logging.getLogger("awin_inspector")

PROFILE_DIR = Path(__file__).resolve().parent / ".affiliate_profile"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "affiliate"
OUTPUT_FILE = OUTPUT_DIR / "awin_scanned_programmes.json"
PUBLISHER_ID = os.getenv("AWIN_PUBLISHER_ID", "3081079")


async def main():
    logger.info("Launching authenticated Awin scanner...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    results = {
        "active_programmes": [],
        "pending_programmes": [],
        "brand_searches": {},
    }

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=True,
            viewport={"width": 1366, "height": 768},
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # 1. Inspect Active programmes
        logger.info("1. Inspecting Active Joined Programmes...")
        await page.goto(f"https://ui.awin.com/awin/affiliate/{PUBLISHER_ID}/merchant-directory/index/tab/active/page/1", timeout=40000, wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)

        rows = await page.locator("table tbody tr").all()
        logger.info("Active table rows found: %d", len(rows))
        for r in rows:
            text = (await r.inner_text()).strip()
            links = await r.locator("a[href*='merchant-profile']").all()
            for l in links:
                name = (await l.inner_text()).strip()
                href = await l.get_attribute("href") or ""
                mid = href.split("/")[-1] if href else ""
                if name and mid:
                    results["active_programmes"].append({"mid": mid, "name": name, "profile_url": href, "raw": text})

        # 2. Inspect Pending programmes
        logger.info("2. Inspecting Pending Programmes...")
        await page.goto(f"https://ui.awin.com/awin/affiliate/{PUBLISHER_ID}/merchant-directory/index/tab/pending/page/1", timeout=40000, wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)

        rows_pending = await page.locator("table tbody tr").all()
        logger.info("Pending table rows found: %d", len(rows_pending))
        for r in rows_pending:
            text = (await r.inner_text()).strip()
            links = await r.locator("a[href*='merchant-profile']").all()
            for l in links:
                name = (await l.inner_text()).strip()
                href = await l.get_attribute("href") or ""
                mid = href.split("/")[-1] if href else ""
                if name and mid:
                    results["pending_programmes"].append({"mid": mid, "name": name, "profile_url": href, "raw": text})

        # 3. Search target brands
        target_brands = ["renogy", "ecoflow", "bluetti", "jackery", "tradingview", "nordvpn", "athletic greens"]
        for brand in target_brands:
            logger.info("3. Searching for brand: %s...", brand)
            search_url = f"https://ui.awin.com/awin/affiliate/{PUBLISHER_ID}/merchant-directory/index/search/{brand}"
            await page.goto(search_url, timeout=40000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3500)

            found_items = []
            links = await page.locator("a[href*='merchant-profile']").all()
            seen_mids = set()
            for l in links:
                name = (await l.inner_text()).strip()
                href = await l.get_attribute("href") or ""
                mid = href.split("/")[-1] if href else ""
                if name and mid and mid not in seen_mids:
                    seen_mids.add(mid)
                    found_items.append({"mid": mid, "name": name, "profile_url": href})
            
            logger.info("Found %d results for '%s'", len(found_items), brand)
            results["brand_searches"][brand] = found_items

        await ctx.close()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print("\n" + "=" * 65)
    print("AWIN SCAN RESULTS SUMMARY")
    print("=" * 65)
    print(f"Total Active Joined Programmes: {len(results['active_programmes'])}")
    for a in results["active_programmes"]:
        print(f"  [ACTIVE] MID: {a['mid']} | {a['name']}")

    print(f"\nTotal Pending Programmes: {len(results['pending_programmes'])}")
    for p in results["pending_programmes"]:
        print(f"  [PENDING] MID: {p['mid']} | {p['name']}")

    print("\nTarget Brand Search Results:")
    for b, items in results["brand_searches"].items():
        print(f"  Brand '{b.upper()}': {len(items)} found")
        for it in items[:3]:
            print(f"    -> MID: {it['mid']} | Name: {it['name']} | Profile: {it['profile_url']}")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
