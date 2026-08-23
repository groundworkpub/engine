"""Groundwork Google SERP Scanner — patchright backend (Mode B, isolated).

Purpose: live Google organic position scanning (keyword-gap rank verification)
via a real browser routed through geo-coherent residential egress. Provides rank
data that the Suggest/autocomplete path cannot (position of competitors vs
gworky.com on the organic SERP).

Why patchright + NATIVE proxy-auth (verified live, 2026-08-23):
  - Chromium's `--proxy-server` strips `user:pass@` (→ 407 with no dialog in
    headless). patchright/Playwright solve this properly via
    `proxy={"server","username","password"}`.
  - Verified: exit IP = real US residential (74.79.32.157, Spectrum/Charter ASN),
    no 407. patchright is already wired in the engine (15/15 diagnostics).

Safety constraints:
  - NEVER fires an unproxied SERP hit (Google bot-gates on bare/direct IPs —
    verified live: "unusual traffic" page). Refuses to run without egress.
  - Graceful degradation: bot-gate / CAPTCHA → empty outcome, no crash.
  - Saves session state so repeat scans look like returning organic users
    (cookies/UA persistence), reducing the chance of bot flags.

Return contract matches `competitor_gap.search_searxng` (list of {"url",...}) so
`run_gap_scan` can consume it as a drop-in optional backend.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_TOP_N = 15
DEFAULT_TIMEOUT = 30.0
# Google SERP result anchors & selectors (best-effort; resilient to drift).
_GOOGLE_OWN_DOMAINS = (
    "google.com", "gstatic.com", "googleusercontent.com", "googleapis.com",
    "googleadservices.com", "googlesyndication.com", "g.co", "goo.gl",
)


@dataclass
class SerpOutcome:
    query: str
    results: list[dict[str, Any]] = field(default_factory=list)
    bot_gated: bool = False
    error: str | None = None
    engine: str = "patchright"

    @property
    def domains(self) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for r in self.results:
            d = extract_domain(r.get("url", ""))
            if d and d not in seen:
                seen.add(d)
                out.append(d)
        return out

    def as_search_row(self) -> list[dict[str, Any]]:
        """Same shape as `competitor_gap.search_searxng` output (url/title)."""
        return [
            {"url": r["url"], "title": r.get("title", ""), "rank": r.get("rank")}
            for r in self.results
        ]


def extract_domain(url: str) -> str:
    if not url:
        return ""
    netloc = urlparse(url).netloc.lower()
    if not netloc:
        return ""
    return netloc[4:] if netloc.startswith("www.") else netloc


def is_google_owned(domain: str) -> bool:
    return not domain or any(domain == d or domain.endswith("." + d) for d in _GOOGLE_OWN_DOMAINS)


def parse_serp_links(raw: list[dict[str, Any]], top_n: int = DEFAULT_TOP_N) -> list[dict[str, Any]]:
    """Ranked {url, title} from raw SERP anchor records; dedupe + drop Google-own."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    rank = 0
    for link in raw:
        href = str(link.get("href") or "")
        if not href.startswith("http"):
            continue
        domain = extract_domain(href)
        if is_google_owned(domain):
            continue
        if domain in seen:
            continue
        seen.add(domain)
        rank += 1
        out.append({"rank": rank, "url": href, "domain": domain, "title": str(link.get("title") or "")})
        if rank >= top_n:
            break
    return out


def _proxy_to_parts(proxy_url: str) -> dict[str, Any] | None:
    """Split an authenticated proxy URL into patchright proxy kwargs.

    Handles ``http://user:pass@host:port`` (DataImpulse). Returns None if the URL
    is malformed or lacks credentials.
    """
    try:
        u = urlparse(proxy_url)
        if not u.hostname or not u.port:
            return None
        # patchright's proxy dict needs the credential fields separate.
        return {
            "server": f"{u.scheme or 'http'}://{u.hostname}:{u.port}",
            "username": u.username or "",
            "password": u.password or "",
        }
    except Exception:  # pragma: no cover - malformed URL
        return None


def _persistent_profile_dir() -> str | None:
    """Optional persistent browser profile for organic-looking return visits.

    Only used when GROUNDWORK_BROWSER_PROFILE is set; otherwise a fresh temp
    profile (no cross-session cookies) is used.
    """
    d = os.environ.get("GROUNDWORK_BROWSER_PROFILE")
    if not d:
        return None
    os.makedirs(d, exist_ok=True)
    return d


