#!/usr/bin/env python3
"""
agents/awin_create_opportunity.py — Groundwork Awin Opportunity Marketplace Automator

Publishes 3 strategic, high-converting partnership packages to Awin Opportunity Marketplace:
1. Home: Solar Battery & Energy Storage Evaluation ($250 Fixed / Blog)
2. Money: Prime Consumer Credit & Debt Optimization ($250 Fixed / Category Placement)
3. Tech: Digital Privacy & Security Software Scorecard ($200 Fixed / Blog)
"""

import asyncio
import json
import logging
from pathlib import Path
from playwright.async_api import async_playwright
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env.local")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("awin_opportunity")

PROFILE_DIR = Path(__file__).resolve().parent / ".affiliate_profile"
PUBLISHER_ID = os.getenv("AWIN_PUBLISHER_ID", "3081079")
AWIN_EMAIL = os.getenv("AWIN_EMAIL", os.getenv("AWIN_USER", ""))
AWIN_PASSWORD = os.getenv("AWIN_PASSWORD", os.getenv("AWIN_PASS", ""))

OPPORTUNITY_PACKAGES = [
    {
        "id_name": "home_solar",
        "pillar": "home",
        "title": "Residential Solar & Battery Storage Guide",
        "description": (
            "Comprehensive, independent hardware evaluation and calculator placement for residential solar battery "
            "backup and portable power systems. Feature your brand within Groundwork's Solar Battery Payback Guide "
            "(https://gworky.com/home), reaching homeowners aged 35-48 at the bottom of the purchasing funnel. "
            "Includes interactive calculator integration, FTC-compliant editorial disclosure, and long-term search placement."
        ),
        "promotional_type": "3:3532677",
        "suggested_start_type": "1",  # Ongoing
        "opportunity_type": "blog",   # Sponsored Content/Blog Post
        "sectors": ["sectors-2"],     # Retail & Shopping
        "commission_type": "fixed",
        "commission_value": "250",
    },
    {
        "id_name": "money_finance",
        "pillar": "money",
        "title": "Consumer Credit & Debt Optimization Guide",
        "description": (
            "Contextual evaluation and interactive tool placement within Groundwork's evidence-based personal finance "
            "pillar (https://gworky.com/money). Targets prime borrowers and debt consolidators actively comparing credit "
            "monitoring, debt relief, and refinancing utilities. Backed by CFPB and Federal Reserve data benchmarks."
        ),
        "promotional_type": "3:3532677",
        "suggested_start_type": "1",  # Ongoing
        "opportunity_type": "cat_plac", # Category Placement
        "sectors": ["sectors-1"],     # Finance & Insurance
        "commission_type": "fixed",
        "commission_value": "250",
    },
    {
        "id_name": "tech_security",
        "pillar": "tech",
        "title": "Digital Privacy & Security Software Matrix",
        "description": (
            "Rigorous benchmark matrix and threat-model comparison within Groundwork's Tech pillar "
            "(https://gworky.com/tech). Directly engages decision-makers, remote workers, and privacy-focused "
            "professionals evaluating VPNs, password managers, and digital security tools. Complete with interactive scorecards."
        ),
        "promotional_type": "3:3532677",
        "suggested_start_type": "1",  # Ongoing
        "opportunity_type": "blog",   # Sponsored Content/Blog Post
        "sectors": ["sectors-3"],     # Telcos & Services
        "commission_type": "fixed",
        "commission_value": "200",
    },
]



