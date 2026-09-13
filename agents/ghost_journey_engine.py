#!/usr/bin/env python3
"""
ghost_journey_engine.py
=======================
Advanced Behavioral Signal Simulation & Algorithmic Signal Manipulation Engine
(The "Ghost User" Architecture) — Groundwork Platform.

Triangulated Signal Architecture (GSC Server-side ↔ GA4 Client-side ↔ Chrome RUM):
  1. Residential / Stealth Proxy Egress: Sticky session pinning (gwk_{uuid}_15m) & subnet diversity.
  2. Google Search Entry & Human Typing: Neuromuscular jitter, regional TLDs, consent bypass.
  3. Competitor Pogo-Sticking: Clicks competitor result, dwells 5-12s, bounces back to SERP.
  4. Groundwork Discovery & High Dwell: Clicks gworky.com, dwells 45-180s (log-normal distribution).
  5. Interactive Conversion: Manipulates calculator sliders, form inputs, scenario toggles, FAQ accordions.
  6. Terminal Search Satisfaction: Never navigates back to Google (intent fully satisfied).
  7. The 8% Chaos Factor: 5% Distracted Bounces + 3% Aimless Wanders to break algorithmic uniformity.
  8. Identity Vault & Returning Cohort: 20-30% returning visitors using persistent storage_state.
  9. Strict Zero Ad-Fraud: 100% blocks all ad networks (AdSense/DoubleClick/Mediavine).

Single Source of Truth: docs/research/seo.md (§3 Module C) & AGENTS.md §2.12
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import sys
import time
import urllib.parse
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Auto-load .env.local
_root = Path(__file__).resolve().parent.parent
_env_path = _root / ".env.local"
if _env_path.exists():
    with open(_env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))

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

# ── Boring Pages for Anomalous Human Verification (1-2% of sessions) ─────────
BORING_PAGES = [
    "/about",
    "/privacy",
    "/terms",
    "/editorial-policy",
    "/faq",
]


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
    is_mobile: bool = False
    has_touch: bool = False
    device_scale_factor: float = 1.0


# Balanced Tier-1 Personas (~55% Mobile, ~45% Desktop across US, UK, AU)
TIER1_PERSONAS: List[GhostPersona] = [
    # US Mobile (Pixel 8 Pro - Android 14)
    GhostPersona(
        name="Elena_Mobile_US",
        geo_region="US",
        google_tld="https://www.google.com",
        user_agent="Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.6613.127 Mobile Safari/537.36",
        sec_ch_ua='"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
        viewport_width=412,
        viewport_height=915,
        timezone="America/New_York",
        accept_language="en-US,en;q=0.9",
        is_mobile=True,
        has_touch=True,
        device_scale_factor=2.625,
    ),
    # US Mobile (iPhone 15 Pro - iOS 17)
    GhostPersona(
        name="Sarah_iPhone_US",
        geo_region="US",
        google_tld="https://www.google.com",
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
        sec_ch_ua='"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
        viewport_width=393,
        viewport_height=852,
        timezone="America/Los_Angeles",
        accept_language="en-US,en;q=0.9",
        is_mobile=True,
        has_touch=True,
        device_scale_factor=3.0,
    ),
    # US Desktop (Windows 11 Chrome)
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
        is_mobile=False,
        has_touch=False,
        device_scale_factor=1.0,
    ),
    # US Desktop (macOS Sonoma M1 Chrome)
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
        is_mobile=False,
        has_touch=False,
        device_scale_factor=2.0,
    ),
    # UK Desktop (Windows 11 Edge)
    GhostPersona(
        name="Oliver_Desktop_UK",
        geo_region="UK",
        google_tld="https://www.google.co.uk",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0",
        sec_ch_ua='"Chromium";v="128", "Not;A=Brand";v="24", "Microsoft Edge";v="128"',
        viewport_width=1536,
        viewport_height=864,
        timezone="Europe/London",
        accept_language="en-GB,en;q=0.9",
        is_mobile=False,
        has_touch=False,
        device_scale_factor=1.25,
    ),
    # AU Mobile (Galaxy S24 Android 14)
    GhostPersona(
        name="Liam_Mobile_AU",
        geo_region="AU",
        google_tld="https://www.google.com.au",
        user_agent="Mozilla/5.0 (Linux; Android 14; SM-S921B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.6613.88 Mobile Safari/537.36",
        sec_ch_ua='"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
        viewport_width=360,
        viewport_height=780,
        timezone="Australia/Sydney",
        accept_language="en-AU,en-US;q=0.9,en;q=0.8",
        is_mobile=True,
        has_touch=True,
        device_scale_factor=3.0,
    ),
]


# ── Behavioral Mathematics & Physics ──────────────────────────────────────────

class BézierMouse:
    """Computes realistic minimum jerk mouse trajectories (Fitts's Law + neuromuscular jitter)."""

    @staticmethod
    def generate_path(
        start: Tuple[float, float],
        end: Tuple[float, float],
        num_points: int = 25,
        jitter_std: float = 1.5,
    ) -> List[Tuple[float, float]]:
        """Generates a cubic Bézier curve with deceleration and Gaussian jitter."""
        p0 = start
        p3 = end

        # Intermediate control points with orthogonal perturbation
        dx = p3[0] - p0[0]
        dy = p3[1] - p0[1]
        dist = math.hypot(dx, dy)
        if dist < 1.0:
            return [p0, p3]

        perp_x = -dy / dist
        perp_y = dx / dist

        # Random control point offsets
        offset1 = random.uniform(-0.25, 0.25) * dist
        offset2 = random.uniform(-0.20, 0.20) * dist

        p1 = (p0[0] + dx * 0.3 + perp_x * offset1, p0[1] + dy * 0.3 + perp_y * offset1)
        p2 = (p0[0] + dx * 0.7 + perp_x * offset2, p0[1] + dy * 0.7 + perp_y * offset2)

        path: List[Tuple[float, float]] = []
        for i in range(num_points):
            # Ease in-out parameter t
            fraction = i / (num_points - 1)
            # Smoothstep easing
            t = fraction * fraction * (3.0 - 2.0 * fraction)

            # Cubic Bézier formula
            bx = (
                (1 - t) ** 3 * p0[0]
                + 3 * (1 - t) ** 2 * t * p1[0]
                + 3 * (1 - t) * t ** 2 * p2[0]
                + t ** 3 * p3[0]
            )
            by = (
                (1 - t) ** 3 * p0[1]
                + 3 * (1 - t) ** 2 * t * p1[1]
                + 3 * (1 - t) * t ** 2 * p2[1]
                + t ** 3 * p3[1]
            )

            # Neuromuscular jitter (only on intermediate points)
            if 0 < i < num_points - 1:
                bx += random.gauss(0, jitter_std)
                by += random.gauss(0, jitter_std)

            path.append((round(bx, 1), round(by, 1)))

        return path

    @staticmethod
    async def move_along_path(page: Any, start: Tuple[float, float], end: Tuple[float, float], steps: int = 25) -> None:
        """Executes smooth mouse movement along the Bézier curve."""
        path = BézierMouse.generate_path(start, end, num_points=steps)
        for x, y in path:
            await page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.008, 0.025))


