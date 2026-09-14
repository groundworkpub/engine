"""
agents/authoritative_ingestor.py
Groundwork Platform — Primary Institutional Ingestion & RAG Context Engine
SSOT: implementation_plan.md — Authoritative Retrieval-Augmented Generation (AAGC)

Provides:
- retrieve_knowledge_context(): Hybrid retrieval querying public.gworky_knowledge_nodes
- format_evidence_for_prompt(): Formats raw tables and equations into Scribe Compiler Context
- fetch_primary_institutional_markdown(): Live crawl fallback for authorized domains
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

logger = logging.getLogger("authoritative_ingestor")


def _load_env_local() -> None:
    """Auto-loads .env.local from project root if present (Rule §2.14)."""
    env_path = ROOT_DIR / ".env.local"
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'").strip('"')
                if k and k not in os.environ:
                    os.environ[k] = v


_load_env_local()

SUPABASE_URL = os.environ.get("NEXT_PUBLIC_SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY", "")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

# Approved institutional domains for live crawl fallback
AUTHORIZED_DOMAINS = (
    "bls.gov",
    "onetonline.org",
    "fred.stlouisfed.org",
    "consumerfinance.gov",
    "fda.gov",
    "cdc.gov",
    "ashrae.org",
    "nrel.gov",
    "nist.gov",
    "sec.gov",
)


def retrieve_knowledge_context(
    pillar: str,
    topic: str,
    client: httpx.Client | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Retrieves the most relevant knowledge nodes from Supabase public.gworky_knowledge_nodes.

    Uses a hybrid approach:
    1. Filter by pillar category.
    2. Rank by text match against title and topic_slug.
    3. Return structured payload including tables_payload and equations_payload.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.warning("Supabase credentials not configured for knowledge node retrieval.")
        return []

    should_close = False
    if client is None:
        client = httpx.Client(timeout=10.0)
        should_close = True

    try:
        # Fetch knowledge nodes for the requested pillar
        url = (
            f"{SUPABASE_URL}/rest/v1/gworky_knowledge_nodes"
            f"?pillar=eq.{pillar}"
            f"&select=id,pillar,source_name,source_url,topic_slug,title,content_markdown,tables_payload,equations_payload"
            f"&limit=10"
        )
        resp = client.get(url, headers=HEADERS)
        if resp.status_code != 200:
            logger.warning(f"Failed to query knowledge nodes: {resp.status_code}")
            return []

        nodes = resp.json() or []
        if not nodes:
            # Fallback to any nodes if pillar is empty
            fallback_url = (
                f"{SUPABASE_URL}/rest/v1/gworky_knowledge_nodes"
                f"?select=id,pillar,source_name,source_url,topic_slug,title,content_markdown,tables_payload,equations_payload"
                f"&limit=5"
            )
            fb_resp = client.get(fallback_url, headers=HEADERS)
            nodes = fb_resp.json() or []

        # Simple semantic scoring against topic words
        topic_words = set(re.findall(r"\w+", topic.lower()))

        def score_node(n: dict[str, Any]) -> int:
            text = f"{n.get('title', '')} {n.get('topic_slug', '')} {n.get('content_markdown', '')}".lower()
            return sum(1 for w in topic_words if len(w) > 3 and w in text)

        scored = sorted(nodes, key=score_node, reverse=True)
        return scored[:limit]
    except Exception as e:
        logger.warning(f"Knowledge node retrieval exception: {e}")
        return []
    finally:
        if should_close:
            client.close()


def format_evidence_for_prompt(nodes: list[dict[str, Any]]) -> str:
    """Formats retrieved knowledge nodes into an explicit Compiler Contract prompt block."""
    if not nodes:
        return ""

    blocks: list[str] = [
        "==================================================================",
        "[GROUNDED INSTITUTIONAL EVIDENCE — MANDATORY COMPILER CONTRACT]",
        "The following empirical datasets and formulas represent institutional ground truth.",
        "1. You MUST construct your opening 40–80 word bolded BLUF direct answer using numbers from this evidence.",
        "2. You MUST construct at least one Markdown comparison table directly deriving from the tables below.",
        "3. You MUST cite the source institution by name (e.g., BLS, FRED, FDA, NIST).",
        "4. NEVER invent hypothetical metrics or synthesize self-attributions like 'According to Groundwork research'.",
        "==================================================================",
    ]

    for idx, node in enumerate(nodes, 1):
        source = node.get("source_name", "Institutional Standard")
        title = node.get("title", "")
        summary = node.get("content_markdown", "")
        tables = node.get("tables_payload") or []
        equations = node.get("equations_payload") or []

        blocks.append(f"\n### Evidence Node #{idx}: [{source}] {title}")
        blocks.append(f"Summary: {summary}")

        if tables:
            blocks.append("\nCanonical Structured Tables:")
            for t in tables:
                t_title = t.get("table_title", "Data Table")
                cols = t.get("columns", [])
                rows = t.get("rows", [])
                if cols and rows:
                    blocks.append(f"**{t_title}**")
                    blocks.append("| " + " | ".join(cols) + " |")
                    blocks.append("| " + " | ".join([":---"] * len(cols)) + " |")
                    for row in rows:
                        blocks.append("| " + " | ".join(str(c) for c in row) + " |")

        if equations:
            blocks.append("\nEmpirical Mathematical Formulas:")
            for eq in equations:
                eq_name = eq.get("formula_name", "Formula")
                formula = eq.get("equation", "")
                desc = eq.get("description", "")
                blocks.append(f"- **{eq_name}**: `{formula}` — {desc}")

    blocks.append("==================================================================\n")
    return "\n".join(blocks)


def fetch_primary_institutional_markdown(url: str) -> str | None:
    """Safely fetches and parses raw text/markdown from authorized primary institutional domains."""
    if not any(domain in url.lower() for domain in AUTHORIZED_DOMAINS):
        logger.warning(f"URL {url} is not in authorized institutional domains list.")
        return None

    try:
        from bs4 import BeautifulSoup

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
        }
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code != 200:
                logger.warning(f"Institutional fetch returned HTTP {resp.status_code} for {url}")
                return None

            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside", "svg"]):
                tag.decompose()

            # Find main content container
            main_el = soup.find("main") or soup.find("article") or soup.find("body")
            if not main_el:
                return None

            text = main_el.get_text(separator="\n", strip=True)
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            cleaned = "\n".join(lines)
            return cleaned[:8000]
    except Exception as e:
        logger.warning(f"Error fetching institutional URL {url}: {e}")
        return None
