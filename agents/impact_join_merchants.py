#!/usr/bin/env python3
"""
agents/impact_join_merchants.py — Programmatic Impact.com Partner Application Automator

Submits professional join requests to target brands on Impact.com that are NOT
available on the Awin network. Mirrors the Awin joiner pattern (Playwright
headless Chrome, persistent profile, pillar-specific pitches).

Prerequisites:
  IMPACT_EMAIL      — login email for app.impact.com
  IMPACT_PASSWORD   — login password for app.impact.com
  (Already set: IMPACT_ACCOUNT_SID, IMPACT_AUTH_TOKEN for API verification)

Verified via Impact brand catalogue (2026-09-19):
  These brands run their affiliate programmes on Impact, not Awin.
  MIDs here are Impact Campaign IDs (numeric) — look them up at
  https://app.impact.com/secure/mediapartner/discovery.ihtml

Created: 2026-09-19
"""

import asyncio
import json
import logging
from pathlib import Path
from playwright.async_api import async_playwright
import os
import httpx
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env.local")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("impact_joiner")

PROFILE_DIR = Path(__file__).resolve().parent / ".impact_profile"
IMPACT_EMAIL    = os.getenv("IMPACT_EMAIL", "")
IMPACT_PASSWORD = os.getenv("IMPACT_PASSWORD", "")
ACCOUNT_SID     = os.getenv("IMPACT_ACCOUNT_SID", "")
AUTH_TOKEN      = os.getenv("IMPACT_AUTH_TOKEN", "")

# ---------------------------------------------------------------------------
# Target Brands — confirmed on Impact.com, NOT on Awin
# Campaign IDs: look up at app.impact.com > Brands > Discovery search
# Leave campaign_id="" if unknown — script will search by brand name
# ---------------------------------------------------------------------------
TARGET_BRANDS = [
    # --- HOME: Security ---
    {"campaign_id": "",      "name": "SimpliSafe",    "search": "SimpliSafe",   "niche": "Home Security Systems", "pillar": "home"},
    # --- MONEY: Personal Finance ---
    {"campaign_id": "",      "name": "NerdWallet",    "search": "NerdWallet",   "niche": "Personal Finance",      "pillar": "money"},
    {"campaign_id": "",      "name": "Experian",      "search": "Experian",     "niche": "Credit Monitoring",     "pillar": "money"},
    # --- BODY: Health & Wellness ---
    {"campaign_id": "",      "name": "Noom",          "search": "Noom",         "niche": "Weight Management",     "pillar": "body"},
    {"campaign_id": "",      "name": "Hims & Hers",   "search": "Hims",         "niche": "Telehealth & Wellness", "pillar": "body"},
    # --- TECH: Privacy ---
    {"campaign_id": "",      "name": "ExpressVPN",    "search": "ExpressVPN",   "niche": "VPN Services",          "pillar": "tech"},
    {"campaign_id": "",      "name": "1Password",     "search": "1Password",    "niche": "Password Management",   "pillar": "tech"},
    # --- LIFE: Legal & Travel ---
    {"campaign_id": "",      "name": "LegalZoom",     "search": "LegalZoom",    "niche": "Legal Services",        "pillar": "life"},
    {"campaign_id": "",      "name": "Booking.com",   "search": "Booking",      "niche": "Travel",                "pillar": "life"},
    {"campaign_id": "",      "name": "KAYAK",         "search": "KAYAK",        "niche": "Travel Search",         "pillar": "life"},
]

