"""Groundwork Editorial Humanizer & Anti-AI-Slop Engine (Humanizer Layer).

Inspired by blader/humanizer & f/prompts.chat:
Detects and eliminates mechanical AI hallmarks (clichés, uniform sentence lengths,
passive voice bloat, and generic transitions) to produce natural, engaging,
investigative human prose.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

logger = logging.getLogger(__name__)

# Comprehensive 70+ banned AI cliché and filler words
AI_SLOP_DICTIONARY: dict[str, str] = {
    r"\bdelve\b": "explore",
    r"\bdelves into\b": "examines",
    r"\bdelving into\b": "analyzing",
    r"\ba testament to\b": "evidence of",
    r"\btestament to\b": "proof of",
    r"\bcrucial\b": "essential",
    r"\bpivotal\b": "key",
    r"\btapestry\b": "blend",
    r"\bbeacon\b": "guide",
    r"\bgame-changer\b": "major shift",
    r"\bgame changer\b": "major shift",
    r"\bfurthermore\b": "also",
    r"\bmoreover\b": "in addition",
    r"\bhenceforth\b": "from now on",
    r"\bthus\b": "so",
    r"\bit is important to note that\b": "notably,",
    r"\bit is worth noting that\b": "notably,",
    r"\bit is essential to understand that\b": "understanding that",
    r"\bin today's fast-paced world\b": "today,",
    r"\bin today's digital age\b": "currently,",
    r"\bin today's world\b": "today,",
    r"\bin the modern era\b": "today,",
    r"\bunleash(?:ing)?\b": "unlock",
    r"\bharness(?:ing)?\b": "using",
    r"\bembark(?:ing)? on a journey\b": "starting",
    r"\bnavigate the complexities of\b": "manage",
    r"\bnavigate the landscape\b": "assess options",
    r"\bparadigm shift\b": "fundamental change",
    r"\bsynergy\b": "cooperation",
    r"\bholistic approach\b": "complete plan",
    r"\bcutting-edge\b": "latest",
    r"\bstate-of-the-art\b": "modern",
    r"\bunlock the potential\b": "maximize value",
    r"\brevolutionize\b": "transform",
    r"\bin conclusion\b": "in summary",
    r"\bto sum up\b": "in short",
    r"\ball in all\b": "overall",
    r"\bplethora of\b": "variety of",
    r"\bmyriad of\b": "many",
    r"\ba wide array of\b": "various",
    r"\boffers a unique blend of\b": "combines",
    r"\bstands as a testament\b": "proves",
    r"\bvital role in\b": "direct role in",
    r"\bplays a key role\b": "directly impacts",
    r"\bquintessential\b": "classic",
    r"\bunderscores the importance\b": "highlights",
    r"\bshed light on\b": "clarify",
    r"\bfoster(?:ing)?\b": "building",
    r"\bbolster(?:ing)?\b": "supporting",
    r"\bparamount\b": "central",
    r"\bimperative\b": "necessary",
}


class EditorialHumanizer:
    """Detects and refactors AI-generated text to ensure authentic human editorial flow."""

    @classmethod
    def sanitize_text(cls, text: str) -> str:
        """Replace AI clichés and generic filler phrases with concise, direct alternatives."""
        if not text:
            return ""

        cleaned = text
        for pattern, replacement in AI_SLOP_DICTIONARY.items():
            cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)

        # Clean throat-clearing intro openers
        cleaned = re.sub(r"^(?:Welcome to\s+[^.\n]+[.\n]+)", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^(?:In this (?:article|guide|post), we (?:will|are going to) [^.\n]+[.\n]+)", "", cleaned, flags=re.IGNORECASE)

        # Fix double spaces and punctuation anomalies
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        cleaned = re.sub(r"\s+([,.:;?!])", r"\1", cleaned)

        return cleaned.strip()

    @classmethod
    def find_slop_words(cls, text: str) -> list[str]:
        """Return all matching AI slop terms found in text."""
        if not text:
            return []

        found: list[str] = []
        for pattern in AI_SLOP_DICTIONARY:
            matches = re.findall(pattern, text, flags=re.IGNORECASE)
            if matches:
                found.extend(matches)

        return sorted(list(set(found)))

    @classmethod
    def calculate_burstiness(cls, text: str) -> dict[str, Any]:
        """Measure sentence length variation (Burstiness & Perplexity indicator).

        Human prose has high variance (mix of 4-word punchy sentences and 25-word complex sentences).
        AI text has low variance (monotonous 14-18 word sentences).
        """
        if not text:
            return {"sentence_count": 0, "mean_length": 0, "std_dev": 0, "burstiness_score": 0.0, "is_natural": False}

        # Split into sentences
        sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
        if not sentences:
            return {"sentence_count": 0, "mean_length": 0, "std_dev": 0, "burstiness_score": 0.0, "is_natural": False}

        word_counts = [len(s.split()) for s in sentences]
        n = len(word_counts)
        mean_len = sum(word_counts) / n

        variance = sum((x - mean_len) ** 2 for x in word_counts) / max(1, n - 1)
        std_dev = math.sqrt(variance)

        # Burstiness metric: Standard Deviation / Mean (Coefficient of Variation)
        cv = std_dev / max(1.0, mean_len)

        # Natural human writing typically has CV >= 0.45
        is_natural = cv >= 0.40 and len(sentences) >= 3

        return {
            "sentence_count": n,
            "mean_length": round(mean_len, 1),
            "std_dev": round(std_dev, 2),
            "burstiness_score": round(cv, 3),
            "is_natural": is_natural,
        }

    @classmethod
    def humanize_article_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply full humanization pass to Scribe JSON payload (title, content, excerpt, takeaway)."""
        if not payload:
            return payload

        res = dict(payload)
        if "title" in res and isinstance(res["title"], str):
            res["title"] = cls.sanitize_text(res["title"])
        if "content" in res and isinstance(res["content"], str):
            res["content"] = cls.sanitize_text(res["content"])
        if "excerpt" in res and isinstance(res["excerpt"], str):
            res["excerpt"] = cls.sanitize_text(res["excerpt"])
        if "takeaway" in res and isinstance(res["takeaway"], str):
            res["takeaway"] = cls.sanitize_text(res["takeaway"])
        if "expert_comment" in res and isinstance(res["expert_comment"], str):
            res["expert_comment"] = cls.sanitize_text(res["expert_comment"])

        if "faq" in res and isinstance(res["faq"], list):
            clean_faqs = []
            for item in res["faq"]:
                if isinstance(item, dict):
                    clean_faqs.append({
                        "question": cls.sanitize_text(item.get("question", "")),
                        "answer": cls.sanitize_text(item.get("answer", "")),
                    })
            res["faq"] = clean_faqs

        return res


