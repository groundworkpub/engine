"""Groundwork Headroom Token & Context Compressor (Headroom Layer).

Inspired by headroomlabs-ai/headroom:
Compresses raw scraped DOM, tool outputs, and search intent chunks before feeding
to LLM prompts to save 60-75% token budget while preserving critical semantic facts.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Elements that contain zero core editorial value
UNWANTED_TAGS = [
    "script", "style", "nav", "header", "footer", "aside", "figure",
    "svg", "noscript", "iframe", "form", "button", "menu", "dialog",
    "canvas", "audio", "video", "track", "source", "embed", "object"
]

# Patterns for ad widgets, social sharing, cookie banners, tracking
BOILERPLATE_PATTERNS = [
    r"(?i)(cookie|privacy|consent|terms of service|all rights reserved|subscribe to our newsletter|sign up for free|advertisement|sponsored|follow us on|share this article|related posts|comments section)",
    r"(?i)(read more at|click here to read|copyright \d{4}|designed by|powered by|hosted on)",
]


class HeadroomCompressor:
    """In-process semantic token compressor for web scraping and tool outputs."""

    @staticmethod
    def compress_html(raw_html: str, target_chars: int = 4000) -> str:
        """Strip boilerplate DOM elements and compress text down to dense content."""
        if not raw_html or not raw_html.strip():
            return ""

        soup = BeautifulSoup(raw_html, "html.parser")

        # 1. Remove all junk tags
        for tag in soup(UNWANTED_TAGS):
            tag.decompose()

        # 2. Extract structured article body or fallback to body text
        main_content = soup.find("article") or soup.find("main") or soup.find(id=re.compile(r"content|article|main", re.I)) or soup.body or soup

        # 3. Clean inline styling and attributes
        for element in main_content.find_all(True):
            element.attrs = {k: v for k, v in element.attrs.items() if k in ["href", "src", "alt", "title"]}

        # 4. Extract paragraphs, headings, and lists
        dense_blocks: list[str] = []
        for el in main_content.find_all(["h1", "h2", "h3", "h4", "p", "li", "table"]):
            text = el.get_text(separator=" ", strip=True)
            if not text:
                continue

            # Skip boilerplate / disclaimer sentences
            if any(re.search(pat, text) for pat in BOILERPLATE_PATTERNS):
                continue

            # Format headings
            if el.name in ["h1", "h2", "h3", "h4"]:
                dense_blocks.append(f"\n## {text}\n")
            elif el.name == "li":
                dense_blocks.append(f"- {text}")
            else:
                dense_blocks.append(text)

        # Fallback if no structured tags found
        if not dense_blocks:
            raw_text = main_content.get_text(separator="\n", strip=True)
            dense_blocks = [line.strip() for line in raw_text.splitlines() if line.strip()]

        cleaned = "\n".join(dense_blocks)

        # 5. Normalize whitespace and repetitive newlines
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

        # 6. Bounded truncation at clean sentence boundary
        if len(cleaned) > target_chars:
            cutoff = cleaned[:target_chars]
            last_period = max(cutoff.rfind(". "), cutoff.rfind(".\n"), cutoff.rfind("? "), cutoff.rfind("! "))
            cleaned = cutoff[:last_period + 1] if last_period > target_chars * 0.7 else cutoff.rsplit("\n", 1)[0]

        return cleaned

    @staticmethod
    def compress_snippets(snippets: list[dict[str, Any] | str], max_chars: int = 2500) -> str:
        """Compress list of search results or RAG context chunks into high-density reference."""
        if not snippets:
            return ""

        dense_items: list[str] = []
        for i, item in enumerate(snippets, 1):
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                title = item.get("title", "").strip()
                body = item.get("body") or item.get("content") or item.get("snippet") or ""
                url = item.get("url") or item.get("link") or ""
                text = f"[{title}] {body}" if title else str(body)
                if url:
                    text += f" (Source: {url})"
            else:
                text = str(item)

            # Strip repetitive noise
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                dense_items.append(f"[{i}] {text}")

        merged = "\n".join(dense_items)
        if len(merged) > max_chars:
            merged = merged[:max_chars].rsplit(" ", 1)[0] + "..."

        return merged

    @staticmethod
    def estimate_token_count(text: str) -> int:
        """Rule of thumb token estimate: ~4 chars per token for English."""
        if not text:
            return 0
        return max(1, len(text) // 4)

    @classmethod
    def compression_stats(cls, original: str, compressed: str) -> dict[str, Any]:
        """Compute token compression metrics."""
        orig_chars = len(original or "")
        comp_chars = len(compressed or "")
        orig_tokens = cls.estimate_token_count(original)
        comp_tokens = cls.estimate_token_count(compressed)
        ratio = (1.0 - (comp_chars / max(1, orig_chars))) * 100.0 if orig_chars > 0 else 0.0

        return {
            "original_chars": orig_chars,
            "compressed_chars": comp_chars,
            "original_tokens": orig_tokens,
            "compressed_tokens": comp_tokens,
            "tokens_saved": max(0, orig_tokens - comp_tokens),
            "compression_ratio_pct": round(max(0.0, ratio), 2),
        }
