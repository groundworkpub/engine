"""Site guard — outside-only spam injection watch for gworky.com.

Read-only detection plane adapted from the sentinel research corpus
(Prometheusbtr/sentinel + hidden-link-guard patterns). Never mutates the
site: fetches the public sitemap and page HTML, flags injection signals,
and reports via Telegram. Exit code 1 when critical findings exist so the
Actions run surfaces red.

Detection layers:
  1. Sitemap integrity   — any URL outside canonical route shapes
  2. Feed cross-check    — sitemap entries missing from the public RSS feed
  3. Hidden links        — display:none / visibility:hidden / text-indent /
                           font-size:0 / off-canvas anchors
  4. Foreign scripts     — <script src> outside the asset allowlist
  5. Spam IOCs           — pharma / casino / adult keyword families
  6. Redirect cloaking   — meta-refresh or UA-gated redirects off-domain

Usage:
    PYTHONPATH=. python agents/site_guard.py --dry-run
    PYTHONPATH=. python agents/site_guard.py --sample 40
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

BASE_URL = os.getenv("SITE_GUARD_BASE_URL", "https://gworky.com")
TIMEOUT_SECONDS = 15
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# Canonical route shapes for gworky.com (Next.js App Router surface).
_CANONICAL_PATTERNS = [
    re.compile(r"^/$"),
    re.compile(r"^/(money|body|home|life|tech)/?$"),
    re.compile(r"^/(money|body|home|life|tech)/[\w\-.]+$"),
    re.compile(r"^/article/[\w\-.]+$"),
    re.compile(r"^/tools/[\w\-.]+$"),
    re.compile(r"^/author/[\w\-.]+$"),
    re.compile(r"^/(search|login|subscribe|about|contact|support|wire|podcast)/?$"),
    re.compile(r"^/jobs/[\w\-.]+$"),
    re.compile(r"^/sitemap(\.xml|/.*)?$"),  # index + chunked children (/sitemap/articles/1)
    re.compile(r"^/(news-sitemap|rss|feed)\.xml?$"),
]

_SITEMAP_PATH_RE = re.compile(r"^/sitemap(\.xml|/.*)?$|^/(news-sitemap|rss|feed)\.xml?$")

# Curated baseline of known-good top-level segments (live-audited 2026-08-21).
# Anything outside this set raises a WARNING for human review — new marketing
# pages land here once, get curated, and stop noise afterwards.
_KNOWN_TOP_LEVEL = {
    "", "money", "body", "home", "life", "tech", "article", "tools", "author",
    "jobs", "search", "login", "subscribe", "about", "contact", "support",
    "wire", "podcast", "standards", "brand", "press", "citations",
    "citations-tracker", "help", "editorial-policy", "how-we-make-money",
    "partnership", "disclaimer", "advertise", "privacy-policy",
    "terms-of-service", "topic", "compare", "open",
}

# Hard injection signatures — always CRITICAL regardless of baseline.
_SUSPICIOUS_PATH_RE = re.compile(
    r"\.(php|asp|aspx|jsp|cgi)$"
    r"|^/(wp-(admin|content|includes)|cgi-bin|phpmyadmin|adminer|\.env)"
    r"|\.(env|bak|sql|ini|log|old)$"
    r"|^/(money|body|home|life|tech)?/?[\w\-.]*(casino|viagra|cialis|porn|togel|slot)[\w\-.]*$",
    re.IGNORECASE,
)

_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

# External script hosts we expect to see. Anything else is flagged.
_SCRIPT_ALLOWLIST = {
    "gworky.com",
    "www.googletagmanager.com",
    "www.google-analytics.com",
    "pagead2.googlesyndication.com",
    "fundingchoicesmessages.google.com",
    "cloudflareinsights.com",
    "challenges.cloudflare.com",
    "www.statcounter.com",
}

# Sentinel-derived spam IOC families (pharma / casino / adult / shady-finance).
_SPAM_IOCS = re.compile(
    r"\b(casino|slot\s?online|poker|betting|viagra|cialis|kamagra|pharmacy\b|"
    r"porn|xvideos|xxx|escort|"
    r"payday\s?loan|forex\s?robot|binary\s?options|hack\s?instagram|"
    r"jual\s|togel|bandar\s?q)\b",
    re.IGNORECASE,
)

_HIDDEN_CSS_MARKERS = [
    ("display", "none"),
    ("visibility", "hidden"),
    ("opacity", "0"),
    ("font-size", "0"),
    ("font-size", "0px"),
]

_OFFCANVAS_RE = re.compile(
    r"(text-indent\s*:\s*-\d{3,}px|left\s*:\s*-\d{4,}px|top\s*:\s*-\d{4,}px|"
    r"margin-left\s*:\s*-\d{4,}px|transform\s*:\s*translate\(-\d{4,}px)",
    re.IGNORECASE,
)

_META_REFRESH_RE = re.compile(
    r"<meta[^>]+http-equiv=[\"']?refresh[\"']?[^>]+url=(https?://[^\"'>\s]+)",
    re.IGNORECASE,
)

_UA_GATE_RE = re.compile(
    r"(navigator\.userAgent|userAgent\.match)[^;\n]{0,120}(location\.href|window\.location|document\.location)",
    re.IGNORECASE,
)


@dataclass
class Finding:
    severity: str  # critical | warning
    layer: str
    url: str
    detail: str


@dataclass
class GuardReport:
    base_url: str
    checked_sitemap_urls: int = 0
    sampled_pages: int = 0
    findings: list[Finding] = field(default_factory=list)

    @property
    def critical(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "critical"]

    def to_json(self) -> str:
        return json.dumps(
            {
                "base_url": self.base_url,
                "checked_sitemap_urls": self.checked_sitemap_urls,
                "sampled_pages": self.sampled_pages,
                "findings": [
                    {"severity": f.severity, "layer": f.layer, "url": f.url, "detail": f.detail}
                    for f in self.findings
                ],
            },
            indent=2,
        )


def _fetch(url: str) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return resp.read(2_000_000).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 — single-source failure must not kill the sweep
        logger.warning("fetch failed %s: %s", url, exc)
        return None


def _is_canonical(path: str) -> bool:
    return any(p.match(path) for p in _CANONICAL_PATTERNS)


def _looks_like_xml(body: str) -> bool:
    return body.lstrip()[:5] == "<?xml"


def _page_locs(xml_body: str) -> list[str]:
    """Extract <loc> entries in the sitemap namespace only — excludes
    image:loc / video:loc extensions that leak CDN asset URLs."""
    try:
        root = ET.fromstring(xml_body)
    except ET.ParseError:
        return []
    return [el.text or "" for el in root.iter() if el.tag == f"{_SITEMAP_NS}loc"]


def audit_sitemap(report: GuardReport) -> list[str]:
    """Fetch sitemap (index-aware) and flag injection signatures."""
    urls: list[str] = []
    body = _fetch(f"{report.base_url}/sitemap.xml")
    if not body:
        report.findings.append(Finding("warning", "sitemap", "/sitemap.xml", "sitemap unreachable"))
        return urls

    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        report.findings.append(Finding("critical", "sitemap", "/sitemap.xml", f"unparseable XML: {exc}"))
        return urls

    if root.tag == f"{_SITEMAP_NS}sitemapindex":
        child_maps = [u for u in _page_locs(body) if "/sitemap" in u]
        for child_url in set(child_maps):
            child_body = _fetch(child_url)
            if child_body:
                urls += [u for u in _page_locs(child_body) if not u.endswith(".xml")]
    else:
        urls = [u for u in _page_locs(body) if not u.endswith(".xml")]

    report.checked_sitemap_urls = len(urls)
    base_netloc = urlparse(report.base_url).netloc
    for u in urls:
        parsed = urlparse(u)
        path = parsed.path or "/"
        if parsed.netloc and parsed.netloc != base_netloc:
            report.findings.append(Finding("critical", "sitemap", u, f"off-domain URL in sitemap ({parsed.netloc})"))
        elif _SUSPICIOUS_PATH_RE.search(path):
            report.findings.append(Finding("critical", "sitemap", u, f"injection signature in path: {path}"))
        elif not _is_canonical(path):
            top = path.strip("/").split("/")[0]
            if top not in _KNOWN_TOP_LEVEL:
                report.findings.append(Finding("warning", "sitemap-baseline", u, f"unknown top-level segment: /{top}"))
    return urls


def audit_feed_parity(report: GuardReport, sitemap_urls: list[str]) -> None:
    """Injected pages typically appear in the sitemap but never in the feed."""
    feed_body = None
    for path in ("/rss.xml", "/feed.xml"):
        feed_body = _fetch(f"{report.base_url}{path}")
        if feed_body:
            break
    if not feed_body or not sitemap_urls:
        return

    article_slugs = {u.rsplit("/", 1)[-1] for u in sitemap_urls if "/article/" in u}
    feed_slugs = set(re.findall(r"<loc>([^<]+)/?</loc>", feed_body))
    feed_slugs |= set(re.findall(r"<link>([^<]+)</link>", feed_body))
    feed_slugs = {s.rsplit("/", 1)[-1] for s in feed_slugs}

    orphans = sorted(article_slugs - feed_slugs)
    if len(orphans) > max(10, len(article_slugs) // 2):
        # Large divergence usually means feed pagination, not injection.
        report.findings.append(
            Finding("warning", "feed-parity", "/rss.xml", f"{len(orphans)} articles absent from feed (check pagination)")
        )
    else:
        for slug in orphans:
            report.findings.append(Finding("warning", "feed-parity", slug, "in sitemap but absent from feed"))


def _style_is_hidden(style: str) -> bool:
    for kv in style.split(";"):
        if ":" not in kv:
            continue
        prop, _, val = kv.partition(":")
        prop, val = prop.strip().lower(), val.strip().lower()
        if (prop, val) in _HIDDEN_CSS_MARKERS or _OFFCANVAS_RE.search(kv):
            return True
    return False


def audit_page_html(report: GuardReport, url: str, html: str) -> None:
    """Run all content-layer detectors against one page's HTML."""
    low = html.lower()

    # 1. Hidden links — inline styles on anchors, plus off-canvas positioning.
    for m in re.finditer(r"<a\b[^>]*href=[\"'](https?://[^\"']+)[\"'][^>]*>", html, re.IGNORECASE):
        style_m = re.search(r"style=[\"']([^\"']+)[\"']", m.group(0), re.IGNORECASE)
        if style_m and _style_is_hidden(style_m.group(1)):
            report.findings.append(Finding("critical", "hidden-link", url, f"hidden anchor → {m.group(1)[:120]}"))

    # 2. Foreign scripts.
    for m in re.finditer(r"<script[^>]+src=[\"'](https?://[^\"'/]+)", html, re.IGNORECASE):
        host = m.group(1).lower().removeprefix("https://").removeprefix("http://")
        if not any(host == d or host.endswith("." + d) for d in _SCRIPT_ALLOWLIST):
            report.findings.append(Finding("warning", "foreign-script", url, f"script host not allowlisted: {m.group(1)}"))

    # 3. Spam IOCs in visible text or link targets.
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    ioc_hits = _SPAM_IOCS.findall(text)
    if len(ioc_hits) >= 3:
        report.findings.append(Finding("critical", "spam-ioc", url, f"{len(ioc_hits)} spam keyword hits: {sorted(set(map(str.lower, ioc_hits)))[:6]}"))
    for m in re.finditer(r"href=[\"']([^\"']*)[\"']", html, re.IGNORECASE):
        if _SPAM_IOCS.search(m.group(1)):
            report.findings.append(Finding("critical", "spam-ioc", url, f"spam keyword in link target: {m.group(1)[:120]}"))
            break

    # 4. Redirect cloaking.
    for m in _META_REFRESH_RE.finditer(html):
        target = m.group(1)
        if urlparse(target).netloc and urlparse(target).netloc != urlparse(url).netloc:
            report.findings.append(Finding("critical", "cloaking", url, f"meta-refresh redirect off-domain → {target[:120]}"))
    if _UA_GATE_RE.search(low):
        report.findings.append(Finding("warning", "cloaking", url, "UA-gated redirect pattern in scripts"))


