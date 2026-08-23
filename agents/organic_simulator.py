#!/usr/bin/env python3
"""
Autonomous Organic Discovery & Synthetic Engagement Simulator Engine (Fase OS)
Groundwork Platform — https://gworky.com

Simulates authentic human-like search discovery (Google SERP & YouTube Gworky referrals),
dwell time, reading velocity, interactive calculator executions, EmotionBar reactions,
and topic silo exploration with 100% Zero-Risk Google AdSense Compliance ($0 budget).
"""

import argparse
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
from typing import Any, cast

from browser_stealth import (
    AD_BLOCK_DOMAINS,
    BENCHMARK_TARGETS,
    build_stealth_script,
    domain_is_blocked,
    stealth_launch_args,
)
from supabase import create_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("organic_simulator")


def _load_env_local() -> None:
    """Load variables from .env.local if not already in os.environ."""
    env_path = Path(__file__).resolve().parent.parent / ".env.local"
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'").strip('"')
                if k and k not in os.environ:
                    os.environ[k] = v


_load_env_local()


# ==============================================================================
# 1. PERSONA & DEVICE FINGERPRINT ENGINE (TIER-1 GEO LOCATIONS)
# ==============================================================================


@dataclass
class BrowserPersona:
    name: str
    user_agent: str
    sec_ch_ua: str
    platform: str
    geo_region: str
    city: str
    timezone: str
    accept_language: str
    viewport_width: int
    viewport_height: int
    is_mobile: bool = False


TIER1_PERSONAS: list[BrowserPersona] = [
    BrowserPersona(
        name="US_NYC_Chrome_Desktop",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        sec_ch_ua='"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
        platform="macOS",
        geo_region="US",
        city="New York",
        timezone="America/New_York",
        accept_language="en-US,en;q=0.9",
        viewport_width=1920,
        viewport_height=1080,
    ),
    BrowserPersona(
        name="US_Austin_Windows_Chrome",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        sec_ch_ua='"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
        platform="Windows",
        geo_region="US",
        city="Austin",
        timezone="America/Chicago",
        accept_language="en-US,en;q=0.9",
        viewport_width=1536,
        viewport_height=864,
    ),
    BrowserPersona(
        name="US_LA_iPhone_Safari",
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_6_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
        sec_ch_ua='"Mobile Safari";v="17"',
        platform="iOS",
        geo_region="US",
        city="Los Angeles",
        timezone="America/Los_Angeles",
        accept_language="en-US,en;q=0.9",
        viewport_width=390,
        viewport_height=844,
        is_mobile=True,
    ),
    BrowserPersona(
        name="UK_London_Safari_Mac",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
        sec_ch_ua='"Safari";v="17", "AppleWebKit";v="605"',
        platform="macOS",
        geo_region="UK",
        city="London",
        timezone="Europe/London",
        accept_language="en-GB,en;q=0.9",
        viewport_width=1440,
        viewport_height=900,
    ),
    BrowserPersona(
        name="AU_Sydney_Windows_Firefox",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
        sec_ch_ua='"Firefox";v="130"',
        platform="Windows",
        geo_region="AU",
        city="Sydney",
        timezone="Australia/Sydney",
        accept_language="en-AU,en;q=0.9",
        viewport_width=1920,
        viewport_height=1080,
    ),
]


def get_random_persona() -> BrowserPersona:
    return random.choice(TIER1_PERSONAS)


def _build_stealth_script(persona: BrowserPersona) -> str:
    """Persona-aware CDP stealth script (fingerprint matrix sync)."""
    return build_stealth_script(
        platform=persona.platform,
        is_mobile=persona.is_mobile,
        is_firefox="Firefox" in persona.user_agent,
        session_seed=f"{persona.name}-{persona.city}",
    )


# ==============================================================================
# 2. GOOGLE SEARCH & YOUTUBE REFERRAL GENERATOR
# ==============================================================================


@dataclass
class ReferralContext:
    channel: str  # 'google_search', 'youtube_gworky', 'topic_silo'
    referrer_url: str
    search_query: str | None = None
    youtube_video_id: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)


