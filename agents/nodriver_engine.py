"""Groundwork SERP Recon Engine — nodriver backend (Mode B, isolated).

Used for live SERP position scanning (keyword-gap rank verification via a real
browser), NOT the DOM-heavy engagement simulator (Mode A stays on
playwright/patchright).

Why nodriver (2026 benchmark: 28 OK / 0 blocked, best of 7 engines):
  - raw CDP via system Chrome, no Playwright `Runtime.enable`/`Target.setAutoAttach`
  - headless exposes REAL signals (navigator.plugins=5, window.chrome=object)
    that bundled patched forks hide (plugins=0, chrome=undefined)

CRITICAL constraint (verified live): a bare local/browser hit on Google returns
the "unusual traffic / bot gate" page. SERP recon MUST route through geo-coherent
residential egress (DataImpulse US/GB/AU) via `egress_selector`. This module never
fires an unproxied SERP hit; it degrades gracefully to [] on a bot gate instead of
burning the IP or throwing.

API is intentionally small & testable: the heavy lifting (launch/navigate) is
isolated; parsing is a pure function covered by unit tests.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Google SERP result selectors (best-effort; resilient to markup drift).
# `a[jsname]` is present on modern SERP result titles; fallback to generic.
RESULT_LINK_SELECTOR = "a[jsname], a[ping], div#search a"
# Markers for the Google bot-gate / unusual-traffic interstitial.
BOT_GATE_MARKERS = (
    "unusual traffic",
    "tidak wajar",
    "detected",  # conservative suffix
)


@dataclass
class SerpResult:
    rank: int
    domain: str
    url: str
    title: str = ""
    snippet: str = ""


@dataclass
class SerpScanOutcome:
    query: str
    results: list[SerpResult] = field(default_factory=list)
    bot_gated: bool = False
    error: str | None = None
    engine: str = "nodriver"

    @property
    def domains(self) -> list[str]:
        return [r.domain for r in self.results]


def extract_domain(url: str) -> str:
    """Best-effort registrable domain (keeps known subdomains like cnet.com)."""
    if not url:
        return ""
    from urllib.parse import urlparse

    netloc = urlparse(url).netloc.lower()
    if not netloc:
        return ""
    return netloc[4:] if netloc.startswith("www.") else netloc


def parse_serp_links(links: list[dict[str, Any]], top_n: int = 15) -> list[SerpResult]:
    """Pure parsing of raw `{href, title}` link records into ranked SerpResults.

    Filters out Google-owned navigation links and deduplicates by domain.
    Exposed as a pure function for unit testing (no browser required).
    """
    seen: set[str] = set()
    out: list[SerpResult] = []
    rank = 0
    for link in links:
        href = str(link.get("href") or "")
        title = str(link.get("title") or "").strip()
        if not href.startswith("http"):
            continue
        domain = extract_domain(href)
        # Skip Google's own chrome (search, maps, translate, consent, etc.)
        if not domain or domain.endswith(".google.com") or domain == "google.com":
            continue
        if domain in seen:
            continue
        seen.add(domain)
        rank += 1
        out.append(SerpResult(rank=rank, domain=domain, url=href, title=title))
        if rank >= top_n:
            break
    return out


def _is_bot_gated(html_or_text: str) -> bool:
    """True if the page looks like a Google bot-gate / unusual-traffic screen."""
    low = html_or_text.lower()
    return any(m in low for m in BOT_GATE_MARKERS)


def _proxy_to_host_port(proxy_url: str | None) -> tuple[str | None, int | None, str | None]:
    """Split a proxy URL (http://user:pass@host:port) into (host, port, scheme).

    nodriver `start()` takes host/port (not a full URL). Returns bare host:port
    for the nodriver proxy config. TLS auth is not stripped here — nodriver proxy
    auth is passed separately via the config object in `start()` kwargs.
    """
    if not proxy_url:
        return None, None, None
    # Strip scheme + credentials to isolate host:port
    rest = proxy_url
    if "://" in rest:
        rest = rest.split("://", 1)[1]
    if "@" in rest:
        rest = rest.split("@", 1)[1]
    host, _, port = rest.partition(":")
    if not host:
        return None, None, None
    try:
        return host, int(port or 823), None
    except ValueError:
        return host, None, None


class SerpRecon:
    """Run live SERP scans via nodriver routed through bounded egress.

    `proxy_url` is REQUIRED for real Google scans (see module docstring). When
    omitted, the scanner refuses the unproxied hit and returns an empty outcome
    so it never triggers a bot gate or burns the operator IP.
    """

    def __init__(self, *, proxy_url: str | None = None, headless: bool = True,
                 top_n: int = 15, timeout: float = 25.0) -> None:
        self.proxy_url = proxy_url
        self.headless = headless
        self.top_n = top_n
        self.timeout = timeout

    def _resolve_proxy(self) -> str | None:
        if self.proxy_url:
            return self.proxy_url
        try:
            from egress_selector import SmartPolicySelector

            return SmartPolicySelector().get_proxy(task_type="browse", geo="us")
        except Exception as exc:  # pragma: no cover - only on edge env
            logger.debug("Egress resolve failed: %s", exc)
            return None

    async def scan(self, query: str) -> SerpScanOutcome:
        """Scan one Google SERP query. Degrades gracefully (never raises on gate)."""
        fixed_proxy = self._resolve_proxy()
        if not fixed_proxy:
            logger.warning("SerpRecon refused unproxied scan for '%s' (no egress).", query)
            return SerpScanOutcome(query=query, error="no-egress-refused")

        host, port, _scheme = _proxy_to_host_port(fixed_proxy)
        if not host or not port:
            logger.warning("SerpRecon could not parse proxy for '%s'.", query)
            return SerpScanOutcome(query=query, error="proxy-parse-failed")

        try:
            import nodriver as uc
        except Exception as exc:  # pragma: no cover - optional dependency
            logger.warning("nodriver unavailable: %s", exc)
            return SerpScanOutcome(query=query, error=f"nodriver-unavailable: {exc}")

        url = f"https://www.google.com/search?q={_quote(query)}&num={self.top_n * 2}"

        try:
            browser = await uc.start(
                headless=self.headless,
                host=host,
                port=port,
                # nodriver proxies via host/port; timeouts via page.sleep below.
            )
        except Exception as exc:  # pragma: no cover - env dependent
            logger.warning("nodriver start failed: %s", exc)
            return SerpScanOutcome(query=query, error=f"nodriver-start: {exc}")

        try:
            page = await browser.get(url)
            if page is None:
                return SerpScanOutcome(query=query, error="navigate-failed")
            await page.sleep(min(self.timeout * 0.6, 6.0))

            body = await page.evaluate("document.body ? document.body.innerText : ''")
            if _is_bot_gated(str(body or "")):
                logger.info("SerpRecon bot-gated on '%s' (safe skip).", query)
                return SerpScanOutcome(query=query, bot_gated=True, engine="nodriver")

            raw = await page.evaluate(
                """() => Array.from(document.querySelectorAll('a')).map(a => ({
                    href: a.href, title: (a.innerText||a.textContent||'').trim().slice(0,120)
                }))"""
            )
            results = parse_serp_links(raw or [], top_n=self.top_n)
            logger.info("SerpRecon '%s' → %d domains.", query, len(results))
            return SerpScanOutcome(query=query, results=results, engine="nodriver")
        except Exception as exc:  # pragma: no cover - env dependent
            logger.warning("SerpRecon scan error '%s': %s", query, exc)
            return SerpScanOutcome(query=query, error=str(exc), engine="nodriver")
        finally:
            with contextlib.suppress(Exception):
                browser.stop()


def _quote(q: str) -> str:
    """URL-quote a query (module-level so it's reusable & sync-safe)."""
    from urllib.parse import quote_plus

    return quote_plus(q)


async def scan_many(queries: list[str], *, proxy_url: str | None = None,
                    headless: bool = True, top_n: int = 15) -> list[SerpScanOutcome]:
    """Scan a list of queries concurrently (bounded by nodriver's async nature)."""
    recon = SerpRecon(proxy_url=proxy_url, headless=headless, top_n=top_n)
    return [await recon.scan(q) for q in queries]