# ---------------------------------------------------------------------------
# Pillar-Specific Pitch Messages
# ---------------------------------------------------------------------------
PITCH_MESSAGES: dict[str, str] = {
    "home": """Groundwork (https://gworky.com) is an independent research and consumer utility platform for homeowners aged 35–48 across the US, UK, and Australia.

We publish hands-on security system evaluations, interactive home protection cost calculators, and product comparisons verified against third-party testing data. Our /home pillar draws readers actively evaluating residential security upgrades — high-intent buyers at the bottom of the funnel.

We plan to feature your brand within our home security comparison guides, with FTC-compliant sponsored disclosures and zero-spam editorial standards.""",

    "money": """Groundwork (https://gworky.com) is an independent consumer finance research platform serving adults 35–48 across the US, UK, and Australia.

We produce evidence-based personal finance guides, interactive debt payoff calculators, and credit monitoring comparisons cross-referenced against CFPB and Federal Reserve data. Our /money pillar attracts high-intent readers actively making financial decisions — not casual browsers.

We plan to contextually feature your brand within our credit, personal finance, and insurance research guides, with transparent FTC-compliant affiliate disclosures.""",

    "body": """Groundwork (https://gworky.com) is an independent health and wellness research platform for adults 35–48 in the US, UK, and Australia.

We publish evidence-based guides on preventive health, telehealth, weight management, and longevity, cross-referenced against peer-reviewed research and clinical data. Our readers are health-conscious adults researching verified solutions — not general wellness browsers.

We plan to feature your brand within our telehealth and health optimization research guides, with clear editorial context, evidence-backed claims, and FTC/ASA-compliant disclosures.""",

    "tech": """Groundwork (https://gworky.com) is an independent technology research platform for decision-makers aged 35–48 across the US, UK, and Australia.

We publish rigorous comparisons of VPN services, password managers, and digital security tools, informed by independent technical audits and real user threat models. Our /tech pillar attracts professionals actively evaluating and purchasing digital security products.

We plan to contextually feature your product within our VPN and security comparison guides, with transparent FTC-compliant disclosures.""",

    "life": """Groundwork (https://gworky.com) is an independent life decisions research platform serving adults 35–48 across the US, UK, and Australia.

We publish practical, verified guides on legal services, travel planning, and major life decisions — with real cost comparisons and first-person research. Our /life pillar covers legal document preparation, travel search, and career tools, attracting readers actively planning significant purchases.

We plan to contextually feature your service within our legal and travel comparison guides, with FTC/ASA-compliant affiliate disclosures.""",
}

DISCOVERY_URL = "https://app.impact.com/secure/mediapartner/discovery.ihtml"
LOGIN_URL = "https://app.impact.com/secure/login"


async def ensure_authenticated(page):
    logger.info("Navigating to Impact.com login...")
    await page.goto(LOGIN_URL, timeout=40000, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    if "login" not in page.url and "app.impact.com" in page.url:
        logger.info("Already authenticated (session active).")
        return

    if not IMPACT_EMAIL or not IMPACT_PASSWORD:
        raise ValueError(
            "IMPACT_EMAIL and IMPACT_PASSWORD must be set in .env.local to use this script.\n"
            "Add these to your .env.local and re-run."
        )

    try:
        email_field = page.locator('input[type="email"], input[name="username"], #username').first
        await email_field.wait_for(state="visible", timeout=10000)
        await email_field.fill(IMPACT_EMAIL)
        logger.info("Filled email field.")
    except Exception as e:
        logger.error("Could not fill email field: %s", e)
        raise

    try:
        pwd_field = page.locator('input[type="password"], input[name="password"]').first
        await pwd_field.wait_for(state="visible", timeout=8000)
        await pwd_field.fill(IMPACT_PASSWORD)
        await page.locator('button[type="submit"], input[type="submit"], button:has-text("Log in")').first.click()
        await page.wait_for_timeout(5000)
        logger.info("Submitted login. URL: %s", page.url)
    except Exception as e:
        logger.error("Login flow failed: %s", e)
        raise


async def find_and_apply(page, brand):
    name = brand["name"]
    search_term = brand["search"]
    pillar = brand.get("pillar", "life")
    pitch = PITCH_MESSAGES.get(pillar, PITCH_MESSAGES["life"])

    logger.info("--------------------------------------------------")
    logger.info("Searching for: %s (pillar: %s)", name, pillar)

    # Navigate to Brand Discovery
    await page.goto(DISCOVERY_URL, timeout=35000, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)
    await page.screenshot(path=f"scratch/impact_discovery_{name.replace(' ', '_')}.png")

    # Search for brand
    try:
        search_box = page.locator('input[placeholder*="Search"], input[type="search"], input[name="query"]').first
        await search_box.wait_for(state="visible", timeout=8000)
        await search_box.fill(search_term)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(3000)
        logger.info("Searched for: %s", search_term)
    except Exception as e:
        logger.warning("Search box not found for %s: %s", name, e)
        return {"name": name, "pillar": pillar, "status": "search_failed"}

    # Find the brand result and click Apply/Request
    page_text = await page.locator("body").inner_text()
    if name.lower() not in page_text.lower() and search_term.lower() not in page_text.lower():
        logger.warning("Brand '%s' not found in search results.", name)
        await page.screenshot(path=f"scratch/impact_notfound_{name.replace(' ', '_')}.png")
        return {"name": name, "pillar": pillar, "status": "not_found"}

    # Try clicking Apply / Request to Join
    try:
        apply_btn = page.locator(
            'button:has-text("Apply"), button:has-text("Request"), a:has-text("Apply"), '
            'button:has-text("Join"), a:has-text("Request to Join")'
        ).first
        await apply_btn.wait_for(state="visible", timeout=6000)
        await apply_btn.click()
        await page.wait_for_timeout(3000)
        logger.info("Clicked Apply button for %s", name)
    except Exception as e:
        logger.warning("Apply button not found for %s: %s", name, e)
        await page.screenshot(path=f"scratch/impact_apply_{name.replace(' ', '_')}.png")
        return {"name": name, "pillar": pillar, "status": "apply_btn_not_found"}

    # Fill application form if a modal/page appears
    await page.screenshot(path=f"scratch/impact_form_{name.replace(' ', '_')}.png")
    try:
        msg_area = page.locator("textarea").first
        await msg_area.wait_for(state="visible", timeout=6000)
        await msg_area.fill(pitch)
        logger.info("Filled pitch for %s (%s)", name, pillar)
    except Exception:
        logger.info("No message textarea found for %s — may auto-apply.", name)

    # Submit
    try:
        submit = page.locator(
            'button[type="submit"], button:has-text("Submit"), button:has-text("Send Request")'
        ).first
        await submit.click()
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"scratch/impact_submitted_{name.replace(' ', '_')}.png")
        logger.info("✓ Application submitted for %s!", name)
        return {"name": name, "pillar": pillar, "status": "submitted"}
    except Exception as e:
        logger.error("Submit failed for %s: %s", name, e)
        return {"name": name, "pillar": pillar, "status": "submit_error"}


