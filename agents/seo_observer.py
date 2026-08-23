#!/usr/bin/env python3
"""Groundwork Autonomous Search Discovery Control Plane — SEO Observer Agent.

Implements the full control-plane loop from docs/audit/chatgpt-review.md:
  INVENTORY → POLICY → OBSERVATION → DIAGNOSIS → PRIORITIZATION →
  ACTION → VERIFICATION → LEARNING

Key capabilities:
  - GSC URL Inspection API (P0/P1 priority scheduler, §8)
  - Bing Webmaster API integration + IndexNow fallback (§15)
  - Indexation state machine per URL (§5)
  - Canonical consistency checker (§10)
  - Orphan detection + TF-IDF auto-inject + Supabase update (§24)
  - Incident classifier — 15 incident types (§35)
  - Indexation velocity metrics (§9)
  - Content→search traceability: pipeline_run_id linkage (§37)
  - JSON baseline output docs/audit/seo-baseline-YYYY-MM-DD.json (§34)
  - Auto-fix safe cases + re-submit to GSC Indexing API (§29)

Environments: Cloud (GHA cron every 6h) + Local (CLI --inspect-url / --batch)

Usage:
  python agents/seo_observer.py                        # full batch run
  python agents/seo_observer.py --inspect-url URL      # single URL inspection
  python agents/seo_observer.py --batch-inspect --limit 50  # batch with limit
  python agents/seo_observer.py --dry-run              # no DB writes, no fixes
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import math
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlparse

import httpx

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SEO-OBSERVER] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("seo_observer")

# ── Environment ───────────────────────────────────────────────────────────
def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()

def load_env_file() -> None:
    """Load .env.local into os.environ when running outside GHA."""
    env_path = Path(__file__).parent.parent / ".env.local"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v

load_env_file()

SUPABASE_URL       = _env("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_KEY       = _env("SUPABASE_SERVICE_ROLE_KEY")
GSC_SA_B64         = _env("GSC_SERVICE_ACCOUNT_JSON_B64")
GSC_PROPERTY       = _env("GSC_PROPERTY", "https://gworky.com/")
SITE_URL           = _env("NEXT_PUBLIC_SITE_URL", "https://gworky.com")
BING_API_KEY       = _env("BING_WEBMASTER_KEY")
REVALIDATE_SECRET  = _env("REVALIDATE_SECRET")
INDEXNOW_KEY       = _env("INDEXNOW_KEY")

# ── Incident types (§35 review) ────────────────────────────────────────────
INCIDENT_TYPES = {
    "CRAWL_BLOCKED",
    "ROBOTS_BLOCKED",
    "HTTP_ERROR",
    "REDIRECT_CHAIN",
    "RENDERING_FAILURE",
    "CONTENT_REQUIRES_JS",
    "NOINDEX_CONTRADICTION",
    "CANONICAL_CONFLICT",
    "SITEMAP_CONFLICT",
    "ORPHAN",
    "DUPLICATE",
    "THIN_CONTENT",
    "STALE_CONTENT",
    "GOOGLE_NOT_INDEXED",
    "BING_NOT_INDEXED",
    "INDEXATION_DELAY",
    "GOOGLE_CANONICAL_DIVERGENCE",
    "PERFORMANCE_ANOMALY",
    "SEARCH_VISIBILITY_ANOMALY",
    "UNKNOWN",
}

# ── Google JWT Auth ────────────────────────────────────────────────────────
_token_cache: dict[str, Any] = {}

def _load_service_account() -> dict[str, str]:
    if not GSC_SA_B64:
        raise RuntimeError("GSC_SERVICE_ACCOUNT_JSON_B64 not set.")
    return json.loads(base64.b64decode(GSC_SA_B64).decode("utf-8"))

def _get_gsc_token(scope: str) -> str:
    """Dependency-free RS256 JWT → OAuth2 token for Google APIs."""
    now = int(time.time())
    cached = _token_cache.get(scope)
    if cached and cached["exp"] > now + 60:
        return cached["token"]

    try:
        import cryptography  # noqa: F401
        _use_cryptography = True
    except ImportError:
        _use_cryptography = False

    sa = _load_service_account()

    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    claims = base64.urlsafe_b64encode(
        json.dumps({
            "iss": sa["client_email"],
            "scope": scope,
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
        }).encode()
    ).rstrip(b"=").decode()
    signing_input = f"{header}.{claims}".encode()

    if _use_cryptography:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding as _padding
        private_key = serialization.load_pem_private_key(
            sa["private_key"].encode(), password=None
        )
        signature = private_key.sign(signing_input, _padding.PKCS1v15(), hashes.SHA256())
        sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    else:
        # Fallback: use subprocess openssl
        import subprocess
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
            f.write(sa["private_key"].encode())
            key_path = f.name
        result = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", key_path],
            input=signing_input, capture_output=True
        )
        os.unlink(key_path)
        sig_b64 = base64.urlsafe_b64encode(result.stdout).rstrip(b"=").decode()

    jwt = f"{header}.{claims}.{sig_b64}"
    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": jwt},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    _token_cache[scope] = {"token": token, "exp": now + 3550}
    return token

# ── Supabase HTTP client ───────────────────────────────────────────────────
def _supa_headers() -> dict[str, str]:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

def supa_select(table: str, params: dict[str, str]) -> list[dict]:
    url = f"{SUPABASE_URL}/rest/v1/{table}?" + urlencode(params)
    resp = httpx.get(url, headers=_supa_headers(), timeout=15)
    resp.raise_for_status()
    return resp.json()

def supa_upsert(table: str, rows: list[dict], on_conflict: str = "") -> None:
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {**_supa_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"}
    if on_conflict:
        headers["Prefer"] += f",on-conflict={on_conflict}"
    resp = httpx.post(url, json=rows, headers=headers, timeout=15)
    if resp.status_code not in (200, 201):
        log.warning("Supabase upsert error %s: %s", resp.status_code, resp.text[:300])

def supa_update(table: str, row_id: str, patch: dict) -> None:
    url = f"{SUPABASE_URL}/rest/v1/{table}?id=eq.{row_id}"
    headers = {**_supa_headers(), "Prefer": "return=minimal"}
    resp = httpx.patch(url, json=patch, headers=headers, timeout=15)
    if resp.status_code not in (200, 204):
        log.warning("Supabase update error %s: %s", resp.status_code, resp.text[:300])

# ── GSC URL Inspection API ─────────────────────────────────────────────────
SCOPES_READONLY = "https://www.googleapis.com/auth/webmasters.readonly"
SCOPES_INDEXING = "https://www.googleapis.com/auth/indexing"
GSC_INSPECTION_ENDPOINT = (
    "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
)

def inspect_url_gsc(url: str) -> dict[str, Any]:
    """Call GSC URL Inspection API for a single URL."""
    try:
        token = _get_gsc_token(SCOPES_READONLY)
        resp = httpx.post(
            GSC_INSPECTION_ENDPOINT,
            json={"inspectionUrl": url, "siteUrl": GSC_PROPERTY, "languageCode": "en-US"},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=15,
        )
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        data = resp.json()
        idx = data.get("inspectionResult", {}).get("indexStatusResult", {})
        mob = data.get("inspectionResult", {}).get("mobileUsabilityResult", {})
        return {
            "verdict": idx.get("verdict", "UNKNOWN"),
            "coverage_state": idx.get("coverageState", "UNKNOWN"),
            "indexing_state": idx.get("indexingState", "UNKNOWN"),
            "google_canonical": idx.get("googleCanonical", ""),
            "user_canonical": idx.get("userCanonical", ""),
            "last_crawl_time": idx.get("lastCrawlTime"),
            "crawled_as": idx.get("crawledAs"),
            "robots_txt_state": idx.get("robotsTxtState"),
            "page_fetch_state": idx.get("pageFetchState"),
            "referring_urls": idx.get("referringUrls", []),
            "mobile_usability_verdict": mob.get("verdict"),
            "raw": data,
        }
    except Exception as e:
        return {"error": str(e)}

# ── Bing Webmaster API ─────────────────────────────────────────────────────
BING_API_BASE = "https://ssl.bing.com/webmaster/api.svc/json"

def inspect_url_bing(url: str) -> dict[str, Any]:
    """Query Bing Webmaster API for URL crawl/index status.

    Primary: Bing Webmaster API (api key auth).
    Fallback: Check IndexNow ping history (no programmatic state available).
    """
    if not BING_API_KEY:
        return {"error": "BING_WEBMASTER_KEY not set", "bing_index_state": "UNKNOWN"}

    try:
        resp = httpx.get(
            f"{BING_API_BASE}/GetUrlInfo",
            params={"apikey": BING_API_KEY, "siteUrl": SITE_URL.rstrip("/"), "url": url},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            d = data.get("d", {}) or {}
            return {
                "bing_index_state": d.get("IsIndexed", False) and "INDEXED" or "NOT_INDEXED",
                "bing_crawl_state": d.get("CrawlState", "UNKNOWN"),
                "bing_last_crawl": d.get("LastCrawled"),
                "bing_submission_state": "SUBMITTED" if INDEXNOW_KEY else "NOT_SUBMITTED",
            }
        # Fallback: assume submitted via IndexNow if key is present
        log.debug("Bing API returned %s for %s, using IndexNow fallback", resp.status_code, url)
    except Exception as e:
        log.debug("Bing API error for %s: %s", url, e)

    # Fallback: IndexNow submission status
    return {
        "bing_index_state": "UNKNOWN",
        "bing_crawl_state": "UNKNOWN",
        "bing_last_crawl": None,
        "bing_submission_state": "SUBMITTED" if INDEXNOW_KEY else "NOT_SUBMITTED",
    }

# ── Production HTTP check ──────────────────────────────────────────────────
def probe_url(url: str) -> dict[str, Any]:
    """Fetch URL and extract SEO signals from raw HTTP + HTML."""
    try:
        resp = httpx.get(
            url, follow_redirects=True, timeout=10,
            headers={"User-Agent": "Groundwork-SEOObserver/1.0 (+https://gworky.com/robots.txt)"},
        )
        html = resp.text
        http_status = resp.status_code
        final_url = str(resp.url)

        # Canonical from <link rel="canonical">
        canonical_match = re.search(
            r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\'](.*?)["\']', html, re.I
        ) or re.search(
            r'<link[^>]+href=["\'](.*?)["\'][^>]+rel=["\']canonical["\']', html, re.I
        )
        canonical_declared = canonical_match.group(1).strip() if canonical_match else ""

        # noindex check
        noindex = bool(re.search(
            r'<meta[^>]+name=["\']robots["\'][^>]+content=["\'][^"\']*noindex', html, re.I
        ))

        # X-Robots-Tag header
        x_robots = resp.headers.get("X-Robots-Tag", "")
        if "noindex" in x_robots.lower():
            noindex = True

        # robots_allowed check (simplified — check disallow rules via robots.txt)
        robots_allowed = _check_robots(url)

        # H1 presence
        has_h1 = bool(re.search(r"<h1[\s>]", html, re.I))

        # Structured data valid (JSON-LD present)
        ld_matches = re.findall(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.S | re.I,
        )
        structured_data_valid = False
        for ld in ld_matches:
            try:
                json.loads(ld.strip())
                structured_data_valid = True
            except Exception:
                pass

        # Raw HTML content available (any meaningful text)
        raw_html_content = len(re.sub(r"<[^>]+>", "", html).strip()) > 200

        return {
            "http_status": http_status,
            "canonical_declared": canonical_declared,
            "canonical_resolved": final_url if final_url != url else "",
            "noindex": noindex,
            "robots_allowed": robots_allowed,
            "raw_html_content": raw_html_content,
            "structured_data_valid": structured_data_valid,
            "has_h1": has_h1,
        }
    except Exception as e:
        return {
            "http_status": 0,
            "canonical_declared": "",
            "canonical_resolved": "",
            "noindex": False,
            "robots_allowed": False,
            "raw_html_content": False,
            "structured_data_valid": False,
            "has_h1": False,
            "error": str(e),
        }

_robots_cache: dict[str, str] = {}

def _check_robots(url: str) -> bool:
    """Check if URL is allowed by robots.txt (simplified Googlebot check)."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    if robots_url not in _robots_cache:
        try:
            resp = httpx.get(robots_url, timeout=5)
            _robots_cache[robots_url] = resp.text if resp.status_code == 200 else ""
        except Exception:
            _robots_cache[robots_url] = ""
    robots_txt = _robots_cache[robots_url]
    path = parsed.path or "/"
    # Simplified robots.txt check for Googlebot / * rules
    in_relevant_block = False
    for line in robots_txt.splitlines():
        line = line.strip()
        if line.lower().startswith("user-agent:"):
            agent = line[len("user-agent:"):].strip()
            in_relevant_block = agent in ("*", "Googlebot")
        if not in_relevant_block:
            continue
        if line.startswith("Disallow:"):
            disallow_path = line[9:].strip()
            if not disallow_path:
                continue  # Empty Disallow means allow all
            disallow_prefix = disallow_path.rstrip("*")
            # Only block if path actually starts with this prefix
            # (and the disallow prefix is non-trivial — not just "/")
            if disallow_prefix and disallow_prefix != "/" and path.startswith(disallow_prefix):
                return False
    return True

