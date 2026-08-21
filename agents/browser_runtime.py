"""Groundwork Unified Browser Runtime (Browser Layer).

Factory that provides either Playwright Chromium (default) or Camoufox
Firefox (native C++ anti-fingerprint) browser contexts.

Usage:
    async with BrowserRuntime.create("chromium", headed=True) as page:
        await page.goto("https://gworky.com")

Supports smart dual-mode: headed + TUI locally, headless in cloud.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


def _is_cloud() -> bool:
    return os.getenv("CI") == "true" or os.getenv("GITHUB_ACTIONS") == "true"


@dataclass
class BrowserPersona:
    """Realistic browser profile for anti-fingerprint consistency."""

    user_agent: str = ""
    viewport: dict[str, int] = field(default_factory=lambda: {"width": 1920, "height": 1080})
    locale: str = "en-US"
    timezone_id: str = "America/New_York"
    color_scheme: str = "light"
    sec_ch_ua: str = ""
    webgl_vendor: str = ""
    platform: str = "macOS"

    @staticmethod
    def random_desktop() -> BrowserPersona:
        """Return a random Tier-1 desktop persona."""
        import random

        personas = [
            BrowserPersona(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                timezone_id="America/New_York",
                sec_ch_ua='"Chromium";v="128", "Google Chrome";v="128"',
                webgl_vendor="Apple",
                platform="macOS",
            ),
            BrowserPersona(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0",
                viewport={"width": 1536, "height": 864},
                locale="en-GB",
                timezone_id="Europe/London",
                sec_ch_ua='"Chromium";v="128", "Microsoft Edge";v="128"',
                webgl_vendor="Google Inc. (ANGLE)",
                platform="Windows",
            ),
            BrowserPersona(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
                viewport={"width": 1440, "height": 900},
                locale="en-AU",
                timezone_id="Australia/Sydney",
                sec_ch_ua="",
                webgl_vendor="Apple",
                platform="macOS",
            ),
        ]
        return random.choice(personas)

    @staticmethod
    def random_mobile() -> BrowserPersona:
        """Return a random Tier-1 mobile persona."""
        import random

        personas = [
            BrowserPersona(
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
                viewport={"width": 390, "height": 844},
                locale="en-US",
                timezone_id="America/Los_Angeles",
                platform="iOS",
            ),
            BrowserPersona(
                user_agent="Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36",
                viewport={"width": 412, "height": 915},
                locale="en-US",
                timezone_id="America/Chicago",
                platform="Android",
            ),
        ]
        return random.choice(personas)


class BrowserRuntime:
    """Unified browser factory — Playwright Chromium (default) + Camoufox Firefox."""

    @staticmethod
    @asynccontextmanager
    async def create(
        engine: str = "chromium",
        proxy_url: str | None = None,
        headed: bool | None = None,
        persona: BrowserPersona | None = None,
    ) -> AsyncGenerator[Any, None]:
        """Create a browser page context.

        Args:
            engine: "chromium" (default) or "camoufox"
            proxy_url: Optional proxy server URL
            headed: Show browser window (auto-detect if None: headed locally, headless in cloud)
            persona: Browser persona for anti-fingerprint. Random if None.

        Yields:
            Playwright Page object with configured persona.
        """
        if headed is None:
            headed = not _is_cloud()

        if persona is None:
            persona = BrowserPersona.random_desktop()

        if engine == "camoufox":
            async for page in BrowserRuntime._create_camoufox(proxy_url, headed, persona):
                yield page
        else:
            async for page in BrowserRuntime._create_playwright(proxy_url, headed, persona):
                yield page

    @staticmethod
    @asynccontextmanager
    async def _create_playwright(
        proxy_url: str | None,
        headed: bool,
        persona: BrowserPersona,
    ) -> AsyncGenerator[Any, None]:
        """Launch Playwright Chromium with persona."""
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            launch_kwargs: dict[str, Any] = {"headless": not headed}
            if proxy_url:
                launch_kwargs["proxy"] = {"server": proxy_url}

            browser = await pw.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                user_agent=persona.user_agent,
                viewport=persona.viewport,
                locale=persona.locale,
                timezone_id=persona.timezone_id,
                color_scheme=persona.color_scheme,
            )
            page = await context.new_page()

            # Inject sec-ch-ua and other client hints
            if persona.sec_ch_ua:
                await page.set_extra_http_headers(
                    {
                        "sec-ch-ua": persona.sec_ch_ua,
                        "sec-ch-ua-platform": f'"{persona.platform}"',
                    }
                )

            try:
                yield page
            finally:
                await context.close()
                await browser.close()

    @staticmethod
    @asynccontextmanager
    async def _create_camoufox(
        proxy_url: str | None,
        headed: bool,
        persona: BrowserPersona,
    ) -> AsyncGenerator[Any, None]:
        """Launch Camoufox (native C++ anti-fingerprint Firefox)."""
        try:
            from camoufox.async_api import AsyncCamoufox
        except ImportError:
            logger.warning("Camoufox not installed — falling back to Playwright Chromium")
            async for page in BrowserRuntime._create_playwright(proxy_url, headed, persona):
                yield page
            return

        cf_kwargs: dict[str, Any] = {"headless": not headed}

        if proxy_url:
            # Camoufox uses a different proxy format
            cf_kwargs["proxy"] = {"server": proxy_url}

        # Camoufox GeoIP database for realistic TZ/locale matching
        geoip_db = os.environ.get("CAMOUFOX_GEOIP_DB")
        if geoip_db:
            cf_kwargs["geoip"] = geoip_db

        async with AsyncCamoufox(**cf_kwargs) as browser:
            page = await browser.new_page()
            try:
                yield page
            finally:
                await page.close()

    @staticmethod
    def is_camoufox_available() -> bool:
        """Check if Camoufox is installed."""
        try:
            import camoufox  # noqa: F401

            return True
        except ImportError:
            return False

    @staticmethod
    def available_engines() -> list[str]:
        """List available browser engines."""
        engines = ["chromium"]  # Always available if playwright is installed
        if BrowserRuntime.is_camoufox_available():
            engines.append("camoufox")
        return engines
