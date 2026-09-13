#!/usr/bin/env python3
"""
agents/community_manager.py — Autonomous Local Community Manager for Groundwork.
Leverages authenticated Playwright browser sessions to manage:
1. Reddit (r/GroundworkDecisions): create posts, upload flairs, reply to threads.
2. Quora (groundwork.quora.com): post answers, update about section.
3. X (Twitter @gworkycom): post tweets, reply with sniper insights.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, async_playwright

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("community_manager")

ROOT_DIR = Path(__file__).resolve().parent.parent


def load_env() -> dict[str, str]:
    """Loads environment credentials without overriding system env."""
    env: dict[str, str] = {}
    env_file = ROOT_DIR / ".env.local"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("\"'")
    return env


ENV = load_env()


async def get_authenticated_context(browser: Any, platform: str) -> BrowserContext:
    """Creates a browser context pre-loaded with authenticated platform cookies."""
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        viewport={"width": 1440, "height": 900},
        locale="en-US",
        timezone_id="America/New_York",
    )
    await context.add_init_script("delete Object.getPrototypeOf(navigator).webdriver")

    if platform == "reddit":
        await context.add_cookies([
            {
                "name": "reddit_session",
                "value": ENV.get("REDDIT_SESSION", ""),
                "domain": ".reddit.com",
                "path": "/",
                "secure": True,
            },
            {
                "name": "token_v2",
                "value": ENV.get("REDDIT_TOKEN_V2", ""),
                "domain": ".reddit.com",
                "path": "/",
                "secure": True,
            },
        ])
    elif platform == "x":
        await context.add_cookies([
            {
                "name": "auth_token",
                "value": ENV.get("TWITTER_AUTH_TOKEN", ""),
                "domain": ".x.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
            },
            {
                "name": "ct0",
                "value": ENV.get("TWITTER_CT0", ""),
                "domain": ".x.com",
                "path": "/",
                "secure": True,
            },
        ])
    elif platform == "quora":
        await context.add_cookies([
            {
                "name": "m-b",
                "value": ENV.get("QUORA_M_B", ""),
                "domain": ".quora.com",
                "path": "/",
                "secure": True,
            },
            {
                "name": "m-lat",
                "value": ENV.get("QUORA_M_LAT", ""),
                "domain": ".quora.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
            },
            {
                "name": "m-uid",
                "value": ENV.get("QUORA_M_UID", ""),
                "domain": ".quora.com",
                "path": "/",
                "secure": True,
            },
        ])

    return context


async def post_to_reddit(title: str, body: str, subreddit: str = "GroundworkDecisions", headless: bool = True) -> bool:
    """Posts a new discussion/guide to Reddit using official session cookies."""
    logger.info(f"Posting to r/{subreddit}: '{title[:50]}...'")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        context = await get_authenticated_context(browser, "reddit")
        page = await context.new_page()

        submit_url = f"https://www.reddit.com/r/{subreddit}/submit"
        await page.goto(submit_url, wait_until="domcontentloaded", timeout=40000)
        await page.wait_for_timeout(3000)

        # 1. Fill Title
        title_loc = page.locator('#post-composer__title textarea, post-composer-title textarea, textarea[placeholder*="Title" i]').first
        if await title_loc.is_visible():
            await title_loc.fill(title)
        else:
            await page.locator('#post-composer__title, post-composer-title').first.click()
            await page.keyboard.type(title, delay=10)

        await page.wait_for_timeout(1000)

        # 2. Switch to Native Markdown Mode
        await page.evaluate("""() => {
            const composer = document.querySelector("shreddit-composer");
            if (composer && typeof composer.onSwitchToMarkdown === "function") {
                composer.onSwitchToMarkdown();
            }
        }""")
        await page.wait_for_timeout(1000)

        # 3. Fill Native Markdown Textarea (pierces shadow DOM)
        md_ta = page.locator("shreddit-composer shreddit-markdown-composer textarea").first
        if await md_ta.is_visible():
            await md_ta.fill(body)
        else:
            # Fallback to direct keyboard typing if markdown composer not found
            body_el = page.locator('#post-composer_bodytext, shreddit-composer').first
            await body_el.click()
            await page.keyboard.insert_text(body)

        await page.wait_for_timeout(1500)

        # 4. Click Submit / Post Button
        post_btn = page.locator('#submit-post-button button, button:has-text("Post")').first
        if await post_btn.is_visible() and not await post_btn.is_disabled():
            await post_btn.click()
            await page.wait_for_timeout(7000)
            cur_url = page.url
            logger.info(f"Successfully posted to r/{subreddit}! Current URL: {cur_url}")
            await browser.close()
            return True
        else:
            logger.error("Could not locate active Post button on Reddit submit page.")
            await browser.close()
            return False


async def post_to_x(text: str, headless: bool = True) -> bool:
    """Posts a tweet to X using official session cookies."""
    logger.info(f"Posting tweet to X: '{text[:60]}...'")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        context = await get_authenticated_context(browser, "x")
        page = await context.new_page()

        await page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        editor_sel = 'div[contenteditable="true"][data-testid="tweetTextarea_0"]'
        await page.wait_for_selector(editor_sel, timeout=15000)
        await page.click(editor_sel)
        await page.evaluate(f"document.execCommand('insertText', false, {json.dumps(text)})")
        await page.wait_for_timeout(2000)

        btn = page.locator('[data-testid="tweetButton"]').first
        if await btn.is_visible():
            await btn.click()
            await page.wait_for_timeout(4000)
            logger.info("Successfully posted tweet to X!")
            await browser.close()
            return True
        else:
            logger.error("Could not locate Tweet button on X.")
            await browser.close()
            return False


async def post_to_quora_space(text: str, headless: bool = True) -> bool:
    """Posts a post or guide into Groundwork Quora Space."""
    logger.info(f"Posting to Groundwork Quora Space: '{text[:60]}...'")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        context = await get_authenticated_context(browser, "quora")
        page = await context.new_page()

        await page.goto("https://groundwork.quora.com/", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        # 1. Click "Post in Groundwork" trigger (.puppeteer_test_tribe_composer)
        trigger = page.locator(".puppeteer_test_tribe_composer").first
        if await trigger.is_visible():
            await trigger.click()
            await page.wait_for_timeout(2000)

            # 2. Type text into div.doc
            doc = page.locator("div.doc").first
            if await doc.is_visible():
                await doc.click()
                await page.wait_for_timeout(500)
                await page.keyboard.type(text, delay=2)
                await page.wait_for_timeout(1000)

                # 3. Click Next button
                next_btn = page.locator("button:has-text('Next')").first
                if await next_btn.is_visible() and not await next_btn.is_disabled():
                    await next_btn.click()
                    await page.wait_for_timeout(2000)

                # 4. Click Final Post button
                post_btn = page.locator("button:has-text('Post')").first
                if await post_btn.is_visible() and not await post_btn.is_disabled():
                    await post_btn.click()
                    await page.wait_for_timeout(5000)
                    logger.info("Successfully posted to Groundwork Quora Space!")
                    await browser.close()
                    return True

        logger.warning("Quora composer not triggered or failed to submit.")
        await browser.close()
        return False


INITIAL_REDDIT_POSTS = [
    {
        "title": "Welcome to Groundwork Decisions: The Unvarnished Math Behind Smarter Financial & Home Choices (Read First)",
        "body": """### Welcome to r/GroundworkDecisions