async def verify_via_api() -> list:
    """Check existing Impact campaigns via REST API."""
    if not ACCOUNT_SID or not AUTH_TOKEN:
        logger.warning("IMPACT_ACCOUNT_SID / IMPACT_AUTH_TOKEN not set — skipping API verify.")
        return []
    try:
        resp = httpx.get(
            f"https://api.impact.com/Mediapartners/{ACCOUNT_SID}/Campaigns",
            auth=(ACCOUNT_SID, AUTH_TOKEN),
            headers={"Accept": "application/json"},
            params={"PageSize": 50},
            timeout=12,
        )
        if resp.status_code == 200:
            campaigns = resp.json().get("Campaigns", [])
            logger.info("Impact active campaigns: %d", len(campaigns))
            for c in campaigns:
                logger.info("  ✅ [%s] %s — %s", c.get("Id"), c.get("Name"), c.get("AdvertiserName"))
            return campaigns
    except Exception as e:
        logger.warning("Impact API verify error: %s", e)
    return []


async def main():
    if not IMPACT_EMAIL or not IMPACT_PASSWORD:
        logger.error(
            "❌ IMPACT_EMAIL and IMPACT_PASSWORD not set in .env.local.\n"
            "   Add them and re-run:\n"
            "   IMPACT_EMAIL=your@email.com\n"
            "   IMPACT_PASSWORD=yourpassword"
        )
        return

    logger.info("Starting Impact.com batch applications — %d targets...", len(TARGET_BRANDS))
    Path("scratch").mkdir(exist_ok=True)
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

        for brand in TARGET_BRANDS:
            res = await find_and_apply(page, brand)
            results.append(res)
            await page.wait_for_timeout(2000)

        await browser.close()

    # API verification
    logger.info("==================================================")
    await verify_via_api()

    # Summary
    by_status: dict[str, list] = {}
    for r in results:
        by_status.setdefault(r["status"], []).append(r["name"])
    for status, names in by_status.items():
        logger.info("  %s: %d — %s", status, len(names), ", ".join(names))

    output_path = Path("scratch/impact_join_results.json")
    output_path.write_text(json.dumps(results, indent=2))
    logger.info("Batch complete. Results saved to %s", output_path)


if __name__ == "__main__":
    asyncio.run(main())