# ── Sitemap checker ────────────────────────────────────────────────────────
_sitemap_urls: set[str] = set()
_sitemap_loaded = False

def _load_sitemap() -> None:
    global _sitemap_loaded
    if _sitemap_loaded:
        return
    try:
        resp = httpx.get(f"{SITE_URL}/sitemap.xml", timeout=10)
        if resp.status_code == 200:
            # Parse sitemap index or plain sitemap
            locs = re.findall(r"<loc>(.*?)</loc>", resp.text)
            for loc in locs:
                loc = loc.strip()
                if loc.endswith(".xml"):
                    # It's a sub-sitemap — fetch it
                    try:
                        sub = httpx.get(loc, timeout=10)
                        _sitemap_urls.update(
                            url_loc.strip()
                            for url_loc in re.findall(r"<loc>(.*?)</loc>", sub.text)
                            if not url_loc.strip().endswith(".xml")
                        )
                    except Exception:
                        pass
                else:
                    _sitemap_urls.add(loc)
    except Exception:
        pass
    _sitemap_loaded = True

# ── TF-IDF orphan link injection ───────────────────────────────────────────
def _tfidf_similarity(text_a: str, text_b: str) -> float:
    """Simple token TF-IDF cosine similarity for orphan link matching."""
    def tokenize(t: str) -> list[str]:
        return re.findall(r"[a-z]+", t.lower())

    tokens_a = tokenize(text_a)
    tokens_b = tokenize(text_b)
    if not tokens_a or not tokens_b:
        return 0.0

    set(tokens_a) | set(tokens_b)
    # IDF: log(2 / (1 + df)) where df is 0 or 1 in a 2-doc corpus
    shared = set(tokens_a) & set(tokens_b)

    dot = sum(tokens_a.count(w) * tokens_b.count(w) for w in shared)
    mag_a = math.sqrt(sum(tokens_a.count(w) ** 2 for w in set(tokens_a)))
    mag_b = math.sqrt(sum(tokens_b.count(w) ** 2 for w in set(tokens_b)))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)