We started this community for a simple reason: **most high-stakes financial, home, and modern living decisions are loaded with asymmetric information and hidden markups.**

Whether it's an HVAC contractor quoting $18,500 for a 3-ton heat pump, a mortgage broker advertising "no-cost refinance" with an amortized points catch, or solar installers presenting 30-year projections with unrealistic utility escalation assumptions — people deserve to see the real numbers before signing.

---

### 🧮 What We Do Here:
1. **Quote & Rate Sanity Checks:** Post your real contractor quotes, loan estimates, or insurance renewal notices (with personal info redacted). We break down wholesale equipment costs, fair labor hours, and contractor margins.
2. **Open Methodology Calculators:** We build and maintain 100% free, open decision models at [gworky.com](https://gworky.com).
   - [Mortgage Refinance Break-Even Calculator](https://gworky.com/tools/mortgage-refinance)
   - [HVAC Heat Pump Replacement Benchmark](https://gworky.com/tools/heat-pump-roi-calculator)
   - [NEM 3.0 Solar & Battery Payback Model](https://gworky.com/tools/solar-roi-calculator)
   - [High-Yield Savings & Cash Compound Velocity](https://gworky.com/tools/hysa-compound-interest)
   - [EV vs Hybrid vs Gas Total Cost of Ownership](https://gworky.com/tools/ev-vs-gas-calculator)

---

### 🛡️ Community Rules:
1. **Math & Evidence First:** Back claims with tangible numbers, wholesale benchmarks, or verifiable terms.
2. **Include Context on Quotes:** When asking for a quote audit, always include your state/climate zone, square footage, equipment SEER2/tonnage, and fee breakdown.
3. **Zero Tolerance for Sales Solicitations:** Contractors or lenders soliciting business in comments or DMs will be permanently banned.

Drop your questions and quotes below. Let's crunch the math!"""
    },
    {
        "title": "The Unvarnished Anatomy of an $18,500 HVAC Quote: Equipment Wholesale vs. Labor vs. Dealer Fees",
        "body": """If you have received an HVAC replacement quote recently, you have probably experienced sticker shock. Quotes for a basic 3-ton inverter heat pump system routinely cross $16,000–$22,000 in 2026.

Here is what the actual unvarnished cost breakdown looks like behind the contractor invoice:

### 1. Wholesale Equipment Cost (~$4,800 – $6,500)
For a quality 16–18 SEER2 variable-capacity inverter heat pump with matched air handler (e.g., Bosch IDS, Mitsubishi, or Carrier Comfort series), contractor wholesale cost from distributors is roughly **$4,800 to $6,500**. 

### 2. Fair Labor Hours & Billable Rate (~$2,400 – $3,600)
A standard direct change-out requires **2 certified technicians for 1 to 1.5 days (16 to 24 total man-hours)**.
Even at a healthy, sustainable commercial shop rate of **$140 to $165 per billable man-hour** (which covers technician wages, insurance, van depreciation, and overhead), fair labor sits between **$2,240 and $3,960**.

### 3. Ancillaries & Permitting (~$800 – $1,200)
Pad, electrical whip/disconnect, nitrogen purge, new refrigerant line set flush, and municipal permit add **$800 to $1,200**.

### The Math Verdict:
- **True Contractor Basis:** $4,800 + $2,400 + $800 = **~$8,000 – $11,500**.
- **A 50% Gross Margin Quote:** ~$12,000 to $14,500.
- When you see a quote at **$18,500+**, you are paying either a **65%+ gross margin** or financing dealer fees.

### 🚩 The "0% APR" Dealer Trap:
If a contractor offers "0% financing for 60 months", lenders (like GreenSky, GoodLeap, or Synchrony) charge the contractor an upfront **Dealer Fee of 18% to 28%** of the loan value. The contractor simply inflates the quote from $13,500 to $18,500 to pay the bank. **Always demand the cash price.**

You can benchmark your exact zip code and tonnage using the open methodology model at [gworky.com/tools/heat-pump-roi-calculator](https://gworky.com/tools/heat-pump-roi-calculator). What quotes are you seeing in your area?"""
    },
    {
        "title": "Why a 50 bps Mortgage Rate Drop Often Loses Money (The Amortization Reset Trap)",
        "body": """With rates fluctuating, many homeowners who took out loans at 6.75%–7.25% are being targeted with refinance marketing claiming *"Save $250/month by refinancing to 6.25%!"*

Before paying thousands in upfront friction, run this math checkpoint:

### 1. The Friction Hurdle
Closing costs (origination, appraisal, title, recording, points) typically total **1.5% to 2.5% of your loan balance**. On a $450,000 mortgage, that is **$6,750 to $11,250 in cash or added principal**.

### 2. The True Break-Even Horizon
If your monthly principal & interest payment drops by $210:
- $8,400 closing costs ÷ $210/mo savings = **40 months (3.3 years) just to break even**.
- If you move, sell, or refinance again before month 40, **you lost money**.

### 3. The 30-Year Amortization Reset
If you are 4 years into a 30-year mortgage, your monthly payment has already started shifting toward principal. If you refinance into a brand-new 30-year loan to lower the payment:
- You wipe out the 4 years of progress.
- The first 24–36 months of your new loan will be 80%+ pure interest again.
- You will pay significantly more in total lifetime interest unless you maintain your original remaining payoff timeline (26 years).

Audit your exact numbers and break-even month on an open amortization table before letting a lender pull your credit: [gworky.com/tools/mortgage-refinance](https://gworky.com/tools/mortgage-refinance)."""
    }
]


async def seed_reddit_community(headless: bool = True):
    """Publishes the 3 core starter posts to complete r/GroundworkDecisions setup."""
    logger.info("Starting initial seed of 3 foundation posts to r/GroundworkDecisions...")
    for idx, post in enumerate(INITIAL_REDDIT_POSTS, 1):
        logger.info(f"Seeding Post {idx}/3: {post['title'][:50]}...")
        success = await post_to_reddit(post["title"], post["body"], subreddit="GroundworkDecisions", headless=headless)
        if success:
            logger.info(f"Post {idx} published successfully!")
        else:
            logger.warning(f"Post {idx} encountered an issue during submission.")
        await asyncio.sleep(5)
    logger.info("Reddit foundation seeding completed!")


async def open_all_dashboards():
    """Opens all three platforms (Reddit, Quora, X) in an authenticated visible browser window."""
    logger.info("Opening all Groundwork Community dashboards in authenticated headed browser...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        # Create multi-cookie context with all credentials
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id="America/New_York",
        )
        await context.add_init_script("delete Object.getPrototypeOf(navigator).webdriver")

        cookies = []
        if ENV.get("REDDIT_SESSION"):
            cookies.extend([
                {"name": "reddit_session", "value": ENV["REDDIT_SESSION"], "domain": ".reddit.com", "path": "/", "secure": True},
                {"name": "token_v2", "value": ENV["REDDIT_TOKEN_V2"], "domain": ".reddit.com", "path": "/", "secure": True},
            ])
        if ENV.get("TWITTER_AUTH_TOKEN"):
            cookies.extend([
                {"name": "auth_token", "value": ENV["TWITTER_AUTH_TOKEN"], "domain": ".x.com", "path": "/", "secure": True, "httpOnly": True},
                {"name": "ct0", "value": ENV["TWITTER_CT0"], "domain": ".x.com", "path": "/", "secure": True},
            ])
        if ENV.get("QUORA_M_B"):
            cookies.extend([
                {"name": "m-b", "value": ENV["QUORA_M_B"], "domain": ".quora.com", "path": "/", "secure": True},
                {"name": "m-lat", "value": ENV["QUORA_M_LAT"], "domain": ".quora.com", "path": "/", "secure": True, "httpOnly": True},
                {"name": "m-uid", "value": ENV["QUORA_M_UID"], "domain": ".quora.com", "path": "/", "secure": True},
            ])
        await context.add_cookies(cookies)

        # Tab 1: Reddit
        page1 = await context.new_page()
        await page1.goto("https://www.reddit.com/r/GroundworkDecisions/")

        # Tab 2: Quora Space
        page2 = await context.new_page()
        await page2.goto("https://groundwork.quora.com/")

        # Tab 3: X Profile
        page3 = await context.new_page()
        await page3.goto("https://x.com/gworkycom")

        logger.info("All 3 dashboards open. Keeping browser active for interactive use (Ctrl+C to exit)...")
        while True:
            await asyncio.sleep(1)


def main():
    parser = argparse.ArgumentParser(description="Groundwork Local Community Browser Automation")
    parser.add_argument("--action", choices=["post-reddit", "post-x", "post-quora", "seed-reddit", "open-dashboards"], required=True)
    parser.add_argument("--title", type=str, default="")
    parser.add_argument("--text", type=str, default="")
    parser.add_argument("--headed", action="store_true", help="Launch browser in headed mode (visible window)")
    args = parser.parse_args()

    headless = not args.headed

    if args.action == "open-dashboards":
        asyncio.run(open_all_dashboards())
    elif args.action == "seed-reddit":
        asyncio.run(seed_reddit_community(headless=headless))
    elif args.action == "post-reddit":
        if not args.title or not args.text:
            print("Error: --title and --text are required for post-reddit.")
            sys.exit(1)
        asyncio.run(post_to_reddit(args.title, args.text, headless=headless))
    elif args.action == "post-x":
        if not args.text:
            print("Error: --text is required for post-x.")
            sys.exit(1)
        asyncio.run(post_to_x(args.text, headless=headless))
    elif args.action == "post-quora":
        if not args.text:
            print("Error: --text is required for post-quora.")
            sys.exit(1)
        asyncio.run(post_to_quora_space(args.text, headless=headless))


if __name__ == "__main__":
    main()
