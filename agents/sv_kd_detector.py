#!/usr/bin/env python3
"""Zero-cost Search Volume estimate + local Vector Keyword Difficulty (KD) detector.

Implements the TOA-documented "Detector Module" using ONLY free lanes already
wired in this repo, so it satisfies the $0/month infrastructure invariant:

  SV  — Google Trends anonymous explore ratio (best-effort, no impersonation)
         scaled against an anchor keyword with known volume, PLUS Google
         Autocomplete presence + rank depth as a volume-vicinity proxy.
  KD  — SPEC-0825 V2 KD_effective = KD_base * (1 - Penalty_vuln), scaled 0–1:
         * KD_base = 0.35*SMS + 0.35*TOC_BM25+ + 0.30*GPB
             SMS  Semantic Match Score   — exact target keyword in title/URL/H1
                                           of top-10 organic competitors
             TOC_BM25+ Pure-stdlib Okapi BM25+ pairwise topical overlap
             GPB   Gravitational Potential Barrier — DA-mass-weighted authority
         * Penalty_vuln = Clamp(0.35*SWI + 0.15*H_norm + 0.20*ZEF, 0.0, 0.60)
             SWI   SERP Weakness Index (forum/calculator-intent/outdated signals)
             H_norm Shannon intent entropy over 5 classes
             ZEF   Zombie Exploit Factor (dead SERP domains, HEAD >= 400)

Guardrails honored:
  - No TLS impersonation (plain httpx, standard browser User-Agent).
  - No IP rotation to dodge 403s; every upstream is graceful per-source.
  - Pure stdlib TF-IDF, reusing agents.target_intel primitives (no numpy/sklearn).
  - Every lane fails soft (SKIPPED) so the pipeline never dies on one source.

Usable standalone:
    python agents/sv_kd_detector.py --keyword "tdee calculator" --anchor-query "seo"
    python agents/sv_kd_detector.py --keyword "solar battery payback" --html-depth 3 --rdap
"""

import argparse
import asyncio
import contextlib
import hashlib
import json
import logging
import math
import os
import re
import sys
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, timedelta
from functools import lru_cache
from typing import Any

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.pseo_pipeline import _query_openserp_organic, fetch_google_autocomplete
from agents.target_intel import _idf, _tfidf_terms, _tokenize

logger = logging.getLogger("sv_kd_detector")

TRENDS_BASE = "https://trends.google.com/trends/api"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
TIME_RANGE = "today 12-m"

# Zero-cost DFB authority heuristics — never device-detect or spoof; purely data.
AUTHORITY_DOMAINS = {
    "wikipedia.org", "nih.gov", "cdc.gov", "edu", "gov", "who.int",
    "mayoclinic.org", "webmd.com", "healthline.com", "nerdwallet.com",
    "investopedia.com", "forbes.com", "bankrate.com", "energysage.com",
    "reddit.com", "quora.com", "cnbc.com", "consumerreports.org",
}
TLD_WEIGHT = {".gov": 1.0, ".edu": 1.0, ".org": 0.7}

ANCHOR_DEFAULT = ("seo", 100_000)  # spec: "SEO" ≈ 100,000 searches/mo
MIN_INTEREST = 1.0  # below this, Trends returns 0 for everyone anyway


# ── SPEC-0825 V2 systems & constants ──────────────────────────────────────────
BROWSER_CHROME_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Sec-Ch-Ua": '"Not.A/Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Upgrade-Insecure-Requests": "1",
}
WIKI_OPENSEARCH = "https://en.wikipedia.org/w/api.php"
WIKI_PAGEVIEWS_BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
ZIPF_K = 1.07
ZIPF_S = 0.50
TOOL_INTENT_MARKERS = ("calculator", "calculate", "convert", "tool", "checker", "estimator", "planner")
CALC_INTENT_MARKERS = ("calculator", "calc", "compute", "how much", "cost estimator", "what is my")
MASS_AUTHORITY = {
    "wikipedia.org", "nih.gov", "cdc.gov", "who.int", "health.gov", "nutrition.gov",
    "mayoclinic.org", "webmd.com", "nerdwallet.com", "investopedia.com", "forbes.com",
    "bankrate.com", "energysage.com", "consumerreports.org", "cnbc.com", "politico.com",
    "reuters.com", "healthline.com",
}
MASS_ACADEMIC = {"wikipedia.org", "nih.gov", "cdc.gov", "who.int", "health.gov", "nutrition.gov"}
_ZIPF_CACHE: dict[str, list[str]] = {}


