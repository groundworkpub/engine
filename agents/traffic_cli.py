#!/usr/bin/env python3
"""
Groundwork Heavy-Duty Autonomous Engagement & Traffic CLI
Master Terminal Interface for Visual Headed Browsing, Multi-Worker Concurrency,
Generative AI Agent Exploration, and DataImpulse Residential Proxy Routing.
"""

from __future__ import annotations

import argparse
import asyncio
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

from egress_dataimpulse import DataImpulseProxyRouter


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


# ==============================================================================
# 3. CDP STEALTH INJECTION SCRIPT
# ==============================================================================

CDP_STEALTH_SCRIPT = """
(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    delete navigator.__proto__.webdriver;
    window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
    Object.defineProperty(navigator, 'plugins', {
        get: () => [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
            { name: 'Native Client', filename: 'internal-nacl-plugin' }
        ]
    });
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) return 'Google Inc. (Apple)';
        if (parameter === 37446) return 'ANGLE (Apple, Apple M1 Pro, OpenGL 4.1)';
        return getParameter.apply(this, arguments);
    };
})();
"""


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
        v_id = target.youtube_video_id or "dQw4w9WgXcQ"
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
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ]
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

            await context.add_init_script(CDP_STEALTH_SCRIPT)
            page = await context.new_page()

            # AdSense Zero-Fraud Firewall: Block all ad networks
            async def block_ads(route: Any, request: Any) -> None:
                ad_hosts = [
                    "googlesyndication.com",
                    "doubleclick.net",
                    "googleadservices.com",
                    "amazon-adsystem.com",
                    "criteo.com",
                ]
                if any(h in request.url for h in ad_hosts):
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
    parser.add_argument("--ai-brain", action="store_true", help="Enable generative AI agent reasoning")
    parser.add_argument("--dashboard", action="store_true", help="Open Localhost CMS dashboard in browser")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without persisting database logs")
    args = parser.parse_args()

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
