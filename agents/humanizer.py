"""Groundwork Editorial Humanizer & Anti-AI-Slop Engine (Humanizer Layer).

Inspired by blader/humanizer & f/prompts.chat, extended with the
ultimate-humanizer corpus (50-pattern taxonomy, 2-pass self-audit,
5-Dimension scoring):
Detects and eliminates mechanical AI hallmarks (clichés, uniform sentence lengths,
passive voice bloat, generic transitions, and new-era LLM structural tells) to
produce natural, engaging, investigative human prose.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Quality gate: payloads scoring below this trigger a Scribe refinement pass.
HUMAN_SCORE_THRESHOLD = 70.0

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
    # New-era lexical tells (ultimate-humanizer corpus)
    r"\bleverag(?:e|ing)\b": "use",
    r"\butiliz(?:e|ing)\b": "use",
    r"\bfacilitat(?:e|ing)\b": "help",
    r"\bunpack(?:ing)?\b": "examine",
    r"\bunravel(?:ing)?\b": "untangle",
    r"\bdemystif(?:y|ying)\b": "explain",
    r"\bseamless(?:ly)?\b": "smoothly",
    r"\brobust\b": "reliable",
    r"\belevat(?:e|ing)\b": "raise",
    r"\bsupercharg(?:e|ing)\b": "boost",
    r"\bturbocharg(?:e|ing)\b": "boost",
    r"\bnext-level\b": "better",
    r"\beffortless(?:ly)?\b": "easily",
    r"\bin the realm of\b": "in",
    r"\bnavigat(?:e|ing) the\b": "handle the",
    r"\bwhen it comes to\b": "for",
    r"\bat the end of the day\b": "ultimately",
    r"\blet's (?:be honest|face it)\b": "realistically,",
    r"\bthe truth is\b": "",
    r"\bhere's the thing\b": "",
    r"\blook no further\b": "",
    r"\bdive(?:\s+in| deep)?\b": "start",
    r"\btreasure trove\b": "rich source",
    r"\bbustling\b": "busy",
    r"\bvibrant\b": "lively",
    r"\bever-evolving\b": "changing",
    r"\bever-changing\b": "changing",
    r"\brich tapestry\b": "mix",
    r"\bhidden gem\b": "lesser-known option",
    r"\bwhether you(?:'re| are) a\b": "if you are a",
    r"\bmultifaceted\b": "complex",
    r"\bnuanced understanding\b": "detailed view",
    r"\binterplay between\b": "relationship between",
    r"\bmeticulous(?:ly)?\b": "careful",
    r"\bcomprehensive guide\b": "guide",
    r"\bin-depth analysis\b": "analysis",
    r"\bserves as a\b": "is a",
    r"\bacts as a\b": "is a",
    r"\bmagic(?:al)?\b(?!\skingdom)": "effective",
    r"\btransformative\b": "major",
    r"\bembark\b": "start",
    r"\bjourney\b": "process",
}

# New-era structural detectors (P45–P50, June-2026 spam-classifier era).
# These are counted, not auto-replaced — they feed the 5-D score.
_NEW_ERA_PATTERNS: dict[str, re.Pattern[str]] = {
    # P45 — em-dash density anomaly
    "P45_em_dash": re.compile(r"—|--"),
    # P46 — negation-contrast frames ("not just X but Y", "not only... but also")
    "P46_negation_contrast": re.compile(
        r"\bnot just\b[^.\n]{3,60}\bbut\b|\bnot only\b[^.\n]{5,80}\bbut also\b", re.IGNORECASE
    ),
    # P47 — rule-of-three triads
    "P47_triad": re.compile(r"\b[\w-]+,\s+[\w-]+,\s+and\s+[\w-]+\b", re.IGNORECASE),
    # P48 — vague authority appeals without attribution
    "P48_vague_authority": re.compile(
        r"\b(experts?|studies|research(?:ers)?|analysts?|scientists?)\s+"
        r"(say|says|said|show|shows|suggest|suggests|agree|believe)\b",
        re.IGNORECASE,
    ),
    # P49 — rhetorical question / hook openers
    "P49_rhetorical_opener": re.compile(
        r"^(?:have you ever|what if (?:I|we) told you|imagine|picture this|"
        r"isn't it (?:time|funny)|why do(?:es)?n't everyone)\b",
        re.IGNORECASE,
    ),
    # P50 — bold-keyword stuffing (**term**: definition)
    "P50_bold_stuffing": re.compile(r"\*\*[^*\n]{1,40}\*\*\s*:", re.IGNORECASE),
}

# Passive voice heuristic (be-verb + past participle-ish).
_PASSIVE_RE = re.compile(
    r"\b(?:is|are|was|were|been|being|be)\s+\w+(?:ed|en)\b", re.IGNORECASE
)

_VAGUE_QUANTIFIERS = re.compile(
    r"\b(many|several|numerous|various|some|a lot of|plenty of|countless)\b", re.IGNORECASE
)

_CONCRETE_SIGNALS = re.compile(
    r"(\$\s?\d[\d,.]*|\d+(?:\.\d+)?%|\b\d{4}\b|\b\d+(?:\.\d+)?\s?(?:percent|dollars|years|months|weeks|days|hours)\b)"
)

_RHETORIC_OPENERS_RE = _NEW_ERA_PATTERNS["P49_rhetorical_opener"]


@dataclass
class HumanScore:
    """5-Dimension human-likeness score (0–100 per dimension)."""

    lexical: float = 100.0       # D1 slop-word density
    burstiness: float = 0.0      # D2 sentence-length variance
    structure: float = 100.0     # D3 P45–P50 structural tells
    voice: float = 100.0         # D4 passive ratio + human markers
    specificity: float = 50.0    # D5 concrete vs vague signals
    overall: float = 0.0
    findings: list[str] = field(default_factory=list)

    def finalize(self) -> HumanScore:
        self.overall = round(
            self.lexical * 0.30
            + self.burstiness * 0.20
            + self.structure * 0.20
            + self.voice * 0.15
            + self.specificity * 0.15,
            1,
        )
        return self

    @property
    def passes_gate(self) -> bool:
        return self.overall >= HUMAN_SCORE_THRESHOLD


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
    def audit_text(cls, text: str) -> HumanScore:
        """Pass 2 — measure 5-Dimension human-likeness without mutating text."""
        score = HumanScore()
        if not text or not text.strip():
            return score.finalize()

        words = len(text.split())
        if words < 20:
            # Too short to judge structurally; lexical only.
            slop = cls.find_slop_words(text)
            score.lexical = max(0.0, 100 - len(slop) * 25)
            return score.finalize()

        per_1k = 1000.0 / words

        # D1 — lexical slop density (25 pts per hit/1k, floor 0)
        slop_hits = len(cls.find_slop_words(text))
        total_slop_occurrences = sum(
            len(re.findall(p, text, flags=re.IGNORECASE)) for p in AI_SLOP_DICTIONARY
        )
        density = total_slop_occurrences * per_1k
        score.lexical = round(max(0.0, 100 - density * 25), 1)
        if slop_hits:
            score.findings.append(f"lexical: {slop_hits} distinct slop terms")

        # D2 — burstiness (CV mapped: 0.6+ → 100)
        burst = cls.calculate_burstiness(text)
        cv = burst["burstiness_score"]
        score.burstiness = round(min(100.0, cv / 0.6 * 100), 1)
        if not burst["is_natural"]:
            score.findings.append(f"burstiness: CV {cv} below natural range")

        # D3 — structural tells P45–P50
        penalties = 0.0
        em_dashes = len(_NEW_ERA_PATTERNS["P45_em_dash"].findall(text)) * per_1k
        if em_dashes > 2.0:
            penalties += min(30, (em_dashes - 2.0) * 15)
            score.findings.append(f"P45: em-dash density {em_dashes:.1f}/1k")
        for key in ("P46_negation_contrast", "P47_triad", "P48_vague_authority", "P50_bold_stuffing"):
            hits = len(_NEW_ERA_PATTERNS[key].findall(text))
            if hits:
                penalties += min(25, hits * per_1k * 8)
                score.findings.append(f"{key.split('_')[0]}: {hits} hit(s)")
        if _RHETORIC_OPENERS_RE.match(text.strip()):
            penalties += 10
            score.findings.append("P49: rhetorical opener")
        paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
        if len(paragraphs) >= 4:
            para_sizes = [len(re.findall(r"[.!?]+", p)) for p in paragraphs]
            size_variance = max(para_sizes) - min(para_sizes)
            if size_variance == 0 and max(para_sizes) > 0:
                penalties += 15
                score.findings.append("P50: uniform paragraph rhythm")
        score.structure = round(max(0.0, 100 - penalties), 1)

        # D4 — voice: passive ratio + human markers
        sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
        passive_hits = len(_PASSIVE_RE.findall(text))
        passive_ratio = passive_hits / max(1, len(sentences))
        voice_score = 100 - passive_ratio * 60
        if re.search(r"\b(?:don't|doesn't|can't|won't|it's|you're|we're|there's)\b", text, re.IGNORECASE):
            voice_score += 5  # contractions signal human register
        if re.search(r"\b(?:I|we|our)\b", text):
            voice_score += 5  # first-person presence
        score.voice = round(min(100.0, max(0.0, voice_score)), 1)
        if passive_ratio > 0.4:
            score.findings.append(f"voice: passive ratio {passive_ratio:.2f}")

        # D5 — specificity: concrete signals vs vague quantifiers
        concrete = len(_CONCRETE_SIGNALS.findall(text))
        vague = len(_VAGUE_QUANTIFIERS.findall(text))
        specificity = 50 + min(50, concrete * per_1k * 6) - min(40, vague * per_1k * 8)
        score.specificity = round(max(0.0, min(100.0, specificity)), 1)
        if vague > concrete:
            score.findings.append("specificity: vague quantifiers outweigh concrete data")

        return score.finalize()

    @classmethod
    def humanize_with_audit(cls, payload: dict[str, Any]) -> tuple[dict[str, Any], HumanScore]:
        """Full 2-pass pipeline: sanitize (pass 1) then audit (pass 2).

        Returns the sanitized payload unchanged in shape plus its HumanScore,
        so callers can gate refinement without polluting DB-bound schemas.
        """
        clean = cls.humanize_article_payload(payload)
        prose = " ".join(
            str(clean.get(k, "")) for k in ("title", "content", "excerpt", "takeaway") if clean.get(k)
        )
        return clean, cls.audit_text(prose)

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

