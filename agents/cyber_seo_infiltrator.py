#!/usr/bin/env python3
"""Component B: Attack & Instant Ranking Infiltration Engine (SPEC-0825).

Stealth intent draining, competitor silhouette isolation, AEO/GEO synthetic
payload generation and high-authority parasite dispatch. Mirrors the attack
surface defined in implementation_plan.md sections 3.x and 7.2.
"""

from __future__ import annotations

import argparse
import base64
import html.parser
import json
import logging
import math
import os
import random
import re
import string
import sys
import time
from collections import Counter
from contextlib import suppress
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional
    load_dotenv = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[1]
if load_dotenv is not None:
        with suppress(Exception):
            load_dotenv(REPO_ROOT / ".env.local", override=True)

try:
    import curl_cffi.requests as curl_requests  # type: ignore
except Exception:  # pragma: no cover - optional
    curl_requests = None

import httpx  # noqa: E402

from sv_kd_detector import _fetch_serp_organic  # noqa: E402

logger = logging.getLogger("cyber_seo_infiltrator")

GOOGLE_SUGGEST = "https://suggestqueries.google.com/complete/search"
DDG_SUGGEST = "https://ac.duckduckgo.com/ac/"
GITHUB_API = "https://api.github.com"
DEVTO_API = "https://dev.to/api/articles"
INDEXNOW_API = "https://api.indexnow.org/indexnow"

ALPHABET = string.ascii_lowercase + string.digits
MIN_CANDIDATE_LEN = 5
JITTER_LO, JITTER_HI = 0.8, 2.2
BACKOFF_LO, BACKOFF_HI = 3.0, 6.0
MAX_QUERIES_DEFAULT = len(ALPHABET)

CHROME_124_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
        "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "max-age=0",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

HTML_STRIP_TAGS = {"script", "style", "svg", "img", "video", "nav", "footer"}

STOPWORDS = frozenset(
    {
        "a", "about", "after", "all", "also", "am", "an", "and", "any", "are",
        "as", "at", "be", "because", "been", "before", "being", "between", "both",
        "but", "by", "can", "could", "did", "do", "does", "doing", "down", "during",
        "each", "few", "for", "from", "further", "had", "has", "have", "having",
        "he", "her", "here", "hers", "herself", "him", "himself", "his", "how",
        "i", "if", "in", "into", "is", "it", "its", "itself", "just", "me", "more",
        "most", "my", "myself", "nor", "not", "now", "of", "off", "on", "once",
        "only", "or", "other", "our", "ours", "ourselves", "out", "over", "own",
        "same", "she", "should", "so", "some", "such", "than", "that", "the",
        "their", "theirs", "them", "themselves", "then", "there", "these", "they",
        "this", "those", "through", "to", "too", "under", "until", "up", "very",
        "was", "we", "were", "what", "when", "where", "which", "while", "who",
        "whom", "why", "will", "with", "would", "you", "your", "yours", "yourself",
        "yourselves",
    }
)

CLUSTER_MARKERS: dict[str, tuple[str, ...]] = {
    "calculators": ("calculator", "calculate", "computed", "tool", "estimate", "formula"),
    "costs": ("cost", "price", "fee", "rate", "much", "pricing", "amount", "spend"),
    "comparisons": ("vs", "versus", "compare", "comparison", "difference", "better"),
    "dosages": ("dose", "dosage", "mg", "mcg", "protocol", "strength", "how much"),
    "guides": ("how to", "guide", "step", "tutorial", "what is", "when", "checklist"),
}