# ── SPEC-0825 §2.1.A  Zipf-Mandelbrot autocomplete + combinatorial branching ──
def _suggest(query: str) -> list[str]:
    """Cached autocomplete descendants (bounds duplicate Google calls per keyword)."""
    if query not in _ZIPF_CACHE:
        _ZIPF_CACHE[query] = fetch_google_autocomplete(query) or []
    return _ZIPF_CACHE[query]


@lru_cache(maxsize=256)
def _zipf_weight(r: int) -> float:
    """Zipf-Mandelbrot rank weight w(r) = 1/(r + S)^K."""
    return 1.0 / ((r + ZIPF_S) ** ZIPF_K)


def compute_zipf_autocomplete(keyword: str) -> dict[str, Any]:
    """P_sug(k) = w(rank)/Σw over Google Autocomplete top-10; 0.05 long-tail; 0.0 absent."""
    suggests = _suggest(keyword)
    if not suggests:
        return {"p_sug": 0.0, "present": False, "rank": None, "long_tail": False, "suggests": []}
    lowered = keyword.lower()
    total = sum(_zipf_weight(r) for r in range(1, 11))
    rank = next((i for i, s in enumerate(suggests[:10]) if s.strip().lower() == lowered), None)
    if rank is not None:
        p = _zipf_weight(rank + 1) / total
        return {"p_sug": round(min(1.0, p), 4), "present": True, "rank": rank, "long_tail": False, "suggests": suggests[:6]}
    rank = next((i for i, s in enumerate(suggests[:10]) if lowered in s), None)
    if rank is not None:
        p = _zipf_weight(rank + 1) / total
        return {"p_sug": round(min(1.0, p), 4), "present": True, "rank": rank, "long_tail": False, "suggests": suggests[:6]}
    if any(lowered in s for s in suggests):
        return {"p_sug": 0.05, "present": True, "rank": None, "long_tail": True, "suggests": suggests[:6]}
    return {"p_sug": 0.0, "present": False, "rank": None, "long_tail": False, "suggests": suggests[:6]}


def compute_branching_factor(keyword: str) -> dict[str, Any]:
    """B(k): fraction of probe variants yielding >=3 suggestions (spec §2.1.B)."""
    variants = [keyword, "how to " + keyword, keyword + " vs", keyword + " cost", keyword + " best"]
    counts: dict[str, int] = {}

    def _probe(v: str) -> None:
        counts[v] = len(_suggest(v))

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_probe, v): v for v in variants}
        for fut in futures:
            try:
                fut.result(timeout=8.0)
            except Exception as e:
                logger.debug("Branching probe failed for '%s': %s", futures[fut], e)
    b = sum(1 for v in variants if counts.get(v, 0) >= 3) / len(variants)
    return {"branching_factor": round(min(1.0, b), 3), "probe_counts": counts}


# ── SPEC-0825 §2.1.C  Wikidata/Wikimedia monthly views ────────────────────────
def _month_boundaries() -> tuple[str, str]:
    """Previous-calendar-month boundaries as YYYYMMDD00 pageviews window."""
    today = date.today()
    first_this = today.replace(day=1)
    first_prev = (first_this - timedelta(days=1)).replace(day=1)
    return first_prev.strftime("%Y%m%d") + "00", first_this.strftime("%Y%m%d") + "00"


