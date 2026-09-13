#!/usr/bin/env python3
"""
Groundwork Heavy-Duty Autonomous Engagement & Traffic CLI
Master Terminal Interface for Visual Headed Browsing, Multi-Worker Concurrency,
Generative AI Agent Exploration, and DataImpulse Residential Proxy Routing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import sys
import time
import urllib.parse
import uuid
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

_agents_dir = str(Path(__file__).resolve().parent)
if _agents_dir not in sys.path:
    sys.path.insert(0, _agents_dir)

from browser_stealth import (
    build_stealth_script,
    domain_is_blocked,
    stealth_launch_args,
)
from egress_dataimpulse import DataImpulseProxyRouter
from egress_selector import EgressSelector


# Load environment from .env.local
def _load_env() -> None:
    env_file = Path(__file__).resolve().parent.parent / ".env.local"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'").strip('"')
                if k and k not in os.environ:
                    os.environ[k] = v


_load_env()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("traffic_cli")


# ==============================================================================
# 2. PERSONA & DEVICE PROFILES
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


PERSONAS: list[BrowserPersona] = [
    BrowserPersona(
        name="US_NYC_Mac_Chrome",
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
        name="US_Seattle_Win_Chrome",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        sec_ch_ua='"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
        platform="Windows",
        geo_region="US",
        city="Seattle",
        timezone="America/Los_Angeles",
        accept_language="en-US,en;q=0.9",
        viewport_width=1536,
        viewport_height=864,
    ),
    BrowserPersona(
        name="UK_London_Mac_Safari",
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
        name="AU_Sydney_Win_Firefox",
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
    BrowserPersona(
        name="US_Austin_iPhone_Safari",
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_6_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
        sec_ch_ua='"Mobile Safari";v="17"',
        platform="iOS",
        geo_region="US",
        city="Austin",
        timezone="America/Chicago",
        accept_language="en-US,en;q=0.9",
        viewport_width=390,
        viewport_height=844,
        is_mobile=True,
    ),
]


def get_persona() -> BrowserPersona:
    return random.choice(PERSONAS)


def _build_stealth_script(persona: BrowserPersona) -> str:
    """Persona-aware CDP stealth script (fingerprint matrix sync)."""
    return build_stealth_script(
        platform=persona.platform,
        is_mobile=persona.is_mobile,
        is_firefox="Firefox" in persona.user_agent,
        session_seed=f"{persona.name}-{persona.city}",
    )


# ==============================================================================
# 4. REFERRAL SYNTHESIS & TARGET MATCHER
# ==============================================================================


@dataclass
class SessionTarget:
    article_id: str
    article_slug: str
    article_title: str
    pillar: str
    word_count: int
    canonical_url: str
    youtube_video_id: str | None = None
    related_tool_slug: str | None = None


PILLAR_TOOLS = {
    "home": "solar-payback-calculator",
    "money": "refinance-calculator",
    "body": "tdee-macro-calculator",
    "tech": "ai-api-pricing-calculator",
    "life": "freelance-rate-calculator",
}


def build_referral(target: SessionTarget, preferred_channel: str | None = None) -> tuple[str, str, dict[str, str]]:
    """Generates authentic Google Search, YouTube, or Topic Silo referrers."""
    site_url = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://gworky.com").rstrip("/")
    dest_url = f"{site_url}/article/{target.article_slug}"

    if not preferred_channel or preferred_channel == "all":
        dice = random.random()
        channel = "google_search" if dice < 0.60 else ("youtube_gworky" if dice < 0.90 else "topic_silo")
    else:
        channel = preferred_channel

    if channel == "google_search":
        q = f"{target.pillar} {target.article_title.lower()}".replace("how to how to", "how to")
        ved = f"2ahUKEwi{uuid.uuid4().hex[:12]}_{uuid.uuid4().hex[:6]}"
        ref = f"https://www.google.com/url?sa=t&rct=j&q={urllib.parse.quote_plus(q)}&esrc=s&source=web&cd=1&ved={ved}&url={urllib.parse.quote_plus(dest_url)}"
        headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
        return channel, ref, headers

    elif channel == "youtube_gworky":
        v_id = target.youtube_video_id or "-yh59eacYJM"
        ref = f"https://www.youtube.com/watch?v={v_id}"
        headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
        return channel, ref, headers

    else:
        ref = f"{site_url}/{target.pillar}"
        headers = {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
        return channel, ref, headers


# ==============================================================================
# 5. HEAVY-DUTY PLAYWRIGHT WORKER ENGINE
# ==============================================================================


async def run_single_session(
    target: SessionTarget,
    persona: BrowserPersona,
    supabase: Any,
    headed: bool = False,
    use_ai: bool = False,
    channel: str | None = None,
    dry_run: bool = False,
    worker_id: int = 1,
) -> dict[str, Any]:
    """Executes a full-DOM Playwright session with CDP stealth and AdSense firewall."""
    from playwright.async_api import async_playwright

    session_id = f"gw_{uuid.uuid4().hex[:10]}"
    proxy_url = DataImpulseProxyRouter.get_proxy_url(persona.geo_region, session_id)
    ref_channel, referrer_url, extra_headers = build_referral(target, preferred_channel=channel)

    logger.info(f"⚡ [Worker #{worker_id}] Session [{session_id}] via {ref_channel.upper()} for: {target.article_slug}")
    if proxy_url:
        logger.info(f"   🛡️ Proxy: DataImpulse ({persona.geo_region}) -> {proxy_url.split('@')[-1]}")

    start_time = time.time()
    actions = [f"arrived_from_{ref_channel}"]
    scroll_depth = 0
    status = "completed"
    error_msg = None

    try:
        async with async_playwright() as p:
            launch_args = stealth_launch_args()
            launch_kwargs: dict[str, Any] = {
                "headless": not headed,
                "args": launch_args,
            }
            if headed:
                launch_kwargs["slow_mo"] = 100  # Visible human speed

            if proxy_url:
                launch_kwargs["proxy"] = {"server": proxy_url}

            browser = await p.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                user_agent=persona.user_agent,
                viewport={"width": persona.viewport_width, "height": persona.viewport_height},
                locale=persona.accept_language.split(",")[0],
                timezone_id=persona.timezone,
                extra_http_headers={
                    "Referer": referrer_url,
                    "Sec-Ch-Ua": persona.sec_ch_ua,
                    **extra_headers,
                },
            )

            await context.add_init_script(_build_stealth_script(persona))
            page = await context.new_page()

            # AdSense Zero-Fraud Firewall (shared SSOT list — blocks ALL ad networks)
            async def block_ads(route: Any, request: Any) -> None:
                if domain_is_blocked(request.url):
                    await route.abort()
                else:
                    await route.continue_()

            await page.route("**/*", block_ads)

            # 1. Open target article
            try:
                await page.goto(target.canonical_url, wait_until="load", timeout=30000)
            except Exception:
                await page.goto(target.canonical_url, wait_until="commit", timeout=20000)
            actions.append(f"loaded_article_{target.word_count}_words")

            # 2. Human Reading & Cubic Bezier Scrolling
            wpm = random.uniform(180, 240)
            target_dwell = int((target.word_count / wpm) * 60 * random.uniform(0.6, 1.0))
            target_dwell = max(15, min(target_dwell, 120))  # Max 2 mins in test

            checkpoints = [15, 35, 55, 75, 90, 100]
            for pct in checkpoints:
                await page.evaluate(
                    f"window.scrollTo({{ top: ((document.body ? document.body.scrollHeight : (document.documentElement ? document.documentElement.scrollHeight : 1000)) || 1000) * {pct / 100.0}, behavior: 'smooth' }});"
                )
                scroll_depth = pct
                await asyncio.sleep(target_dwell / len(checkpoints))

            actions.append(f"scrolled_{scroll_depth}pct")

            # 3. EmotionBar Interaction
            try:
                emotion_btns = page.locator("button[data-emotion]")
                btn_count = await emotion_btns.count()
                if btn_count > 0:
                    btn = emotion_btns.nth(random.randint(0, btn_count - 1))
                    await btn.click(timeout=3000)
                    actions.append("clicked_emotion_bar")
            except Exception:
                pass

            # 4. Multi-Step Rabbit-Hole: Run Paired Calculator
            if target.related_tool_slug and random.random() < 0.60:
                site_url = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://gworky.com").rstrip("/")
                tool_url = f"{site_url}/tools/{target.related_tool_slug}"
                await page.goto(tool_url, wait_until="domcontentloaded", timeout=30000)
                actions.append(f"opened_tool_{target.related_tool_slug}")

                # Simulate calculator input interaction
                try:
                    inputs = page.locator('input[type="number"], input[type="range"]')
                    inp_count = await inputs.count()
                    if inp_count > 0:
                        inp = inputs.first
                        await inp.fill(str(random.randint(10, 500)))
                        actions.append(f"simulated_calc_input_{target.related_tool_slug}")
                except Exception:
                    pass

                await asyncio.sleep(random.uniform(4, 10))

            await browser.close()

    except Exception as e:
        logger.error(f"❌ Worker #{worker_id} error: {e}")
        status = "failed"
        error_msg = str(e)

    elapsed_dwell = max(int(time.time() - start_time), 15)

    telemetry = {
        "session_id": session_id,
        "article_slug": target.article_slug,
        "geo_region": persona.geo_region,
        "target_platform": ref_channel,
        "keyword": referrer_url,
        "dwell_time_seconds": elapsed_dwell,
        "scroll_depth_percent": scroll_depth,
        "actions_triggered": actions,
        "status": status,
        "error_message": error_msg,
    }

    # Persist to Supabase
    if not dry_run and supabase:
        try:
            supabase.table("synthetic_engagement_logs").insert(telemetry).execute()
            logger.info(
                f"✅ [Worker #{worker_id}] Telemetry Logged -> {target.article_slug} (Dwell: {elapsed_dwell}s, Scroll: {scroll_depth}%)"
            )
        except Exception as e:
            logger.error(f"Failed to persist telemetry: {e}")
    else:
        logger.info(
            f"🧪 [Worker #{worker_id}] [DRY-RUN] Telemetry -> Dwell: {elapsed_dwell}s, Scroll: {scroll_depth}%, Actions: {len(actions)}"
        )

    return telemetry


# ==============================================================================
# 5B. SUBSYSTEM D: QUALIFIED YOUTUBE WATCH-TIME & SHORTS LOOP ENGINE
# ==============================================================================


async def run_youtube_watch_session(
    video_url: str,
    persona: BrowserPersona,
    duration_sec: int = 300,
    headed: bool = False,
    search_keyword: str | None = None,
    funnel_mode: str = "auto",
    quality: str = "144p",
    worker_id: int = 1,
    egress_mode: str = "auto",
) -> dict[str, Any]:
    """Executes a qualified YouTube Watch-Time session with multi-source organic funnel, 144p bandwidth throttle, and search-to-watch."""
    from playwright.async_api import async_playwright

    session_id = f"yt_{uuid.uuid4().hex[:10]}"
    
    # Tiered Multi-Egress Selector (Structured Auth - Zero 407 Bug)
    if egress_mode == "direct" or os.environ.get("PREFER_DIRECT_EGRESS") == "true":
        proxy_config = None
        logger.info(f"🌐 [Worker #{worker_id}] Using Tier 3 Direct Clean Egress")
    else:
        egress_mgr = EgressSelector()
        proxy_config = egress_mgr.get_playwright_proxy(
            task_type="youtube_watch",
            geo=persona.geo_region,
            session_id=session_id,
        )
    
    # Extract video ID for in-page click targeting
    video_id = ""
    if "youtu.be/" in video_url:
        video_id = video_url.split("youtu.be/")[1].split("?")[0].split("&")[0]
    elif "v=" in video_url:
        video_id = video_url.split("v=")[1].split("&")[0]

    # Resolve discovery funnel mode (Natural Organic Multi-Vector Distribution)
    if funnel_mode == "auto":
        roll = random.random()
        if roll < 0.35:
            active_funnel = "search"          # 35% YouTube Search
        elif roll < 0.60:
            active_funnel = "channel"         # 25% Channel Pages / Browse
        elif roll < 0.80:
            active_funnel = "google_search"   # 20% External (Google SERP Referrer)
        elif roll < 0.90:
            active_funnel = "embed"           # 10% External (gworky.com embed)
        else:
            active_funnel = "suggested"       # 10% Suggested / Shorts Bridge
    else:
        active_funnel = funnel_mode

    logger.info(f"▶️ [Worker #{worker_id}] YouTube Watch-Time [{session_id}] | Funnel: {active_funnel} | Target: {video_url} ({duration_sec}s, {quality})")

    start_time = time.time()
    actions = ["initialized_youtube_worker", f"funnel_{active_funnel}"]

    async with async_playwright() as p:
        launch_kwargs: dict[str, Any] = {
            "headless": not headed,
            "args": stealth_launch_args(),
        }
        if headed:
            launch_kwargs["slow_mo"] = 60
        if proxy_config:
            launch_kwargs["proxy"] = proxy_config

        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            user_agent=persona.user_agent,
            viewport={"width": persona.viewport_width, "height": persona.viewport_height},
            locale=persona.accept_language.split(",")[0],
            timezone_id=persona.timezone,
        )
        await context.add_init_script(_build_stealth_script(persona))
        page = await context.new_page()

        # AdSense Zero-Fraud Firewall (Protect YouTube video stream)
        async def block_ads(route: Any, request: Any) -> None:
            url_low = request.url.lower()
            if any(t in url_low for t in ("googlevideo.com", "youtube.com", "ytimg.com", "google.com", "ggpht.com")):
                await route.continue_()
                return
            if domain_is_blocked(request.url):
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", block_ads)

        # Keyword resolution for search-driven funnels (AI Cognitive Intent Synthesis)
        if not search_keyword:
            fallback_pool = [
                "Groundwork Master Briefing 2026",
                "household capital allocation debt mortgages wealth Groundwork",
                "mortgage recast vs refinance 2026 interest rate traps Groundwork",
                "Groundwork personal finance research guide",
                "AI tools autonomous defense analysis Groundwork",
                "evidence based financial decisions Groundwork",
            ]
            try:
                from llm_router import LLMRouter
                router = LLMRouter()
                prompt = (
                    "You are simulating a realistic human viewer in the US/UK searching for an insightful guide on YouTube or Google. "
                    f"The target video discusses personal finance, mortgages, wealth allocation, and evidence-based living: {video_url}. "
                    "Output ONLY a natural, realistic 3-6 word search query that a real person would type into the search bar. "
                    "Do not include quotes, punctuation, or any other text."
                )
                query = await asyncio.to_thread(router.generate, prompt, "text", 30, 0.7, 3.0)
                if query and len(query.strip()) > 5:
                    clean_q = query.strip().replace('"', '').replace("'", "").replace("\n", " ")
                    logger.info(f"🧠 [Worker #{worker_id}] AI Cognitive Intent Synthesized: '{clean_q}'")
                    search_keyword = clean_q
                else:
                    search_keyword = random.choice(fallback_pool)
            except Exception as e:
                logger.debug(f"AI cognitive intent fallback: {e}")
                search_keyword = random.choice(fallback_pool)

        in_page_clicked = False

        try:
            # Funnel Step 1: Execute funnel discovery
            if active_funnel == "search":
                logger.info(f"🔍 [Worker #{worker_id}] Executing YouTube Search-to-Watch: '{search_keyword}'")
                try:
                    await page.goto("https://www.youtube.com", wait_until="commit", timeout=20000)
                    await asyncio.sleep(random.uniform(1.5, 2.5))
                    for btn_text in ["Accept all", "I agree", "Reject all", "Before you continue", "Accept"]:
                        try:
                            btn = page.locator(f"button:has-text('{btn_text}')").first
                            if await btn.is_visible(timeout=800):
                                await btn.click()
                                break
                        except Exception:
                            pass
                    search_box = page.locator('input#search, input[name="search_query"]').first
                    if await search_box.is_visible(timeout=3000):
                        await search_box.click()
                        for char in search_keyword:
                            await page.keyboard.type(char, delay=random.randint(25, 75))
                        await page.keyboard.press("Enter")
                        await asyncio.sleep(random.uniform(2.5, 4.0))
                        actions.append("searched_keyword")

                        # In-page click discovery: search for video thumbnail or title in results
                        if video_id:
                            for scroll_pass in range(3):
                                target_card = page.locator(f"ytd-video-renderer a[href*='{video_id}'], ytd-video-renderer a#video-title[href*='{video_id}']").first
                                if await target_card.is_visible(timeout=1500):
                                    logger.info(f"🎯 [Worker #{worker_id}] Found target video in YouTube Search results! In-page clicking...")
                                    box = await target_card.bounding_box()
                                    if box:
                                        await page.mouse.move(box["x"] + box["width"]/2, box["y"] + box["height"]/2, steps=8)
                                    await target_card.click()
                                    in_page_clicked = True
                                    actions.append("clicked_search_result")
                                    break
                                await page.evaluate("window.scrollBy({ top: 450, behavior: 'smooth' });")
                                await asyncio.sleep(random.uniform(1.0, 1.8))
                except Exception as e:
                    logger.warning(f"Search navigation notice: {e}")

            elif active_funnel == "channel":
                logger.info(f"📺 [Worker #{worker_id}] Executing Channel Browse: @gworkycom/videos (UC566b5USRdeZdErzEVUuOzQ)")
                try:
                    await page.goto("https://www.youtube.com/@gworkycom/videos", wait_until="commit", timeout=22000)
                    await asyncio.sleep(random.uniform(2.0, 3.5))
                    for btn_text in ["Accept all", "I agree", "Reject all", "Before you continue"]:
                        try:
                            btn = page.locator(f"button:has-text('{btn_text}')").first
                            if await btn.is_visible(timeout=800):
                                await btn.click()
                                break
                        except Exception:
                            pass
                    await page.evaluate("window.scrollBy({ top: 350, behavior: 'smooth' });")
                    await asyncio.sleep(random.uniform(1.2, 2.0))

                    if video_id:
                        target_card = page.locator(f"ytd-rich-item-renderer a[href*='{video_id}'], ytd-grid-video-renderer a[href*='{video_id}']").first
                        if await target_card.is_visible(timeout=2000):
                            logger.info(f"🎯 [Worker #{worker_id}] Found target video in Channel Videos grid! In-page clicking...")
                            await target_card.click()
                            in_page_clicked = True
                            actions.append("clicked_channel_video")
                except Exception as e:
                    logger.warning(f"Channel browse warning: {e}")

            elif active_funnel == "google_search":
                logger.info(f"🌐 [Worker #{worker_id}] Executing Google Search External Referral: '{search_keyword}'")
                try:
                    google_search_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(search_keyword)}"
                    await page.goto(google_search_url, wait_until="commit", timeout=20000)
                    await asyncio.sleep(random.uniform(2.0, 3.5))
                    await page.evaluate("window.scrollBy({ top: 250, behavior: 'smooth' });")
                    actions.append("visited_google_serp")
                except Exception as e:
                    logger.warning(f"Google search referrer warning: {e}")

            elif active_funnel == "embed":
                logger.info(f"🌐 [Worker #{worker_id}] Executing Embed Web Referral via gworky.com")
                try:
                    await page.goto("https://gworky.com/wire", wait_until="commit", timeout=20000)
                    await asyncio.sleep(random.uniform(2.5, 4.0))
                    await page.evaluate("window.scrollBy({ top: 350, behavior: 'smooth' });")
                    actions.append("visited_embed_referrer")
                except Exception as e:
                    logger.warning(f"Embed referrer warning: {e}")

            elif active_funnel in ("suggested", "shorts_bridge"):
                logger.info(f"📱 [Worker #{worker_id}] Executing Suggested / Shorts Bridge Co-visitation")
                try:
                    short_bridge_url = "https://youtu.be/Ygr-u9OZZWY"
                    await page.goto(short_bridge_url, wait_until="commit", timeout=20000)
                    await asyncio.sleep(random.uniform(5.0, 8.0))
                    actions.append("watched_shorts_bridge")
                except Exception as e:
                    logger.warning(f"Shorts bridge warning: {e}")

            # Step 2: Ensure target video is active (canonical watch URL prevents 302 redirect loops under proxy)
            canonical_watch_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else video_url
            clean_url = canonical_watch_url
            curr_url = page.url
            if in_page_clicked or (video_id and video_id in curr_url):
                logger.info(f"🎬 [Worker #{worker_id}] Target video active via organic in-page navigation: {curr_url}")
                actions.append("navigated_in_page_organic")
            else:
                ref_map = {
                    "search": f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(search_keyword)}",
                    "channel": "https://www.youtube.com/@gworkycom/videos",
                    "google_search": "https://www.google.com/",
                    "embed": "https://gworky.com/wire",
                    "suggested": "https://www.youtube.com/shorts/Ygr-u9OZZWY",
                }
                chosen_referer = ref_map.get(active_funnel, "https://www.youtube.com/")
                logger.info(f"🎬 [Worker #{worker_id}] Loading target video with referrer [{chosen_referer}]: {clean_url}")
                nav_success = False
                for attempt in range(2):
                    try:
                        await page.goto(clean_url, referer=chosen_referer, wait_until="domcontentloaded", timeout=25000)
                        nav_success = True
                        break
                    except Exception as e:
                        logger.warning(f"Worker #{worker_id} navigation attempt {attempt+1} notice: {e}")
                        await asyncio.sleep(2)

                if not nav_success:
                    if proxy_config:
                        logger.warning(f"⚠️ [Worker #{worker_id}] Proxy failed to reach YouTube. Tripping circuit breaker for automatic direct fallback.")
                        try:
                            from egress_dataimpulse import DataImpulseProxyRouter
                            for _ in range(3):
                                DataImpulseProxyRouter.record_failure()
                        except Exception:
                            pass
                    logger.error(f"❌ [Worker #{worker_id}] Navigation failed completely. Aborting.")
                    return {"status": "failed_navigation", "session_id": session_id, "duration": 0}

                actions.append("opened_video_url")

            # Wait for player attachment
            try:
                await page.wait_for_selector("#movie_player, ytd-player, video", state="attached", timeout=15000)
            except Exception:
                pass

            # Auto-dismiss Google / GDPR consent dialogs
            for btn_text in ["Accept all", "I agree", "Reject all", "Before you continue", "Accept"]:
                try:
                    btn = page.locator(f"button:has-text('{btn_text}')").first
                    if await btn.is_visible(timeout=1000):
                        await btn.click()
                        actions.append(f"dismissed_{btn_text.lower().replace(' ', '_')}")
                        break
                except Exception:
                    pass

            # Trigger play button if large overlay is present
            try:
                play_btn = page.locator(".ytp-play-button, .ytp-large-play-button").first
                if await play_btn.is_visible(timeout=1500):
                    await play_btn.click()
            except Exception:
                pass

            # Inject 144p resolution & start playback muted for guaranteed browser autoplay compliance
            await page.evaluate("""() => {
                try {
                    const player = document.getElementById('movie_player');
                    if (player) {
                        player.setPlaybackQualityRange('tiny', 'tiny');
                        player.setPlaybackQuality('tiny');
                        if (player.playVideo) player.playVideo();
                    }
                    const btn = document.querySelector('.ytp-large-play-button, button.ytp-play-button');
                    if (btn) btn.click();
                    const v = document.querySelector('video');
                    if (v) {
                        v.muted = true;
                        v.play().catch(() => {});
                    }
                } catch(e) {}
            }""")
            actions.append("played_video_144p")

            # ── HARD STRICT PLAYBACK GATE, SMART SEEKING & AUTO-LOOPING ───────
            sample_interval = 10.0
            elapsed = 0.0
            last_t = -1.0
            stalled_streak = 0
            accumulated_playback = 0.0
            speeds = [1.0, 1.25, 1.0]

            # Smart Seeking & Looping State Machine (Mandatory Default)
            loops_completed = 0
            total_v_duration = 0.0
            curr_t = 0.0
            checkpoint_dwell_timer = 0.0
            current_checkpoint_idx = 0
            # Define 5 progressive checkpoints across video timeline
            base_checkpoints = [0.03, 0.25, 0.50, 0.75, 0.95]
            active_checkpoints = [min(0.98, max(0.01, cp + random.uniform(-0.02, 0.03))) for cp in base_checkpoints]
            # Dynamic checkpoint dwell so video reaches 100% completion and loops within duration_sec
            remaining_time = max(10.0, duration_sec - (time.time() - start_time))
            remaining_steps = max(1, len(active_checkpoints) - 1)
            target_checkpoint_dwell = max(8.0, min(30.0, (remaining_time * 0.48) / remaining_steps))

            while elapsed < duration_sec:
                sleep_dur = min(sample_interval, max(1.0, duration_sec - elapsed))
                await asyncio.sleep(sleep_dur)
                elapsed = time.time() - start_time

                # Sample DOM playback state
                state = await page.evaluate("""() => {
                    const v = document.querySelector('video');
                    const player = document.getElementById('movie_player');
                    if (!v) return { found: false };
                    return {
                        found: true,
                        currentTime: v.currentTime,
                        paused: v.paused,
                        duration: v.duration,
                        ended: v.ended,
                        playerState: player && player.getPlayerState ? player.getPlayerState() : null
                    };
                }""")

                if not state.get("found"):
                    stalled_streak += 1
                    logger.warning(f"⚠️ [Worker #{worker_id}] Video element not found (streak={stalled_streak})")
                else:
                    curr_t = float(state.get("currentTime", 0.0))
                    paused = state.get("paused", True)
                    p_state = state.get("playerState")
                    total_v_duration = float(state.get("duration", 0.0) or 0.0)
                    is_ended = bool(state.get("ended", False)) or (p_state == 0)

                    # Advance confirmed when currentTime increases while playing
                    if curr_t > last_t and not paused and p_state == 1:
                        # Once active playback is verified, ensure active audio context in DOM (Chromium --mute-audio protects speakers)
                        try:
                            await page.evaluate("""() => {
                                const p = document.getElementById('movie_player');
                                if (p && p.unMute) p.unMute();
                                if (p && p.setVolume) p.setVolume(25);
                                const v = document.querySelector('video');
                                if (v) { v.muted = false; v.volume = 0.25; }
                            }""")
                        except Exception:
                            pass

                        # Protect against timeline jumps inflating real streamed time
                        delta_t = curr_t - (last_t if last_t >= 0 else 0)
                        if delta_t > sample_interval * 2.0:
                            # A seek occurred; credit actual real-time streaming interval
                            real_streamed = min(sample_interval, sleep_dur)
                        else:
                            real_streamed = min(delta_t, sample_interval * 1.5)
                        accumulated_playback += real_streamed
                        checkpoint_dwell_timer += real_streamed
                        last_t = curr_t
                        stalled_streak = 0
                    else:
                        # If unstarted (-1), attempt multi-layer user-gesture & universal hotkey 'k'
                        if p_state == -1:
                            logger.info(f"⏳ [Worker #{worker_id}] Player unstarted (state=-1). Sending user gesture click & 'k' hotkey...")
                            try:
                                for btn_sel in ["button:has-text('Accept all')", "button:has-text('I agree')", "button:has-text('Reject all')", "form[action*='consent'] button", ".ytp-large-play-button"]:
                                    b = page.locator(btn_sel).first
                                    if await b.is_visible(timeout=400):
                                        await b.click()
                                        break
                                await page.mouse.click(640, 360)
                                await page.keyboard.press("k")
                                await page.keyboard.press("Space")
                                await page.locator("video, #movie_player, .ytp-play-button").first.click(timeout=1000)
                            except Exception:
                                pass
                        elif p_state == 3:
                            logger.info(f"⏳ [Worker #{worker_id}] Video buffering at {curr_t:.1f}s (playerState=3)...")
                        else:
                            stalled_streak += 1
                            logger.warning(f"⚠️ [Worker #{worker_id}] Playback stalled at {curr_t:.1f}s (paused={paused}, state={p_state}, streak={stalled_streak})")

                        # Auto-recovery: trigger movie_player.playVideo() and idempotent play
                        try:
                            await page.evaluate("""() => {
                                const player = document.getElementById('movie_player');
                                if (player && player.playVideo) player.playVideo();
                                const btn = document.querySelector('.ytp-large-play-button, button.ytp-play-button');
                                if (btn) {
                                    const v = document.querySelector('video');
                                    if (v && v.paused) btn.click();
                                }
                                const v = document.querySelector('video');
                                if (v) {
                                    v.muted = false;
                                    v.volume = 0.25;
                                    if (v.paused) v.play().catch(() => {});
                                }
                            }""")
                        except Exception:
                            pass

                    # Smart Seeking & 100% Completion Replay Engine
                    if total_v_duration >= 60.0:
                        # 1. Video finished check (100% Completion)
                        if is_ended or (curr_t >= total_v_duration - 3.0 and total_v_duration > 0):
                            loops_completed += 1
                            logger.info(
                                f"🔁 [Worker #{worker_id}] Video reached 100% completion! "
                                f"Full View Loop #{loops_completed} completed. Triggering Replay..."
                            )
                            actions.append(f"full_loop_{loops_completed}_100pct")
                            try:
                                await page.evaluate("""() => {
                                    const p = document.getElementById('movie_player');
                                    const v = document.querySelector('video');
                                    if (p && p.seekTo) { p.seekTo(0, true); p.playVideo(); }
                                    else if (v) { v.currentTime = 0; if (v.paused) v.play().catch(() => {}); }
                                }""")
                            except Exception:
                                pass
                            # Reset loop state with new random variations
                            active_checkpoints = [min(0.98, max(0.01, cp + random.uniform(-0.02, 0.03))) for cp in base_checkpoints]
                            current_checkpoint_idx = 0
                            checkpoint_dwell_timer = 0.0
                            rem_time = max(10.0, duration_sec - elapsed)
                            rem_steps = max(1, len(active_checkpoints) - 1)
                            target_checkpoint_dwell = max(8.0, min(30.0, (rem_time * 0.48) / rem_steps))
                            last_t = 0.0
                        # 2. Checkpoint Seeking progression
                        elif checkpoint_dwell_timer >= target_checkpoint_dwell:
                            if current_checkpoint_idx < len(active_checkpoints) - 1:
                                current_checkpoint_idx += 1
                                target_pct = active_checkpoints[current_checkpoint_idx]
                                target_sec = round(total_v_duration * target_pct, 1)
                                logger.info(
                                    f"⏩ [Worker #{worker_id}] Smart seeking to chapter {current_checkpoint_idx+1}/{len(active_checkpoints)} "
                                    f"({int(target_pct*100)}% -> {int(target_sec)}s / {int(total_v_duration)}s)..."
                                )
                                actions.append(f"seek_ch_{current_checkpoint_idx}_{int(target_pct*100)}pct")
                                try:
                                    await page.evaluate(f"""() => {{
                                        const p = document.getElementById('movie_player');
                                        const v = document.querySelector('video');
                                        if (p && p.seekTo) {{ p.seekTo({target_sec}, true); p.playVideo(); }}
                                        else if (v) {{ v.currentTime = {target_sec}; if (v.paused) v.play().catch(() => {{}}); }}
                                    }}""")
                                except Exception:
                                    pass
                                checkpoint_dwell_timer = 0.0
                                rem_time = max(10.0, duration_sec - elapsed)
                                rem_steps = (len(active_checkpoints) - 1) - current_checkpoint_idx
                                if rem_steps <= 0:
                                    # Final chapter before 100% completion; dwell remainder
                                    target_checkpoint_dwell = max(8.0, rem_time)
                                else:
                                    target_checkpoint_dwell = max(8.0, min(30.0, (rem_time * 0.48) / rem_steps))
                                last_t = target_sec

                # Circuit breaker: if stalled for 4 consecutive samples (60s), abort
                if stalled_streak >= 4:
                    logger.error(f"❌ [Worker #{worker_id}] Video stalled for 60s without advancement. Aborting.")
                    break

                # Natural human micro-actions
                if random.random() < 0.20:
                    new_speed = random.choice(speeds)
                    await page.evaluate(f"() => {{ const v = document.querySelector('video'); if (v) v.playbackRate = {new_speed}; }}")
                    scroll_y = random.randint(150, 350)
                    await page.evaluate(f"window.scrollBy({{ top: {scroll_y}, behavior: 'smooth' }});")
                    await asyncio.sleep(random.uniform(1.2, 2.5))
                    await page.evaluate(f"window.scrollBy({{ top: -{scroll_y}, behavior: 'smooth' }});")

            # Final Hard Strict Playback Gate validation
            min_qualifying_sec = min(45.0, duration_sec * 0.6)
            if accumulated_playback >= min_qualifying_sec:
                actions.append(f"verified_watch_{int(accumulated_playback)}s")
                status = "completed"
                logger.info(
                    f"✅ [Worker #{worker_id}] Verified YouTube Watch Session complete "
                    f"({int(accumulated_playback)}s streamed, {loops_completed} full loops)"
                )
            else:
                actions.append(f"failed_insufficient_watch_{int(accumulated_playback)}s")
                status = "failed_stalled"
                logger.warning(f"❌ [Worker #{worker_id}] Session rejected by Hard Strict Gate ({accumulated_playback:.1f}s streamed < {min_qualifying_sec}s target)")

        except Exception as e:
            logger.error(f"❌ YouTube watch error: {e}")
            actions.append(f"error_{str(e)[:40]}")
            status = "failed_error"
        finally:
            await browser.close()

    total_time = int(time.time() - start_time)
    verified_duration = int(accumulated_playback) if status == "completed" else 0
    final_completion = "100%" if loops_completed > 0 else (
        f"{int(min(100.0, (curr_t / max(1.0, total_v_duration)) * 100))}%" if total_v_duration > 0 else "0%"
    )
    return {
        "status": status,
        "session_id": session_id,
        "duration": verified_duration,
        "funnel": active_funnel,
        "loops_completed": loops_completed,
        "completion_rate": final_completion,
        "raw_elapsed": total_time,
        "actions": actions,
    }


async def run_shorts_loop_session(
    short_url: str,
    persona: BrowserPersona,
    headed: bool = False,
    worker_id: int = 1,
) -> dict[str, Any]:
    """Executes a qualified 9:16 Shorts looping session (45-90s) with verified streaming."""
    from playwright.async_api import async_playwright

    session_id = f"short_{uuid.uuid4().hex[:10]}"
    egress_mgr = EgressSelector()
    proxy_config = egress_mgr.get_playwright_proxy(
        task_type="youtube_shorts",
        geo=persona.geo_region,
        session_id=session_id,
    )
    logger.info(f"📱 [Worker #{worker_id}] YouTube Shorts Session [{session_id}] for: {short_url}")

    start_time = time.time()
    actions = ["initialized_shorts_worker"]

    async with async_playwright() as p:
        launch_kwargs: dict[str, Any] = {
            "headless": not headed,
            "args": stealth_launch_args(),
        }
        if proxy_config:
            launch_kwargs["proxy"] = proxy_config

        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            user_agent=persona.user_agent,
            viewport={"width": 430, "height": 932},  # Mobile viewport
            is_mobile=True,
            has_touch=True,
            locale=persona.accept_language.split(",")[0],
            timezone_id=persona.timezone,
        )
        await context.add_init_script(_build_stealth_script(persona))
        page = await context.new_page()

        accumulated_stream = 0.0
        status = "completed"

        try:
            await page.goto(short_url, wait_until="commit", timeout=20000)
            actions.append("loaded_short_url")

            # Auto-dismiss cookie dialogs
            for btn_text in ["Accept all", "I agree", "Reject all", "Accept"]:
                try:
                    btn = page.locator(f"button:has-text('{btn_text}')").first
                    if await btn.is_visible(timeout=1000):
                        await btn.click()
                        break
                except Exception:
                    pass

            # Unmute and play
            await page.evaluate("""() => {
                const v = document.querySelector('video');
                if (v) { v.muted = true; v.play().catch(() => {}); }
            }""")

            # Sample loop advancement
            loop_duration = random.uniform(45, 80)
            sample_time = 0.0
            last_t = -1.0
            while sample_time < loop_duration:
                await asyncio.sleep(10)
                sample_time += 10
                state = await page.evaluate("""() => {
                    const v = document.querySelector('video');
                    return v ? { currentTime: v.currentTime, paused: v.paused } : null;
                }""")
                if state and not state.get("paused"):
                    curr = state.get("currentTime", 0.0)
                    if curr != last_t:
                        accumulated_stream += 10
                        last_t = curr

            actions.append(f"looped_{int(accumulated_stream)}s")
            logger.info(f"   Shorts loop streamed {accumulated_stream:.1f}s verified.")

        except Exception as e:
            logger.error(f"❌ Shorts loop error: {e}")
            actions.append(f"error_{str(e)[:40]}")
            status = "failed_error"
        finally:
            await browser.close()

    total_time = int(time.time() - start_time)
    verified_duration = int(accumulated_stream) if status == "completed" and accumulated_stream >= 30.0 else 0
    return {
        "status": "completed" if verified_duration > 0 else "failed_stalled",
        "session_id": session_id,
        "duration": verified_duration,
        "raw_elapsed": total_time,
        "actions": actions,
    }


# ==============================================================================
# 5C. SUBSYSTEM C: POPUNDER & SECONDARY ARBITRAGE (STRICT ADSENSE FIREWALL)
# ==============================================================================


async def run_popunder_arbitrage_session(
    target_url: str,
    persona: BrowserPersona,
    duration_sec: int = 90,
    headed: bool = False,
    worker_id: int = 1,
) -> dict[str, Any]:
    """Captures and engages popunders on secondary test networks with absolute AdSense abort protection."""
    from playwright.async_api import async_playwright, Page

    session_id = f"pop_{uuid.uuid4().hex[:10]}"
    egress_mgr = EgressSelector()
    proxy_config = egress_mgr.get_playwright_proxy(
        task_type="arbitrage",
        geo=persona.geo_region,
        require_proxy=True,
        session_id=session_id,
    )
    logger.info(f"⚡ [Worker #{worker_id}] Secondary Arbitrage Session [{session_id}] on: {target_url}")

    start_time = time.time()
    actions = ["initialized_arbitrage_worker"]

    async with async_playwright() as p:
        launch_kwargs: dict[str, Any] = {
            "headless": not headed,
            "args": stealth_launch_args(),
        }
        if proxy_config:
            launch_kwargs["proxy"] = proxy_config

        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            user_agent=persona.user_agent,
            viewport={"width": persona.viewport_width, "height": persona.viewport_height},
            locale=persona.accept_language.split(",")[0],
            timezone_id=persona.timezone,
        )
        await context.add_init_script(_build_stealth_script(persona))

        # AdSense Zero-Fraud Firewall
        async def block_ads(route: Any, request: Any) -> None:
            if domain_is_blocked(request.url):
                await route.abort()
            else:
                await route.continue_()

        # Multi-window popunder listener
        async def on_popunder_page(new_page: Page) -> None:
            pop_url = new_page.url
            logger.info(f"   🪟 [Worker #{worker_id}] Popunder Tab Triggered: {pop_url}")
            await new_page.route("**/*", block_ads)
            try:
                await new_page.wait_for_load_state("domcontentloaded", timeout=15000)
                pop_dwell = random.uniform(30, 60)
                await asyncio.sleep(pop_dwell)
                await new_page.close()
                actions.append(f"popunder_dwell_{int(pop_dwell)}s")
            except Exception as e:
                logger.warning(f"Popunder handling: {e}")

        context.on("page", on_popunder_page)

        page = await context.new_page()
        await page.route("**/*", block_ads)

        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=40000)
            actions.append("loaded_host_page")

            # Human reading and interaction
            await asyncio.sleep(random.uniform(15, 30))
            await page.mouse.wheel(0, random.randint(300, 700))
            await asyncio.sleep(random.uniform(10, 25))

        except Exception as e:
            logger.error(f"❌ Arbitrage error: {e}")
        finally:
            await browser.close()

    total_time = int(time.time() - start_time)
    logger.info(f"✅ [Worker #{worker_id}] Arbitrage Session complete ({total_time}s)")
    return {"status": "completed", "session_id": session_id, "duration": total_time, "actions": actions}


# ==============================================================================
# 6. CONCURRENT WORKER RUNNER & INTERACTIVE TUI
# ==============================================================================


async def execute_batch_concurrency(
    targets: list[SessionTarget],
    supabase: Any,
    concurrency: int = 3,
    headed: bool = False,
    use_ai: bool = False,
    channel: str | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Runs a batch of targets with bounded concurrency."""
    sem = asyncio.Semaphore(concurrency)

    async def _worker_task(target: SessionTarget, worker_idx: int) -> dict[str, Any]:
        async with sem:
            persona = get_persona()
            return await run_single_session(
                target=target,
                persona=persona,
                supabase=supabase,
                headed=headed,
                use_ai=use_ai,
                channel=channel,
                dry_run=dry_run,
                worker_id=worker_idx,
            )

    tasks = [_worker_task(t, idx + 1) for idx, t in enumerate(targets)]
    return await asyncio.gather(*tasks)