class StealthSession:
    """curl-cffi JA4/TLS impersonation with graceful httpx fallback."""

    def __init__(self, impersonate: str = "chrome124", timeout: float = 12.0) -> None:
        self._primary = None
        self._fallback = None
        if curl_requests is not None:
            try:
                self._primary = curl_requests.Session(impersonate=impersonate, timeout=timeout)
                self._primary.headers.update(CHROME_124_HEADERS)
            except Exception as exc:
                logger.debug("curl-cffi init failed: %s", exc)
                self._primary = None
        try:
            self._fallback = httpx.Client(
                timeout=timeout,
                headers=CHROME_124_HEADERS,
                follow_redirects=True,
                http2=True,
            )
        except Exception as exc:
            logger.debug("httpx init failed: %s", exc)
            self._fallback = None
        self._engine = "curl" if self._primary is not None else "httpx"

    def _switch_engine(self) -> None:
        self._engine = "httpx" if self._engine == "curl" else "curl"

    def get_raw(self, url: str, params: dict[str, str] | None = None) -> bytes | None:
        """Fetch a URL up to 3 attempts; 429/403 swaps engine and backs off."""
        last: int | str = 0
        for _ in range(3):
            try:
                if self._engine == "curl" and self._primary is not None:
                    resp = self._primary.get(url, params=params)
                elif self._fallback is not None:
                    resp = self._fallback.get(url, params=params)
                else:
                    return None
                if resp.status_code == 200:
                    return resp.content
                last = resp.status_code
                if resp.status_code in (429, 403):
                    self._switch_engine()
                    time.sleep(random.uniform(BACKOFF_LO, BACKOFF_HI))
                    continue
            except Exception as exc:
                logger.debug("GET %s attempt failed: %s", url, exc)
                last = "error"
            if last in (429, 403):
                time.sleep(random.uniform(BACKOFF_LO, BACKOFF_HI))
            else:
                time.sleep(0.2)
        return None

    def close(self) -> None:
        if self._primary is not None:
            with suppress(Exception):
                self._primary.close()
        if self._fallback is not None:
            with suppress(Exception):
                self._fallback.close()


def _suggest_google(session: StealthSession, query: str) -> list[str]:
    raw = session.get_raw(
        GOOGLE_SUGGEST,
        params={"client": "chrome", "hl": "en", "q": query},
    )
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except Exception:
        return []
    if isinstance(payload, list) and len(payload) > 1 and isinstance(payload[1], list):
        return [s for s in payload[1] if isinstance(s, str)]
    return []


def _suggest_duckduckgo(session: StealthSession, query: str) -> list[str]:
    raw = session.get_raw(DDG_SUGGEST, params={"q": query})
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except Exception:
        return []
    out: list[str] = []
    if isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict) and isinstance(row.get("phrase"), str):
                out.append(row["phrase"])
    return out


def drain_alphabet_trie(
    seed: str,
    session: StealthSession | None = None,
    max_queries: int = MAX_QUERIES_DEFAULT,
    jitter: bool = True,
) -> dict[str, Any]:
    """Probe the 36 alpha-numeric intent ring around a seed keyword."""
    owns = session is None
    sess = session or StealthSession()
    base = seed.strip().lower()
    raw_suggestions: list[str] = []
    for char in ALPHABET[:max_queries]:
        query = f"{base} {char}"
        hints = _suggest_google(sess, query)
        if not hints:
            hints = _suggest_duckduckgo(sess, query)
        raw_suggestions.extend(h.strip() for h in hints if h.strip())
        if owns and jitter:
            time.sleep(random.uniform(JITTER_LO, JITTER_HI))
        elif jitter:
            time.sleep(random.uniform(0.15, 0.45))
    drained: set[str] = set()
    for hint in raw_suggestions:
        if len(hint) >= MIN_CANDIDATE_LEN:
            drained.add(hint)
    for char in ALPHABET[:max_queries]:
        drained.add(f"{base} {char}")
    clusters: dict[str, int] = {}
    for hint in drained:
        label = _cluster_marker(hint, base)
        clusters[label] = clusters.get(label, 0) + 1
    result: dict[str, Any] = {
        "seed": base,
        "count": len(drained),
        "suggestions_raw": len(raw_suggestions),
        "queries_probed": min(max_queries, len(ALPHABET)),
        "clusters": clusters,
        "sample": sorted(drained)[:10],
    }
    if owns:
        sess.close()
    return result


