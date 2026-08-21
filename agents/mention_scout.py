#!/usr/bin/env python3
"""Mention Scout — Unlinked brand-mention discovery for Groundwork (T3.1).

Finds third-party pages that mention gworky/Groundwork without linking back,
then notifies the founder via Telegram for manual outreach / link reclamation.

Sources:
  1. Hacker News (Algolia search API, no auth)
  2. DuckDuckGo HTML SERP via DataImpulse residential egress ("gworky.com" -site:gworky.com)

State dedup persists across runs in state/mention_scout_state.json.
"""

from __future__ import annotations

import asyncio
import base64
import html as html_lib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_FOUNDER_CHAT_ID", "")
BRAND = "gworky"
BRAND_DOMAIN = "gworky.com"

STATE_PATH = Path(os.environ.get("MENTION_SCOUT_STATE", "state/mention_scout_state.json"))

HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
DDG_URL = "https://html.duckduckgo.com/html/"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

HREF_RE = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
DDG_RESULT_RE = re.compile(r'href="//duckduckgo\.com/l/\?uddg=([^&"]+)', re.IGNORECASE)
BING_CK_RE = re.compile(r'href="(https://www\.bing\.com/ck/a[^"]+)"', re.IGNORECASE)
BING_U_RE = re.compile(r"[&?]u=a1([^&]+)")


def extract_bing_result_urls(page_html: str) -> list[str]:
    """Decode Bing /ck/a redirect links (u=a1<base64url>) into organic result URLs."""
    urls: list[str] = []
    for m in BING_CK_RE.finditer(html_lib.unescape(page_html)):
        u = BING_U_RE.search(m.group(1))
        if not u:
            continue
        padded = u.group(1) + "=" * (-len(u.group(1)) % 4)
        try:
            target = base64.urlsafe_b64decode(padded).decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            continue
        host = urlparse(target).netloc
        if (
            target.startswith("http")
            and host
            and "bing." not in host
            and "microsoft" not in host
            and BRAND_DOMAIN not in host
            and target not in urls
        ):
            urls.append(target)
    return urls


@dataclass
class Finding:
    source: str
    url: str
    title: str
    snippet: str = ""
    kind: str = "unlinked"  # unlinked | linked_reference

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "url": self.url, "title": self.title, "snippet": self.snippet[:200], "kind": self.kind}


def load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {"seen_urls": [], "findings": []}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["seen_urls"] = state["seen_urls"][-2000:]
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _mentions_brand(text: str) -> bool:
    t = text.lower()
    return BRAND in t or BRAND_DOMAIN in t


def find_unlinked(page_html: str) -> bool:
    """True when the page mentions the brand but never links to it."""
    if not _mentions_brand(page_html):
        return False
    return all(BRAND_DOMAIN not in href.lower() for href in HREF_RE.findall(page_html))


def extract_ddg_result_urls(html: str) -> list[str]:
    urls: list[str] = []
    for raw in DDG_RESULT_RE.findall(html):
        target = unquote(raw)
        host = urlparse(target).netloc
        if target.startswith("http") and host and BRAND_DOMAIN not in host and target not in urls:
            urls.append(target)
    return urls


async def scan_hn(client: httpx.AsyncClient) -> list[Finding]:
    findings: list[Finding] = []
    try:
        res = await client.get(HN_SEARCH_URL, params={"query": BRAND, "tags": "(story,comment)", "hitsPerPage": 30})
        res.raise_for_status()
        hits = res.json().get("hits", [])
    except Exception as exc:  # noqa: BLE001
        print(f"[mention_scout] HN scan failed: {exc}")
        return findings
    for hit in hits:
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        text = " ".join(filter(None, [hit.get("title"), hit.get("comment_text"), hit.get("story_title")]))
        if not _mentions_brand(text):
            continue
        is_link_hit = bool(hit.get("url")) and BRAND_DOMAIN in str(hit.get("url", "")).lower()
        findings.append(
            Finding(
                source="hn",
                url=url,
                title=(hit.get("title") or hit.get("story_title") or "HN comment")[:120],
                snippet=re.sub(r"<[^>]+>", "", str(hit.get("comment_text") or ""))[:200],
                kind="linked" if is_link_hit else "unlinked",
            )
        )
    return findings


async def scan_serp(client: httpx.AsyncClient, max_pages: int = 5) -> list[Finding]:
    findings: list[Finding] = []
    proxy = None
    try:
        from egress_dataimpulse import DataImpulseProxyRouter

        if DataImpulseProxyRouter.is_available():
            proxy = DataImpulseProxyRouter.get_proxy_url(country="us")
    except Exception:  # noqa: BLE001
        proxy = None
    query = f'"{BRAND_DOMAIN}" -site:{BRAND_DOMAIN}'
    try:
        async with httpx.AsyncClient(headers=BROWSER_HEADERS, timeout=40, proxy=proxy, follow_redirects=True) as serp_client:
            res = await serp_client.get("https://www.bing.com/search", params={"q": query, "count": 20})
            res.raise_for_status()
            candidates = extract_bing_result_urls(res.text)
            if not candidates:
                ddg = await serp_client.get(DDG_URL, params={"q": query})
                ddg.raise_for_status()
                candidates = extract_ddg_result_urls(ddg.text)
    except Exception as exc:  # noqa: BLE001
        print(f"[mention_scout] SERP scan failed: {exc}")
        return findings
    for page_url in candidates:
        try:
            page = await client.get(page_url, headers=BROWSER_HEADERS, timeout=25, follow_redirects=True)
            page.raise_for_status()
        except Exception:  # noqa: BLE001
            continue
        if find_unlinked(page.text):
            title_m = re.search(r"<title[^>]*>([^<]+)</title>", page.text, re.IGNORECASE)
            findings.append(
                Finding(
                    source="serp",
                    url=page_url,
                    title=(title_m.group(1).strip() if title_m else page_url)[:120],
                    kind="unlinked",
                )
            )
    return findings


async def notify(findings: list[Finding]) -> int:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID or not findings:
        return 0
    sent = 0
    async with httpx.AsyncClient(timeout=20) as client:
        for f in findings[:10]:
            icon = "\U0001f517" if f.kind == "linked" else "\U0001f4dd"
            text = (
                f"{icon} <b>[Mention Scout]</b> {f.kind.upper()} mention\n\n"
                f"<b>{f.source.upper()}:</b> {f.title}\n{f.url}\n"
            )
            if f.snippet:
                text += f"\n<i>\"{f.snippet[:160]}\"</i>"
            try:
                res = await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
                )
                if res.status_code == 200:
                    sent += 1
            except Exception:  # noqa: BLE001
                continue
    return sent


async def run(max_new: int = 10) -> dict[str, int]:
    state = load_state()
    seen = set(state["seen_urls"])
    async with httpx.AsyncClient(headers=BROWSER_HEADERS, timeout=30) as client:
        results = await asyncio.gather(scan_hn(client), scan_serp(client))
    new_findings: list[Finding] = []
    for batch in results:
        for f in batch:
            if f.url not in seen:
                new_findings.append(f)
                seen.add(f.url)
    notified = await notify(new_findings[:max_new])
    state["seen_urls"].extend(f.url for f in new_findings)
    state["findings"].extend(f.to_dict() for f in new_findings)
    state["findings"] = state["findings"][-500:]
    save_state(state)
    summary = {"hn": len(results[0]), "serp": len(results[1]), "new": len(new_findings), "notified": notified}
    print(f"[mention_scout] {summary}")
    return summary


if __name__ == "__main__":
    asyncio.run(run())
