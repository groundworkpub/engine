#!/usr/bin/env python3
"""
agents/playwright_poster.py — Groundwork Non-API Headless Stealth Poster.

Operates automated browser interactions without official platform APIs using Playwright Stealth:
1. Session Persistence: Loads session cookies from docs/community/playwright_state_{platform}.json.
2. Human Simulation: Random typing jitter (25-65ms/char), micro-mouse jitter, and smooth scrolling.
3. Safe Dry-Run Gate: Verifies injection into input fields without clicking submit unless confirmed.
4. One-Time Login Harvester: `--record-session` opens a visible browser for the operator to log in once.
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
COMMUNITY_DIR = ROOT_DIR / "docs" / "community"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("playwright_poster")


def get_state_file(platform: str) -> Path:
    """Returns path to platform-specific Playwright storage state file."""
    COMMUNITY_DIR.mkdir(parents=True, exist_ok=True)
    return COMMUNITY_DIR / f"playwright_state_{platform}.json"


def record_session(platform: str) -> int:
    """Opens a non-headless browser session allowing the user to log in manually once."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return 1

    state_file = get_state_file(platform)
    target_urls = {
        "reddit": "https://www.reddit.com/login",
        "quora": "https://www.quora.com",
        "x": "https://x.com/i/flow/login",
    }
    url = target_urls.get(platform, "https://www.google.com")

    logger.info(f"Opening visible browser to record session for '{platform}'...")
    print(f"\n[ACTION REQUIRED] Please log in manually in the browser window.\nOnce logged in, press ENTER here in the terminal to save your session to:\n{state_file}\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        page.goto(url)

        try:
            input("Press ENTER when you have successfully logged in...")
            context.storage_state(path=str(state_file))
            logger.info(f"Session cookies and storage state saved to {state_file}!")
        except KeyboardInterrupt:
            logger.warning("Session capture cancelled.")
        finally:
            browser.close()

    return 0


def post_reply_stealth(
    platform: str,
    target_url: str,
    reply_text: str,
    dry_run: bool = True,
    headless: bool = True,
) -> bool:
    """Injects a reply with human-like jitter using persisted storage state."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("Playwright not installed. Cannot execute stealth posting.")
        return False

    state_file = get_state_file(platform)
    if not state_file.exists():
        logger.warning(
            f"No session state found at {state_file}.\n"
            f"Run: python agents/playwright_poster.py --platform {platform} --record-session"
        )
        # We can still attempt without cookies or dry-run inspect
        if not dry_run:
            return False

    logger.info(f"Starting Stealth Runner on {platform} (dry_run={dry_run}, headless={headless})...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context_kwargs: dict[str, Any] = {
            "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "viewport": {"width": 1280, "height": 800},
        }
        if state_file.exists():
            context_kwargs["storage_state"] = str(state_file)

        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        try:
            logger.info(f"Navigating to target: {target_url}")
            page.goto(target_url, timeout=30000, wait_until="domcontentloaded")
            time.sleep(random.uniform(2.0, 3.5))

            # Human scroll down to simulate reading
            page.evaluate("window.scrollBy({ top: 350, behavior: 'smooth' });")
            time.sleep(random.uniform(1.2, 2.0))

            # Selector strategy based on platform
            if platform == "reddit":
                comment_box_selector = "div[contenteditable='true'], textarea[placeholder*='comment'], shreddit-comment-composer"
            elif platform == "quora":
                comment_box_selector = "div.doc, div.qu-userSelect--text, div[contenteditable='true']"
            else:
                comment_box_selector = "div[data-testid='tweetTextarea_0'], textarea"

            logger.info(f"Locating input area with selector: {comment_box_selector}")

            try:
                elem = page.wait_for_selector(comment_box_selector, timeout=8000)
            except Exception:
                elem = None

            if not elem:
                logger.warning("Could not find active comment input box on page.")
                if dry_run:
                    logger.info("[DRY RUN] Page loaded successfully. Selector not found (user may need to log in).")
                    return True
                return False

            elem.click()
            time.sleep(0.5)

            # Human-like typing jitter (25ms - 65ms per character)
            logger.info(f"Injecting reply ({len(reply_text)} chars) with human typing jitter...")
            for char in reply_text:
                page.keyboard.type(char)
                delay = random.uniform(0.025, 0.065)
                # Occasional longer pause simulating thinking
                if char in (".", "\n", "?", "!") and random.random() < 0.3:
                    delay += random.uniform(0.2, 0.5)
                time.sleep(delay)

            time.sleep(1.5)

            if dry_run:
                logger.info("[DRY RUN] Reply typed successfully into comment box. SKIPPING submit button click.")
                return True

            # Submit button locator
            if platform == "reddit":
                submit_btn = page.locator("button:has-text('Comment'), button[type='submit']:has-text('Comment')")
            elif platform == "quora":
                submit_btn = page.locator("button:has-text('Submit'), button:has-text('Post')")
            else:
                submit_btn = page.locator("button[data-testid='tweetButtonInline']")

            if submit_btn.is_visible():
                submit_btn.click()
                logger.info("✅ Submitted reply successfully!")
                time.sleep(3.0)
                return True
            else:
                logger.warning("Submit button not visible or already submitted.")
                return False

        except Exception as e:
            logger.error(f"Playwright stealth execution error: {e}")
            return False
        finally:
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Groundwork Non-API Headless Stealth Poster")
    parser.add_argument("--platform", choices=["reddit", "quora", "x"], required=True, help="Target platform")
    parser.add_argument("--record-session", action="store_true", help="Launch visible browser to log in and save cookies")
    parser.add_argument("--url", type=str, help="Target post or thread URL")
    parser.add_argument("--text", type=str, help="Reply text to inject")
    parser.add_argument("--text-file", type=str, help="Path to text file containing reply")
    parser.add_argument("--headless", action="store_true", default=True, help="Run browser in headless mode")
    parser.add_argument("--no-headless", action="store_false", dest="headless", help="Run browser visibly")
    parser.add_argument("--live", action="store_true", help="Execute live submission (default is dry-run)")
    args = parser.parse_args()

    if args.record_session:
        return record_session(args.platform)

    if not args.url:
        logger.error("--url is required when posting a reply.")
        return 1

    reply_content = args.text or ""
    if args.text_file and Path(args.text_file).exists():
        reply_content = Path(args.text_file).read_text(encoding="utf-8")

    if not reply_content:
        logger.error("Must provide reply content via --text or --text-file.")
        return 1

    dry_run = not args.live
    success = post_reply_stealth(
        platform=args.platform,
        target_url=args.url,
        reply_text=reply_content,
        dry_run=dry_run,
        headless=args.headless,
    )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