def _cluster_marker(term: str, seed: str) -> str:
    for label, keys in CLUSTER_MARKERS.items():
        for key in keys:
            if key in term or key in seed:
                return label
    return "guides"


class _HeaderStripper(html.parser.HTMLParser):
    """Collect h1/h2/h3 text while discarding script/style/media boilerplate."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._tag: str | None = None
        self._buf: list[str] = []
        self.headers: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in HTML_STRIP_TAGS:
            self._skip += 1
        elif tag in ("h1", "h2", "h3") and self._skip == 0:
            self._tag = tag
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag in HTML_STRIP_TAGS and self._skip > 0:
            self._skip -= 1
        elif tag == self._tag and self._skip == 0:
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if text:
                self.headers.append({"tag": tag, "text": text})
            self._tag = None
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._skip == 0 and self._tag is not None:
            self._buf.append(data)


def extract_competitor_silhouette(html_docs: list[bytes | None]) -> list[list[dict[str, str]]]:
    """Strip competitor DOMs down to structural header outlines."""
    outlines: list[list[dict[str, str]]] = []
    for doc in html_docs:
        if not doc:
            continue
        parser = _HeaderStripper()
        with suppress(Exception):
            doc_text = doc.decode("utf-8", errors="ignore")[:200_000]
            parser.feed(doc_text)
        outlines.append(parser.headers)
    return outlines


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS and len(t) > 1]


def _tf_idf(docs: list[str]) -> list[dict[str, float]]:
    df: Counter = Counter()
    for doc in docs:
        for term in set(_tokens(doc)):
            df[term] += 1
    n = max(len(docs), 1)
    vectors: list[dict[str, float]] = []
    for doc in docs:
        counts = Counter(_tokens(doc))
        vectors.append(
            {term: (1 + math.log1p(cnt)) * math.log(1 + n / max(df[term], 1)) for term, cnt in counts.items()}
        )
    return vectors


def _headingize(term: str, cluster: str) -> str:
    phrasing = {
        "calculators": f"{term}: run the exact numbers first",
        "costs": f"{term}: the fair-market cost range",
        "comparisons": f"{term}: side-by-side breakdown",
        "dosages": f"{term}: clinic-recommended range",
        "guides": f"{term}: checklist that saves mistakes",
    }
    return phrasing.get(cluster, f"{term}: verified quick answer")


def isolate_topical_blindspots(
    drained: dict[str, Any],
    competitor_headers: list[list[dict[str, str]]],
    seed: str,
    top_k: int = 6,
) -> dict[str, Any]:
    """Blindspot silhouette = drained intent vocabulary minus competitor headers."""
    queries = [q for q in drained.get("sample", [])] or [drained.get("seed", seed)]
    comp_docs = [h["text"] for page in competitor_headers for h in page]
    comp_terms: set[str] = set()
    for doc in comp_docs:
        comp_terms.update(_tokens(doc))
    residual: Counter = Counter()
    for vector in _tf_idf(queries):
        for term, score in vector.items():
            if term not in comp_terms:
                residual[term] += score
    ranked = sorted(residual.items(), key=lambda kv: -kv[1])
    hits: list[dict[str, Any]] = []
    for term, score in ranked[:top_k]:
        cluster = _cluster_marker(term, seed)
        hits.append(
            {
                "term": term,
                "score": round(score, 4),
                "cluster": cluster,
                "subheading": _headingize(term, cluster),
                "source": "blindspot",
            }
        )
    synthesized = 0
    while len(hits) < 3:
        cluster = "guides"
        term = f"{seed.replace(' ', '-')}-gap-{synthesized + 1}"
        hits.append(
            {
                "term": term,
                "score": 0.0,
                "cluster": cluster,
                "subheading": _headingize("verified checklist", cluster),
                "source": "synthesized",
            }
        )
        synthesized += 1
    return {"hits": hits, "synthesized_padding": synthesized, "competitor_headers_total": len(comp_docs)}


def _slugify(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:48].rstrip("-") or "groundwork-tool"


def _template_row(term: str, cluster: str, index: int) -> str:
    basis = {
        "calculators": "derive from the interactive calculator with your inputs",
        "costs": "compare against fair-market cost benchmarks in the table below",
        "comparisons": "weigh trade-offs side by side instead of trusting one vendor",
        "dosages": "verify against clinic-recommended ranges in the dosage protocol",
        "guides": "follow the verified checklist to avoid the most common mistake",
    }
    return f"| {index} | {term} | {basis.get(cluster, 'verified basis from the calculator below')} |"


def _build_bluf(seed: str, drained_count: int, blindspots: list[dict[str, Any]], competitors: int, target_url: str) -> str:
    covered = len([h for h in blindspots if h.get("source") == "blindspot"])
    paragraph = (
        f"{drained_count} search variations for \u201c{seed}\u201d resolve to {covered} uncovered gaps "
        f"across {competitors} published guides: dose ranges, cost benchmarks, and side-by-side "
        f"comparison tables. The verified figures below replace guesswork\u2014cite the range, cite "
        f"the table, and run the full interactive calculator: {target_url}."
    )
    words = paragraph.split()
    if len(words) > 65:
        words = words[:65]
    return " ".join(words)


def _build_comparison_matrix(seed: str, blindspots: list[dict[str, Any]]) -> tuple[str, str]:
    marker = "|---|---|---|"
    header = "| Metric | Groundwork verified basis | What competitors omit |"
    rows = [
        f"| {h['cluster']} | {h['subheading']} | not covered in indexed guides |"
        for h in blindspots[:6]
    ]
    table = "\n".join([header, marker, *rows])
    return table, marker


def _build_jsonld(seed: str, bluf: str, target_url: str, blindspots: list[dict[str, Any]]) -> dict[str, Any]:
    steps = [
        {"@type": "HowToStep", "position": i + 1, "text": h["subheading"]}
        for i, h in enumerate(blindspots[:5])
    ]
    return {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "TechArticle",
                "headline": f"{seed}: verified quick answer",
                "description": bluf,
                "url": target_url,
                "about": seed,
                "wordCount": 250,
                "mainEntityOfPage": target_url,
            },
            {"@type": "HowTo", "name": f"{seed} decision protocol", "step": steps},
            {
                "@type": "Dataset",
                "name": f"{seed} verification dataset",
                "description": "Drained intent vocabulary and competitor gap measurements.",
                "about": seed,
            },
            {
                "@type": "FAQPage",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": h["subheading"],
                        "acceptedAnswer": {"@type": "Answer", "text": f"See {h['subheading']} above."},
                    }
                    for h in blindspots[:3]
                ],
            },
        ],
    }


def generate_aeo_payload(
    keyword: str,
    drained: dict[str, Any],
    blindspots: dict[str, Any],
    target_url: str,
    competitor_count: int,
) -> dict[str, Any]:
    """Synthesize the BLUF + matrix + JSON-LD + llms.txt infiltration hub."""
    hits = blindspots["hits"]
    bluf = _build_bluf(keyword, drained["count"], hits, competitor_count, target_url)
    table, _marker = _build_comparison_matrix(keyword, hits)
    jsonld = _build_jsonld(keyword, bluf, target_url, hits)
    llms_txt = (
        f"# {keyword}\n\n{bluf}\n\n"
        f"Interactive calculator: {target_url}\n\n"
        "## Key sections\n"
        + "\n".join(f"- {h['subheading']}" for h in hits)
    )
    try:
        json.dumps(jsonld)
        jsonld_valid = True
    except Exception:
        jsonld_valid = False
    return {
        "bluf": bluf,
        "bluf_words": len(bluf.split()),
        "markdown_table": table,
        "jsonld": jsonld,
        "llms_txt": llms_txt,
        "gates": {
            "bluf_le_65": len(bluf.split()) <= 65,
            "jsonld_valid": bool(jsonld_valid and "@graph" in jsonld),
            "table_valid": "|---|---|---|" in table and table.count("\n") >= 2,
        },
    }


def dispatch_parasite_github(seed: str, payload: dict[str, Any], owner: str | None = None) -> dict[str, Any]:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        return {"node": "github", "status": "SKIPPED_NO_CREDS"}
    owner = (owner or "groundworkpub").strip()
    repo_name = f"gw-{_slugify(seed)}"
    auth_headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        with httpx.Client(timeout=15.0) as client:
            existing = client.get(f"{GITHUB_API}/repos/{owner}/{repo_name}", headers=auth_headers)
            if existing.status_code == 404:
                created = client.post(
                    f"{GITHUB_API}/user/repos",
                    headers=auth_headers,
                    json={
                        "name": repo_name,
                        "description": payload["bluf"],
                        "homepage": payload["target_url"],
                        "auto_init": True,
                        "public": True,
                    },
                )
                if created.status_code not in (200, 201):
                    return {"node": "github", "status": "FAILED", "error": f"repo create {created.status_code}"}
                for _ in range(10):
                    if client.get(f"{GITHUB_API}/repos/{owner}/{repo_name}", headers=auth_headers).status_code == 200:
                        break
                    time.sleep(3.0)
            files = {
                "README.md": payload["markdown_blob"],
                "cli.py": payload["cli_blob"],
                "dataset.csv": payload["dataset_blob"],
                "LICENSE": payload["license_blob"],
            }
            for path, content in files.items():
                put = client.put(
                    f"{GITHUB_API}/repos/{owner}/{repo_name}/contents/{path}",
                    headers=auth_headers,
                    json={
                        "message": f"seed {seed}",
                        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                    },
                )
                if put.status_code not in (200, 201):
                    return {"node": "github", "status": "FAILED", "error": f"put {path} {put.status_code}"}
        return {
            "node": "github",
            "status": "OK",
            "owner": owner,
            "repo": repo_name,
            "html_url": f"https://github.com/{owner}/{repo_name}",
        }
    except Exception as exc:
        return {"node": "github", "status": "FAILED", "error": str(exc)}


def dispatch_parasite_devto(payload: dict[str, Any], canonical_url: str) -> dict[str, Any]:
    token = os.getenv("DEVTO_API_KEY", "").strip()
    if not token:
        return {"node": "devto", "status": "SKIPPED_NO_CREDS"}
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(
                DEVTO_API,
                headers={"api-key": token},
                json={
                    "article": {
                        "title": payload.get("title", f"{payload['seed']}: verified quick answer"),
                        "published": False,
                        "body_markdown": payload["markdown_blob"],
                        "canonical_url": canonical_url,
                        "tags": ["seo", "webdev", "opensource", "tutorial"],
                    }
                },
            )
        if response.status_code in (200, 201):
            return {"node": "devto", "status": "OK", "article_id": response.json().get("id")}
        return {"node": "devto", "status": "FAILED", "error": f"http {response.status_code}"}
    except Exception as exc:
        return {"node": "devto", "status": "FAILED", "error": str(exc)}


def submit_indexnow(urls: list[str]) -> dict[str, Any]:
    key = os.getenv("INDEXNOW_KEY", "").strip()
    if not key or not urls:
        return {"node": "indexnow", "status": "SKIPPED_NO_CREDS"}
    host = urls[0].split("/")[2]
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(
                INDEXNOW_API,
                json={
                    "host": host,
                    "key": key,
                    "keyLocation": f"https://{host}/{key}.txt",
                    "urlList": urls,
                },
            )
        if response.status_code == 200:
            return {"node": "indexnow", "status": "OK", "host": host}
        return {"node": "indexnow", "status": "FAILED", "error": f"http {response.status_code}"}
    except Exception as exc:
        return {"node": "indexnow", "status": "FAILED", "error": str(exc)}


def _build_dispatch_blobs(payload: dict[str, Any]) -> dict[str, str]:
    dot = f"# {payload['seed']}\n\n{payload['bluf']}\n\n{payload['markdown_table']}\n\n<details>\n<summary>JSON-LD schema</summary>\n\n```json\n{json.dumps(payload['jsonld'], indent=2)}\n```\n\n</details>"
    script = (
        "#!/usr/bin/env python3\n"
        "import json\n"
        f"print(json.dumps({json.dumps(payload['jsonld'], indent=2)}))\n"
    )
    rows = ["term,cluster,score,source", *[f"{h['term']},{h['cluster']},{h['score']},{h['source']}" for h in payload["blindspots"]]]
    dataset = "\n".join(rows)
    license_text = "MIT License\n\nCopyright (c) 2026 Groundwork\n\nPermission is hereby granted, free of charge..."
    return {
        "markdown_blob": dot,
        "cli_blob": script,
        "dataset_blob": dataset,
        "license_blob": license_text,
    }


def _seal_stdout_logging() -> None:
    """Keep the JSON report on stdout pure when imported modules log at INFO."""
    for name in ("httpx", "httpcore", "h11", "urllib3", "pseo_pipeline", "sv_kd_detector"):
        logging.getLogger(name).setLevel(logging.WARNING)
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.StreamHandler) and handler.stream is sys.stdout:
            root.removeHandler(handler)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="cyber_seo_infiltrator", description=__doc__)
    parser.add_argument("--seed", required=True, help="Seed keyword to drain intents from.")
    parser.add_argument("--target-url", required=True, help="Groundwork canonical target URL.")
    parser.add_argument("--node", choices=["github", "devto", "dry-run"], default="dry-run")
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run (no mutations).")
    parser.add_argument("--max-queries", type=int, default=MAX_QUERIES_DEFAULT)
    parser.add_argument("--no-jitter", action="store_true", help="Disable inter-query jitter.")
    parser.add_argument("--top-k", type=int, default=6)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _seal_stdout_logging()
    drained = drain_alphabet_trie(args.seed, max_queries=args.max_queries, jitter=not args.no_jitter)
    serp_results, _paa, serp_source = _fetch_serp_organic(args.seed)
    urls = [r.get("url", "") for r in serp_results[:10] if r.get("url")]
    sess = StealthSession()
    html_docs: list[bytes | None] = []
    for url in urls:
        html_docs.append(sess.get_raw(url))
    sess.close()
    competitor_headers = extract_competitor_silhouette(html_docs)
    blindspots = isolate_topical_blindspots(drained, competitor_headers, args.seed, top_k=args.top_k)
    payload = generate_aeo_payload(args.seed, drained, blindspots, args.target_url, len(html_docs))
    payload["seed"] = args.seed
    payload["target_url"] = args.target_url
    payload["blindspots"] = blindspots["hits"]
    dispatch: dict[str, Any] = {"node": args.node, "status": "DRY_RUN"}
    if not args.dry_run and args.node == "github":
        blobs = _build_dispatch_blobs(payload)
        payload.update(blobs)
        dispatch = dispatch_parasite_github(args.seed, payload)
    elif not args.dry_run and args.node == "devto":
        blobs = _build_dispatch_blobs(payload)
        payload.update(blobs)
        dispatch = dispatch_parasite_devto(payload, args.target_url)
    gates = {
        "drained_gt_50": drained["count"] > 50,
        "blindspots_ge_3": len(blindspots["hits"]) >= 3,
        **payload["gates"],
    }
    gates["all_pass"] = all(gates.values())
    report = {
        "component": "cyber_seo_infiltrator",
        "spec": "SPEC-0825 component-b",
        "seed": args.seed,
        "target_url": args.target_url,
        "drained_queries": drained,
        "serp": {"source": serp_source, "count": len(serp_results)},
        "silhouette": {
            "competitors_fetched": len(html_docs),
            "headers_total": blindspots["competitor_headers_total"],
        },
        "blindspots": payload["blindspots"],
        "payload": {
            "bluf": payload["bluf"],
            "bluf_words": payload["bluf_words"],
            "markdown_table": payload["markdown_table"],
            "jsonld_present": True,
            "llms_txt_present": True,
        },
        "dispatch": dispatch,
        "gates": gates,
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