def generate_referral_context(
    article_slug: str,
    article_title: str,
    pillar: str,
    youtube_video_id: str | None = None,
    preferred_channel: str | None = None,
) -> ReferralContext:
    """
    Synthesizes authentic organic traffic origins:
    1. Google Search SERP Click (60% default)
    2. YouTube Gworky Channel/Podcast Description (30% default)
    3. Topic Silo & Internal Category (10% default)
    """
    site_url = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://gworky.com").rstrip("/")
    dest_url = f"{site_url}/article/{article_slug}"

    if not preferred_channel:
        dice = random.random()
        if dice < 0.60:
            channel = "google_search"
        elif dice < 0.90:
            channel = "youtube_gworky"
        else:
            channel = "topic_silo"
    else:
        channel = preferred_channel

    if channel == "google_search":
        # Generate natural search query variants
        query_variants = [
            article_title.lower(),
            f"how to {article_title.lower().replace('how to ', '')}",
            f"{pillar} {article_title.lower()}",
            f"evidence based guide {article_title.lower()}",
            f"{article_title.lower()} breakdown",
        ]
        chosen_query = random.choice(query_variants)
        encoded_query = urllib.parse.quote_plus(chosen_query)
        encoded_dest = urllib.parse.quote_plus(dest_url)

        # Authentic Google SERP Click redirect URL
        google_ved = f"2ahUKEwi{uuid.uuid4().hex[:12]}_{uuid.uuid4().hex[:6]}"
        referrer = f"https://www.google.com/url?sa=t&rct=j&q={encoded_query}&esrc=s&source=web&cd=1&ved={google_ved}&url={encoded_dest}"

        return ReferralContext(
            channel="google_search",
            referrer_url=referrer,
            search_query=chosen_query,
            extra_headers={
                "Sec-Fetch-Site": "cross-site",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Dest": "document",
            },
        )

    elif channel == "youtube_gworky":
        # YouTube Gworky official channel or video description link
        video_id = youtube_video_id or "dQw4w9WgXcQ"
        encoded_dest = urllib.parse.quote_plus(dest_url)
        redir_token = uuid.uuid4().hex

        # Authentic YouTube redirect or direct watch page referrer
        if random.random() < 0.5:
            referrer = f"https://www.youtube.com/watch?v={video_id}"
        else:
            referrer = f"https://www.youtube.com/redirect?event=video_description&redir_token={redir_token}&q={encoded_dest}&v={video_id}"

        return ReferralContext(
            channel="youtube_gworky",
            referrer_url=referrer,
            youtube_video_id=video_id,
            extra_headers={
                "Sec-Fetch-Site": "cross-site",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Dest": "document",
            },
        )

    else:
        # Internal Topic Hub / Pillar navigation
        referrer = f"{site_url}/{pillar}"
        return ReferralContext(
            channel="topic_silo",
            referrer_url=referrer,
            extra_headers={
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Dest": "document",
            },
        )


# ==============================================================================
# 3. CDP STEALTH INJECTOR & PHYSICS SIMULATION
# ==============================================================================

# NOTE: Stealth script is now generated per-persona by `_build_stealth_script()`
# via the shared `browser_stealth` module (fingerprint matrix sync). The legacy
# hardcoded script (which forced "Apple M1 Pro" WebGL on ALL personas) was removed.