@lru_cache(maxsize=256)
def fetch_wikimedia_monthly_views(keyword: str) -> dict[str, Any]:
    """Opensearch title resolution + Wikimedia pageviews monthly views (spec §2.1.C)."""
    try:
        with httpx.Client(timeout=4.0, headers=BROWSER_CHROME_HEADERS, follow_redirects=True) as client:
            res = client.get(
                WIKI_OPENSEARCH,
                params={"action": "opensearch", "search": keyword, "limit": 1, "namespace": 0, "format": "json"},
            )
            if res.status_code != 200:
                return {"ok": False, "reason": f"opensearch {res.status_code}"}
            payload = res.json()
            titles = payload[1] if len(payload) > 1 and payload[1] else []
            if not titles:
                return {"ok": False, "reason": "no article"}
            title = urllib.parse.quote(titles[0].replace(" ", "_"))
        first_prev, first_this = _month_boundaries()
        url = f"{WIKI_PAGEVIEWS_BASE}/en.wikipedia/all-access/user/{title}/monthly/{first_prev}/{first_this}"
        with httpx.Client(timeout=6.0, headers=BROWSER_CHROME_HEADERS) as client:
            res = client.get(url)
            if res.status_code != 200:
                return {"ok": False, "reason": f"pageviews {res.status_code}"}
            items = res.json().get("items") or []
        views = sum(int(item.get("views", 0)) for item in items)
        if views <= 0:
            return {"ok": False, "reason": "zero views"}
        return {"ok": True, "title": titles[0], "monthly_views": views, "sv_wiki": round(views * 1.85, 1)}
    except Exception as e:
        logger.debug("Wikimedia probe failed for '%s': %s", keyword, e)
        return {"ok": False, "reason": "exception"}


# ── SPEC-0825 §2.1.D  Okapi BM25+ topical overlap (pure stdlib) ───────────────
def compute_bm25_toc(documents: list[str]) -> dict[str, Any]:
    """Mean pairwise Okapi BM25+ cosine overlap over document list (0..1)."""
    tok = [list(_tokenize(d)) for d in documents]
    tok = [t for t in tok if t]
    n = len(tok)
    if n < 2:
        return {"toc_bm25": 0.0, "pairs": 0, "docs": n}
    lens = [len(t) for t in tok]
    avgdl = sum(lens) / n
    k1, b, delta = 1.2, 0.75, 1.0
    df = Counter(term for t in tok for term in set(t))
    idf = {term: math.log((n - df[term] + 0.5) / (df[term] + 0.5) + 1.0) for term in df}

    def _vector(tokens: list[str]) -> dict[str, float]:
        f = Counter(tokens)
        tlen = len(tokens)
        return {
            term: (c * (k1 + 1)) / (c + k1 * (1 - b + b * (tlen / avgdl))) + delta
            for term, c in f.items()
        }

    vecs = [_vector(t) for t in tok]

    def _cosine(a: dict[str, float], vb: dict[str, float]) -> float:
        common = set(a) & set(vb)
        dot = sum(a[t] * idf.get(t, 0.0) * vb[t] * idf.get(t, 0.0) for t in common)
        na = math.sqrt(sum(v * v * (idf.get(t, 0.0) ** 2) for t, v in a.items())) or 1.0
        nb = math.sqrt(sum(v * v * (idf.get(t, 0.0) ** 2) for t, v in vb.items())) or 1.0
        return dot / (na * nb) if na and nb else 0.0

    sims = [_cosine(vecs[i], vecs[j]) for i in range(n) for j in range(i + 1, n)]
    toc = sum(sims) / len(sims) if sims else 0.0
    return {"toc_bm25": round(min(1.0, toc), 3), "pairs": len(sims), "docs": n}


# ── SPEC-0825 §2.1.E  Gravitational Potential Barrier + Shannon entropy ──────
def _gpb_mass(domain: str) -> float:
    """DA-mass: 1.0 gov/edu/root-authority, 0.7 .org, 0.2 forums, 0.4 commercial."""
    for root in MASS_AUTHORITY:
        if domain == root or domain.endswith("." + root):
            return 1.0
    if any(tail in domain for tail in ("reddit.com", "quora.com", "forum", "stackexchange")):
        return 0.2
    tld = "." + domain.rsplit(".", 1)[-1]
    if tld in (".gov", ".edu", ".mil", ".int"):
        return 1.0
    if tld == ".org":
        return 0.7
    return 0.4


def compute_gravitational_barrier(results: list[dict[str, Any]]) -> dict[str, Any]:
    """GPB = Σ(M_i/i^1.35)/Σ(1/i^1.35) over top-10 SERP domains (0..1)."""
    urls = [(r.get("url") or r.get("link") or "") for r in results[:10]]
    domains = [extract_domain(u) for u in urls]
    domains = [d for d in domains if d]
    n = len(domains)
    if not n:
        return {"gpb": 0.0, "domains": [], "masses": []}
    masses = [_gpb_mass(d) for d in domains]
    denom = sum(1.0 / (i ** 1.35) for i in range(1, n + 1))
    numer = sum(m / ((i + 1) ** 1.35) for i, m in enumerate(masses))
    gpb = numer / denom if denom else 0.0
    return {"gpb": round(min(1.0, gpb), 3), "domains": domains, "masses": masses}