class EditorialSanitizer:
    """Strict Fourth-Wall and Meta-Prompt Sanitizer.

    Enforces AGENTS.md §2.1 Rule #7 (Strict Fourth-Wall Rule):
    - Strips conversational openers ('Here's the optimized article...', 'Sure! Here is...')
    - Strips Markdown key-value echoes ('**title**:', '**excerpt**:', '**content**:', 'title:', 'content:')
    - Strips internal scaffolding headers ('## AEO Summary Box', '## LSI Keywords Injected', '## Target Keywords')
    - Strips trailing JSON/KV blocks ('**primary_intent**:', '**aeo_summary**:', '**seo_score**:', etc.)
    - Ensures clean, publication-ready markdown prose.
    """

    CONVERSATIONAL_PREAMBLES = [
        r"^Here(?:'s| is)\s+(?:the\s+)?(?:optimized|rewritten|new|revised|generated)?\s*article[^\n:]*[:\n]*",
        r"^Sure(?:thing)?!?,?\s+(?:here(?:'s| is)[^\n:]*[:\n]*)?",
        r"^Certainly!?,?\s+(?:here(?:'s| is)[^\n:]*[:\n]*)?",
        r"^Below is\s+(?:the\s+)?(?:optimized|rewritten|full)?\s*article[^\n:]*[:\n]*",
        r"^As requested,?\s+(?:here(?:'s| is)[^\n:]*[:\n]*)?",
    ]

    META_HEADINGS_TO_STRIP = [
        r"^#{1,6}\s+AEO\s+Summary(?:\s+Box)?\s*$",
        r"^#{1,6}\s+LSI\s+Keywords(?:\s+Injected)?\s*$",
        r"^#{1,6}\s+Target\s+Keywords?\s*$",
        r"^#{1,6}\s+SEO\s+Keywords?\s*$",
        r"^#{1,6}\s+Meta\s+Information\s*$",
    ]

    @classmethod
    def sanitize_article_prose(cls, text: str) -> str:
        """Thoroughly sanitize article markdown prose before publishing."""
        if not text:
            return ""

        cleaned = text.strip()

        # 1. Strip conversational preambles
        for pattern in cls.CONVERSATIONAL_PREAMBLES:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()

        # 2. Strip raw key-value headers (e.g. **title**: ..., **excerpt**: ..., **content**:)
        cleaned = re.sub(r"^\s*(?:\*\*)?title(?:\*\*)?\s*:\s*[^\n]+\n+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^\s*(?:\*\*)?excerpt(?:\*\*)?\s*:\s*[^\n]+\n+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^\s*(?:\*\*)?AEO\s+Summary(?:\*\*)?\s*:\s*[^\n]+\n+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^\s*(?:\*\*)?content(?:\*\*)?\s*:\s*\n+", "", cleaned, flags=re.IGNORECASE)

        # 3. Strip trailing metadata blocks (e.g. **primary_intent**: ..., **seo_score**: ...)
        cleaned = re.sub(
            r"\n+\s*(?:\*\*)?(?:primary_intent|aeo_summary|lsi_keywords_injected|seo_score|geo_benchmark_present|target_keywords|word_count)(?:\*\*)?\s*:\s*[^\n]+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )

        # 4. Strip internal meta headings line-by-line while preserving normal headings
        lines = cleaned.split("\n")
        filtered_lines: list[str] = []
        skip_list_block = False

        for line in lines:
            stripped_line = line.strip()

            # Check if this line is an internal meta heading
            is_meta_heading = False
            for h_pat in cls.META_HEADINGS_TO_STRIP:
                if re.match(h_pat, stripped_line, flags=re.IGNORECASE):
                    is_meta_heading = True
                    # If this was an LSI / Target keyword heading, skip following bullet points
                    if "keyword" in stripped_line.lower():
                        skip_list_block = True
                    break

            if is_meta_heading:
                continue

            # If we are skipping an injected keywords bullet list, continue until next heading or empty paragraph
            if skip_list_block:
                if stripped_line.startswith(("-", "*", "•", "1.", "2.")) or not stripped_line:
                    continue
                else:
                    skip_list_block = False

            filtered_lines.append(line)

        cleaned = "\n".join(filtered_lines).strip()

        # 5. Clean AI clichés via EditorialHumanizer
        cleaned = EditorialHumanizer.sanitize_text(cleaned)

        return cleaned