async def ensure_authenticated(page):
    logger.info("Verifying authentication...")
    await page.goto("https://ui.awin.com/login", timeout=40000, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    try:
        accept_btn = page.locator("#accept, button:has-text('Accept all')").first
        if await accept_btn.is_visible():
            await accept_btn.click()
            await page.wait_for_timeout(1000)
    except Exception:
        pass

    if "id.awin.com" in page.url or "login" in page.url or "prelogin" in page.url:
        logger.info("Session expired. Authenticating via Auth0...")
        try:
            email_field = page.locator('#email, #username, input[name="username"], input[type="email"]').first
            if await email_field.is_visible():
                await email_field.fill(AWIN_EMAIL)
                await page.locator('button[type="submit"], #login, button:has-text("Continue")').first.click()
                await page.wait_for_timeout(3000)
        except Exception as e:
            logger.warning("Email step note: %s", e)

        try:
            pwd_field = page.locator('#password, input[name="password"], input[type="password"]:not([hidden])').first
            await pwd_field.wait_for(state="visible", timeout=12000)
            await pwd_field.fill(AWIN_PASSWORD)
            await page.locator('button[type="submit"], button:has-text("Continue"), button:has-text("Log in")').first.click()
            await page.wait_for_timeout(6000)
        except Exception as e:
            logger.warning("Password step note: %s", e)

    # Wait for full dashboard redirect before navigating
    try:
        await page.wait_for_url("**/publisher/**", timeout=15000)
    except Exception:
        pass

    logger.info("Landed on URL: %s | Title: %s", page.url, await page.title())


async def publish_opportunity(page, opp: dict):
    create_url = f"https://ui.awin.com/awin/affiliate/{PUBLISHER_ID}/opportunity-market/create"
    logger.info("==================================================")
    logger.info("Publishing Opportunity: %s", opp["title"])
    await page.goto(create_url, timeout=40000, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    # 1. Promotional Type (https://gworky.com)
    try:
        promo_sel = page.locator('select[name="promotionalType"], select#promotionalType').first
        await promo_sel.select_option(value=opp["promotional_type"])
        logger.info("  ✓ Selected Promotional Type: %s", opp["promotional_type"])
    except Exception as e:
        logger.warning("  ⚠ Select promotionalType note: %s", e)

    # 2. Opportunity Title
    try:
        title_input = page.locator("input#title, input[name='title']").first
        await title_input.fill(opp["title"])
        logger.info("  ✓ Filled title: %s", opp["title"][:40])
    except Exception as e:
        logger.error("  ❌ Failed to fill title: %s", e)
        return {"id": opp["id_name"], "status": "error_title"}

    # 3. Description
    try:
        desc_input = page.locator("textarea#description, textarea[name='description']").first
        await desc_input.fill(opp["description"])
        logger.info("  ✓ Filled description (%d chars)", len(opp["description"]))
    except Exception as e:
        logger.error("  ❌ Failed to fill description: %s", e)
        return {"id": opp["id_name"], "status": "error_desc"}

    # 4. Suggested Start Date (Ongoing = "1")
    try:
        start_sel = page.locator('select[name="suggestedstarttype"], select#suggestedstarttype').first
        await start_sel.select_option(value=opp["suggested_start_type"])
        logger.info("  ✓ Set Suggested Start Type to Ongoing ('1')")
    except Exception as e:
        logger.warning("  ⚠ select suggestedstarttype note: %s", e)

    # 5. Opportunity Type (blog or cat_plac)
    try:
        opp_sel = page.locator('select[name="opportunityType"], select#opportunityType').first
        await opp_sel.select_option(value=opp["opportunity_type"])
        logger.info("  ✓ Selected Opportunity Type: %s", opp["opportunity_type"])
    except Exception as e:
        logger.warning("  ⚠ select opportunityType note: %s", e)

    # 6. Sectors Checkboxes
    for sector_id in opp["sectors"]:
        try:
            cb = page.locator(f"#{sector_id}")
            if await cb.is_visible() and not await cb.is_checked():
                await cb.check(force=True)
                logger.info("  ✓ Checked sector: %s", sector_id)
        except Exception as e:
            logger.warning("  ⚠ Check sector %s note: %s", sector_id, e)

    # 7. Price Terms: Commission Type and Amount
    try:
        comm_type_sel = page.locator('select[name="commissionType"], select#commissionType').first
        await comm_type_sel.select_option(value=opp["commission_type"])
        logger.info("  ✓ Set Commission Type: %s", opp["commission_type"])
    except Exception as e:
        logger.warning("  ⚠ select commissionType note: %s", e)

    try:
        # Awin uses jQuery currency mask: must set both visible placeholder input and hidden #commissionValue
        # and dispatch input, change, and blur events
        val_str = str(opp["commission_value"]) + ".00"
        await page.evaluate("""(val) => {
            const inputs = document.querySelectorAll('input[placeholder="Amount"]');
            inputs.forEach(inp => {
                inp.value = val;
                inp.dispatchEvent(new Event('input', { bubbles: true }));
                inp.dispatchEvent(new Event('change', { bubbles: true }));
                inp.dispatchEvent(new Event('blur', { bubbles: true }));
            });
            const comm = document.getElementById('commissionValue');
            if (comm) {
                comm.value = val;
                comm.dispatchEvent(new Event('input', { bubbles: true }));
                comm.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }""", val_str)
        logger.info("  ✓ Dispatched JS events to set Commission Amount: $%s", val_str)
    except Exception as e:
        logger.warning("  ⚠ Fill commissionValue note: %s", e)

    # Screenshot before clicking publish
    scratch_dir = Path("scratch")
    scratch_dir.mkdir(exist_ok=True)
    before_shot = scratch_dir / f"awin_opp_filled_{opp['id_name']}.png"
    await page.screenshot(path=str(before_shot))
    logger.info("  📸 Saved filled form screenshot to %s", before_shot)

    # 8. Click Publish Button
    try:
        publish_btn = page.locator('#saveBtn, button:has-text("Publish"), input[value="Publish"]').first
        logger.info("  Clicking Publish button...")
        await publish_btn.click(force=True)
        await page.wait_for_timeout(5000)

        after_shot = scratch_dir / f"awin_opp_published_{opp['id_name']}.png"
        await page.screenshot(path=str(after_shot))
        logger.info("  📸 Saved after-publish screenshot to %s", after_shot)
        
        # Check for success message or validation error
        body_text = await page.inner_text("body")
        if "successfully saved" in body_text or "Opportunity Marketplace" in await page.title():
            logger.info("  ✅ Successfully published Opportunity: %s", opp["title"])
            return {"id": opp["id_name"], "status": "published", "title": opp["title"]}
        else:
            logger.warning("  ⚠ Check publish outcome for %s (see %s)", opp["title"], after_shot)
            return {"id": opp["id_name"], "status": "submitted_unconfirmed", "title": opp["title"]}
    except Exception as e:
        logger.error("  ❌ Failed clicking publish button: %s", e)
        return {"id": opp["id_name"], "status": "error_publish"}


async def main():
    logger.info("Starting Groundwork Awin Opportunity Marketplace Automation (%d packages)...", len(OPPORTUNITY_PACKAGES))
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            viewport={"width": 1366, "height": 768},
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()
        await ensure_authenticated(page)

        # Check existing opportunities in market list to avoid duplicates
        await page.goto(f"https://ui.awin.com/awin/affiliate/{PUBLISHER_ID}/opportunity-market", timeout=40000, wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)
        existing_content = await page.content()

        for opp in OPPORTUNITY_PACKAGES:
            if opp["title"] in existing_content:
                logger.info("⏩ Opportunity already exists live: %s", opp["title"])
                results.append({"id": opp["id_name"], "status": "already_live", "title": opp["title"]})
                continue

            res = await publish_opportunity(page, opp)
            results.append(res)
            await page.wait_for_timeout(3000)

        # Final verification screenshot
        await page.goto(f"https://ui.awin.com/awin/affiliate/{PUBLISHER_ID}/opportunity-market", timeout=40000, wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)
        await page.screenshot(path="scratch/awin_final_3_opportunities.png", full_page=True)

        await browser.close()

    result_file = Path("scratch/awin_opp_publish_results.json")
    result_file.write_text(json.dumps(results, indent=2))
    logger.info("==================================================")
    logger.info("Opportunity Publication Summary:")
    for r in results:
        logger.info("  %s: %s", r.get("status"), r.get("title", r.get("id")))
    logger.info("Full results written to %s", result_file)


if __name__ == "__main__":
    asyncio.run(main())