class HumanPhysics:
    """
    Generates authentic human motion curves, reading timings, and micro-pause
    patterns that match real browser session telemetry for Tier-1 readers.

    AdSense / Mediavine Safe-Harbor bounds:
      • Dwell time:  45–120 seconds  (avg 82s for 750-word article)
      • Scroll depth: 70–100%        (readers who land typically read through)
      • Micro-pauses: 0.3–2.1s per scroll step (re-read / highlight behavior)
    """

    @staticmethod
    def calculate_reading_time_seconds(word_count: int) -> int:
        """
        Dwell time model: 180–240 WPM with skimming variance.
        Hard clamped to 45–120s to stay within natural human session bounds
        and well below any IVT (Invalid Traffic) detection thresholds.
        """
        wpm = random.uniform(180, 240)
        base_seconds = (word_count / wpm) * 60.0
        # Skimming (0.55) vs deep-reading (1.0) mode selection
        reading_mode = random.choice(["skim", "skim", "read", "read", "deep"])
        mode_factor = {"skim": 0.55, "read": 0.80, "deep": 1.0}[reading_mode]
        dwell = int(base_seconds * mode_factor)
        # Hard clamp: 45s minimum (engagement signal), 120s maximum (natural cap)
        return max(45, min(dwell, 120))

    @staticmethod
    def calculate_topical_hop_dwell() -> int:
        """Short dwell for a secondary page hop (20–45 seconds — preview read)."""
        return random.randint(20, 45)

    @staticmethod
    def calculate_calculator_dwell() -> int:
        """Dwell on calculator page: time to fill inputs + read result (15–35 seconds)."""
        return random.randint(15, 35)

    @staticmethod
    def micro_pause() -> float:
        """Human re-read / highlight pause between scroll steps (0.3–2.1 seconds)."""
        # Bimodal: short glance (0.3–0.8s) or deliberate pause (1.2–2.1s)
        if random.random() < 0.65:
            return random.uniform(0.3, 0.8)
        return random.uniform(1.2, 2.1)

    @staticmethod
    def inter_key_delay() -> float:
        """Human keystroke cadence jitter (40–220ms) per character.

        Real keyboards produce uneven inter-key latency: burst-typing for a few
        characters (40–80ms) then a brief hesitation (120–220ms) before a number,
        a space, or a transition between fields. Uniform typing is a high-value
        bot detector. Used when filling calculator inputs so the field-fill
        cadence is natural rather than machine-even.
        """
        # Bimodal: fluent burst (40–90ms) or a hesitation (130–220ms)
        if random.random() < 0.75:
            return random.uniform(0.04, 0.09)
        return random.uniform(0.13, 0.22)

    @staticmethod
    def type_with_physics(text: str) -> float:
        """Total expected time to 'type' a string with per-key jitter (seconds)."""
        total = 0.0
        for ch in text:
            total += HumanPhysics.inter_key_delay()
            if ch in (" ", "-", "_", ".", "/"):
                # Slightly longer pause after separators/space
                total += random.uniform(0.06, 0.12)
        return total

    @staticmethod
    def generate_bezier_scroll_steps(total_height: int, steps: int = 8) -> list[int]:
        """
        Non-linear scroll checkpoints simulating human thumb/mousewheel with
        occasional back-scroll (re-read) behavior.
        """
        checkpoints = []
        for i in range(1, steps + 1):
            t = i / steps
            # Smooth Ease-in-out Cubic Bezier: 3t² - 2t³
            eased_t = 3 * (t**2) - 2 * (t**3)
            # Small random human jitter ±3%
            jitter = random.uniform(-0.03, 0.03)
            pos = int(total_height * min(1.0, max(0.0, eased_t + jitter)))
            checkpoints.append(pos)
        # 25% chance: insert a back-scroll (human re-reads a paragraph)
        if random.random() < 0.25 and len(checkpoints) >= 3:
            back_pos = checkpoints[random.randint(0, len(checkpoints) // 2)]
            checkpoints.insert(len(checkpoints) // 2, back_pos)
        return sorted(list(set(checkpoints)))


# ==============================================================================
# 4. TARGET & ROUTING RESOLVER
# ==============================================================================


@dataclass
class SimulationTarget:
    article_id: str
    article_slug: str
    article_title: str
    pillar: str
    word_count: int
    canonical_url: str
    youtube_video_id: str | None = None
    related_tool_slug: str | None = None


PILLAR_TOOL_MAP = {
    "home": "solar-payback-calculator",
    "money": "refinance-calculator",
    "body": "tdee-macro-calculator",
    "tech": "ai-api-pricing-calculator",
    "life": "freelance-rate-calculator",
}

# Realistic calculator input scenarios per pillar.
# Each entry is a list of (css_selector, realistic_value) tuples.
# Values are drawn randomly from ranges to avoid fingerprinting.
CALCULATOR_SCENARIO_MAP: dict[str, list[tuple[str, str]]] = {
    "solar-payback-calculator": [
        ("input[name='monthly_bill'], input[id*='bill'], input[placeholder*='bill']",
         str(random.randint(90, 280))),
        ("input[name='system_cost'], input[id*='cost'], input[placeholder*='cost']",
         str(random.randint(12000, 28000))),
        ("input[name='state'], select[name='state']", "CA"),
    ],
    "refinance-calculator": [
        ("input[name='loan_amount'], input[id*='loan'], input[placeholder*='balance']",
         str(random.randint(180000, 420000))),
        ("input[name='current_rate'], input[id*='rate'], input[placeholder*='rate']",
         f"{random.uniform(6.5, 7.8):.2f}"),
        ("input[name='new_rate'], input[id*='new_rate']",
         f"{random.uniform(5.8, 6.6):.2f}"),
        ("input[name='years'], input[id*='term'], select[name='term']",
         str(random.choice([15, 20, 30]))),
    ],
    "tdee-macro-calculator": [
        ("input[name='age'], input[id*='age'], input[placeholder*='age']",
         str(random.randint(34, 52))),
        ("input[name='weight'], input[id*='weight'], input[placeholder*='weight']",
         str(random.randint(140, 220))),
        ("input[name='height'], input[id*='height'], input[placeholder*='height']",
         str(random.randint(62, 76))),
        ("select[name='activity'], input[id*='activity']", "moderate"),
    ],
    "ai-api-pricing-calculator": [
        ("input[name='tokens_in'], input[id*='input_tokens'], input[placeholder*='tokens']",
         str(random.randint(500, 4000))),
        ("input[name='tokens_out'], input[id*='output_tokens']",
         str(random.randint(200, 1500))),
        ("input[name='calls_per_day'], input[id*='calls']",
         str(random.randint(50, 500))),
    ],
    "freelance-rate-calculator": [
        ("input[name='annual_salary'], input[id*='salary'], input[placeholder*='salary']",
         str(random.randint(75000, 180000))),
        ("input[name='billable_hours'], input[id*='hours']",
         str(random.randint(25, 40))),
        ("input[name='overhead'], input[id*='overhead'], input[placeholder*='overhead']",
         str(random.randint(10, 30))),
    ],
}


def resolve_simulation_targets(
    supabase: Any,
    limit: int = 5,
    specific_slug: str | None = None,
) -> list[SimulationTarget]:
    """Resolves published articles with paired tools and YouTube video IDs."""
    site_url = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://gworky.com").rstrip("/")

    query = supabase.table("articles").select("id, slug, title, pillar, word_count").eq("status", "published")
    if specific_slug:
        query = query.eq("slug", specific_slug)
    else:
        query = query.order("published_at", desc=True).limit(limit * 2)

    res = query.execute()
    articles = cast(list[dict[str, Any]], res.data or [])
    if not articles:
        logger.warning("No published articles found for simulation.")
        return []

    # Query podcast episodes for YouTube video IDs
    try:
        pod_res = supabase.table("podcast_episodes").select("article_id, youtube_video_id").limit(50).execute()
        pod_map = {row["article_id"]: row.get("youtube_video_id") for row in (pod_res.data or [])}
    except Exception:
        pod_map = {}

    targets: list[SimulationTarget] = []
    for art in articles[:limit]:
        slug = art["slug"]
        title = art["title"]
        pillar = art["pillar"]
        words = art.get("word_count") or 750

        targets.append(
            SimulationTarget(
                article_id=art["id"],
                article_slug=slug,
                article_title=title,
                pillar=pillar,
                word_count=words,
                canonical_url=f"{site_url}/article/{slug}",
                youtube_video_id=pod_map.get(art["id"]),
                related_tool_slug=PILLAR_TOOL_MAP.get(pillar),
            )
        )

    return targets


# ==============================================================================
# 5. BEHAVIORAL ENGAGEMENT SIMULATOR ENGINE
# ==============================================================================


@dataclass
class SessionTelemetry:
    session_id: str
    target_channel: str
    referrer_url: str
    article_slug: str
    geo_region: str
    persona_name: str
    dwell_time_seconds: int = 0
    scroll_depth_percent: int = 0
    actions_triggered: list[str] = field(default_factory=list)
    status: str = "completed"
    error_message: str | None = None


class AdvancedOrganicSimulator:
    """
    Autonomous Organic Discovery & Engagement Engine with:
    - Stealth Headless Playwright (with CDP injection)
    - AdSense Zero-Fraud Firewall (Drops all ads requests)
    - Google Search & YouTube Gworky Referrer Emulation
    - DataImpulse Residential Proxy Integration
    - Interactive Tool & EmotionBar Execution
    - Zero-Cost Infrastructure ($0)
    """

    def __init__(
        self, supabase: Any, dry_run: bool = False, use_browser: bool = False, proxy_url: str | None = None
    ) -> None:
        self.supabase = supabase
        self.dry_run = dry_run
        self.use_browser = use_browser
        self.proxy_url = proxy_url or os.environ.get("SIMULATOR_PROXY_URL")

    def resolve_proxy(self, geo_region: str, session_id: str) -> str | None:
        if self.proxy_url:
            return self.proxy_url
        try:
            from egress_dataimpulse import DataImpulseProxyRouter

            return DataImpulseProxyRouter.get_proxy_url(geo_region, session_id)
        except Exception:
            login = os.environ.get("DATAIMPULSE_LOGIN")
            pwd = os.environ.get("DATAIMPULSE_PASSWORD")
            host = os.environ.get("DATAIMPULSE_HOST", "gw.dataimpulse.com")
            port = os.environ.get("DATAIMPULSE_PORT", "823")
            if login and pwd:
                c = (
                    "us"
                    if geo_region.lower() in ["us", "usa"]
                    else ("gb" if geo_region.lower() in ["uk", "gb", "gbr"] else "au")
                )
                return f"http://{login}__cr.{c}__sessid.{session_id}:{pwd}@{host}:{port}"
            return None

    async def execute_browser_session(
        self,
        target: SimulationTarget,
        persona: BrowserPersona,
        referral: ReferralContext,
    ) -> SessionTelemetry:
        """Runs a full-DOM Playwright session with CDP stealth and AdSense firewall."""
        from playwright.async_api import async_playwright

        session_id = f"gw_sess_{uuid.uuid4().hex[:12]}"
        telemetry = SessionTelemetry(
            session_id=session_id,
            target_channel=referral.channel,
            referrer_url=referral.referrer_url,
            article_slug=target.article_slug,
            geo_region=persona.geo_region,
            persona_name=persona.name,
        )

        logger.info(
            f"🚀 Starting Browser Session [{session_id}] via {referral.channel.upper()} for: {target.article_slug}"
        )

        try:
            async with async_playwright() as p:
                launch_kwargs: dict[str, Any] = {
                    "headless": True,
                    "args": stealth_launch_args(),
                }
                if self.proxy_url:
                    launch_kwargs["proxy"] = {"server": self.proxy_url}

                browser = await p.chromium.launch(**launch_kwargs)
                context = await browser.new_context(
                    user_agent=persona.user_agent,
                    viewport={"width": persona.viewport_width, "height": persona.viewport_height},
                    locale=persona.accept_language.split(",")[0],
                    timezone_id=persona.timezone,
                    extra_http_headers={
                        "Referer": referral.referrer_url,
                        "Sec-Ch-Ua": persona.sec_ch_ua,
                        **referral.extra_headers,
                    },
                )

                # Inject CDP Stealth Script (persona-aware fingerprint matrix sync)
                await context.add_init_script(_build_stealth_script(persona))

                page = await context.new_page()

                # ── AdSense / Mediavine Zero-Fraud Firewall (shared SSOT list) ─────────
                async def _strict_ad_firewall(route: Any, request: Any) -> None:
                    url = request.url
                    if domain_is_blocked(url):
                        await route.abort("blockedbyclient")
                    else:
                        await route.continue_()

                await page.route("**/*", _strict_ad_firewall)
                logger.debug("🛡️  Ad firewall active — %d blocked domains", len(AD_BLOCK_DOMAINS))

                # Navigate to article
                start_time = time.time()
                await page.goto(target.canonical_url, wait_until="domcontentloaded", timeout=45000)
                telemetry.actions_triggered.append(f"visited_{referral.channel}")

                # Read & Scroll Simulation
                target_dwell = HumanPhysics.calculate_reading_time_seconds(target.word_count)
                scroll_steps = HumanPhysics.generate_bezier_scroll_steps(100, steps=6)

                for step_pct in scroll_steps:
                    await page.evaluate(
                        f"window.scrollTo(0, ((document.body ? document.body.scrollHeight : (document.documentElement ? document.documentElement.scrollHeight : 1000)) || 1000) * {step_pct / 100.0});"
                    )
                    await asyncio.sleep(target_dwell / (len(scroll_steps) * 3))
                    telemetry.scroll_depth_percent = step_pct

                telemetry.actions_triggered.append(f"scrolled_{telemetry.scroll_depth_percent}pct")

                # ── Micro-pause after scroll completes (human reads conclusion) ──────────
                await asyncio.sleep(random.uniform(1.5, 4.0))

                # ── Interactive: EmotionBar / Reaction click ──────────────────────────────
                try:
                    emotion_btns = page.locator("button[data-emotion], [data-testid='emotion-btn']")
                    btn_count = await emotion_btns.count()
                    if btn_count > 0:
                        btn = emotion_btns.nth(random.randint(0, btn_count - 1))
                        await asyncio.sleep(random.uniform(0.4, 1.2))  # Hesitation before click
                        await btn.click(timeout=3000)
                        telemetry.actions_triggered.append("clicked_emotion_bar")
                        logger.debug("😊 EmotionBar click registered")
                except Exception:
                    pass

                # ── Interactive: Topical Internal Hop (same-pillar article) ─────────────
                # 55% of real readers click to another article in the same topic silo.
                site_url = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://gworky.com").rstrip("/")
                if random.random() < 0.55:
                    try:
                        # Find an internal same-pillar link on the page
                        hop_selector = (
                            f"a[href*='/{target.pillar}/'], "
                            f"a[href*='/article/']:not([href='{target.canonical_url}'])"
                        )
                        hop_links = page.locator(hop_selector)
                        hop_count = await hop_links.count()
                        if hop_count > 0:
                            chosen = hop_links.nth(random.randint(0, min(hop_count - 1, 4)))
                            hop_href = await chosen.get_attribute("href")
                            if hop_href:
                                hop_url = hop_href if hop_href.startswith("http") else f"{site_url}{hop_href}"
                                await asyncio.sleep(HumanPhysics.micro_pause())
                                await page.goto(hop_url, wait_until="domcontentloaded", timeout=30000)
                                hop_dwell = HumanPhysics.calculate_topical_hop_dwell()
                                # Quick preview scroll on the hopped page
                                hop_scroll_steps = HumanPhysics.generate_bezier_scroll_steps(100, steps=4)
                                for pct in hop_scroll_steps:
                                    await page.evaluate(
                                        f"window.scrollTo(0, ((document.body||document.documentElement).scrollHeight||1000)*{pct/100.0});"
                                    )
                                    await asyncio.sleep(hop_dwell / (len(hop_scroll_steps) * 4))
                                telemetry.actions_triggered.append(f"topical_hop:{hop_url[-45:]}")
                                logger.debug("🔗 Topical hop → %s (%ds)", hop_url[-50:], hop_dwell)
                    except Exception as e:
                        logger.debug("Topical hop skipped: %s", e)

                # ── Interactive: Calculator Simulation (40% probability) ──────────────────
                # Simulates a reader using the paired tool — the strongest engagement signal.
                if target.related_tool_slug and random.random() < 0.40:
                    tool_url = f"{site_url}/tools/{target.related_tool_slug}"
                    try:
                        await asyncio.sleep(HumanPhysics.micro_pause())  # Think before clicking
                        await page.goto(tool_url, wait_until="domcontentloaded", timeout=30000)
                        telemetry.actions_triggered.append(f"opened_tool:{target.related_tool_slug}")

                        # Fill calculator inputs with realistic values
                        scenarios = CALCULATOR_SCENARIO_MAP.get(target.related_tool_slug, [])
                        filled = 0
                        for selector, value in scenarios:
                            try:
                                # Try each comma-separated selector variant
                                for sel in [s.strip() for s in selector.split(",")]:
                                    el = page.locator(sel).first
                                    if await el.count() > 0:
                                        # H6: per-keystroke human cadence (not a uniform pause),
                                        # plus a short think-time before the field gets focus.
                                        await asyncio.sleep(random.uniform(0.25, 0.55))
                                        await el.click(timeout=3000)
                                        await asyncio.sleep(HumanPhysics.type_with_physics(value))
                                        await el.fill(value, timeout=3000)
                                        filled += 1
                                        break
                            except Exception:
                                pass  # Input not found on this page variant — skip

                        if filled > 0:
                            # Click calculate / submit button
                            try:
                                calc_btn = page.locator(
                                    "button[type='submit'], button:has-text('Calculate'), "
                                    "button:has-text('Get Result'), button:has-text('Estimate')"
                                ).first
                                if await calc_btn.count() > 0:
                                    await asyncio.sleep(random.uniform(0.5, 1.5))
                                    await calc_btn.click(timeout=3000)
                                    telemetry.actions_triggered.append("calculator_submitted")
                            except Exception:
                                pass

                        # Read the result — strongest dwell signal
                        calc_dwell = HumanPhysics.calculate_calculator_dwell()
                        await asyncio.sleep(calc_dwell)
                        telemetry.actions_triggered.append(
                            f"calculator_result_read:{calc_dwell}s"
                        )
                        logger.debug(
                            "🧮 Calculator [%s] — %d fields filled, result read %ds",
                            target.related_tool_slug, filled, calc_dwell,
                        )
                    except Exception as e:
                        logger.debug("Calculator simulation notice: %s", e)

                elapsed = int(time.time() - start_time)
                telemetry.dwell_time_seconds = max(elapsed, target_dwell // 2)

                await browser.close()
                telemetry.status = "completed"

        except Exception as e:
            logger.error(f"Error in browser session [{session_id}]: {e}")
            telemetry.status = "failed"
            telemetry.error_message = str(e)

        return telemetry

    async def execute_fast_synthetic_session(
        self,
        target: SimulationTarget,
        persona: BrowserPersona,
        referral: ReferralContext,
    ) -> SessionTelemetry:
        """Fast lightweight HTTP-level synthetic session with realistic physics calculations."""
        import httpx

        session_id = f"gw_sess_{uuid.uuid4().hex[:12]}"
        dwell = HumanPhysics.calculate_reading_time_seconds(target.word_count)
        scroll = random.randint(65, 100)

        actions = [
            f"arrived_from_{referral.channel}",
            f"read_article_{target.word_count}_words",
            f"scrolled_{scroll}pct",
        ]

        if referral.search_query:
            actions.append(f"searched_query:{referral.search_query[:30]}")
        if referral.youtube_video_id:
            actions.append(f"youtube_ref:{referral.youtube_video_id}")

        if random.random() < 0.70:
            actions.append("inspected_faq_schema")
        if random.random() < 0.50:
            actions.append(f"interacted_tool_{target.related_tool_slug or 'calculator'}")
        if random.random() < 0.40:
            actions.append("voted_emotion_bar")

        telemetry = SessionTelemetry(
            session_id=session_id,
            target_channel=referral.channel,
            referrer_url=referral.referrer_url,
            article_slug=target.article_slug,
            geo_region=persona.geo_region,
            persona_name=persona.name,
            dwell_time_seconds=dwell,
            scroll_depth_percent=scroll,
            actions_triggered=actions,
            status="completed",
        )

        # Ping canonical URL with authentic referral headers
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                await client.get(
                    target.canonical_url,
                    headers={
                        "User-Agent": persona.user_agent,
                        "Referer": referral.referrer_url,
                        "Accept-Language": persona.accept_language,
                        "Sec-Ch-Ua": persona.sec_ch_ua,
                        **referral.extra_headers,
                    },
                )
        except Exception as e:
            logger.debug(f"HTTP Ping notice for {target.article_slug}: {e}")

        return telemetry

    async def log_session_to_supabase(self, t: SessionTelemetry) -> None:
        """Persists session telemetry into Supabase `synthetic_engagement_logs`."""
        if self.dry_run:
            logger.info(
                f"🧪 [DRY-RUN] Telemetry: {t.session_id} | {t.target_channel} | {t.dwell_time_seconds}s | {t.scroll_depth_percent}% | {t.actions_triggered}"
            )
            return

        payload = {
            "session_id": t.session_id,
            "article_slug": t.article_slug,
            "geo_region": t.geo_region,
            "target_platform": t.target_channel,
            "keyword": t.referrer_url,
            "dwell_time_seconds": t.dwell_time_seconds,
            "scroll_depth_percent": t.scroll_depth_percent,
            "actions_triggered": t.actions_triggered,
            "status": t.status,
            "error_message": t.error_message,
        }

        try:
            self.supabase.table("synthetic_engagement_logs").insert(payload).execute()
            logger.info(
                f"✅ Telemetry Persisted [{t.session_id}] ({t.target_channel.upper()}) → {t.article_slug} (Dwell: {t.dwell_time_seconds}s, Scroll: {t.scroll_depth_percent}%)"
            )
        except Exception as e:
            logger.error(f"Failed to log session to Supabase: {e}")


# ==============================================================================
# 6. CLI ORCHESTRATOR & ENTRYPOINT
# ==============================================================================


async def main() -> None:
    parser = argparse.ArgumentParser(description="Groundwork Autonomous Organic Traffic & Engagement Engine")
    parser.add_argument("--limit", type=int, default=5, help="Number of article targets to simulate")
    parser.add_argument("--slug", type=str, default=None, help="Target a specific article slug")
    parser.add_argument(
        "--channel",
        choices=["google_search", "youtube_gworky", "topic_silo"],
        default=None,
        help="Force specific referral channel",
    )
    parser.add_argument("--browser", action="store_true", help="Use full headless Playwright DOM browser")
    parser.add_argument("--proxy", type=str, default=None, help="Optional HTTP/SOCKS5 proxy URL")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without writing to Supabase")
    parser.add_argument(
        "--benchmark",
        nargs="?",
        const="sannysoft",
        choices=["sannysoft", "browserleaks_js", "browserleaks_webrtc", "diagnostics", "sov"],
        help="Run stealth effectiveness benchmark (default: sannysoft) and exit; 'sov' measures brand share-of-voice vs big brands",
    )
    parser.add_argument(
        "--benchmark-proxy",
        action="store_true",
        help="Use DataImpulse residential egress during benchmark (US geo)",
    )
    args = parser.parse_args()

    # ── Benchmark Mode: fingerprint verification against detection suites ──────
    if args.benchmark:
        from browser_diagnostics import run_fingerprint_diagnostics

        benchmark_proxy: str | None = None
        if args.benchmark_proxy:
            try:
                from egress_selector import SmartPolicySelector

                benchmark_proxy = SmartPolicySelector().get_proxy(task_type="browse", geo="us")
                logger.info(f"🌐 Benchmark egress: {benchmark_proxy}")
            except Exception as e:
                logger.debug(f"Benchmark proxy fallback: {e}")

        from playwright.async_api import async_playwright

        # ── SOV Benchmark: brand share-of-voice vs big brands (no browser) ────────
        if args.benchmark == "sov":
            import json
            import urllib.parse
            import urllib.request

            def _suggest(query: str, max_results: int = 10) -> list[str]:
                url = f"https://suggestqueries.google.com/complete/search?client=firefox&q={urllib.parse.quote(query)}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                return [str(s) for s in data[1][:max_results]] if len(data) > 1 else []

            hijack_triggers = ("calculator", "alternative", " vs ", "review", "rates", "cost", "quote", "best")
            brands = {
                "money": ["nerdwallet", "bankrate", "smartasset", "investopedia"],
                "body": ["healthline", "webmd", "verywellhealth"],
                "home": ["energysage", "angi", "bobvila"],
                "tech": ["cnet", "pcmag", "tomsguide"],
            }
            rows = []
            total_kw = 0
            for pillar, brand_list in brands.items():
                for brand in brand_list:
                    try:
                        sugs = _suggest(brand)
                    except Exception as e:
                        logger.warning("Suggest fail %s: %s", brand, e)
                        continue
                    hijack = [s for s in sugs if any(t in s.lower() for t in hijack_triggers) and "gworky" not in s]
                    for kw in hijack:
                        total_kw += 1
                        # SOV proxy: does Groundwork already publish on this keyword?
                        # Best-effort: match a pillar-relevant slug segment; real check
                        # is SERP presence (future SerpBear). Mark as coverage gap.
                        rows.append((pillar, brand, kw))
            logger.info(
                f"\n══════ SOV BENCHMARK — Brand Hijack Surface ══════\n"
                f"  Total brand-adjacent keywords surfaced: {total_kw}\n"
                f"  (Coverage check → SerpBear/SERP; this run enumerates the hijack surface)\n"
                f"═══════════════════════════════════════════════════"
            )
            for pillar, brand, kw in rows[:15]:
                logger.info(f"  [{pillar.upper():4}] {brand:<14} → {kw}")
            return

        passed_total = 0
        failed_total = 0
        for persona in random.sample(TIER1_PERSONAS, k=min(3, len(TIER1_PERSONAS))):
            logger.info(f"\n=== BENCHMARK — Persona: {persona.name} ({persona.platform}) ===")
            async with async_playwright() as p:
                launch_kwargs: dict[str, Any] = {
                    "headless": True,
                    "args": stealth_launch_args(),
                }
                if benchmark_proxy:
                    launch_kwargs["proxy"] = {"server": benchmark_proxy}
                browser = await p.chromium.launch(**launch_kwargs)
                context = await browser.new_context(
                    user_agent=persona.user_agent,
                    viewport={"width": persona.viewport_width, "height": persona.viewport_height},
                    locale=persona.accept_language.split(",")[0],
                    timezone_id=persona.timezone,
                    extra_http_headers={"Sec-Ch-Ua": persona.sec_ch_ua},
                )
                await context.add_init_script(_build_stealth_script(persona))
                page = await context.new_page()

                if args.benchmark == "diagnostics":
                    report = await run_fingerprint_diagnostics(page)
                    from browser_diagnostics import print_diagnostics_report

                    print_diagnostics_report(report)
                    passed_total += report["tests_passed"]
                    failed_total += report["tests_run"] - report["tests_passed"]
                else:
                    target_url = BENCHMARK_TARGETS[args.benchmark]
                    try:
                        await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
                        await asyncio.sleep(3)
                    except Exception as e:
                        logger.error(f"Benchmark load failed: {e}")
                        continue
                    # Extract pass/fail signal from detection suite tables
                    outcome = await page.evaluate(
                        """() => {
                            const rows = Array.from(document.querySelectorAll('tr'));
                            const total = rows.length;
                            let passed = 0;
                            for (const tr of rows) {
                                const tds = Array.from(tr.querySelectorAll('td'));
                                if (tds.length === 0) continue;
                                const last = tds[tds.length - 1];
                                const txt = (last.textContent || '').toLowerCase();
                                const cls = (last.className || '').toLowerCase();
                                if (/pass|present|ok|✓|√|passed/.test(txt) || /pass/.test(cls)) passed++;
                            }
                            return { total, passed, title: document.title };
                        }"""
                    )
                    logger.info(
                        f"  🎯 {target_url}\n"
                        f"  Title: {outcome.get('title', 'N/A')}\n"
                        f"  Detected rows: {outcome.get('total')} | Passed signals: {outcome.get('passed')}"
                    )
                    # Fallback: fingerprint diagnostics as secondary signal
                    diag = await run_fingerprint_diagnostics(page)
                    logger.info(
                        f"  🔬 Fingerprint diagnostics: {diag['tests_passed']}/{diag['tests_run']} passed"
                        f" (webdriver={diag['details']['navigator_props']['properties'].get('webdriver')})"
                    )
                    passed_total += outcome.get("passed", 0)
                    failed_total += outcome.get("total", 0) - outcome.get("passed", 0)

                await browser.close()

        logger.info(
            f"\n══════ BENCHMARK SUMMARY ══════\n"
            f"  Combined passed signals: {passed_total} | failed: {failed_total}\n"
            f"═══════════════════════════════"
        )
        return

    # Supabase Client Init
    supabase_url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_key:
        logger.error("Supabase credentials not configured in environment.")
        sys.exit(1)

    proxy_url = args.proxy
    if not proxy_url:
        try:
            from egress_selector import SmartPolicySelector

            proxy_url = SmartPolicySelector().get_proxy(task_type="browse", geo="us")
            if proxy_url:
                logger.info(f"🌐 Smart Egress auto-selected proxy: {proxy_url}")
        except Exception as e:
            logger.debug(f"Egress selector fallback: {e}")

    supabase = create_client(supabase_url, supabase_key)
    engine = AdvancedOrganicSimulator(
        supabase=supabase,
        dry_run=args.dry_run,
        use_browser=args.browser,
        proxy_url=proxy_url,
    )

    targets = resolve_simulation_targets(supabase, limit=args.limit, specific_slug=args.slug)
    if not targets:
        logger.info("No targets resolved. Exiting.")
        return

    logger.info(f"🎯 Resolved {len(targets)} simulation targets across Tier-1 regions.")

    for idx, target in enumerate(targets, 1):
        persona = get_random_persona()
        referral = generate_referral_context(
            article_slug=target.article_slug,
            article_title=target.article_title,
            pillar=target.pillar,
            youtube_video_id=target.youtube_video_id,
            preferred_channel=args.channel,
        )

        logger.info(f"\n--- Target [{idx}/{len(targets)}]: {target.article_title[:45]}... ---")
        logger.info(f"👤 Persona: {persona.name} ({persona.city}, {persona.geo_region})")
        logger.info(f"🔗 Channel: {referral.channel} (Referrer: {referral.referrer_url[:60]}...)")

        if args.browser:
            telemetry = await engine.execute_browser_session(target, persona, referral)
        else:
            telemetry = await engine.execute_fast_synthetic_session(target, persona, referral)

        await engine.log_session_to_supabase(telemetry)


if __name__ == "__main__":
    asyncio.run(main())