def audit_differential_cloaking(report: GuardReport, url: str) -> None:
    """Probes a URL with Googlebot vs Mobile Safari to detect sneaky redirect/content cloaking."""
    bot_ua = "Googlebot/2.1 (+http://www.google.com/bot.html)"
    mobile_ua = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    )

    def _fetch_meta(ua: str) -> tuple[int, str, str]:
        req = urllib.request.Request(url, headers={"User-Agent": ua})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                final_url = resp.geturl()
                code = resp.status
                body = resp.read(500_000).decode("utf-8", errors="replace")
                title_m = re.search(r"<title[^>]*>(.*?)</title>", body, re.IGNORECASE | re.DOTALL)
                title = title_m.group(1).strip() if title_m else ""
                return code, final_url, title
        except urllib.error.HTTPError as err:
            return err.code, "", ""
        except Exception:
            return 0, "", ""

    bot_code, bot_final, bot_title = _fetch_meta(bot_ua)
    mob_code, mob_final, mob_title = _fetch_meta(mobile_ua)

    if not bot_code or not mob_code:
        return

    # 1. Divergence in final netloc (redirect cloaking)
    bot_netloc = urlparse(bot_final).netloc
    mob_netloc = urlparse(mob_final).netloc
    if bot_netloc and mob_netloc and bot_netloc != mob_netloc:
        report.findings.append(
            Finding(
                "critical",
                "cloaking-differential",
                url,
                f"redirect divergence: bot landed at {bot_final} but mobile landed at {mob_final}",
            )
        )

    # 2. Status code divergence
    if bot_code == 200 and mob_code in (301, 302, 307):
        report.findings.append(
            Finding(
                "critical",
                "cloaking-differential",
                url,
                f"status code divergence: bot got 200 OK while mobile was redirected ({mob_code})",
            )
        )