def calculate_gaussian_click_offset(
    center_x: float,
    center_y: float,
    box_width: float,
    box_height: float,
    sigma_ratio: float = 0.15,
) -> Tuple[float, float]:
    """Calculates click coordinates using 2D Gaussian offset from element center.

    Clamped within element bounding box to prevent miss-clicks.
    """
    sigma_x = max(1.0, box_width * sigma_ratio)
    sigma_y = max(1.0, box_height * sigma_ratio)

    offset_x = random.gauss(0, sigma_x)
    offset_y = random.gauss(0, sigma_y)

    # Clamp within element margins (keep 2px inside border)
    max_dx = max(0.0, (box_width / 2.0) - 2.0)
    max_dy = max(0.0, (box_height / 2.0) - 2.0)

    clamped_x = center_x + max(-max_dx, min(max_dx, offset_x))
    clamped_y = center_y + max(-max_dy, min(max_dy, offset_y))

    return round(clamped_x, 1), round(clamped_y, 1)


def calculate_lognormal_dwell(mu: float = 4.17, sigma: float = 0.55, min_dwell: float = 15.0, max_dwell: float = 360.0) -> float:
    """Returns log-normal dwell time in seconds (median ~65s, σ ~0.55)."""
    raw_dwell = math.exp(random.gauss(mu, sigma))
    return round(max(min_dwell, min(max_dwell, raw_dwell)), 1)


