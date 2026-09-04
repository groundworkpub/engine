#!/usr/bin/env python3
"""
ghost_journey_engine.py
=======================
Advanced Behavioral Signal Simulation & Algorithmic Signal Manipulation Engine
(The "Ghost User" Architecture) — Groundwork Platform.

Simulates authentic organic user search behavior on Google SERPs to deliver
powerful behavioral trust signals (NavBoost / RankBrain):
  1. Residential / Stealth Proxy Egress: Avoids datacenter bot detection.
  2. Google Search Entry & Human Typing: Jitter keystrokes, regional TLDs, consent bypass.
  3. Competitor Pogo-Sticking: Clicks competitor result, dwells 5-12s, bounces back to SERP.
  4. Groundwork Discovery & High Dwell: Clicks gworky.com, dwells 45-180s, scrolls smoothly.
  5. Interactive Conversion: Manipulates calculator sliders, form inputs, emotion buttons.
  6. Terminal Search Satisfaction: Never navigates back to Google (intent fully satisfied).
  7. Strict Zero Ad-Fraud: 100% blocks all ad networks (AdSense/DoubleClick/Mediavine).

Single Source of Truth: docs/research/seo.md (§3 Module C)
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import sys
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from browser_stealth import (
        AD_BLOCK_DOMAINS,
        ANALYTICS_DOMAINS,
        build_stealth_script,
        domain_is_blocked,
        stealth_launch_args,
    )
except ImportError:
    from agents.browser_stealth import (
        AD_BLOCK_DOMAINS,
        ANALYTICS_DOMAINS,
        build_stealth_script,
        domain_is_blocked,
        stealth_launch_args,
    )

logger = logging.getLogger("ghost_journey")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] ghost_journey: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# ── Target Competitors to Pogo-Stick Against (per Pillar) ─────────────────────
COMPETITOR_BENCHMARKS: Dict[str, List[str]] = {
    "money": [
        "bankrate.com", "nerdwallet.com", "smartasset.com", "investopedia.com",
        "fool.com", "creditkarma.com", "thebalance.com", "lendingtree.com"
    ],
    "body": [
        "healthline.com", "webmd.com", "medicalnewstoday.com", "examine.com",
        "verywellhealth.com", "mayoclinic.org", "health.com"
    ],
    "home": [
        "energysage.com", "sunrun.com", "solarreviews.com", "bobvila.com",
        "thisoldhouse.com", "angi.com", "thumbtack.com"
    ],
    "life": [
        "legalzoom.com", "rocketlawyer.com", "tripadvisor.com", "nomadicmatt.com",
        "edmunds.com", "kbb.com", "thepointsguy.com"
    ],
    "tech": [
        "tomsguide.com", "wirecutter.com", "rtings.com", "cnet.com",
        "theverge.com", "zdnet.com", "techradar.com"
    ],
}


@dataclass
class GhostPersona:
    name: str
    geo_region: str  # US, UK, AU
    google_tld: str  # google.com, google.co.uk, google.com.au
    user_agent: str
    sec_ch_ua: str
    viewport_width: int
    viewport_height: int
    timezone: str
    accept_language: str


TIER1_PERSONAS: List[GhostPersona] = [
    GhostPersona(
        name="Elena_Desktop_US",
        geo_region="US",
        google_tld="https://www.google.com",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        sec_ch_ua='"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
        viewport_width=1440,
        viewport_height=900,
        timezone="America/New_York",
        accept_language="en-US,en;q=0.9",
    ),
    GhostPersona(
        name="Marcus_Windows_US",
        geo_region="US",
        google_tld="https://www.google.com",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        sec_ch_ua='"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
        viewport_width=1920,
        viewport_height=1080,
        timezone="America/Chicago",
        accept_language="en-US,en;q=0.9",
    ),
    GhostPersona(
        name="Oliver_Desktop_UK",
        geo_region="UK",
        google_tld="https://www.google.co.uk",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
        sec_ch_ua='"Chromium";v="127", "Not;A=Brand";v="24", "Google Chrome";v="127"',
        viewport_width=1440,
        viewport_height=900,
        timezone="Europe/London",
        accept_language="en-GB,en;q=0.9",
    ),
    GhostPersona(
        name="Liam_Desktop_AU",
        geo_region="AU",
        google_tld="https://www.google.com.au",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        sec_ch_ua='"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
        viewport_width=1536,
        viewport_height=864,
        timezone="Australia/Sydney",
        accept_language="en-AU,en-US;q=0.9,en;q=0.8",
    ),
]


class HumanBehavior:
    """Physics-based human interaction generators."""

    @staticmethod
    async def type_with_jitter(element: Any, text: str) -> None:
        """Types text into an element with human-like delays and occasional typo corrections."""
        for i, char in enumerate(text):
            # 3% chance of typo
            if random.random() < 0.03 and char.isalpha():
                wrong_char = chr(ord(char) + 1 if ord(char) < ord('z') else ord('a'))
                await element.type(wrong_char, delay=random.randint(60, 140))
                await asyncio.sleep(random.uniform(0.1, 0.25))
                await element.press("Backspace")
                await asyncio.sleep(random.uniform(0.08, 0.18))

            delay_ms = random.randint(45, 160)
            if char == " ":
                delay_ms += random.randint(50, 120)  # Pause between words
            await element.type(char, delay=delay_ms)

    @staticmethod
    async def smooth_scroll(page: Any, target_scroll_y: int, steps: int = 8) -> None:
        """Smoothly scrolls the page down with variable deceleration."""
        current_y = await page.evaluate("window.scrollY || 0")
        distance = target_scroll_y - current_y
        if distance <= 0:
            return

        for i in range(1, steps + 1):
            fraction = (1 - (1 - (i / steps)) ** 2)  # Ease out quadratic
            step_y = current_y + int(distance * fraction)
            await page.evaluate(f"window.scrollTo(0, {step_y});")
            await asyncio.sleep(random.uniform(0.15, 0.35))


class GhostJourneyEngine:
    """Executes full-DOM SERP Pogo-Sticking and Terminal Satisfaction journeys."""

    def __init__(
        self,
        proxy_url: Optional[str] = None,
        allow_analytics: bool = True,
        engine: str = "playwright",
    ):
        self.proxy_url = proxy_url
        self.allow_analytics = allow_analytics
        self.engine = engine

    def _resolve_proxy(self) -> Optional[str]:
        """Resolves active proxy from env or args."""
        if self.proxy_url:
            return self.proxy_url
        # Check residential proxy or Cloudflare egress worker
        for env_var in [
            "RESIDENTIAL_PROXY_URL",
            "DATAIMPULSE_PROXY_URL",
            "WEBSHARE_PROXY_URL",
            "CLOUDFLARE_EGRESS_WORKER_URL",
        ]:
            val = os.environ.get(env_var, "").strip()
            if val:
                return val
        return None

    async def execute_journey(
        self,
        target_keyword: str,
        target_url: str,
        pillar: str = "money",
        pogo_competitor: bool = True,
        min_dwell_seconds: int = 45,
        max_dwell_seconds: int = 120,
    ) -> Dict[str, Any]:
        """Executes the full 6-phase Ghost User journey.

        Phases:
          1. Navigate to Google SERP
          2. Type keyword with jitter
          3. Click competitor, dwell briefly, bounce back (Pogo-sticking)
          4. Click Groundwork target result
          5. High dwell, smooth reading scroll, interact with widgets/forms
          6. Terminate session without returning to Google
        """
        persona = random.choice(TIER1_PERSONAS)
        effective_proxy = self._resolve_proxy()
        session_id = f"ghost_{uuid.uuid4().hex[:10]}"

        telemetry: Dict[str, Any] = {
            "session_id": session_id,
            "keyword": target_keyword,
            "target_url": target_url,
            "pillar": pillar,
            "persona": persona.name,
            "geo_region": persona.geo_region,
            "proxy_used": bool(effective_proxy),
            "pogo_competitor_domain": None,
            "pogo_dwell_seconds": 0,
            "groundwork_dwell_seconds": 0,
            "scroll_depth_percent": 0,
            "interactions": [],
            "status": "pending",
            "error": None,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        logger.info(
            f"🕵️ Initializing Ghost User [{session_id}] | Keyword: '{target_keyword}' | Region: {persona.geo_region}"
        )

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            telemetry["status"] = "error"
            telemetry["error"] = "Playwright not installed in environment"
            logger.error("[-] Playwright not available.")
            return telemetry

        async with async_playwright() as p:
            launch_kwargs: Dict[str, Any] = {
                "headless": True,
                "args": stealth_launch_args(),
            }
            if effective_proxy:
                launch_kwargs["proxy"] = {"server": effective_proxy}

            browser = await p.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                user_agent=persona.user_agent,
                viewport={"width": persona.viewport_width, "height": persona.viewport_height},
                locale=persona.accept_language.split(",")[0],
                timezone_id=persona.timezone,
                extra_http_headers={
                    "Sec-Ch-Ua": persona.sec_ch_ua,
                    "Accept-Language": persona.accept_language,
                },
            )

            # Inject CDP Stealth Script
            await context.add_init_script(build_stealth_script(persona))
            page = await context.new_page()

            # Strict AdSense & IVT Zero-Fraud Firewall
            async def _ad_firewall(route: Any, request: Any) -> None:
                req_url = request.url
                if domain_is_blocked(req_url, allow_analytics=self.allow_analytics):
                    await route.abort("blockedbyclient")
                else:
                    await route.continue_()

            await page.route("**/*", _ad_firewall)

            try:
                # ── PHASE 1: Google Search Entry ──────────────────────────────
                logger.info(f"[*] Navigating to {persona.google_tld}...")
                await page.goto(persona.google_tld, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(random.uniform(1.2, 2.5))

                # Handle Google Cookie Consent dialogues if present
                for selector in [
                    "button#L2AGLb",
                    "button:has-text('Accept all')",
                    "button:has-text('I agree')",
                    "button:has-text('Accept')",
                    "div[role='none'] button",
                ]:
                    try:
                        consent_btn = page.locator(selector).first
                        if await consent_btn.is_visible(timeout=1500):
                            await consent_btn.click()
                            await asyncio.sleep(random.uniform(0.8, 1.5))
                            logger.info("[+] Bypassed Google consent modal.")
                            break
                    except Exception:
                        pass

                # ── PHASE 2: Keystroke Typing with Jitter ─────────────────────
                search_input = page.locator("textarea[name='q'], input[name='q']").first
                await search_input.wait_for(state="visible", timeout=10000)
                await search_input.click()
                await asyncio.sleep(random.uniform(0.3, 0.7))

                logger.info(f"[*] Typing search query: '{target_keyword}'...")
                await HumanBehavior.type_with_jitter(search_input, target_keyword)
                await asyncio.sleep(random.uniform(0.5, 1.2))
                await page.keyboard.press("Enter")

                # Wait for SERP results
                await page.wait_for_selector("#search, div.g, div[data-sokoban-container]", timeout=20000)
                telemetry["interactions"].append("serp_loaded")
                await asyncio.sleep(random.uniform(1.5, 3.0))

                # Initial SERP scan scroll
                await HumanBehavior.smooth_scroll(page, random.randint(300, 600))
                await asyncio.sleep(random.uniform(1.0, 2.5))

                # ── PHASE 3: Competitor Pogo-Sticking ──────────────────────────
                if pogo_competitor:
                    competitor_pool = COMPETITOR_BENCHMARKS.get(pillar, COMPETITOR_BENCHMARKS["money"])
                    # Find organic links on the page
                    comp_link = None
                    comp_domain_found = None

                    all_links = await page.locator("#search a, div.g a").all()
                    for link_loc in all_links:
                        href = await link_loc.get_attribute("href") or ""
                        for comp in competitor_pool:
                            if comp in href and "google." not in href:
                                comp_link = link_loc
                                comp_domain_found = comp
                                break
                        if comp_link:
                            break

                    if comp_link and comp_domain_found:
                        logger.info(f"[!] Executing Pogo-Stick against competitor: {comp_domain_found}")
                        telemetry["pogo_competitor_domain"] = comp_domain_found
                        await comp_link.click(timeout=10000)

                        # Dwell briefly on competitor (dissatisfied user)
                        pogo_dwell = random.uniform(5.0, 11.0)
                        await asyncio.sleep(pogo_dwell)
                        telemetry["pogo_dwell_seconds"] = round(pogo_dwell, 1)

                        # Quick scroll down on competitor page
                        try:
                            await page.evaluate("window.scrollBy(0, 350);")
                            await asyncio.sleep(random.uniform(1.0, 2.5))
                        except Exception:
                            pass

                        # Bounce back to Google SERP (Negative NavBoost signal to competitor)
                        logger.info("[!] Dissatisfied bounce back to Google SERP...")
                        await page.go_back(wait_until="domcontentloaded", timeout=20000)
                        await asyncio.sleep(random.uniform(1.8, 3.5))
                        telemetry["interactions"].append("pogo_bounce_executed")

                # ── PHASE 4: Groundwork Target Discovery & Click ───────────────
                logger.info(f"[*] Locating Groundwork target on SERP...")
                gworky_link = page.locator("a[href*='gworky.com']").first
                found_gworky = False

                try:
                    if await gworky_link.is_visible(timeout=3000):
                        found_gworky = True
                except Exception:
                    pass

                if not found_gworky:
                    # Scroll further down to look for Groundwork
                    await HumanBehavior.smooth_scroll(page, random.randint(800, 1400))
                    try:
                        if await gworky_link.is_visible(timeout=3000):
                            found_gworky = True
                    except Exception:
                        pass

                if found_gworky:
                    logger.info("[+] Groundwork result visible on SERP! Clicking...")
                    await gworky_link.click(timeout=15000)
                else:
                    # If not on SERP page 1, navigate directly with simulated Google referer
                    logger.info("[-] Groundwork not on SERP page 1. Direct navigation with search referer...")
                    encoded_q = urllib.parse.quote_plus(target_keyword)
                    google_ref = f"{persona.google_tld}/url?sa=t&rct=j&q={encoded_q}&esrc=s&source=web&url={urllib.parse.quote_plus(target_url)}"
                    await page.goto(target_url, referer=google_ref, wait_until="domcontentloaded", timeout=30000)

                telemetry["interactions"].append("groundwork_landed")

                # ── PHASE 5: Deep Terminal Dwell & Interaction ────────────────
                target_dwell = random.uniform(min_dwell_seconds, max_dwell_seconds)
                logger.info(f"[*] Deep Reading Simulation: target dwell = {target_dwell:.1f}s...")
                start_dwell = time.time()

                # Progressive Reading Scroll (4-6 steps)
                scroll_steps = [25, 45, 70, 90, 100]
                for pct in scroll_steps:
                    max_h = await page.evaluate("document.body.scrollHeight || 1200")
                    target_y = int(max_h * (pct / 100.0))
                    await HumanBehavior.smooth_scroll(page, target_y)
                    telemetry["scroll_depth_percent"] = pct
                    # Reading micro-pause per section
                    await asyncio.sleep(target_dwell / (len(scroll_steps) * 1.8))

                # Interactive Calculator / Form Manipulation
                try:
                    inputs = await page.locator("input[type='range'], input[type='number'], input[type='text'], button[type='submit'], [data-calc-btn]").all()
                    if inputs:
                        widget = random.choice(inputs)
                        await widget.scroll_into_view_if_needed()
                        await asyncio.sleep(random.uniform(0.6, 1.4))
                        tag_name = await widget.evaluate("el => el.tagName.toLowerCase()")
                        if tag_name == "input":
                            input_type = await widget.get_attribute("type") or "text"
                            if input_type in ("text", "number"):
                                await widget.click()
                                await widget.fill(str(random.randint(50, 500)))
                                telemetry["interactions"].append("calculator_input_modified")
                        elif tag_name == "button":
                            await widget.click()
                            telemetry["interactions"].append("calculator_button_clicked")
                except Exception as ex:
                    logger.debug("Widget interaction skipped: %s", ex)

                # Dwell remainder
                elapsed = time.time() - start_dwell
                if elapsed < target_dwell:
                    await asyncio.sleep(target_dwell - elapsed)

                telemetry["groundwork_dwell_seconds"] = round(time.time() - start_dwell, 1)

                # ── PHASE 6: Clean Terminal Satisfaction ──────────────────────
                # Crucial: NEVER navigate back to Google SERP.
                # The browser simply closes after the satisfying conversion.
                logger.info(f"[✔] Journey Completed! Terminal Dwell: {telemetry['groundwork_dwell_seconds']}s. Intent Satisfied.")
                telemetry["status"] = "success"
                telemetry["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            except Exception as e:
                telemetry["status"] = "failed"
                telemetry["error"] = str(e)
                logger.error(f"[✖] Journey failed during execution: {e}")
            finally:
                await browser.close()

        # Persist telemetry to Supabase if available
        self._save_telemetry_supabase(telemetry)
        return telemetry

    def _save_telemetry_supabase(self, telemetry: Dict[str, Any]) -> None:
        """Saves synthetic engagement log to Supabase PostgreSQL."""
        supabase_url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or "https://keflumlrmggffyrsrmlk.supabase.co"
        supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not supabase_key:
            return

        try:
            import urllib.request
            req = urllib.request.Request(
                f"{supabase_url.rstrip('/')}/rest/v1/synthetic_engagement_logs",
                data=json.dumps({
                    "session_id": telemetry["session_id"],
                    "target_channel": "google_search_pogostick",
                    "referrer_url": "https://www.google.com/",
                    "article_slug": telemetry["target_url"].split("/")[-1],
                    "geo_region": telemetry["geo_region"],
                    "persona_name": telemetry["persona"],
                    "dwell_seconds": int(telemetry["groundwork_dwell_seconds"]),
                    "scroll_depth_percent": telemetry["scroll_depth_percent"],
                    "actions_triggered": telemetry["interactions"],
                    "device_type": "desktop",
                    "pogo_competitor": telemetry.get("pogo_competitor_domain"),
                    "created_at": telemetry["started_at"],
                }).encode("utf-8"),
                headers={
                    "apikey": supabase_key,
                    "Authorization": f"Bearer {supabase_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                pass
        except Exception:
            pass


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Ghost User Behavioral SERP Journey Engine")
    parser.add_argument("--keyword", type=str, default="mortgage refinance calculator vs bankrate", help="Target search query")
    parser.add_argument("--url", type=str, default="https://gworky.com/tools/mortgage-refinance-calculator", help="Groundwork target landing URL")
    parser.add_argument("--pillar", type=str, default="money", choices=["money", "body", "home", "life", "tech"])
    parser.add_argument("--no-pogo", action="store_true", help="Skip competitor pogo-stick bounce")
    parser.add_argument("--dwell", type=int, default=45, help="Target dwell time in seconds")
    parser.add_argument("--proxy", type=str, default=None, help="Residential proxy URL")
    args = parser.parse_args()

    engine = GhostJourneyEngine(proxy_url=args.proxy)
    res = await engine.execute_journey(
        target_keyword=args.keyword,
        target_url=args.url,
        pillar=args.pillar,
        pogo_competitor=not args.no_pogo,
        min_dwell_seconds=args.dwell,
        max_dwell_seconds=args.dwell + 30,
    )
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    import json
    asyncio.run(main())