class GoogleSerpScanner:
    """Scan Google organic SERP via patchright (native proxy-auth) + egress."""

    def __init__(
        self,
        *,
        proxy_url: str | None = None,
        headless: bool = True,
        top_n: int = DEFAULT_TOP_N,
        timeout: float = DEFAULT_TIMEOUT,
        rate_limit_seconds: float = 6.0,
        engine: str = "patchright",
    ) -> None:
        self.proxy_url = proxy_url
        self.headless = headless
        self.top_n = top_n
        self.timeout = timeout
        self.rate_limit_seconds = rate_limit_seconds
        self.engine = engine  # patchright (native proxy-auth) | playwright
        self._last_scan = 0.0

    def _resolve_proxy(self) -> str | None:
        if self.proxy_url:
            return self.proxy_url
        try:
            from egress_selector import SmartPolicySelector

            return SmartPolicySelector().get_proxy(task_type="serp_recon", geo="us")
        except Exception as exc:  # pragma: no cover - edge env
            logger.debug("Egress resolve failed: %s", exc)
            return None

    async def _get_playwright(self):
        """Return (async_playwright_fn, channel) for the configured engine."""
        if self.engine == "patchright":
            from patchright.async_api import async_playwright
            return async_playwright, "chrome"
        from playwright.async_api import async_playwright
        return async_playwright, None

    async def scan(self, query: str) -> SerpOutcome:
        """Scan one Google query. Never raises on bot-gate / missing egress."""
        # Rate-limit consecutive scans (behave like a human, not a burst).
        now = time.monotonic()
        gap = now - self._last_scan
        if self._last_scan and gap < self.rate_limit_seconds:
            await asyncio.sleep(self.rate_limit_seconds - gap)
        self._last_scan = time.monotonic()

        fixed_proxy = self._resolve_proxy()
        proxy_parts = _proxy_to_parts(fixed_proxy or "")
        if not proxy_parts:
            logger.warning("GoogleSerpScanner refused unproxied scan for '%s' (no egress).", query)
            return SerpOutcome(query=query, error="no-egress-refused")

        pw_fn, channel = await self._get_playwright()
        try:
            async with pw_fn() as p:
                launch_kwargs: dict[str, Any] = {
                    "headless": self.headless,
                    "proxy": proxy_parts,
                }
                if channel:
                    launch_kwargs["channel"] = channel
                browser = await p.chromium.launch(**launch_kwargs)
                profile = _persistent_profile_dir()
                ctx_kwargs: dict[str, Any] = {
                    "user_agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                    ),
                    "locale": "en-US",
                    "viewport": {"width": 1440, "height": 900},
                }
                if profile:
                    ctx_kwargs["user_data_dir"] = profile
                context = await browser.new_context(**ctx_kwargs)
                page = await context.new_page()

                # ── Warm-up (the real bot-gate fix) ────────────────────────────────
                # A cold headless hit on /search is flagged. First establish an
                # organic-looking session: visit homepage, accept consent, let the
                # cookie/privacy jar propagate, THEN run the search query.
                try:
                    await page.goto(
                        "https://www.google.com/", wait_until="domcontentloaded",
                        timeout=self.timeout * 1000,
                    )
                    await page.wait_for_timeout(1800)
                    await _try_accept_consent(page)
                    await page.wait_for_timeout(1200)
                except Exception as warmup_exc:  # pragma: no cover - env dependent
                    logger.debug("Warm-up notice: %s", warmup_exc)

                url = f"https://www.google.com/search?q={_quote(query)}&num={self.top_n * 2}&hl=en"
                await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
                await page.wait_for_timeout(2500)

                # Bot-gate / interstitial detection (verified Google string).
                body = await page.evaluate("document.body ? document.body.innerText : ''")
                if _is_bot_gated(str(body or "")):
                    logger.info("GoogleSerpScanner bot-gated on '%s' (safe skip).", query)
                    await browser.close()
                    return SerpOutcome(query=query, bot_gated=True)

                raw = await page.evaluate(
                    """() => Array.from(document.querySelectorAll('a')).map(a => ({
                        href: a.href, title: (a.innerText||a.textContent||'').trim().slice(0,140)
                    }))"""
                )
                results = parse_serp_links(raw or [], top_n=self.top_n)
                logger.info("GoogleSerpScanner '%s' → %d domains.", query, len(results))
                await browser.close()
                return SerpOutcome(query=query, results=results)
        except Exception as exc:  # pragma: no cover - env dependent
            logger.warning("GoogleSerpScanner scan error '%s': %s", query, exc)
            return SerpOutcome(query=query, error=str(exc))


def _quote(q: str) -> str:
    from urllib.parse import quote_plus

    return quote_plus(q)


_BOT_GATE_MARKERS = (
    "unusual traffic",
    "tidak wajar",
    "unusual de tráfico",
    "automated queries",
    "captcha",
    "are you a human",
    "verify you are a human",
    "verify you're",
    "give us a moment",
    "confirm you're not a robot",
)


def _is_bot_gated(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _BOT_GATE_MARKERS)


async def _try_accept_consent(page: Any) -> None:
    """Best-effort Google consent acceptance (button text varies by region).

    Returns silently on failure — consent may be pre-set for the profile, or the
    button label differs. Never raises; a failed consent is not fatal.
    """
    for text in ("Accept all", "I agree", "Accept", "Agree", "Allow all"):
        try:
            loc = page.get_by_text(text, exact=False)
            if await loc.count() > 0:
                await loc.first.click(timeout=3000)
                await page.wait_for_timeout(700)
                return
        except Exception:
            pass
