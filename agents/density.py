"""Density measurement for Scribe quality gates (T2.2 statistics, T2.3 entities).

Pure offline heuristics — no NER model dependencies. Two signals:

- Statistics density: target >= 10 statistical data points per 1,000 words
  (pattern observed in repeat-cited pages: ~12.3 stats / 1k words).
- Named-entity density: target >= 15 distinct named entities per article
  (people, institutions, products, standards, laws) correlated with a 4.8x
  higher citation probability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Statistical data point patterns (each match counts once).
STAT_PATTERNS = [
    r"\$\d[\d,.]*\s*(?:\b(?:billion|million|trillion|thousand)\b)?",
    r"\b\d+(?:\.\d+)?%",
    r"\b\d+(?:\.\d+)?\s?(?:x|bps|basis\s+points)\b",
    r"\b\d+(?:,\d{3})*(?:\.\d+)?\s*(?:years|months|weeks|days|hours|minutes)"
    r"?\s*(?:old|annual|monthly|weekly|daily)?",
    r"\b\d+(?:,\d{3})*(?:\.\d+)?\s*(?:people|participants|respondents|households"
    r"|workers|adults|children|patients|students|families|jobs|claims|filings)\b",
    r"\b\d+(?:\.\d+)?\s*(?:per\s+(?:1,?000|100,?000|cent|person|household))\b",
]

# Acronyms of institutions/agencies/standards (2-6 uppercase letters).
ACRONYM_PATTERN = re.compile(r"\b[A-Z]{2,6}\b")

# Capitalized single words appearing mid-sentence (e.g. "NerdWallet",
# "Bankrate", "Harvard") — sentence-initial capitals are excluded.
SINGLE_CAP_PATTERN = re.compile(r"\b[A-Z][a-z]{2,}\b")

# Capitalized multi-word proper-noun phrases (e.g. "Federal Reserve",
# "Inflation Reduction Act", "NerdWallet"). Dots excluded so matching never
# crosses sentence boundaries; conjunctions handled during post-processing.
PHRASE_PATTERN = re.compile(
    r"\b[A-Z][a-zA-Z0-9&\-]+(?:\s+(?:of|for|the)?\s*[A-Z][a-zA-Z0-9&\-]+){1,3}\b"
)

# Common sentence-initial words that must not count as entities.
STOP_ENTITIES = {
    "the", "this", "that", "these", "those", "there", "here", "it", "its",
    "if", "but", "and", "or", "so", "however", "meanwhile", "instead", "also",
    "then", "when", "while", "although", "because", "since", "after", "before",
    "our", "their", "your", "his", "her", "one", "two", "three", "first",
    "second", "third", "next", "last", "most", "some", "any", "all", "both",
    "each", "every", "few", "many", "much", "more", "less", "other", "another",
    "such", "only", "own", "same", "than", "too", "very", "can", "will",
    "just", "should", "now", "table", "figure", "chart", "graph", "step",
    "key", "takeaway", "note", "tip", "warning", "example", "question",
    "answer", "faq", "introduction", "conclusion", "overview", "summary",
}

# Words that appear inside legitimate phrases but break naive matching.
_PHRASE_INNER_STOP = {"of", "for", "the", "and"}

COMPILED_STAT_PATTERNS = [re.compile(p, re.IGNORECASE) for p in STAT_PATTERNS]


@dataclass
class DensityReport:
    word_count: int
    stat_count: int
    stats_per_1k: float
    entity_count: int
    entities: list[str] = field(default_factory=list)

    @property
    def passes_stats_gate(self) -> bool:
        return self.stats_per_1k >= 10.0

    @property
    def passes_entity_gate(self) -> bool:
        return self.entity_count >= 15


def extract_stats(content: str) -> int:
    """Count statistical data-point occurrences in the text."""
    total = 0
    for pattern in COMPILED_STAT_PATTERNS:
        total += len(pattern.findall(content))
    return total


def _clean_phrase(segment: str) -> list[str]:
    words = segment.split()
    stop = STOP_ENTITIES | _PHRASE_INNER_STOP
    while words and (words[0].lower() in stop or len(words[0]) <= 1):
        words.pop(0)
    while words and (words[-1].lower() in stop or len(words[-1]) <= 1):
        words.pop()
    return words


def _is_sentence_start(text: str, start: int) -> bool:
    if start == 0:
        return True
    prev = text[:start].rstrip()
    if not prev:
        return True
    return prev[-1] in ".!?:;"


def extract_entities(content: str) -> set[str]:
    """Extract distinct named entities via phrase + acronym heuristics."""
    # Strip markdown syntax so headings/links do not distort matching.
    stripped = re.sub(r"[#*`>\[\]()!_]|https?://\S+", " ", content)
    entities: set[str] = set()

    for match in ACRONYM_PATTERN.findall(stripped):
        if match.lower() not in STOP_ENTITIES:
            entities.add(match)

    # Phrases first: their component words must not resurface as singles.
    phrase_words: set[str] = set()
    for phrase in PHRASE_PATTERN.findall(stripped):
        # Conjunctions and sentence punctuation split candidate phrases so
        # "BLS and NIH" never merges into one entity.
        for segment in re.split(r"\b(?:and|or|but)\b|[.;!?]", phrase):
            words = _clean_phrase(segment)
            if len(words) >= 2:
                entities.add(" ".join(words))
                phrase_words.update(w.lower() for w in words)

    for match in SINGLE_CAP_PATTERN.finditer(stripped):
        word = match.group(0)
        if (
            word.lower() in STOP_ENTITIES
            or word.lower() in phrase_words
            or _is_sentence_start(stripped, match.start())
        ):
            continue
        entities.add(word)

    return entities


def audit_density(content: str) -> DensityReport:
    """Produce a full density report for an article body."""
    words = len(content.split())
    stat_count = extract_stats(content)
    entities = extract_entities(content)
    return DensityReport(
        word_count=words,
        stat_count=stat_count,
        stats_per_1k=round((stat_count / words) * 1000, 1) if words else 0.0,
        entity_count=len(entities),
        entities=sorted(entities),
    )