def _intent_class(r: dict[str, Any]) -> str:
    """Assign one of {TOOL, FORUM, ACADEMIC, COMMERCIAL, EDITORIAL} to a result."""
    url = r.get("url") or r.get("link") or ""
    dom = extract_domain(url)
    text = f"{r.get('title') or ''} {r.get('snippet') or r.get('description') or ''}".lower()
    if dom and any(f in dom for f in ("reddit", "quora", "forum", "stackexchange")):
        return "FORUM"
    if dom and (dom in MASS_ACADEMIC or dom.endswith(".edu")):
        return "ACADEMIC"
    if any(m in text or m in dom for m in TOOL_INTENT_MARKERS):
        return "TOOL"
    if any(m in text for m in ("amazon", "price", "cost", "buy", "shop", "deals", "compare", "reviews")):
        return "COMMERCIAL"
    return "EDITORIAL"


def compute_shannon_entropy(results: list[dict[str, Any]]) -> dict[str, Any]:
    """H_norm = H/log2(5) over the 5 intent classes (0..1)."""
    classes = [_intent_class(r) for r in results[:10]]
    counts = Counter(classes)
    n = max(1, len(classes))
    h = -sum((c / n) * math.log2(c / n) for c in counts.values() if c > 0)
    h_norm = h / math.log2(5) if h else 0.0
    return {"h": round(h, 4), "h_norm": round(min(1.0, h_norm), 4), "classes": dict(counts)}


# ── SPEC-0825 §2.1.F  Zombie SERP probe (dead-domain exploit factor) ─────────
async def _head_status(sem: asyncio.Semaphore, client: httpx.AsyncClient, url: str) -> int | None:
    async with sem:
        try:
            res = await client.head(url, follow_redirects=True, timeout=httpx.Timeout(3.5, connect=3.0))
            return res.status_code
        except Exception:
            return None


def probe_zombie_serp(urls: list[str], budget: float = 1.8) -> dict[str, Any]:
    """HEAD-probe top-10 SERP domains; status >= 400 marks a zombie. ZEF = zombies/probed."""
    targets = [u for u in urls[:10] if u]
    if not targets:
        return {"zombie_count": 0, "probed": 0, "zombie_exploit_factor": 0.0}
    statuses: list[int | None] = []

    async def _run() -> None:
        sem = asyncio.Semaphore(5)
        async with httpx.AsyncClient(headers=BROWSER_CHROME_HEADERS, follow_redirects=True) as client:
            tasks = [asyncio.create_task(_head_status(sem, client, u)) for u in targets]
            try:
                await asyncio.wait_for(asyncio.gather(*tasks), timeout=budget)
            except TimeoutError:
                for task in tasks:
                    task.cancel()
            for task in tasks:
                if task.done() and not task.cancelled() and task.exception() is None:
                    statuses.append(task.result())
                else:
                    statuses.append(None)

    asyncio.run(_run())
    results = [s for s in statuses if s is not None]
    zombies = sum(1 for s in results if s >= 400)
    zef = zombies / len(results) if results else 0.0
    return {"zombie_count": zombies, "probed": len(results), "zombie_exploit_factor": round(min(1.0, zef), 3)}


# ── SPEC-0825 V2 SWI penalty, SimHash near-dup audit ─────────────────────────
def _oldest_year(r: dict[str, Any]) -> int | None:
    text = r.get("snippet") or r.get("description") or ""
    m = re.search(r"\b(20\d\d)\b", text)
    return int(m.group(1)) if m else None