def classify_chaos_behavior(random_seed: Optional[int] = None) -> str:
    """The 8% Chaos Factor classifier:

    - 5% distracted_bounce: lands, scrolls abruptly, leaves in 3-8s.
    - 3% aimless_wander: random erratic scroll, clicks whitespace, zero conversion.
    - 92% normal: authentic high-intent reading / calculation.
    """
    if random_seed is not None:
        rng = random.Random(random_seed)
        val = rng.random()
    else:
        val = random.random()

    if val < 0.05:
        return "distracted_bounce"
    elif val < 0.08:
        return "aimless_wander"
    return "normal"


# ── Persona Identity & State Vault ────────────────────────────────────────────

class PersonaVault:
    """Manages persistent browser profiles (storage_state, cookies, client_id) for Returning Visitors."""

    def __init__(self, vault_dir: Optional[Path] = None):
        self.vault_dir = vault_dir or (_root / "data" / "ghost_profiles")
        self.vault_dir.mkdir(parents=True, exist_ok=True)

    def get_or_create_profile(self, returning: bool = False, geo_region: str = "US") -> Tuple[GhostPersona, Optional[Path], str]:
        """Resolves persona and optional existing storage_state path.

        Returns (persona, storage_state_path, profile_id).
        """
        existing_profiles = list(self.vault_dir.glob("*.json"))
        # Filter for non-expired profiles matching geo
        valid_returning: List[Path] = []
        now = time.time()
        for p in existing_profiles:
            try:
                age_days = (now - p.stat().st_mtime) / 86400.0
                if 1.0 <= age_days <= 14.0:
                    valid_returning.append(p)
            except Exception:
                pass

        if returning and valid_returning:
            picked_path = random.choice(valid_returning)
            profile_id = picked_path.stem
            # Load metadata
            persona_candidates = [p for p in TIER1_PERSONAS if p.geo_region == geo_region]
            persona = random.choice(persona_candidates) if persona_candidates else random.choice(TIER1_PERSONAS)
            logger.info(f"🔄 PersonaVault: Loaded Returning Visitor profile '{profile_id}' (Age: ~{picked_path.stat().st_mtime:.0f})")
            return persona, picked_path, profile_id

        # New first-time profile
        profile_id = f"gwk_prof_{uuid.uuid4().hex[:12]}"
        persona_candidates = [p for p in TIER1_PERSONAS if p.geo_region == geo_region]
        persona = random.choice(persona_candidates) if persona_candidates else random.choice(TIER1_PERSONAS)
        logger.info(f"✨ PersonaVault: Created New Visitor profile '{profile_id}' ({persona.name})")
        return persona, None, profile_id

    def save_profile_state(self, profile_id: str, state_data: Dict[str, Any]) -> Path:
        """Persists storage_state to vault JSON."""
        target_file = self.vault_dir / f"{profile_id}.json"
        with open(target_file, "w", encoding="utf-8") as f:
            json.dump(state_data, f, indent=2)
        logger.info(f"💾 PersonaVault: Persisted storage state for '{profile_id}' ({len(state_data.get('cookies', []))} cookies)")
        return target_file


# ── Human In-Page Behaviors ───────────────────────────────────────────────────