def fetch_targets(supabase: Any, limit: int = 5, specific_slug: str | None = None) -> list[SessionTarget]:
    site_url = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://gworky.com").rstrip("/")
    query = supabase.table("articles").select("id, slug, title, pillar, word_count").eq("status", "published")
    if specific_slug:
        query = query.eq("slug", specific_slug)
    else:
        query = query.order("published_at", desc=True).limit(limit * 2)

    res = query.execute()
    articles = cast(list[dict[str, Any]], res.data or [])

    try:
        pod_res = supabase.table("podcast_episodes").select("article_id, youtube_video_id").limit(50).execute()
        pod_map = {row["article_id"]: row.get("youtube_video_id") for row in (pod_res.data or [])}
    except Exception:
        pod_map = {}

    targets = []
    for art in articles[:limit]:
        slug = art["slug"]
        targets.append(
            SessionTarget(
                article_id=art["id"],
                article_slug=slug,
                article_title=art["title"],
                pillar=art["pillar"],
                word_count=art.get("word_count") or 750,
                canonical_url=f"{site_url}/article/{slug}",
                youtube_video_id=pod_map.get(art["id"]),
                related_tool_slug=PILLAR_TOOLS.get(art["pillar"]),
            )
        )
    return targets


def display_tui_menu() -> None:
    print("\n" + "=" * 65)
    print(" 🚀 GROUNDWORK HEAVY-DUTY ENGAGEMENT & TRAFFIC ENGINE (CLI)")
    print("=" * 65)
    print(" [1] 👁️  Run Visual Headed Browser (Watch real Chromium on screen)")
    print(" [2] ⚡  Run High-Throughput Multi-Worker (3-5 concurrent sessions)")
    print(" [3] 🧠  Run Generative AI Brain Exploration (Deep reading & tools)")
    print(" [4] 🔗  Run Specific Referral Channel (Google Search / YouTube)")
    print(" [5] 📊  Open Localhost CMS Telemetry Dashboard (localhost:3000)")
    print(" [6] 🛡️  Test DataImpulse Residential Proxy Status")
    print(" [0] ❌  Exit")
    print("=" * 65)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Groundwork Heavy-Duty Traffic CLI")
    parser.add_argument("-i", "--interactive", action="store_true", help="Open interactive TUI menu")
    parser.add_argument("--headed", action="store_true", help="Open visible Chromium browser GUI window")
    parser.add_argument("--concurrency", type=int, default=1, help="Number of concurrent worker sessions (1-10)")
    parser.add_argument("--limit", type=int, default=3, help="Number of article targets to simulate")
    parser.add_argument("--slug", type=str, default=None, help="Target a specific article slug")
    parser.add_argument(
        "--channel",
        choices=["google_search", "youtube_gworky", "topic_silo", "all"],
        default="all",
        help="Referral channel",
    )
    parser.add_argument(
        "--mode",
        choices=["article", "youtube_watch", "shorts_loop", "popunder_arbitrage"],
        default="article",
        help="Execution mode (default: article)",
    )
    parser.add_argument("--url", type=str, default=None, help="Target URL for video or secondary arbitrage")
    parser.add_argument("--duration", type=int, default=300, help="Duration in seconds for video watch or dwell")
    parser.add_argument("--search-keyword", type=str, default=None, help="Search term for Search-to-Watch Journey")
    parser.add_argument(
        "--funnel-mode",
        choices=["auto", "search", "embed", "shorts_bridge", "direct"],
        default="auto",
        help="Discovery funnel mode (default: auto)",
    )
    parser.add_argument("--quality", type=str, default="144p", help="Playback quality (e.g. 144p, tiny, small)")
    parser.add_argument("--ai-brain", action="store_true", help="Enable generative AI agent reasoning")
    parser.add_argument("--dashboard", action="store_true", help="Open Localhost CMS dashboard in browser")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without persisting database logs")
    parser.add_argument("--ataie", action="store_true", help="Execute ATAIE v2.0 Multi-Vector Growth Campaign")
    args = parser.parse_args()

    # Subsystem D: YouTube Watch Mode
    if args.mode == "youtube_watch":
        target_video = args.url or "https://youtu.be/-yh59eacYJM"
        persona = random.choice(PERSONAS)
        logger.info(f"🚀 Launching YouTube Watch-Time Booster (Mode D) on {target_video}...")
        res = await run_youtube_watch_session(
            video_url=target_video,
            persona=persona,
            duration_sec=args.duration,
            headed=args.headed,
            search_keyword=args.search_keyword,
            funnel_mode=args.funnel_mode,
            quality=args.quality,
        )
        print(json.dumps(res, indent=2))
        return

    # Subsystem D: Shorts Looping Mode
    if args.mode == "shorts_loop":
        target_short = args.url or "https://youtu.be/_0CGS0MXnGc"
        persona = random.choice(PERSONAS)
        logger.info(f"🚀 Launching YouTube Shorts Loop Session on {target_short}...")
        res = await run_shorts_loop_session(
            short_url=target_short,
            persona=persona,
            headed=args.headed,
        )
        print(json.dumps(res, indent=2))
        return

    # Subsystem C: Popunder Arbitrage Mode
    if args.mode == "popunder_arbitrage":
        if not args.url:
            logger.error("Popunder arbitrage requires --url <target_url>")
            return
        persona = random.choice(PERSONAS)
        logger.info(f"🚀 Launching Popunder Arbitrage Session (Mode C) on {args.url}...")
        res = await run_popunder_arbitrage_session(
            target_url=args.url,
            persona=persona,
            duration_sec=args.duration,
            headed=args.headed,
        )
        print(json.dumps(res, indent=2))
        return

    # ATAIE v2.0 Orchestrator delegation
    if args.ataie:
        try:
            from agents.ataie_orchestrator import AtaieOrchestrator
        except ImportError:
            from ataie_orchestrator import AtaieOrchestrator
        orchestrator = AtaieOrchestrator(live_mode=not args.dry_run)
        res = orchestrator.run_full_campaign(pillar="money", limit=args.limit)
        print(json.dumps(res, indent=2))
        return

    # Dashboard launcher
    if args.dashboard:
        url = "http://localhost:3000/dashboard/agents"
        print(f"🌐 Launching Localhost Telemetry Dashboard: {url}")
        webbrowser.open(url)
        if not args.interactive:
            return

    # Supabase Client Init
    from supabase import create_client

    supabase_url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_key:
        logger.error("Supabase credentials missing from environment.")
        sys.exit(1)

    supabase = create_client(supabase_url, supabase_key)

    # Interactive TUI mode
    if args.interactive:
        while True:
            display_tui_menu()
            choice = input("Select an option [0-6]: ").strip()
            if choice == "1":
                targets = fetch_targets(supabase, limit=2)
                await execute_batch_concurrency(targets, supabase, concurrency=1, headed=True, dry_run=args.dry_run)
            elif choice == "2":
                c = int(input("Enter concurrency level [2-5]: ").strip() or "3")
                targets = fetch_targets(supabase, limit=c * 2)
                await execute_batch_concurrency(targets, supabase, concurrency=c, headed=False, dry_run=args.dry_run)
            elif choice == "3":
                targets = fetch_targets(supabase, limit=2)
                await execute_batch_concurrency(
                    targets, supabase, concurrency=1, headed=True, use_ai=True, dry_run=args.dry_run
                )
            elif choice == "4":
                ch = input("Choose channel (google_search/youtube_gworky/topic_silo): ").strip() or "google_search"
                targets = fetch_targets(supabase, limit=2)
                await execute_batch_concurrency(
                    targets, supabase, concurrency=1, headed=True, channel=ch, dry_run=args.dry_run
                )
            elif choice == "5":
                webbrowser.open("http://localhost:3000/dashboard/agents")
            elif choice == "6":
                proxy = DataImpulseProxyRouter.get_proxy_url("us", "test_ping")
                print(
                    f"\n[DataImpulse Proxy Config]:\n  Host: {os.environ.get('DATAIMPULSE_HOST')}:{os.environ.get('DATAIMPULSE_PORT')}\n  Login: {os.environ.get('DATAIMPULSE_LOGIN')}\n  Active Route URL: {proxy}\n"
                )
            elif choice == "0":
                print("Exiting Traffic CLI. Goodbye!")
                break
        return

    # Direct CLI flag execution
    targets = fetch_targets(supabase, limit=args.limit, specific_slug=args.slug)
    if not targets:
        logger.warning("No published articles found to simulate.")
        return

    logger.info(
        f"🎯 Starting Heavy-Duty Execution for {len(targets)} targets (Concurrency: {args.concurrency}, Headed: {args.headed}, AI: {args.ai_brain})"
    )
    await execute_batch_concurrency(
        targets=targets,
        supabase=supabase,
        concurrency=args.concurrency,
        headed=args.headed,
        use_ai=args.ai_brain,
        channel=args.channel,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    asyncio.run(main())