def _swi_score(keyword: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Local SERP Weakness Index = 0.40*I(Forum) + 0.35*I(NoCalc) + 0.25*OutdatedRatio."""
    kw_l = keyword.lower()
    calc_intent = any(m in kw_l for m in CALC_INTENT_MARKERS)
    calc_present = any(
        "calculator" in (r.get("url") or "") or "calculator" in (r.get("title") or "").lower()
        for r in results[:10]
    )
    forum_present = any(
        r.get("url") and any(f in r["url"].lower() for f in ("reddit.", "quora.", "forum."))
        for r in results[:10]
    )
    years = [_oldest_year(r) for r in results[:10]]
    now = date.today().year
    outdated = sum(1 for y in years if y is not None and y < now - 2)
    n = max(1, len(results[:10]))
    swi = 0.40 * float(forum_present) + 0.35 * float(calc_intent and not calc_present) + 0.25 * (outdated / n)
    return {
        "swi": round(min(1.0, swi), 3),
        "calc_intent": calc_intent,
        "calc_present": calc_present,
        "forum_present": forum_present,
        "outdated_ratio": round(outdated / n, 3),
    }


def _simhash(text: str) -> int:
    h = 0
    for tok in _tokenize(text):
        h ^= int.from_bytes(hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest(), "big")
    return h


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _near_dup_ratio(results: list[dict[str, Any]]) -> float:
    """Fraction of top-10 document pairs with SimHash hamming distance <= 3."""
    docs = [_simhash(f"{r.get('title') or ''} {r.get('snippet') or r.get('description') or ''}") for r in results[:10]]
    near = 0
    total = 0
    for i in range(len(docs)):
        for j in range(i + 1, len(docs)):
            total += 1
            if docs[i] and docs[j] and _hamming(docs[i], docs[j]) <= 3:
                near += 1
    return round(near / total, 3) if total else 0.0


# ── Google Trends (anonymous explore + cookie warm-up, best-effort) ──────────
def _trends_client() -> httpx.Client:
    """Stateful client that warms NID cookies via the Trends landing page."""
    client = httpx.Client(timeout=15.0, headers=HEADERS, follow_redirects=True)
    with contextlib.suppress(Exception):
        client.get(TRENDS_BASE.replace("/trends/api", "") + "/?geo=US&hl=en-US")  # best-effort cookie warm-up
    return client


def _trends_interest(keyword: str) -> float | None:
    """Relative interest 0–100 for a keyword over the last 12 months (US).

    Reverse-engineered anonymous endpoint used by pytrends; no auth, no
    impersonation. Returns None on ANY failure (404/429/CAPTCHA → SKIPPED).
    """
    try:
        req = {
            "comparisonItem": [{"keyword": keyword, "geo": "US", "time": TIME_RANGE}],
            "category": 0,
            "property": "",
        }
        url = f"{TRENDS_BASE}/explore"
        params = {"hl": "en-US", "tz": "-420", "req": json.dumps(req, separators=(",", ":"))}
        with _trends_client() as client:
            resp = client.get(url, params=params)
            if resp.status_code != 200:
                logger.debug("Trends explore non-200 (%s) for '%s'.", resp.status_code, keyword)
                return None
            raw = resp.text
            if raw.startswith(")]}',"):
                raw = raw[5:]
            payload = json.loads(raw)
        widgets = payload.get("widgets") or []
        timeseries = next((w for w in widgets if w.get("id") == "TIMESERIES"), None)
        if not timeseries:
            logger.debug("No TIMESERIES widget for '%s' (Trends gated).", keyword)
            return None
        data = timeseries.get("data") or {}
        timeline = data.get("timelineData") or []
        values = [p.get("value", [0])[0] for p in timeline]
        values = [v for v in values if isinstance(v, (int, float))]
        if not values:
            return None
        return float(sum(values)) / len(values)
    except Exception as e:
        logger.debug("Trends probe failed for '%s': %s", keyword, e)
        return None


# ── Resilient SERP fetch (free-lane fallback chain) ──────────────────────────
def _fetch_serp_organic(keyword: str) -> tuple[list[dict[str, Any]], bool, str]:
    """Organic top-10 across free lanes: OpenSERP -> SearchAPI -> Serper.

    Returns (normalized_organic, paa_present, source_name). Normalizes each
    lane's result shape to {title, url, snippet}. Never raises.
    """
    organic, paa = _query_openserp_organic(keyword)
    if organic:
        return organic, paa, "openserp"

    if os.environ.get("SEARCHAPI_API_KEY"):
        try:
            with httpx.Client(timeout=8.0) as client:
                res = client.get(
                    "https://www.searchapi.io/api/v1/search",
                    params={"engine": "google", "q": keyword, "api_key": os.environ["SEARCHAPI_API_KEY"], "num": 10},
                )
            if res.status_code == 200:
                rows = res.json().get("organic_results", [])
                norm = [
                    {"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", "")}
                    for r in rows
                ]
                if norm:
                    return norm, True, "searchapi"
        except Exception as e:
            logger.debug("SearchAPI lane failed for '%s': %s", keyword, e)

    if os.environ.get("SERPER_API_KEY"):
        try:
            with httpx.Client(timeout=8.0) as client:
                res = client.post(
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": os.environ["SERPER_API_KEY"]},
                    json={"q": keyword, "num": 10},
                )
            if res.status_code == 200:
                rows = res.json().get("organic", [])
                norm = [
                    {"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", "")}
                    for r in rows
                ]
                if norm:
                    return norm, bool(res.json().get("peopleAlsoAsk") or res.json().get("relatedSearches")), "serper"
        except Exception as e:
            logger.debug("Serper lane failed for '%s': %s", keyword, e)

    return [], False, "none"


# ── Vector KD: SMS / TOC / DFB ────────────────────────────────────────────────
def _norm_text(text: str) -> str:
    return " ".join(_tokenize(text))


def _sm_score(keyword: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Exact-keyword presence in title/URL/H1 of top-10 competitors (0..1)."""
    kw = _norm_text(keyword)
    tokens = set(kw.split())
    matched = 0
    details: list[dict[str, Any]] = []
    for r in results[:10]:
        title = _norm_text(r.get("title") or "")
        url = (r.get("url") or r.get("link") or "").lower()
        hit_title = kw in title
        hit_url = kw.replace(" ", "-") in url or kw.replace(" ", "") in url
        hits = {"title": hit_title, "url": hit_url}
        if tokens and tokens.issubset(set(title.split())):
            hits["title_tokens"] = True
            hit_title = True
        matched += 1 if (hit_title or hit_url) else 0
        details.append({"domain": extract_domain(url), "hits": hits})
    n = max(1, len(results[:10]))
    sms = round(matched / n, 3)
    return {"sms": sms, "matched": matched, "total": n, "details": details}


def _toc_score(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Mean pairwise TF-IDF cosine of competitor docs (0..1)."""
    docs = []
    for r in results[:10]:
        text = _norm_text(f"{r.get('title') or ''} {r.get('snippet') or r.get('description') or ''}")
        if text:
            docs.append(_tokenize(text))
    if len(docs) < 2:
        return {"toc": 0.0, "pairs": 0, "docs": len(docs)}
    idf = _idf(docs)
    vectors = [_tfidf_terms(d, idf) for d in docs]
    vecs = [dict(v) for v in vectors]

    def cosine(a: dict[str, float], b: dict[str, float]) -> float:
        common = set(a) & set(b)
        dot = sum(a[t] * b[t] for t in common)
        na = math.sqrt(sum(v * v for v in a.values())) or 1.0
        nb = math.sqrt(sum(v * v for v in b.values())) or 1.0
        return dot / (na * nb)

    sims = [cosine(vecs[i], vecs[j]) for i in range(len(vecs)) for j in range(i + 1, len(vecs))]
    toc = round(sum(sims) / len(sims), 3) if sims else 0.0
    return {"toc": toc, "pairs": len(sims), "docs": len(docs)}


def extract_domain(url: str) -> str:
    """Bare hostname (public suffix naive; sufficient for DFB heuristics)."""
    if not url:
        return ""
    host = url.split("://")[-1].split("/")[0].lower()
    parts = host.split(".")
    if len(parts) > 2 and parts[-2] in {"co", "com", "ac", "gov"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _domain_age(url: str) -> float | None:
    """RDAP registration age in years via rdap.org (free, coarse)."""
    domain = extract_domain(url)
    if not domain:
        return None
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            resp = client.get(f"https://rdap.org/domain/{domain}")
            if resp.status_code != 200:
                return None
            events = resp.json().get("events") or []
            reg = next((e.get("eventDate") for e in events if e.get("eventAction") == "registration"), None)
            if not reg:
                return None
            from datetime import datetime

            born = datetime.fromisoformat(reg.replace("Z", "+00:00"))
            years = (datetime.now(UTC) - born).days / 365.25
            return round(min(years, 50.0), 1)
    except Exception as e:
        logger.debug("RDAP age lookup failed for %s: %s", domain, e)
        return None


def _dfb_score(results: list[dict[str, Any]], use_rdap: bool) -> dict[str, Any]:
    """Zero-cost domain footprint (0..1): authority TLD/brand + optional RDAP age."""
    domains: list[str] = []
    scores: list[float] = []
    brand_counter: dict[str, int] = {}

    for r in results[:10]:
        dom = extract_domain(r.get("url") or r.get("link") or "")
        if not dom:
            continue
        domains.append(dom)
        base = TLD_WEIGHT.get("." + dom.rsplit(".", 1)[-1], 0.3)
        if dom in AUTHORITY_DOMAINS or dom.endswith(".gov") or dom.endswith(".edu"):
            base = max(base, 1.0)
        brand_counter[dom] = brand_counter.get(dom, 0) + 1
        scores.append(base)

    dfb = round(sum(scores) / len(scores), 3) if scores else 0.0

    # Domain repetition = strong branded footprint (same top-10 domain multiple times).
    repeats = round(max(brand_counter.values(), default=0) / 10, 3)

    rdap_note: dict[str, Any] = {"enabled": use_rdap}
    if use_rdap and domains:
        ages = [_domain_age(d) for d in domains[:3]]
        ages = [a for a in ages if a is not None]
        rdap_note["mean_age_years"] = round(sum(ages) / len(ages), 1) if ages else None

    final = round(min(1.0, dfb * 0.7 + repeats * 0.3), 3)
    return {"dfb": final, "domains": sorted(set(domains)), "repeats": repeats, "rdap": rdap_note}


def compute_kd(keyword: str, results: list[dict[str, Any]], html_depth: int = 0, use_rdap: bool = False) -> dict[str, Any]:
    """SPEC-0825 V2 KD_effective = KD_base * (1 - Penalty_vuln), bounded [0,1]."""
    sm = _sm_score(keyword, results)
    docs = [
        _norm_text(f"{r.get('title') or ''} {r.get('snippet') or r.get('description') or ''}")
        for r in results[:10]
    ]
    toc = compute_bm25_toc(docs)
    gpb = compute_gravitational_barrier(results)
    entropy = compute_shannon_entropy(results)
    swi = _swi_score(keyword, results)
    zef = probe_zombie_serp([r.get("url") or r.get("link") or "" for r in results])
    dfb = _dfb_score(results, use_rdap)

    kd_base = 0.35 * sm["sms"] + 0.35 * toc["toc_bm25"] + 0.30 * gpb["gpb"]
    penalty = max(0.0, min(0.60, 0.35 * swi["swi"] + 0.15 * entropy["h_norm"] + 0.20 * zef["zombie_exploit_factor"]))
    kd_eff = round(min(1.0, max(0.0, kd_base * (1.0 - penalty))), 3)
    band = ("open", "moderate", "hard")[0 if kd_eff < 0.40 else (1 if kd_eff < 0.70 else 2)]
    return {
        "kd": kd_eff,
        "kd_band": band,
        "kd_100": round(kd_eff * 100, 1),
        "kd_base": round(kd_base, 3),
        "kd_effective": kd_eff,
        "penalty_vuln": round(penalty, 3),
        "sms": sm,
        "toc": toc,
        "toc_bm25": toc,
        "gpb": gpb,
        "shannon": entropy,
        "swi": swi,
        "zombie": zef,
        "dfb": dfb,
        "audit_flags": {
            "near_dup_ratio": _near_dup_ratio(results),
            "multi_root_barrier": gpb["gpb"] >= 0.8,
            "zombie_heavy": zef["zombie_exploit_factor"] >= 0.4,
            "calc_intent_absent": swi["calc_intent"] and not swi["calc_present"],
        },
        "html_depth_used": 0,
        "html_depth_requested": html_depth,
    }


# ── SV estimate (Trends anchor ratio + Zipf/Bayesian synthesis) ──────────────
def _autocomplete_proxy(zipf: dict[str, Any]) -> dict[str, Any]:
    """Derive the legacy autocomplete proxy from the V2 zipf payload (no refetch)."""
    rank = zipf.get("rank")
    if not zipf.get("present"):
        return {"present": False, "score": None, "rank": None, "suggests": []}
    if rank is not None:
        return {"present": True, "score": round(1.0 - (rank / 10.0), 2), "rank": rank, "suggests": zipf.get("suggests") or []}
    return {"present": True, "score": 0.2, "rank": None, "suggests": zipf.get("suggests") or []}


def estimate_search_volume(
    keyword: str,
    anchor_query: str = ANCHOR_DEFAULT[0],
    anchor_volume: int = ANCHOR_DEFAULT[1],
) -> dict[str, Any]:
    """SV: Trends anchor ratio primary; Zipf+Wikimedia Bayesian fallbacks."""
    i_kw = _trends_interest(keyword)
    i_anchor = _trends_interest(anchor_query)
    zipf = compute_zipf_autocomplete(keyword)
    branch = compute_branching_factor(keyword)
    auto = _autocomplete_proxy(zipf)
    p = zipf["p_sug"]
    b = branch["branching_factor"]
    sv_wiki = None

    if i_kw is not None and i_anchor and i_anchor >= MIN_INTEREST:
        ratio = i_kw / i_anchor
        raw = anchor_volume * ratio
        estimate = max(10, round(raw, -1 if raw >= 100 else 0))
        method = "trends_anchor"
        confidence = "high" if zipf.get("present") else "medium"
    elif p > 0.0:
        wiki = fetch_wikimedia_monthly_views(keyword)
        sv_wiki = wiki.get("sv_wiki") if wiki.get("ok") else None
        if sv_wiki is not None:
            estimate = int(round(0.55 * sv_wiki + 0.45 * (2500 * p * b)))
            method = "bayesian_wiki"
            confidence = "medium"
        else:
            estimate = int(round(3000 * (p ** 0.8) * (b ** 1.2)))
            method = "zipf_bayesian"
            confidence = "low"
    else:
        estimate = None
        method = "none"
        confidence = "none"

    return {
        "keyword": keyword,
        "estimate": estimate,
        "method": method,
        "confidence": confidence,
        "trends_interest_keyword": i_kw,
        "trends_interest_anchor": i_anchor,
        "anchor": {"query": anchor_query, "volume": anchor_volume},
        "autocomplete": auto,
        "zipf": zipf,
        "branching": branch,
        "sv_wiki": sv_wiki,
    }


# ── Orchestrator + CLI ────────────────────────────────────────────────────────
def analyze_keyword(
    keyword: str,
    anchor_query: str = ANCHOR_DEFAULT[0],
    anchor_volume: int = ANCHOR_DEFAULT[1],
    html_depth: int = 0,
    use_rdap: bool = False,
) -> dict[str, Any]:
    organic, paa, serp_source = _fetch_serp_organic(keyword)
    if not organic:
        logger.warning("No organic results for '%s' (%s down?) — KD degraded.", keyword, serp_source)
    with ThreadPoolExecutor(max_workers=2) as pool:
        sv_fut = pool.submit(estimate_search_volume, keyword, anchor_query, anchor_volume)
        sv = sv_fut.result(timeout=20.0)
    kd = compute_kd(keyword, organic, html_depth, use_rdap) if organic else None
    return {
        "query": keyword,
        "paa_present": paa,
        "serp_source": serp_source,
        "serp_count": len(organic),
        "sv": sv,
        "kd": kd,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Zero-cost SV + local Vector KD detector")
    parser.add_argument("--keyword", required=True)
    parser.add_argument("--anchor-query", default=ANCHOR_DEFAULT[0])
    parser.add_argument("--anchor-volume", type=int, default=ANCHOR_DEFAULT[1])
    parser.add_argument("--html-depth", type=int, default=0, help="reserved for H1-level SMS enrichment")
    parser.add_argument("--rdap", action="store_true", help="enable optional RDAP domain-age lookup")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] sv_kd_detector: %(message)s")
    for handler in list(logging.getLogger().handlers):
        handler.setStream(sys.stderr)  # keep stdout pure for JSON
    result = analyze_keyword(args.keyword, args.anchor_query, args.anchor_volume, args.html_depth, args.rdap)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