class HumanBehavior:
    """Physics-based human interaction generators."""

    @staticmethod
    async def type_with_jitter(element: Any, text: str) -> None:
        """Types text into an element with human-like delays and occasional typo corrections."""
        for char in text:
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

    @staticmethod
    async def click_with_gaussian_offset(page: Any, locator: Any) -> None:
        """Finds element bounding box, computes Gaussian offset, moves smoothly, and clicks."""
        box = await locator.bounding_box()
        if not box:
            await locator.click()
            return

        cx = box["x"] + box["width"] / 2.0
        cy = box["y"] + box["height"] / 2.0
        target_x, target_y = calculate_gaussian_click_offset(cx, cy, box["width"], box["height"])

        # Pre-click hover thinking pause
        await page.mouse.move(target_x, target_y)
        await asyncio.sleep(random.uniform(0.25, 0.85))
        await page.mouse.click(target_x, target_y)


# ── The Ghost Journey Engine ──────────────────────────────────────────────────

class GhostJourneyEngine:
    """Executes triangulated SERP Pogo-Sticking and Terminal Satisfaction journeys."""

    def __init__(
        self,
        proxy_url: Optional[str] = None,
        allow_analytics: bool = True,
        engine: str = "playwright",
        vault_dir: Optional[Path] = None,
    ):
        self.proxy_url = proxy_url
        self.allow_analytics = allow_analytics
        self.engine = engine
        self.vault = PersonaVault(vault_dir=vault_dir)

    def _resolve_proxy(self, geo: str = "us", session_id: Optional[str] = None) -> Optional[str]:
        """Resolves active residential proxy with sticky session pinning."""
        if self.proxy_url:
            return self.proxy_url

        try:
            try:
                from egress_selector import SmartPolicySelector
            except ImportError:
                from agents.egress_selector import SmartPolicySelector

            selector = SmartPolicySelector()
            res = selector.get_proxy(task_type="serp_journey", geo=geo, session_id=session_id)
            if res:
                return res
        except Exception as sel_err:
            logger.debug("SmartPolicySelector resolution skipped: %s", sel_err)

        # Direct fallback from env (only valid forward proxies)
        for env_var in [
            "RESIDENTIAL_PROXY_URL",
            "DATAIMPULSE_PROXY_URL",
            "WEBSHARE_PROXY_URL",
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
        max_dwell_seconds: int = 140,
        is_returning: bool = False,
    ) -> Dict[str, Any]:
        """Executes the complete triangulated Ghost User journey."""
        # 1. Identity & Session Setup
        geo = "US"
        if "uk" in target_url or "co.uk" in target_keyword:
            geo = "UK"
        elif "au" in target_url or "com.au" in target_keyword:
            geo = "AU"

        persona, storage_state_path, profile_id = self.vault.get_or_create_profile(returning=is_returning, geo_region=geo)
        sticky_session_token = f"gwk_{uuid.uuid4().hex[:8]}_15m"
        effective_proxy = self._resolve_proxy(geo=persona.geo_region.lower(), session_id=sticky_session_token)

        # 2. Behavioral Classifier & Chaos Factor
        behavior_mode = classify_chaos_behavior()
        telemetry: Dict[str, Any] = {
            "session_id": f"ghost_{uuid.uuid4().hex[:10]}",
            "profile_id": profile_id,
            "keyword": target_keyword,
            "target_url": target_url,
            "pillar": pillar,
            "persona": persona.name,
            "geo_region": persona.geo_region,
            "is_returning": is_returning or (storage_state_path is not None),
            "behavior_mode": behavior_mode,
            "proxy_used": bool(effective_proxy),
            "pogo_competitor_domain": None,
            "pogo_dwell_seconds": 0.0,
            "groundwork_dwell_seconds": 0.0,
            "scroll_depth_percent": 0,
            "interactions": [],
            "status": "pending",
            "error": None,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        logger.info(
            f"🕵️ Initializing Ghost Journey [{telemetry['session_id']}] | Keyword: '{target_keyword}' | Mode: {behavior_mode} | Returning: {telemetry['is_returning']}"
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
                parsed_p = urllib.parse.urlsplit(effective_proxy)
                proxy_dict: Dict[str, str] = {"server": f"{parsed_p.scheme}://{parsed_p.hostname}:{parsed_p.port}"}
                if parsed_p.username:
                    proxy_dict["username"] = urllib.parse.unquote(parsed_p.username)
                if parsed_p.password:
                    proxy_dict["password"] = urllib.parse.unquote(parsed_p.password)
                launch_kwargs["proxy"] = proxy_dict

            browser = await p.chromium.launch(**launch_kwargs)

            # Context creation (with storage_state if returning visitor)
            context_kwargs: Dict[str, Any] = {
                "user_agent": persona.user_agent,
                "viewport": {"width": persona.viewport_width, "height": persona.viewport_height},
                "locale": persona.accept_language.split(",")[0],
                "timezone_id": persona.timezone,
                "has_touch": persona.has_touch,
                "is_mobile": persona.is_mobile,
                "device_scale_factor": persona.device_scale_factor,
                "extra_http_headers": {
                    "Sec-Ch-Ua": persona.sec_ch_ua,
                    "Accept-Language": persona.accept_language,
                },
            }
            if storage_state_path and storage_state_path.exists():
                context_kwargs["storage_state"] = str(storage_state_path)

            context = await browser.new_context(**context_kwargs)

            # Inject CDP Stealth Script
            await context.add_init_script(build_stealth_script(persona, is_mobile=persona.is_mobile))
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
                # ── PHASE 1: Google Search Entry (Founder-approved by design) ──
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
                            await HumanBehavior.click_with_gaussian_offset(page, consent_btn)
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

                # Wait for SERP results or bot challenge without throwing unhandled timeout
                serp_loaded = False
                for _ in range(20):  # poll up to 10s
                    await asyncio.sleep(0.5)
                    curr_url = page.url
                    if "sorry/index" in curr_url or "recaptcha" in curr_url:
                        break
                    try:
                        if await page.locator("#search, div.g, div[data-sokoban-container]").count() > 0:
                            serp_loaded = True
                            break
                    except Exception:
                        pass

                # Check for Google bot challenge immediately
                if "sorry/index" in page.url or await page.locator("iframe[src*='recaptcha'], div#recaptcha").count() > 0:
                    logger.warning("[-] Google bot challenge encountered on search (sorry/index). Falling back to direct organic referer navigation.")
                    telemetry["interactions"].append("captcha_detected_on_serp")
                    telemetry["captcha_detected"] = True

                    # Graceful fallback: Navigate directly to Groundwork with simulated Google organic search referer
                    encoded_q = urllib.parse.quote_plus(target_keyword)
                    google_ref = f"{persona.google_tld}/url?sa=t&rct=j&q={encoded_q}&esrc=s&source=web&url={urllib.parse.quote_plus(target_url)}"
                    logger.info(f"[*] Navigating directly to Groundwork with organic search referer...")
                    await page.goto(target_url, referer=google_ref, wait_until="domcontentloaded", timeout=30000)

                # ── PHASE 3: Competitor Pogo-Sticking (Striking Distance) ─────
                if not telemetry.get("captcha_detected"):
                    if pogo_competitor and behavior_mode == "normal":
                        competitor_pool = COMPETITOR_BENCHMARKS.get(pillar, COMPETITOR_BENCHMARKS["money"])
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
                            await HumanBehavior.click_with_gaussian_offset(page, comp_link)

                            # Dwell briefly on competitor (dissatisfied user: 5-11s)
                            pogo_dwell = random.uniform(5.0, 11.0)
                            await asyncio.sleep(pogo_dwell)
                            telemetry["pogo_dwell_seconds"] = round(pogo_dwell, 1)

                            # Quick scroll down on competitor page
                            try:
                                await page.evaluate("window.scrollBy(0, 350);")
                                await asyncio.sleep(random.uniform(1.0, 2.2))
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
                        await HumanBehavior.smooth_scroll(page, random.randint(800, 1400))
                        try:
                            if await gworky_link.is_visible(timeout=3000):
                                found_gworky = True
                        except Exception:
                            pass

                    if found_gworky:
                        logger.info("[+] Groundwork result visible on SERP! Clicking with Gaussian offset...")
                        await HumanBehavior.click_with_gaussian_offset(page, gworky_link)
                        try:
                            await page.wait_for_load_state("domcontentloaded", timeout=20000)
                        except Exception:
                            pass
                    else:
                        logger.info("[-] Groundwork not on SERP page 1. Direct navigation with search referer...")
                        encoded_q = urllib.parse.quote_plus(target_keyword)
                        google_ref = f"{persona.google_tld}/url?sa=t&rct=j&q={encoded_q}&esrc=s&source=web&url={urllib.parse.quote_plus(target_url)}"
                        await page.goto(target_url, referer=google_ref, wait_until="domcontentloaded", timeout=30000)

                telemetry["interactions"].append("groundwork_landed")

                # ── PHASE 5: Groundwork In-Page Engagement & Chaos Branches ───
                start_dwell = time.time()

                # Branch A: 5% Distracted Quick Bounce (Chaos Factor)
                if behavior_mode == "distracted_bounce":
                    logger.info("🎲 Chaos Factor: Executing Distracted Quick Bounce (dwell 3-7s)...")
                    await page.evaluate("window.scrollBy(0, 250);")
                    quick_dwell = random.uniform(3.5, 7.5)
                    await asyncio.sleep(quick_dwell)
                    telemetry["groundwork_dwell_seconds"] = round(time.time() - start_dwell, 1)
                    telemetry["interactions"].append("distracted_bounce")
                    telemetry["status"] = "success_chaos"
                    await context.close()
                    return telemetry

                # Branch B: 3% Aimless Wander (Chaos Factor)
                if behavior_mode == "aimless_wander":
                    logger.info("🎲 Chaos Factor: Executing Aimless Wander (dwell 25-40s, erratic scrolling)...")
                    for _ in range(3):
                        await page.evaluate(f"window.scrollBy(0, {random.randint(-300, 600)});")
                        await asyncio.sleep(random.uniform(2.0, 5.0))
                    wander_dwell = random.uniform(25.0, 40.0)
                    elapsed = time.time() - start_dwell
                    if elapsed < wander_dwell:
                        await asyncio.sleep(wander_dwell - elapsed)
                    telemetry["groundwork_dwell_seconds"] = round(time.time() - start_dwell, 1)
                    telemetry["interactions"].append("aimless_wander")
                    telemetry["status"] = "success_chaos"
                    await context.close()
                    return telemetry

                # Branch C: Normal 92% Intentful Journey (Log-Normal Dwell ~65s)
                target_dwell = calculate_lognormal_dwell(mu=4.17, sigma=0.55, min_dwell=min_dwell_seconds, max_dwell=max_dwell_seconds)
                logger.info(f"[*] Deep Reading Simulation: target log-normal dwell = {target_dwell:.1f}s...")

                # Progressive Reading Scroll (25%, 45%, 70%, 90%, 100%)
                scroll_steps = [25, 45, 70, 90, 100]
                for pct in scroll_steps:
                    max_h = await page.evaluate("document.body.scrollHeight || 1200")
                    target_y = int(max_h * (pct / 100.0))
                    await HumanBehavior.smooth_scroll(page, target_y)
                    telemetry["scroll_depth_percent"] = pct
                    # Micro-reading pause per section
                    await asyncio.sleep(target_dwell / (len(scroll_steps) * 1.7))

                # Deep Interactive Calculator / Tool Element Manipulation
                if "/tools/" in target_url or "calculator" in target_url:
                    await self._interact_with_calculator(page, telemetry)

                # Article Interactions (Text selection, copy link, FAQ accordion)
                if "/article/" in target_url or "/money/" in target_url or "/body/" in target_url:
                    await self._interact_with_article(page, telemetry)

                # 1-2% Boring Page Visit (Terms, Privacy, About)
                if random.random() < 0.02:
                    boring_route = random.choice(BORING_PAGES)
                    logger.info(f"📄 Visiting human verification route: {boring_route}")
                    try:
                        await page.goto(f"https://gworky.com{boring_route}", wait_until="domcontentloaded", timeout=15000)
                        await asyncio.sleep(random.uniform(4.0, 8.0))
                        telemetry["interactions"].append(f"visited_{boring_route.strip('/')}")
                    except Exception:
                        pass

                # Dwell remainder
                elapsed = time.time() - start_dwell
                if elapsed < target_dwell:
                    await asyncio.sleep(target_dwell - elapsed)

                telemetry["groundwork_dwell_seconds"] = round(time.time() - start_dwell, 1)

                # ── PHASE 6: Save Storage State & Clean Terminal Satisfaction ─
                # Persist session cookies/localStorage in PersonaVault for returning visitor cohort
                try:
                    storage_state = await context.storage_state()
                    self.vault.save_profile_state(profile_id, storage_state)
                except Exception as save_err:
                    logger.debug("Failed saving storage state: %s", save_err)

                logger.info(f"[✔] Journey Completed! Terminal Dwell: {telemetry['groundwork_dwell_seconds']}s. Intent Fully Satisfied.")
                telemetry["status"] = "success"
                telemetry["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            except Exception as e:
                telemetry["status"] = "failed"
                telemetry["error"] = str(e)
                logger.error(f"[✖] Journey failed during execution: {e}")
            finally:
                await browser.close()

        # Persist structured telemetry log
        self._record_telemetry(telemetry)
        return telemetry

    async def _interact_with_calculator(self, page: Any, telemetry: Dict[str, Any]) -> None:
        """Deeply interacts with calculator sliders, scenario buttons, and accordions."""
        try:
            # 1. Range sliders manipulation
            sliders = await page.locator("input[type='range']").all()
            for slider in sliders[:2]:
                await slider.scroll_into_view_if_needed()
                await asyncio.sleep(random.uniform(0.3, 0.7))
                cur_val = float(await slider.get_attribute("value") or "50")
                min_val = float(await slider.get_attribute("min") or "0")
                max_val = float(await slider.get_attribute("max") or "100")
                delta = (max_val - min_val) * random.uniform(0.05, 0.15) * (1 if random.random() > 0.5 else -1)
                new_val = max(min_val, min(max_val, cur_val + delta))
                await slider.evaluate(f"el => {{ el.value = {new_val}; el.dispatchEvent(new Event('input', {{ bubbles: true }})); el.dispatchEvent(new Event('change', {{ bubbles: true }})); }}")
                telemetry["interactions"].append("slider_dragged")
                await asyncio.sleep(random.uniform(0.4, 0.9))

            # 2. 70% chance of calculation button / tab toggle
            if random.random() < 0.70:
                calc_btns = await page.locator("button:has-text('Calculate'), button:has-text('Compare'), button:has-text('Compute'), [data-calc-btn]").all()
                if calc_btns:
                    btn = random.choice(calc_btns)
                    await HumanBehavior.click_with_gaussian_offset(page, btn)
                    telemetry["interactions"].append("calc_scenario_compute")
                    await asyncio.sleep(random.uniform(1.2, 2.5))

            # 3. Expand FAQ accordions
            faqs = await page.locator("details, [aria-expanded='false']").all()
            if faqs:
                faq = random.choice(faqs)
                await faq.scroll_into_view_if_needed()
                await HumanBehavior.click_with_gaussian_offset(page, faq)
                telemetry["interactions"].append("faq_expanded")
                await asyncio.sleep(random.uniform(1.0, 2.0))
        except Exception as ex:
            logger.debug("Calculator interaction skipped: %s", ex)

    async def _interact_with_article(self, page: Any, telemetry: Dict[str, Any]) -> None:
        """Simulates authentic article reading behaviors (text selection, copy link, internal navigation)."""
        try:
            # 1. 30% chance of text selection (simulating copy/highlight)
            if random.random() < 0.30:
                await page.evaluate("""
                    const p = document.querySelector('article p');
                    if (p) {
                        const range = document.createRange();
                        range.selectNodeContents(p);
                        const sel = window.getSelection();
                        sel.removeAllRanges();
                        sel.addRange(range);
                    }
                """)
                telemetry["interactions"].append("text_highlighted")
                await asyncio.sleep(random.uniform(0.8, 1.6))
                await page.evaluate("window.getSelection().removeAllRanges();")

            # 2. 10% chance of copy link / share button focus
            if random.random() < 0.10:
                share_btn = page.locator("button:has-text('Share'), button:has-text('Copy'), [aria-label*='share']").first
                if await share_btn.is_visible():
                    await HumanBehavior.click_with_gaussian_offset(page, share_btn)
                    telemetry["interactions"].append("share_link_clicked")
                    await asyncio.sleep(random.uniform(0.5, 1.2))

            # 3. 25% chance of following a silo-safe internal link for multi-page depth
            if random.random() < 0.25:
                internal_links = await page.locator("article a[href^='/'], article a[href*='gworky.com']").all()
                if internal_links:
                    link = random.choice(internal_links)
                    href = await link.get_attribute("href") or ""
                    if href and not href.startswith("#") and "subscribe" not in href:
                        logger.info(f"🔗 Following silo-safe internal link: {href}")
                        await HumanBehavior.click_with_gaussian_offset(page, link)
                        await page.wait_for_load_state("domcontentloaded", timeout=15000)
                        await asyncio.sleep(random.uniform(8.0, 18.0))
                        telemetry["interactions"].append(f"followed_internal_{href[:20]}")
        except Exception as ex:
            logger.debug("Article interaction skipped: %s", ex)

    def _record_telemetry(self, telemetry: Dict[str, Any]) -> None:
        """Appends session to local JSONL and syncs to Supabase."""
        # 1. Local JSONL log
        log_file = _root / "logs" / "ghost_journeys.jsonl"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(telemetry) + "\n")
        except Exception:
            pass

        # 2. Supabase PostgreSQL persistence
        supabase_url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or "https://keflumlrmggffyrsrmlk.supabase.co"
        supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not supabase_key:
            return

        try:
            import urllib.request
            status = telemetry.get("status", "success")
            db_status = "completed" if status in ("success", "success_chaos") else ("failed" if status == "failed" else "dry_run")
            source_type = "triangulation" if telemetry.get("proxy_used") else "quarantine_direct"
            req = urllib.request.Request(
                f"{supabase_url.rstrip('/')}/rest/v1/synthetic_engagement_logs",
                data=json.dumps({
                    "session_id": telemetry["session_id"],
                    "keyword_queried": telemetry.get("keyword", ""),
                    "article_slug": telemetry["target_url"].split("/")[-1],
                    "target_platform": "search" if source_type == "triangulation" else "web",
                    "geo_region": telemetry["geo_region"],
                    "dwell_time_seconds": int(telemetry["groundwork_dwell_seconds"] or 0),
                    "scroll_depth_percent": int(telemetry.get("scroll_depth_percent") or 0),
                    "actions_triggered": telemetry["interactions"],
                    "status": db_status,
                    "error_message": telemetry.get("error"),
                    "created_at": telemetry["started_at"],
                    "is_synthetic": True,
                    "source_type": source_type,
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
    parser = argparse.ArgumentParser(description="Ghost User Triangulated Behavioral Journey Engine")
    parser.add_argument("--keyword", type=str, default="mortgage refinance calculator", help="Target search query")
    parser.add_argument("--url", type=str, default="https://gworky.com/tools/mortgage-refinance-calculator", help="Groundwork target landing URL")
    parser.add_argument("--pillar", type=str, default="money", choices=["money", "body", "home", "life", "tech"])
    parser.add_argument("--no-pogo", action="store_true", help="Skip competitor pogo-stick bounce")
    parser.add_argument("--dwell", type=int, default=45, help="Target dwell time in seconds")
    parser.add_argument("--proxy", type=str, default=None, help="Residential proxy URL")
    parser.add_argument("--returning", action="store_true", help="Force a returning visitor session")
    args = parser.parse_args()

    engine = GhostJourneyEngine(proxy_url=args.proxy)
    res = await engine.execute_journey(
        target_keyword=args.keyword,
        target_url=args.url,
        pillar=args.pillar,
        pogo_competitor=not args.no_pogo,
        min_dwell_seconds=args.dwell,
        max_dwell_seconds=args.dwell + 40,
        is_returning=args.returning,
    )
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