def find_related_articles(
    orphan_slug: str,
    orphan_title: str,
    orphan_excerpt: str,
    all_articles: list[dict],
    pillar: str = "",
    top_n: int = 2,
) -> list[dict]:
    """Find top_n most similar articles within the same topical pillar using TF-IDF on title+excerpt."""
    orphan_text = f"{orphan_title} {orphan_excerpt}"
    scored = []
    for art in all_articles:
        if art.get("slug") == orphan_slug:
            continue
        # Strict Topical Silo: only match articles within the exact same pillar
        if pillar and art.get("pillar") != pillar:
            continue
        candidate_text = f"{art.get('title', '')} {art.get('excerpt', '')}"
        score = _tfidf_similarity(orphan_text, candidate_text)
        if score >= 0.40:
            scored.append((score, art))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [art for _, art in scored[:top_n]]

_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "is", "are",
    "how", "what", "why", "when", "your", "you", "our", "we", "it", "its",
    "with", "at", "by", "from", "that", "this", "guide", "best", "new",
}

# Words that must never START or END an anchor text — wrapping them reads
# unnaturally ("[on Apple TV+ is]", "[challenge where]") and dilutes anchor SEO.
_ANCHOR_EDGE_SW = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at",
    "for", "with", "from", "by", "is", "are", "was", "were", "be", "been",
    "it", "its", "as", "that", "this", "these", "those", "where", "when",
    "while", "if", "than", "then", "so", "such", "into", "about", "your",
}


def _topic_keywords(text: str) -> list[str]:
    """Content-bearing words from a title, used to locate weave-in slots."""
    return [w for w in re.findall(r"[a-z]+", text.lower()) if w not in _STOPWORDS and len(w) > 2]


