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

CDP_STEALTH_SCRIPT = """
(() => {
    // 1. Remove automation indicators
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    delete navigator.__proto__.webdriver;

    // 2. Mock Chrome runtime
    window.chrome = {
        runtime: {},
        loadTimes: function() {},
        csi: function() {},
        app: {}
    };

    // 3. Mock plugins
    Object.defineProperty(navigator, 'plugins', {
        get: () => [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
            { name: 'Native Client', filename: 'internal-nacl-plugin' }
        ]
    });

    // 4. Randomize WebGL renderer slightly
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) return 'Google Inc. (Apple)';
        if (parameter === 37446) return 'ANGLE (Apple, Apple M1 Pro, OpenGL 4.1)';
        return getParameter.apply(this, arguments);
    };

    // 5. Spoof Battery & Permissions API
    if (navigator.getBattery) {
        navigator.getBattery = async () => ({
            charging: true,
            chargingTime: 0,
            dischargingTime: Infinity,
            level: 0.98,
            addEventListener: () => {}
        });
    }
})();
"""


class HumanPhysics:
    """Generates natural human motion curves and reading timings."""

    @staticmethod
    def calculate_reading_time_seconds(word_count: int) -> int:
        """Calculates human reading dwell time (180–240 words per minute + inspection pauses)."""
        wpm = random.uniform(180, 240)
        base_seconds = (word_count / wpm) * 60.0
        # Add random pause factor (skimming vs deep reading)
        pause_factor = random.uniform(0.6, 1.1)
        dwell = int(base_seconds * pause_factor)
        return max(15, min(dwell, 180))  # Bound between 15s and 3 minutes

    @staticmethod
    def generate_bezier_scroll_steps(total_height: int, steps: int = 8) -> list[int]:
        """Generates non-linear scroll checkpoints simulating human thumb/mousewheel."""
        checkpoints = []
        for i in range(1, steps + 1):
            t = i / steps
            # Smooth Ease-in-out Cubic Bezier: 3*t^2 - 2*t^3
            eased_t = 3 * (t**2) - 2 * (t**3)
            # Add small random human jitter (+-3%)
            jitter = random.uniform(-0.03, 0.03)
            pos = int(total_height * min(1.0, max(0.0, eased_t + jitter)))
            checkpoints.append(pos)
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
                    "args": [
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                    ],
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

                # Inject CDP Stealth Script
                await context.add_init_script(CDP_STEALTH_SCRIPT)

                page = await context.new_page()

                # AdSense Zero-Fraud Firewall: Block all ad networks
                async def block_ads(route: Any, request: Any) -> None:
                    ad_domains = [
                        "googlesyndication.com",
                        "doubleclick.net",
                        "googleadservices.com",
                        "adnxs.com",
                        "amazon-adsystem.com",
                        "criteo.com",
                    ]
                    if any(domain in request.url for domain in ad_domains):
                        await route.abort()
                    else:
                        await route.continue_()

                await page.route("**/*", block_ads)

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

                # Interactive Action: EmotionBar reaction click
                try:
                    emotion_btns = page.locator("button[data-emotion]")
                    btn_count = await emotion_btns.count()
                    if btn_count > 0:
                        btn = emotion_btns.nth(random.randint(0, btn_count - 1))
                        await btn.click(timeout=3000)
                        telemetry.actions_triggered.append("clicked_emotion_bar")
                except Exception:
                    pass

                # Interactive Action: Navigate to Paired Calculator (Topic Silo)
                if target.related_tool_slug and random.random() < 0.40:
                    site_url = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://gworky.com").rstrip("/")
                    tool_url = f"{site_url}/tools/{target.related_tool_slug}"
                    await page.goto(tool_url, wait_until="domcontentloaded", timeout=30000)
                    telemetry.actions_triggered.append(f"opened_tool_{target.related_tool_slug}")
                    await asyncio.sleep(random.uniform(3, 8))

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
    args = parser.parse_args()

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