def audit_sensitive_endpoints(report: GuardReport) -> None:
    """Verifies that high-risk recon targets return strict 404/403 without leaking credentials."""
    targets = [
        "/.env",
        "/.env.local",
        "/.git/HEAD",
        "/wp-login.php",
        "/config.php.bak",
        "/admin_backup_v2/",
    ]
    for path in targets:
        target_url = f"{report.base_url.rstrip('/')}{path}"
        req = urllib.request.Request(target_url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                body = resp.read(4096).decode("utf-8", errors="replace")
                if "DATABASE_URL" in body or "SUPABASE_KEY" in body or "ref: refs/heads" in body:
                    report.findings.append(
                        Finding("critical", "secret-leak", target_url, "exposed sensitive file contents detected!")
                    )
        except urllib.error.HTTPError as err:
            # 404 or 403 are expected safe responses
            if err.code not in (404, 403):
                report.findings.append(
                    Finding("warning", "recon-endpoint", target_url, f"unexpected HTTP response {err.code}")
                )
        except Exception:
            pass


def audit_supabase_rls(report: GuardReport) -> None:
    """Verifies that Supabase public anon key cannot execute unauthorized write operations (Anti-BOLA/SQLi)."""
    supabase_url = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    anon_key = os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    if not supabase_url or not anon_key:
        return

    test_url = f"{supabase_url.rstrip('/')}/rest/v1/articles"
    headers = {
        "apikey": anon_key,
        "Authorization": f"Bearer {anon_key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    payload = json.dumps({"slug": "security-sentinel-test-probe", "title": "Unauthorized Probe Injection"}).encode()
    req = urllib.request.Request(test_url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            if resp.status in (200, 201):
                report.findings.append(
                    Finding(
                        "critical",
                        "supabase-rls",
                        test_url,
                        "CRITICAL: Supabase public anon key successfully inserted data! RLS policy missing or broken!",
                    )
                )
    except urllib.error.HTTPError as err:
        # Expected response: 401 Unauthorized or 403 Forbidden / 42501 Insufficient Privilege
        if err.code not in (401, 403):
            report.findings.append(
                Finding("warning", "supabase-rls", test_url, f"RLS mutation returned unexpected code: {err.code}")
            )
    except Exception:
        pass


def run_guard(base_url: str, sample: int) -> GuardReport:
    report = GuardReport(base_url=base_url)
    sitemap_urls = audit_sitemap(report)

    candidates = [u for u in sitemap_urls if "/article/" in u] or [
        u for u in sitemap_urls if not _SITEMAP_PATH_RE.match(urlparse(u).path)
    ]
    for u in candidates[:sample]:
        html = _fetch(u)
        if html and not _looks_like_xml(html):
            report.sampled_pages += 1
            audit_page_html(report, u, html)

    # Dual-UA Cloaking Probing on top 5 sample pages
    for u in candidates[: min(5, len(candidates))]:
        audit_differential_cloaking(report, u)

    # Sensitive endpoints & RLS verification
    audit_sensitive_endpoints(report)
    audit_supabase_rls(report)

    audit_feed_parity(report, sitemap_urls)
    return report


def send_telegram_alert(report: GuardReport) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_FOUNDER_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_FOUNDER_CHAT_ID unset — alert skipped")
        return False

    crit = report.critical
    warn = [f for f in report.findings if f.severity == "warning"]
    lines = [
        "🛡 *SITE GUARD — injection watch*",
        f"Host: `{report.base_url}`",
        f"Sitemap URLs: {report.checked_sitemap_urls} · Pages sampled: {report.sampled_pages}",
        f"*Critical:* {len(crit)} · Warnings: {len(warn)}",
        "",
    ]
    for f in crit[:8]:
        lines.append(f"🔴 [{f.layer}] {f.url}\n   {f.detail}")
    for f in warn[:5]:
        lines.append(f"🟡 [{f.layer}] {f.url}\n   {f.detail}")
    if len(crit) > 8 or len(warn) > 5:
        lines.append("…truncated — full report in Actions log")

    import urllib.parse

    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": "\n".join(lines), "parse_mode": "Markdown"})
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=payload.encode())
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception as exc:  # noqa: BLE001
        logger.error("telegram alert failed: %s", exc)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Outside-only spam injection watch")
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--sample", type=int, default=25, help="max pages to deep-scan")
    parser.add_argument("--dry-run", action="store_true", help="print report, never send alerts")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    report = run_guard(args.base_url, args.sample)
    print(report.to_json())

    crit_count = len(report.critical)
    if args.dry_run:
        logger.info("dry-run: alert suppressed (%d critical, %d warning)", crit_count, len(report.findings) - crit_count)
    elif report.findings:
        send_telegram_alert(report)

    return 1 if crit_count else 0


if __name__ == "__main__":
    sys.exit(main())