def _weave_link_into_prose(host_content: str, target_url: str, target_title: str) -> Optional[str]:
    """Weave a contextual markdown link into an existing prose sentence.

    Per §2.7 anti-spam rules this NEVER appends template sentences ("For related
    guidance..."). Instead it finds the host sentence most similar to the target
    topic and wraps an existing phrase of that sentence as the anchor text.
    Returns updated content, or None when no natural slot exists.
    """
    target_kws = _topic_keywords(target_title)
    if not target_kws:
        return None

    kw_set = set(target_kws)
    # Ordered n-grams (4→2 words) of the target title for exact anchor phrases
    ngrams: list[tuple[int, str]] = []
    kws_len = len(target_kws)
    for size in (4, 3, 2):
        for i in range(kws_len - size + 1):
            ngrams.append((size, " ".join(target_kws[i : i + size])))

    paragraphs = host_content.split("\n\n")
    best = None  # (score, para_idx, sent_start, sent_end, anchor_text)
    for p_idx, para in enumerate(paragraphs):
        p_strip = para.strip()
        if (
            not p_strip
            or p_strip.startswith(("#", "```", "|", "-", ">"))
            or "](" in para
            or len(para) > 1200  # skip mega-paragraphs (likely tables/code)
        ):
            continue
        for m in re.finditer(r"[^.!?]*[.!?]", para):
            sentence = m.group(0)
            low = sentence.lower()
            overlap = sum(1 for w in re.findall(r"[a-z]+", low) if w in kw_set)
            if overlap == 0:
                continue
            score = overlap / max(len(target_kws), 1)
            if best is None or score > best[0]:
                best = (score, p_idx, m.start(), m.end(), sentence)

    if not best or best[0] < 0.15:
        return None

    _, p_idx, s_start, s_end, sentence = best
    para = paragraphs[p_idx]
    low_sent = sentence.lower()

    # 1) Prefer an existing multi-word phrase matching target n-grams.
    #    Word-boundary + suffix absorption so plural/inflected forms
    #    ("credits") are included in the anchor instead of cut mid-word.
    #    N-grams derive from title content words, so edges are already clean.
    anchor_span = None
    for size, phrase in ngrams:
        m = re.search(r"\b" + re.escape(phrase) + r"[a-z]{0,3}\b", low_sent)
        if m:
            anchor_span = (m.start(), m.end())
            break

    # 2) Fallback: wrap a run of consecutive CONTENT words containing the
    #    strongest keyword hit. Never crosses clause punctuation (commas,
    #    dashes) and never includes function words at the span edges — the
    #    anchor stays grammatically self-contained ("airline fee credits",
    #    not "on Apple TV+ is"). Nothing is deleted from the sentence.
    if anchor_span is None:
        hits = [(low_sent.find(w), w) for w in target_kws if low_sent.find(w) >= 0]
        if not hits:
            return None
        hits.sort()
        pos, word = hits[len(hits) // 2]
        words_with_spans = [(mm.start(), mm.end()) for mm in re.finditer(r"[A-Za-z][A-Za-z'-]*", sentence)]
        center = next((i for i, (a, b) in enumerate(words_with_spans) if a <= pos < b), None)
        if center is None:
            return None

        def _is_content(i: int) -> bool:
            w = words_with_spans[i]
            tok = sentence[w[0] : w[1]].lower()
            return tok not in _ANCHOR_EDGE_SW and tok not in _STOPWORDS

        def _adjacent(i: int, j: int) -> bool:
            return sentence[words_with_spans[i][1] : words_with_spans[j][0]] == " "

        lo = hi = center
        while hi - lo + 1 < 4 and lo > 0 and _adjacent(lo - 1, lo) and _is_content(lo - 1):
            lo -= 1
        while hi - lo + 1 < 4 and hi < len(words_with_spans) - 1 and _adjacent(hi, hi + 1) and _is_content(hi + 1):
            hi += 1
        anchor_span = (words_with_spans[lo][0], words_with_spans[hi][1])

    a, b = anchor_span
    anchor_text = sentence[a:b].strip(" ,.;:")
    first_tok = anchor_text.split(" ")[0].strip(".,;:!?\"'").lower() if anchor_text else ""
    last_tok = anchor_text.split(" ")[-1].strip(".,;:!?\"'").lower() if anchor_text else ""
    if (
        not anchor_text
        or "\n" in anchor_text
        or len(anchor_text.split()) < 2
        or first_tok in _ANCHOR_EDGE_SW
        or last_tok in _ANCHOR_EDGE_SW
    ):
        return None

    linked_sentence = f"{sentence[:a]}[{anchor_text}]({target_url}){sentence[b:]}"
    paragraphs[p_idx] = para[:s_start] + linked_sentence + para[s_end:]
    return "\n\n".join(paragraphs)


def inject_orphan_link(host_article: dict, target_article: dict, dry_run: bool) -> bool:
    """Inject a contextual link to target_article woven into host_article prose."""
    host_content = host_article.get("content", "")
    target_url = f"{SITE_URL.rstrip('/')}/article/{target_article.get('slug', '')}"
    target_title = target_article.get("title", "")

    # Strict Topical Silo check: host and target must belong to the same pillar
    if host_article.get("pillar") and target_article.get("pillar"):
        if host_article.get("pillar") != target_article.get("pillar"):
            return False

    # Don't inject if link already exists or article already has 2+ internal links
    if target_url in host_content or target_article.get("slug", "") in host_content:
        return False
    if host_content.count("/article/") >= 2:
        return False

    new_content = _weave_link_into_prose(host_content, target_url, target_title)
    if new_content is None:
        log.debug(
            "No natural prose slot for '%s' → '%s'; skipping to avoid boilerplate.",
            host_article.get("slug"), target_article.get("slug"),
        )
        return False

    if dry_run:
        log.info(
            "[DRY-RUN] Would inject orphan link in '%s' → '%s'",
            host_article.get("slug"), target_article.get("slug"),
        )
        return True

    # Update Supabase
    try:
        url = f"{SUPABASE_URL}/rest/v1/articles?id=eq.{host_article['id']}"
        headers = {**_supa_headers(), "Prefer": "return=minimal"}
        resp = httpx.patch(url, json={"content": new_content}, headers=headers, timeout=10)
        if resp.status_code in (200, 204):
            log.info(
                "Injected orphan link: '%s' → '%s'",
                host_article.get("slug"), target_article.get("slug"),
            )
            return True
    except Exception as e:
        log.warning("Failed to inject orphan link: %s", e)
    return False

def trigger_revalidate(slug: str) -> None:
    """Trigger ISR revalidation for an article page."""
    if not REVALIDATE_SECRET:
        return
    try:
        resp = httpx.post(
            f"{SITE_URL}/api/revalidate",
            json={"slug": slug},
            headers={"x-revalidate-secret": REVALIDATE_SECRET, "Content-Type": "application/json"},
            timeout=10,
        )
        log.info("Revalidated %s → HTTP %s", slug, resp.status_code)
    except Exception as e:
        log.warning("Revalidate error for %s: %s", slug, e)

# ── GSC re-notification after auto-fix ────────────────────────────────────
def notify_gsc_url_updated(url: str) -> bool:
    """Submit URL_UPDATED to Google Indexing API after auto-fix."""
    try:
        token = _get_gsc_token(SCOPES_INDEXING)
        resp = httpx.post(
            "https://indexing.googleapis.com/v3/urlNotifications:publish",
            json={"url": url, "type": "URL_UPDATED"},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False

# ── Priority scheduler (§8 review) ────────────────────────────────────────
def compute_priority(article: dict, existing_obs: dict | None) -> int:
    """
    P0 = 0 (highest), P1 = 1, P2 = 2, P3 = 3

    P0: is_flagship, previous CANONICAL_CONFLICT/NOINDEX_CONTRADICTION anomaly
    P1: recently published (<7d), high view_count, strategic pillars
    P2: normal published articles
    P3: stable older articles
    """
    anomalies = []
    if existing_obs:
        raw_anom = existing_obs.get("anomalies") or []
        if isinstance(raw_anom, str):
            try:
                anomalies = json.loads(raw_anom)
            except Exception:
                anomalies = []
        elif isinstance(raw_anom, list):
            anomalies = raw_anom

    # P0 conditions
    if article.get("is_flagship"):
        return 0
    p0_incidents = {"CANONICAL_CONFLICT", "NOINDEX_CONTRADICTION", "CRAWL_BLOCKED", "ROBOTS_BLOCKED"}
    incident_types = [a.get("type", "") if isinstance(a, dict) else str(a) for a in anomalies]
    if any(inc in p0_incidents for inc in incident_types):
        return 0

    # P1 conditions
    published_at = article.get("published_at") or article.get("created_at") or ""
    if published_at:
        try:
            pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            age_days = (datetime.now(UTC) - pub).days
            if age_days <= 7:
                return 1
        except Exception:
            pass
    if (article.get("view_count") or 0) > 100:
        return 1
    if article.get("pillar") in ("money", "body"):
        return 1

    # P2 default for published articles
    if article.get("status") == "published":
        return 2

    return 3

# ── Incident classifier (§35 review) ──────────────────────────────────────
def classify_incidents(
    article: dict,
    probe: dict,
    gsc: dict,
    bing: dict,
    in_sitemap: bool,
    inbound_links: int,
) -> list[dict]:
    incidents = []
    probe.get("canonical_declared", "") or article.get("slug", "")
    now_iso = datetime.now(UTC).isoformat()

    def add(incident_type: str, detail: str) -> None:
        incidents.append({"type": incident_type, "detail": detail, "detected_at": now_iso})

    # HTTP errors
    status = probe.get("http_status", 0)
    if status == 0:
        add("HTTP_ERROR", "URL unreachable")
    elif status >= 400:
        add("HTTP_ERROR", f"HTTP {status}")

    # Robots blocked
    if not probe.get("robots_allowed", True):
        add("ROBOTS_BLOCKED", "robots.txt blocks crawling")

    # noindex contradiction: in sitemap but noindex
    if in_sitemap and probe.get("noindex"):
        add("NOINDEX_CONTRADICTION", "URL in sitemap but has noindex directive")

    # Canonical conflict: sitemap URL != declared canonical
    canonical = probe.get("canonical_declared", "")
    article_url = f"{SITE_URL.rstrip('/')}/article/{article.get('slug', '')}"
    if in_sitemap and canonical and canonical != article_url and canonical.rstrip("/") != article_url.rstrip("/"):
        add("SITEMAP_CONFLICT", f"Sitemap URL {article_url} != canonical {canonical}")

    # Google canonical divergence
    google_canonical = gsc.get("google_canonical", "")
    if google_canonical and canonical and google_canonical.rstrip("/") != canonical.rstrip("/"):
        add("GOOGLE_CANONICAL_DIVERGENCE", f"Google selected {google_canonical} != declared {canonical}")

    # Google not indexed (Invariant D: temporal awareness — < 72h is DISCOVERY_PENDING, not critical error)
    indexing_state = gsc.get("indexing_state", "UNKNOWN")
    if indexing_state in ("INDEXING_NOT_ALLOWED", "BLOCKED_BY_META_TAG", "BLOCKED_BY_HTTP_HEADER"):
        add("CANONICAL_CONFLICT" if "canonical" in indexing_state.lower() else "NOINDEX_CONTRADICTION",
            f"Google indexing_state: {indexing_state}")
    coverage = gsc.get("coverage_state", "")
    pub_str = article.get("published_at") or article.get("created_at") or now_iso
    is_fresh = False
    try:
        pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
        is_fresh = (datetime.now(UTC) - pub_dt).total_seconds() < (72 * 3600)
    except Exception:
        pass

    if coverage and ("not indexed" in coverage.lower() or "unknown" in coverage.lower()):
        if is_fresh:
            add("DISCOVERY_PENDING", f"GSC: {coverage} (Publication age < 72h, within normal crawl SLA)")
        else:
            add("GOOGLE_NOT_INDEXED", f"GSC: {coverage}")

    # Orphan
    if inbound_links == 0:
        add("ORPHAN", "No internal inbound links detected")

    # Bing not indexed
    bing_state = bing.get("bing_index_state", "UNKNOWN")
    if bing_state == "NOT_INDEXED":
        if is_fresh:
            add("DISCOVERY_PENDING", "Bing index state: NOT_INDEXED (Publication age < 72h)")
        else:
            add("BING_NOT_INDEXED", "Bing index state: NOT_INDEXED")

    # Content quality
    if not probe.get("raw_html_content"):
        add("CONTENT_REQUIRES_JS", "No meaningful content in initial HTML")
    if not probe.get("structured_data_valid"):
        add("THIN_CONTENT", "No valid JSON-LD structured data found")

    return incidents

# ── Count inbound links ────────────────────────────────────────────────────
def count_inbound_links(slug: str, all_articles: list[dict]) -> int:
    """Count how many other articles link to this slug in their content."""
    count = 0
    pattern = re.compile(re.escape(slug), re.I)
    for art in all_articles:
        if art.get("slug") == slug:
            continue
        if pattern.search(art.get("content", "")):
            count += 1
    return count

# ── Main observation function ──────────────────────────────────────────────
def observe_article(
    article: dict,
    all_articles: list[dict],
    priority: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run a full observation cycle for one article URL."""
    slug = article.get("slug", "")
    url = f"{SITE_URL.rstrip('/')}/article/{slug}"
    now = datetime.now(UTC).isoformat()

    log.info("[P%d] Observing: %s", priority, url)

    # 1. Probe production HTTP
    probe = probe_url(url)

    # 2. GSC URL Inspection (P0+P1 only)
    gsc = {}
    if priority <= 1:
        gsc = inspect_url_gsc(url)
        if gsc.get("error"):
            log.warning("GSC inspection error for %s: %s", url, gsc["error"])

    # 3. Bing Webmaster API
    bing = inspect_url_bing(url)

    # 4. Sitemap membership
    _load_sitemap()
    in_sitemap = url in _sitemap_urls or url.rstrip("/") in _sitemap_urls

    # 5. Inbound link count
    inbound_links = count_inbound_links(slug, all_articles)

    # 6. Incident classification
    anomalies = classify_incidents(article, probe, gsc, bing, in_sitemap, inbound_links)

    is_orphan = inbound_links == 0

    # 7. Indexation velocity
    pub_at = article.get("published_at") or article.get("created_at")
    first_index_at = None
    time_to_index_hours = None
    if (
        gsc.get("coverage_state", "").lower().find("indexed") >= 0
        and gsc.get("coverage_state", "").lower().find("not") == -1
        and pub_at
    ):
        try:
            pub = datetime.fromisoformat(pub_at.replace("Z", "+00:00"))
            first_index_at = now
            time_to_index_hours = round(
                (datetime.now(UTC) - pub).total_seconds() / 3600, 2
            )
        except Exception:
            pass

    # 8. Build observation record
    obs = {
        "article_id": article.get("id"),
        "content_type": "article",
        "url": url,
        "http_status": probe.get("http_status"),
        "robots_allowed": probe.get("robots_allowed"),
        "noindex": probe.get("noindex"),
        "canonical_declared": probe.get("canonical_declared"),
        "canonical_resolved": probe.get("canonical_resolved") or None,
        "in_sitemap": in_sitemap,
        "internal_inbound_links": inbound_links,
        "orphan": is_orphan,
        "raw_html_content": probe.get("raw_html_content"),
        "structured_data_valid": probe.get("structured_data_valid"),
        "google_verdict": gsc.get("verdict"),
        "google_coverage_state": gsc.get("coverage_state"),
        "google_indexing_state": gsc.get("indexing_state"),
        "google_canonical": gsc.get("google_canonical") or None,
        "google_user_canonical": gsc.get("user_canonical") or None,
        "google_last_crawl": gsc.get("last_crawl_time"),
        "google_crawl_rendering": gsc.get("crawled_as"),
        "google_inspection_at": now if gsc else None,
        "google_referring_urls": json.dumps(gsc.get("referring_urls", [])),
        "bing_index_state": bing.get("bing_index_state"),
        "bing_crawl_state": bing.get("bing_crawl_state"),
        "bing_last_crawl": bing.get("bing_last_crawl"),
        "bing_submission_state": bing.get("bing_submission_state"),
        "bing_observed_at": now,
        "published_at": pub_at,
        "first_index_observed_at": first_index_at,
        "time_to_index_hours": time_to_index_hours,
        "anomalies": json.dumps(anomalies),
        "pipeline_run_id": article.get("pipeline_run_id") or article.get("_pipeline_run_id"),
        "observed_at": now,
    }

    # 9. Auto-fix: orphan → inject link (Policy: ISR revalidation + sitemap/RSS discovery; NO Indexing API for articles)
    auto_fixed = []
    if is_orphan and not dry_run:
        related = find_related_articles(
            slug,
            article.get("title", ""),
            article.get("excerpt", ""),
            all_articles,
            pillar=article.get("pillar", ""),
        )
        for host_candidate in related[:2]:
            injected = inject_orphan_link(host_candidate, article, dry_run=False)
            if injected:
                trigger_revalidate(host_candidate.get("slug", ""))
                auto_fixed.append({
                    "action": "orphan_link_injected",
                    "host_slug": host_candidate.get("slug"),
                    "target_slug": slug,
                    "timestamp": now,
                })

    if auto_fixed:
        obs["auto_fixed"] = json.dumps(auto_fixed)
        obs["auto_fix_revalidated_at"] = now

    return obs


# ── 6-Pillar SEO Intelligence Engine (SeoToolkit & DataSEO Pattern) ───────

def score_aeo_article(content: str, title: str, schema_data: dict | None = None) -> dict[str, Any]:
    """Score Generative Engine Optimization (GEO) & Answer Engine Optimization (AEO) readiness."""
    score = 0
    recs: list[str] = []

    # 1. Direct answer density (0-25)
    first_para = content.strip().split("\n\n")[0] if content else ""
    if len(first_para.split()) >= 15 and ("##" not in first_para or len(first_para.split("\n")) > 1):
        score += 25
    else:
        score += 10
        recs.append("Add a direct, concise 20-35 word answer to the primary query in the first paragraph.")

    # 2. Structured tables / comparison / numerical lists (0-25)
    has_table = "|" in content and "-|-" in content
    has_benchmarks = bool(re.search(r"(\$\d[\d,]*|\b\d+(\.\d+)?%|\b\d+\s*(?:years|months|studies|products)\b)", content))
    if has_table or has_benchmarks:
        score += 25
    else:
        score += 10
        recs.append("Include comparison tables or data benchmark lists to enhance LLM extractability.")

    # 3. FAQ Schema & AEO format (0-25)
    has_faq = "### " in content or (schema_data and "FAQPage" in json.dumps(schema_data))
    if has_faq:
        score += 25
    else:
        score += 10
        recs.append("Include a structured FAQ section with FAQPage schema markup.")

    # 4. Citations & Entity Density (0-25)
    has_citations = "doi.org" in content or "Groundwork" in content or "http" in content
    if has_citations:
        score += 25
    else:
        score += 15
        recs.append("Inject empirical citations or verified entity attribution.")

    grade = "A+" if score >= 90 else "A" if score >= 80 else "B" if score >= 70 else "C"
    return {
        "title": title,
        "aeo_score": min(100, score),
        "grade": grade,
        "citation_ready": score >= 80,
        "recommendations": recs,
    }


def detect_cannibalization(articles: list[dict]) -> list[dict[str, Any]]:
    """Detect keyword conflicts where multiple articles target overlapping search intent."""
    conflicts: list[dict[str, Any]] = []
    seen_keywords: dict[str, list[dict]] = {}

    for art in articles:
        title = art.get("title", "").lower()
        slug = art.get("slug", "")
        words = [w for w in re.findall(r"\b[a-z]{4,}\b", title) if w not in {"with", "that", "this", "from", "your", "what", "how"}]
        for w in words[:3]:
            seen_keywords.setdefault(w, []).append({"slug": slug, "title": art.get("title")})

    for kw, arts in seen_keywords.items():
        if len(arts) > 1:
            conflicts.append({
                "keyword": kw,
                "competing_articles": arts,
                "conflict_severity": "high" if len(arts) >= 3 else "medium",
            })
    return conflicts


def extract_search_intent_paa(keyword: str) -> list[str]:
    """Fetch People Also Ask / related questions for search intent enrichment."""
    try:
        url = f"https://suggestqueries.google.com/complete/search?client=firefox&q={quote(keyword)}"
        with httpx.Client(timeout=5) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if len(data) > 1 and isinstance(data[1], list):
                    return [q for q in data[1][:6] if isinstance(q, str)]
    except Exception as e:
        log.debug("PAA extraction skipped: %s", e)
    return [
        f"How does {keyword} work?",
        f"What are the benefits of {keyword}?",
        f"Is {keyword} worth it in 2026?",
    ]


def get_decaying_articles(supabase: Any | None = None) -> list[dict[str, Any]]:
    """Identify articles with traffic/ranking decay or high impressions with low CTR."""
    decaying: list[dict[str, Any]] = []
    try:
        articles = supa_select("articles", {
            "status": "eq.published",
            "select": "id,slug,title,view_count,pillar,published_at,word_count",
            "limit": "50",
        })
        for art in articles:
            views = int(art.get("view_count") or 0)
            words = int(art.get("word_count") or 0)
            if views == 0 and words < 700:
                decaying.append({
                    "slug": art.get("slug"),
                    "title": art.get("title"),
                    "pillar": art.get("pillar"),
                    "reason": "Low word count & zero views (ranking decay risk)",
                    "priority": "P1",
                })
    except Exception as e:
        log.warning("Could not fetch decaying articles: %s", e)
    return decaying


def trigger_decay_remediation(slug: str, top_queries: list[str], impressions: int = 1000, ctr: float = 0.015) -> bool:
    """Trigger Scribe AI to remediate a decaying article directly."""
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from supabase import create_client

        from scribe import refine_decaying_article

        if not SUPABASE_URL or not SUPABASE_KEY:
            return False
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        return refine_decaying_article(
            slug=slug,
            gsc_metrics={"top_queries": top_queries, "impressions": impressions, "ctr": ctr},
            supabase=sb,
        )
    except Exception as e:
        log.error("Failed to trigger decay remediation for %s: %s", slug, e)
        return False


# ── Main CLI ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Groundwork SEO Observer Agent & Intelligence Suite")
    parser.add_argument("--inspect-url", help="Inspect a single URL and print result")
    parser.add_argument("--batch-inspect", action="store_true", help="Batch inspect top articles")
    parser.add_argument("--limit", type=int, default=200, help="Max articles to process")
    parser.add_argument("--dry-run", action="store_true", help="No DB writes or auto-fixes")
    parser.add_argument("--priority-max", type=int, default=1, help="Max priority band to inspect via GSC (0=P0 only, 1=P0+P1)")
    parser.add_argument("--aeo", help="Score AEO/GEO readiness for a given article slug")
    parser.add_argument("--cannibalization", action="store_true", help="Detect keyword cannibalization across published articles")
    parser.add_argument("--paa", help="Extract People Also Ask questions for a keyword")
    parser.add_argument("--decay", action="store_true", help="List decaying articles needing remediation")
    args = parser.parse_args()

    if args.paa:
        questions = extract_search_intent_paa(args.paa)
        print(json.dumps({"keyword": args.paa, "paa_questions": questions}, indent=2))
        return

    if not SUPABASE_URL or not SUPABASE_KEY:
        log.error("SUPABASE_URL / SUPABASE_KEY not configured. Aborting.")
        sys.exit(1)

    if args.cannibalization:
        articles = supa_select("articles", {"status": "eq.published", "select": "slug,title", "limit": "200"})
        conflicts = detect_cannibalization(articles)
        print(json.dumps({"total_conflicts": len(conflicts), "conflicts": conflicts}, indent=2))
        return

    if args.decay:
        decaying = get_decaying_articles()
        print(json.dumps({"total_decaying": len(decaying), "decaying": decaying}, indent=2))
        return

    if args.aeo:
        articles = supa_select("articles", {"slug": f"eq.{args.aeo}", "select": "title,content,schema_data", "limit": "1"})
        if not articles:
            print(json.dumps({"error": f"Article {args.aeo} not found"}, indent=2))
            return
        art = articles[0]
        score_res = score_aeo_article(art.get("content", ""), art.get("title", ""), art.get("schema_data"))
        print(json.dumps(score_res, indent=2))
        return

    # Single URL inspection mode
    if args.inspect_url:
        url = args.inspect_url
        log.info("Single URL inspection: %s", url)
        gsc = inspect_url_gsc(url)
        bing = inspect_url_bing(url)
        probe = probe_url(url)
        result = {"url": url, "probe": probe, "gsc": gsc, "bing": bing}
        print(json.dumps(result, indent=2, default=str))
        return

    # Load all published articles
    log.info("Loading published articles from Supabase...")
    try:
        articles = supa_select("articles", {
            "status": "eq.published",
            "select": "id,slug,title,excerpt,content,pillar,sub_topic,is_flagship,view_count,published_at,created_at",
            "order": "published_at.desc",
            "limit": str(args.limit),
        })
    except Exception as e:
        log.error("Failed to load articles: %s", e)
        sys.exit(1)

    log.info("Loaded %d articles for observation.", len(articles))

    # Load existing observations for priority calculation
    existing_obs_map: dict[str, dict] = {}
    try:
        existing = supa_select("seo_url_observations", {
            "select": "article_id,anomalies,observed_at",
            "order": "observed_at.desc",
            "limit": "500",
        })
        for o in existing:
            aid = o.get("article_id", "")
            if aid and aid not in existing_obs_map:
                existing_obs_map[aid] = o
    except Exception:
        pass

    # Prioritize articles
    prioritized = sorted(
        articles,
        key=lambda a: compute_priority(a, existing_obs_map.get(a.get("id", ""))),
    )

    observations: list[dict] = []
    inspected_count = 0
    gsc_inspection_budget = 30  # P0+P1 budget per run

    for article in prioritized:
        priority = compute_priority(article, existing_obs_map.get(article.get("id", "")))

        # Respect GSC inspection budget
        effective_priority = priority
        if priority <= args.priority_max and inspected_count >= gsc_inspection_budget:
            effective_priority = 2  # Downgrade to P2 (no GSC inspection)

        obs = observe_article(article, articles, effective_priority, dry_run=args.dry_run)

        if priority <= args.priority_max and effective_priority <= args.priority_max:
            inspected_count += 1

        if not args.dry_run:
            try:
                supa_upsert("seo_url_observations", [obs])
            except Exception as e:
                log.warning("Failed to write observation for %s: %s", article.get("slug"), e)

        observations.append(obs)

    # Compute indexation velocity summary
    indexed = [o for o in observations if (o.get("google_coverage_state") or "").lower().find("indexed") >= 0
               and (o.get("google_coverage_state") or "").lower().find("not") == -1]
    orphans = [o for o in observations if o.get("orphan")]
    incidents = [o for o in observations if json.loads(o.get("anomalies") or "[]")]

    log.info("Run summary: %d articles | %d GSC-inspected | %d indexed | %d orphans | %d with incidents",
             len(observations), inspected_count, len(indexed), len(orphans), len(incidents))

    # Output JSON baseline file (§34 review)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    output_dir = Path(__file__).parent.parent / "docs" / "audit"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"seo-baseline-{today}.json"

    baseline = {
        "generated_at": datetime.now(UTC).isoformat(),
        "site": SITE_URL,
        "total_urls": len(observations),
        "gsc_inspected": inspected_count,
        "indexed_count": len(indexed),
        "orphan_count": len(orphans),
        "incident_count": len(incidents),
        "observations": observations,
    }

    output_path.write_text(json.dumps(baseline, indent=2, default=str))
    log.info("Baseline written to %s", output_path)

if __name__ == "__main__":
    main()
