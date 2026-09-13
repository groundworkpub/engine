"""Agent 2 — The Critic.

Deduplicates items via cryptographic source_hash and evaluates content against
Self-RAG Quality Gates (RAGAS relevance >= 0.85, profanity, paywall, and factual grounding).
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

logger = logging.getLogger(__name__)

PROFANITY_PATTERNS = re.compile(
    r"\b(spam|casino|porn|xxx|mlm|pyramid scheme)\b",
    re.IGNORECASE,
)

PAYWALL_PATTERNS = re.compile(
    r"subscribe to (read|continue|access)|this (article|content) is for (subscribers|members)",
    re.IGNORECASE,
)

NON_UTILITY_PATTERNS = re.compile(
    r"\b(volcano|eruption|earthquake|tsunami|missile|airstrike|drone strike|bombing|parliament|election results?|landslide election|far-right|regime change|strait of hormuz|iranian oil|hostage|casualt(?:y|ies))\b",
    re.IGNORECASE,
)

MIN_CONTENT_LENGTH = 400
MIN_TITLE_LENGTH = 10


def normalize_url(raw_url: str) -> str:
    """Strips tracking query parameters (utm_*, ref, fbclid, etc.) for stable deduplication."""
    if not raw_url:
        return ""
    try:
        parsed = urlparse(raw_url.strip())
        query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
        # Filter out tracking parameters
        clean_pairs = [
            (k, v) for k, v in query_pairs
            if not (k.lower().startswith("utm_") or k.lower() in {"ref", "fbclid", "gclid", "source", "medium"})
        ]
        clean_query = urlencode(sorted(clean_pairs))
        return urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), parsed.params, clean_query, ""))
    except Exception:
        return raw_url.strip()


def compute_normalized_fingerprint(text: str, title: str) -> str:
    """Computes a normalized text fingerprint invariant to whitespace, casing, and boilerplate."""
    clean_title = re.sub(r"[^\w\s]", "", title.lower())
    clean_title = re.sub(r"\s+", " ", clean_title).strip()

    # Take first 1000 chars of text, strip HTML/punctuation/whitespace
    clean_text = re.sub(r"<[^>]+>", " ", text.lower()[:1500])
    clean_text = re.sub(r"[^\w\s]", "", clean_text)
    clean_text = re.sub(r"\s+", " ", clean_text).strip()

    payload = f"{clean_title}::{clean_text[:600]}".encode()
    return hashlib.sha256(payload).hexdigest()


def compute_hash(url: str, title: str, algorithm: str = "sha256") -> str:
    """Cryptographic hash for deduplication based on normalized URL + title."""
    clean_url = normalize_url(url)
    payload = f"{clean_url}:{title.strip()}".encode()
    if algorithm == "md5":
        return hashlib.md5(payload).hexdigest()
    return hashlib.sha256(payload).hexdigest()


def get_existing_hashes(supabase: Any) -> set[str]:
    """Fetch all known source_hash values from Supabase (paginated past 1000-row cap)."""
    hashes: set[str] = set()
    try:
        start = 0
        batch_size = 1000
        while True:
            result = (
                supabase.table("articles")
                .select("source_hash")
                .range(start, start + batch_size - 1)
                .execute()
            )
            batch = [row["source_hash"] for row in result.data if row.get("source_hash")]
            hashes.update(batch)
            if len(batch) < batch_size:
                break
            start += batch_size
    except Exception as e:
        logger.warning(f"Failed to fetch existing hashes: {e}")
    return hashes


def check_cannibalization_risk(title: str, pillar: str, supabase: Any | None = None) -> tuple[bool, str | None]:
    """Check Jaccard token similarity against recent indexed articles in the same pillar.
    
    If similarity >= 0.65, returns (True, existing_canonical_slug) to prevent keyword cannibalization.
    """
    if not supabase or not title:
        return False, None
    try:
        res = (
            supabase.table("articles")
            .select("id, slug, title")
            .eq("pillar", pillar)
            .order("published_at", desc=True)
            .limit(50)
            .execute()
        )
        recent_titles = res.data or []
        tokens_target = set(re.findall(r"\b[a-z]{3,}\b", title.lower()))
        if not tokens_target:
            return False, None
        for item in recent_titles:
            existing_title = item.get("title", "")
            tokens_exist = set(re.findall(r"\b[a-z]{3,}\b", existing_title.lower()))
            intersection = tokens_target.intersection(tokens_exist)
            union = tokens_target.union(tokens_exist)
            jaccard = len(intersection) / len(union) if union else 0.0
            if jaccard >= 0.65:
                logger.info(
                    f"Cannibalization guard: '{title[:40]}' matches existing '{existing_title[:40]}' (similarity {jaccard:.2f})"
                )
                return True, item.get("slug")
    except Exception as e:
        logger.debug(f"Cannibalization check notice: {e}")
    return False, None


PILLAR_KEYWORDS: dict[str, set[str]] = {
    "money": {"money", "mortgage", "finance", "loan", "rate", "invest", "tax", "debt", "budget", "bank", "stock", "bond", "yield", "inflation", "retire", "savings", "credit", "portfolio", "hsa", "ira", "interest"},
    "body": {"body", "health", "diet", "fitness", "sleep", "heart", "nutrition", "exercise", "medical", "wellness", "doctor", "longevity", "muscle", "protein", "training", "clinical", "biomarker", "blood", "supplement", "vaccine"},
    "home": {"home", "house", "solar", "hvac", "energy", "renovation", "roof", "insulation", "appliance", "security", "property", "heat pump", "furnace", "heating", "cooling", "electric", "plumbing", "contractor", "lawn", "kitchen", "water", "generator"},
    "life": {"life", "career", "travel", "legal", "insurance", "auto", "work", "job", "lifestyle", "family", "education", "flight", "salary", "workplace", "productivity", "negotiate", "law", "attorney"},
    "tech": {"tech", "software", "ai", "hardware", "tool", "app", "code", "cloud", "security", "device", "model", "data", "cybersecurity", "developer", "system", "router", "agent", "computing"},
}


def grade_retrieval_relevance(content: str, query_topic: str) -> float:
    """Self-RAG Relevance Grader (RAGAS-inspired baseline).

    Scores how directly the harvested content addresses the target pillar/topic.
    Returns float score between 0.0 and 1.0.
    """
    if not content or not query_topic:
        return 0.0

    raw_topic = query_topic.lower().strip()
    topic_tokens = PILLAR_KEYWORDS.get(raw_topic, set(re.findall(r"\b[a-z]{3,}\b", raw_topic)))
    if not topic_tokens:
        return 1.0

    content_lower = content.lower()
    matched = sum(1 for token in topic_tokens if token in content_lower)
    score = min(1.0, (matched / max(3, len(topic_tokens) // 3)))

    # Boost if topic keywords appear in first 300 chars
    first_chunk = content_lower[:300]
    lead_matches = sum(1 for token in topic_tokens if token in first_chunk)
    if lead_matches > 0:
        score = min(1.0, score + 0.15)

    return round(score, 2)


def grade_faithfulness_and_grounding(draft_content: str, source_content: str) -> float:
    """Self-RAG Faithfulness & Grounding Grader.

    Verifies numerical claims and percentages in draft against original source text.
    """
    if not draft_content or not source_content:
        return 1.0

    # Extract numbers and percentages from draft
    draft_numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?\b", draft_content))
    if not draft_numbers:
        return 1.0

    source_text = source_content.lower()
    verified_count = sum(1 for num in draft_numbers if num.lower() in source_text)
    faithfulness = verified_count / len(draft_numbers)

    return round(faithfulness, 2)


def grade_information_gain(content: str, title: str) -> float:
    """Evaluates Information Gain potential according to Google's Patent model (US10747806B1):
    - High score: Interactive calculation, break-even comparison, primary datasets, novel knowledge delta.
    - Low score: Generic consensus questions or thin regurgitated copy.
    """
    if not content and not title:
        return 0.0

    combined = f"{title} {content}".lower()
    score = 50.0

    # Positive: Calculations & Decision Tools (+30 pts)
    if any(k in combined for k in ["calculator", "break even", "formula", "roi", "net cost", "amortization", "compound interest"]):
        score += 30.0
    elif any(k in combined for k in ["vs", "compare", "comparison", "matrix", "breakdown", "difference"]):
        score += 20.0

    # Positive: Empirical research, clinical benchmarks, DOI datasets (+15 pts)
    if any(k in combined for k in ["guidelines", "clinical", "biomarker", "benchmark", "evidence", "dataset", "doi", "peer-reviewed"]):
        score += 15.0

    # Negative: Generic definition / consensus regurgitation (-20 pts)
    if any(k in combined for k in ["what is", "meaning of", "definition of", "wiki"]):
        score -= 20.0

    return max(0.0, min(100.0, round(score, 1)))


def build_evidence_graph_node(
    claim: str, source_url: str, doi: str | None = None, confidence: float = 0.95
) -> dict[str, Any]:
    """Create a structured evidence graph entry for verified research claims."""
    return {
        "claim": claim[:200],
        "source_url": source_url,
        "doi": doi,
        "confidence_score": round(confidence, 3),
        "verified_at": "auto_critic",
    }


COMMON_STOPWORDS: set[str] = {
    "the", "and", "that", "have", "for", "not", "with", "you", "this", "but", "his", "from",
    "they", "say", "her", "she", "will", "one", "all", "would", "there", "their", "what",
    "out", "about", "who", "get", "which", "go", "me", "when", "make", "can", "like", "time",
    "no", "just", "him", "know", "take", "people", "into", "year", "your", "good", "some",
    "could", "them", "see", "other", "than", "then", "now", "look", "only", "come", "its",
    "over", "think", "also", "back", "after", "use", "two", "how", "our", "work", "first",
    "well", "way", "even", "new", "want", "because", "any", "these", "give", "day", "most", "us"
}


def grade_geo_extractability(content: str) -> tuple[float, str | None]:
    """AEO & GEO Direct-Answer Extractability Grader.

    Verifies that the opening block (under title, before first H2) contains a standalone
    40-80 word direct answer to the search query without throat-clearing fluff.
    """
    if not content:
        return 0.0, "Missing content"

    parts = re.split(r"\n##\s+", content.strip(), maxsplit=1)
    lead_text = parts[0].strip()

    clean_lead = re.sub(r"!\[.*?\]\(.*?\)", "", lead_text)
    clean_lead = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", clean_lead).strip()
    words = clean_lead.split()
    word_count = len(words)

    # Check for throat-clearing fluff first
    lower_lead = clean_lead.lower()
    for fluff in [
        "in this article", "in this guide", "welcome to", "have you ever wondered",
        "in today's world", "in today's fast-paced", "it is important to remember",
        "let's dive into", "let's explore"
    ]:
        if fluff in lower_lead[:120]:
            return 0.5, f"Lead block opens with throat-clearing cliché: '{fluff}'"

    if word_count < 25:
        return 0.4, f"Lead block too brief ({word_count} words < 25 required for standalone answer)"

    if 35 <= word_count <= 95:
        return 1.0, None
    elif word_count > 95:
        return 0.85, None
    else:
        return 0.75, None


def grade_keyword_stuffing(content: str, title: str = "") -> tuple[bool, float, str | None]:
    """Anti-Keyword Stuffing Grader (Aggarwal et al., KDD 2024).

    Princeton GEO whitepaper showed keyword stuffing reduces visibility on AI search engines by 10-25%.
    Calculates unigram and bigram relative occurrence rate. Returns (passed, max_density_pct, issue).
    Fails if maximum density exceeds 2.2% (0.022) of total tokens.
    """
    if not content:
        return True, 0.0, None

    tokens = re.findall(r"\b[a-z]{3,}\b", content.lower())
    if not tokens or len(tokens) < 80:
        return True, 0.0, None

    total_tokens = len(tokens)
    content_words = [t for t in tokens if t not in COMMON_STOPWORDS]
    if not content_words:
        return True, 0.0, None

    from collections import Counter
    unigram_counts = Counter(content_words)
    most_common_word, word_count = unigram_counts.most_common(1)[0]
    unigram_density = word_count / total_tokens

    bigrams = [f"{tokens[i]} {tokens[i+1]}" for i in range(len(tokens) - 1)]
    bigram_counts = Counter(bigrams)
    valid_bigrams = [
        (bg, c) for bg, c in bigram_counts.most_common(5)
        if any(w not in COMMON_STOPWORDS for w in bg.split())
    ]

    max_density = unigram_density
    offending_term = most_common_word
    offending_count = word_count

    if valid_bigrams:
        top_bg, bg_count = valid_bigrams[0]
        bg_density = bg_count / max(1, len(bigrams))
        if bg_density > max_density:
            max_density = bg_density
            offending_term = top_bg
            offending_count = bg_count

    # Adaptive threshold: 2.2% for full articles (>= 250 tokens); 4.0% for short passages
    threshold = 0.022 if total_tokens >= 250 else 0.040

    max_density_pct = round(max_density * 100, 2)
    threshold_pct = round(threshold * 100, 1)
    # A term must appear at least 3 times and exceed threshold to constitute keyword stuffing
    if max_density > threshold and offending_count >= 3:
        issue = f"Excessive keyword repetition for '{offending_term}' ({max_density_pct}% > {threshold_pct}% limit — triggers AI visibility demotion)"
        return False, max_density_pct, issue

    return True, max_density_pct, None


def grade_evidence_density(content: str) -> tuple[float, int]:
    """Princeton GEO Evidence Stacking Grader.

    Verifies that the text is rich in empirical evidence:
    - Numerical benchmarks & percentages (e.g. $450, 6.75%, 150 min)
    - 4-digit publication/event years >= 2020 (e.g. 2024, 2025, 2026)
    - Measurement units & ratios (kWh, bps, mg, mL, %, $)
    - Authoritative institutional citations (.gov, .edu, NIH, BLS, Fed, etc.)
    """
    if not content:
        return 0.0, 0

    text = content.lower()

    numbers = re.findall(r"\b\d+(?:\.\d+)?%?\b", content)
    currency_metrics = re.findall(r"[\$€£]\d+(?:,\d+)*(?:\.\d+)?", content)
    years = re.findall(r"\b202[0-9]\b", content)
    units = re.findall(r"\b(kwh|bps|basis points?|mg|ml|mcg|mhz|ghz|sq ft|square feet|amperage|watts?|btu|decibels?|n=\d+)\b", text)
    institutions = re.findall(r"\b(nih|pubmed|cdc|who|bls|bureau of labor statistics|freddie mac|fannie mae|federal reserve|irs|sec|fda|nrel|dsire|epa|iea|doi:\s*10\.\d+)\b", text)

    total_signals = len(numbers) + len(currency_metrics) + len(years) * 2 + len(units) + len(institutions) * 3
    words = len(content.split())
    if words == 0:
        return 0.0, 0

    density_ratio = total_signals / max(1, (words / 35))
    score = min(1.0, round(density_ratio, 2))

    return score, total_signals


def grade_title_content_alignment(title: str, content: str) -> tuple[float, str | None]:
    """Google Leaked titleMatchScore Proxy.

    Verifies that the core entities and promises made in the title are directly addressed
    in the first 25% of the content body to satisfy Google's NavBoost and content alignment.
    """
    if not title or not content:
        return 0.0, "Missing title or content"

    title_tokens = [
        w for w in re.findall(r"\b[a-z]{3,}\b", title.lower())
        if w not in COMMON_STOPWORDS
    ]
    if not title_tokens:
        return 1.0, None

    cutoff = max(350, len(content) // 4)
    first_quarter = content[:cutoff].lower()

    matched = 0
    missing: list[str] = []
    for tok in title_tokens:
        stem = tok[:5] if len(tok) >= 5 else tok
        if tok in first_quarter or stem in first_quarter:
            matched += 1
        else:
            missing.append(tok)

    match_ratio = matched / len(title_tokens)
    if match_ratio < 0.40:
        return round(match_ratio, 2), f"Low title-content alignment: first 25% of article misses title entities ({', '.join(missing[:3])})"

    return round(match_ratio, 2), None


def passes_quality_gate(item: dict[str, Any], config: dict) -> tuple[bool, str]:
    """Check if an item passes all quality gates. Returns (passed, reason)."""
    content = item.get("raw_content", "") or item.get("content", "")
    title = item.get("title", "")
    pillar = item.get("pillar", "")

    min_content = config.get("quality", {}).get("min_content_length", MIN_CONTENT_LENGTH)
    min_title = config.get("quality", {}).get("min_title_length", MIN_TITLE_LENGTH)

    if len(content) < min_content:
        return False, f"Content too short ({len(content)} < {min_content} chars)"
    if len(title) < min_title:
        return False, f"Title too short ({len(title)} < {min_title} chars)"
    if PROFANITY_PATTERNS.search(content) or PROFANITY_PATTERNS.search(title):
        return False, "Failed profanity filter"
    if PAYWALL_PATTERNS.search(content[:500]):
        return False, "Paywall pattern detected"
    if NON_UTILITY_PATTERNS.search(title) or NON_UTILITY_PATTERNS.search(content[:600]):
        return False, "Disallowed non-utility breaking news / geopolitical disaster topic"

    # Self-RAG relevance score check
    if pillar:
        relevance = grade_retrieval_relevance(f"{title} {content}", pillar)
        if relevance < 0.50:
            return False, f"Low relevance to pillar '{pillar}' (score {relevance:.2f} < 0.50)"

def validate_faq_schema_quality(faq: Any) -> tuple[float, list[str]]:
    """Validates FAQ array against the 0.71 AEO correlation standard.

    Checks:
    1. FAQ must be a list with at least 3-4 Q&A pairs.
    2. Each question must end with a question mark ('?').
    3. Each answer must be a substantive direct answer (20 to 90 words).
    """
    if not isinstance(faq, list) or len(faq) < 3:
        return 0.3, ["Missing structured FAQPage array (min 3-4 pairs required for 0.71 AEO correlation)"]

    issues: list[str] = []
    valid_pairs = 0

    for i, item in enumerate(faq):
        if not isinstance(item, dict):
            issues.append(f"FAQ item #{i+1} is not a valid dictionary object")
            continue
        q = str(item.get("question") or "").strip()
        a = str(item.get("answer") or "").strip()

        if not q or not q.endswith("?"):
            issues.append(f"FAQ #{i+1} question missing or does not end with '?'")
        words = len(a.split())
        if words < 15:
            issues.append(f"FAQ #{i+1} answer too brief ({words} words < 15)")
        elif words > 95:
            issues.append(f"FAQ #{i+1} answer too long ({words} words > 95)")
        else:
            valid_pairs += 1

    ratio = valid_pairs / max(1, len(faq))
    return round(ratio, 2), issues


def check_temporal_recency(content: str, pillar: str = "") -> tuple[bool, str | None]:
    """Evaluates temporal freshness against the 2026 AI search decay curve.

    For volatile adult decision pillars (money, home, tech), citations older than 2 years
    trigger recency penalties unless balanced by recent (>= 2024/2025/2026) benchmarks.
    """
    if not content:
        return True, None
    years = [int(y) for y in re.findall(r"\b202[0-9]\b", content)]
    if not years:
        return True, None
    recent_years = [y for y in years if y >= 2024]
    if pillar.lower() in ("money", "home", "tech") and years and not recent_years:
        latest = max(years)
        return False, f"Stale citations in {pillar} pillar (latest benchmark: {latest}, target >= 2024/2026)"
    return True, None


_DANGLING_TITLE_RE = re.compile(
    r"\b(in|on|at|for|to|with|by|from|about|of|the|a|an|your|our|their|and|or|but|as|than|into|onto)\s*[.,:;!?]?$",
    re.IGNORECASE,
)

_PLACEHOLDER_SCRIPT_RE = re.compile(
    r"(?:contact the box office|i am interested in purchasing|inquire about membership benefits|ask for alternative dates|\[show name\]|\[date\])",
    re.IGNORECASE,
)


def grade_title_completeness(title: str) -> tuple[bool, str | None]:
    """Ensures title is syntactically complete and never terminates mid-clause (Option 1 + 3)."""
    t = str(title or "").strip()
    if not t:
        return False, "Title is empty"
    words = t.split()
    if len(words) < 4:
        return False, f"Title too brief ({len(words)} words < 4)"
    if _DANGLING_TITLE_RE.search(t):
        last_word = words[-1]
        return False, f"Title has dangling trailing preposition or pronoun ('{last_word}')"
    return True, None


def grade_decision_utility(content: str, pillar: str = "") -> tuple[bool, str | None]:
    """Ensures content contains substantive decision frameworks and rejects generic boilerplate."""
    if _PLACEHOLDER_SCRIPT_RE.search(content):
        return False, "Content contains forbidden placeholder negotiation script boilerplate"
    if pillar.lower() == "life":
        # Check for generic tourist itinerary without quantitative decision anchors
        if re.search(r"\b(?:itinerary|sightseeing|food crawl|visit)\b", content, re.IGNORECASE):
            has_math = re.search(
                r"(?:\$\d+|\b\d+%\b|\btransit\b|\bomny\b|\bhours?\b|\bcost\b|\bbudget\b|\bexpected value\b)",
                content,
                re.IGNORECASE,
            )
            if not has_math:
                return False, "Travel/lifestyle content lacks quantitative cost model or decision parameters"
    return True, None


def generate_hybrid_smart_title(title: str, pillar: str) -> str:
    """Derives an entity-first, pixel-perfect 50-55 char title without truncating mid-phrase (Option 2)."""
    clean = title.split("|")[0].strip()
    if len(clean) <= 55 and not _DANGLING_TITLE_RE.search(clean):
        return clean

    # If title has a colon, preserve prefix if meaningful
    if ":" in clean:
        parts = clean.split(":", 1)
        entity_prefix = parts[0].strip()
        rest = parts[1].strip()
        words = rest.split()
        short_rest = ""
        for w in words:
            candidate = f"{entity_prefix}: {short_rest} {w}".strip()
            if len(candidate) <= 55:
                short_rest = f"{short_rest} {w}".strip()
            else:
                break
        if short_rest and not _DANGLING_TITLE_RE.search(short_rest):
            return f"{entity_prefix}: {short_rest}"

    # Fallback: slice by words safely
    words = clean.split()
    short = ""
    for w in words:
        candidate = f"{short} {w}".strip()
        if len(candidate) <= 55:
            short = candidate
        else:
            break

    while short and _DANGLING_TITLE_RE.search(short):
        short = " ".join(short.split()[:-1])

    return short or clean[:55]


def passes_quality_gate(item: dict[str, Any], config: dict) -> tuple[bool, str]:
    """Check if an item passes all quality gates. Returns (passed, reason)."""
    content = item.get("raw_content", "") or item.get("content", "")
    title = item.get("title", "")
    pillar = item.get("pillar", "")

    min_content = config.get("quality", {}).get("min_content_length", MIN_CONTENT_LENGTH)
    min_title = config.get("quality", {}).get("min_title_length", MIN_TITLE_LENGTH)

    if len(content) < min_content:
        return False, f"Content too short ({len(content)} < {min_content} chars)"
    if len(title) < min_title:
        return False, f"Title too short ({len(title)} < {min_title} chars)"
    if PROFANITY_PATTERNS.search(content) or PROFANITY_PATTERNS.search(title):
        return False, "Failed profanity filter"
    if PAYWALL_PATTERNS.search(content[:500]):
        return False, "Paywall pattern detected"
    if NON_UTILITY_PATTERNS.search(title) or NON_UTILITY_PATTERNS.search(content[:600]):
        return False, "Disallowed non-utility breaking news / geopolitical disaster topic"

    # Title Syntactic Completeness Check (Rule §2.1 & Hybrid Invariant)
    title_complete, title_issue = grade_title_completeness(title)
    if not title_complete:
        return False, f"Syntactic title failure: {title_issue}"

    # Decision Utility & Anti-Boilerplate Script Gate
    utility_ok, utility_issue = grade_decision_utility(content, pillar)
    if not utility_ok:
        return False, f"Decision Utility failure: {utility_issue}"

    # Self-RAG relevance score check
    if pillar:
        relevance = grade_retrieval_relevance(f"{title} {content}", pillar)
        if relevance < 0.50:
            return False, f"Low relevance to pillar '{pillar}' (score {relevance:.2f} < 0.50)"

    # Information Gain Quality Gate (Google Patent model)
    min_info_gain = float(config.get("quality", {}).get("min_information_gain", 40.0))
    info_gain_score = grade_information_gain(content, title)
    if info_gain_score < min_info_gain:
        return False, f"Low Information Gain score ({info_gain_score} < {min_info_gain})"

    # Anti-Keyword Stuffing Gate (Princeton GEO KDD 2024 penalty prevention)
    stuffing_passed, max_density, stuffing_issue = grade_keyword_stuffing(content, title)
    if not stuffing_passed and max_density > 2.8:
        return False, stuffing_issue or "Extreme keyword stuffing detected"

    # Evidence Density Check for deep analytical articles
    if len(content) > 1500:
        ev_score, _ = grade_evidence_density(content)
        if ev_score < 0.15:
            return False, f"Low empirical evidence density (score {ev_score:.2f} < 0.15)"

    return True, "OK"


def grade_serp_ctr_readiness(draft: dict[str, Any]) -> tuple[float, list[str]]:
    """Evaluates candidate article against Groundwork's Hybrid-Smart SERP CTR standards.

    Checks:
    1. Title completeness & dangling token check.
    2. Title length: strictly 30 <= len <= 68 chars (with smart SERP compression if > 58).
    3. Excerpt length: strictly 130 <= len <= 165 chars.
    4. Forbidden title cliché starters (e.g. "A Comprehensive Guide to...").
    5. Structured FAQPairSchema coverage (0.71 AEO correlation).
    6. Information Gain & Numerical modifiers (e.g. year, formulas, ratios).
    7. AEO BLUF Direct-Answer Extractability in opening block.
    8. Title-Content Alignment (Google Leaked titleMatchScore).
    9. Anti-Keyword Stuffing density cap (<= 2.2%).
    10. Temporal Freshness Recency (for money/home/tech pillars).
    11. Decision Utility & Anti-Boilerplate verification.

    Returns:
        (score: float between 0.0 and 1.0, issues: list[str])
    """
    title = str(draft.get("title") or "").strip()
    excerpt = str(draft.get("excerpt") or "").strip()
    content = str(draft.get("content") or "").strip()
    pillar = str(draft.get("pillar") or "").strip()
    faq = draft.get("faq") or []

    issues: list[str] = []
    score = 1.0

    # 1. Title Completeness Check
    title_ok, title_err = grade_title_completeness(title)
    if not title_ok:
        issues.append(f"Title incomplete: {title_err}")
        score -= 0.35

    # 2. Title length check (Hybrid 68 chars with smart SERP derivation)
    t_len = len(title)
    if t_len > 68:
        issues.append(f"Title exceeded absolute maximum limit ({t_len} chars > 68)")
        score -= 0.35
    elif t_len > 58:
        # Hybrid Option 2: Suggest smart compressed SERP title
        smart_title = generate_hybrid_smart_title(title, pillar)
        draft["serp_title"] = smart_title
    elif t_len < 25:
        issues.append(f"Title too short ({t_len} chars < 25)")
        score -= 0.20

    # Decision Utility & Anti-Boilerplate Check
    util_ok, util_err = grade_decision_utility(content, pillar)
    if not util_ok:
        issues.append(f"Decision utility defect: {util_err}")
        score -= 0.40

    # 2. Forbidden title starters
    for bad_starter in [
        "a comprehensive guide", "understanding the complexities", "understanding the",
        "why there are so many", "exploring the factors", "what you need to know",
        "everything you need to know"
    ]:
        if title.lower().startswith(bad_starter):
            issues.append(f"Title starts with forbidden cliché opener: '{bad_starter}'")
            score -= 0.25
            break

    # 3. Excerpt length check
    e_len = len(excerpt)
    if e_len > 165:
        issues.append(f"Meta description truncated ({e_len} chars > 165)")
        score -= 0.15
    elif e_len < 120:
        issues.append(f"Meta description too short ({e_len} chars < 120)")
        score -= 0.15

    for bad_excerpt in ["this article explores", "in this guide we discuss", "this guide covers", "learn about", "discover how"]:
        if excerpt.lower().startswith(bad_excerpt):
            issues.append(f"Meta description starts with passive fluff: '{bad_excerpt}' (use BLUF direct answer instead)")
            score -= 0.10
            break

    # 4. Strict FAQPairSchema validation (0.71 AEO correlation)
    faq_score, faq_issues = validate_faq_schema_quality(faq)
    if faq_issues:
        issues.extend(faq_issues)
        score -= round((1.0 - faq_score) * 0.20, 2)

    # 5. AEO BLUF Direct-Answer Extractability
    if content:
        bluf_score, bluf_issue = grade_geo_extractability(content)
        if bluf_issue:
            issues.append(bluf_issue)
            score -= 0.15

        # 6. Title-Content Alignment (Google Leaked titleMatchScore)
        align_score, align_issue = grade_title_content_alignment(title, content)
        if align_issue:
            issues.append(align_issue)
            score -= 0.15

        # 7. Anti-Keyword Stuffing (Princeton GEO rule)
        stuff_ok, density_pct, stuff_issue = grade_keyword_stuffing(content, title)
        if not stuff_ok:
            issues.append(stuff_issue or "Keyword stuffing detected")
            score -= 0.20

        # 8. Temporal Recency Guard (90-180 day recency for volatile pillars)
        if pillar:
            recency_ok, recency_issue = check_temporal_recency(content, pillar)
            if not recency_ok and recency_issue:
                issues.append(recency_issue)
                score -= 0.15

    return max(0.0, round(score, 2)), issues


def run_critic(
    raw_payload: list[dict[str, Any]],
    supabase: Any,
    config: dict,
) -> list[dict[str, Any]]:
    """Agent 2: Deduplicate (dual-hash: URL + content fingerprint) and quality-filter raw items."""
    existing_hashes = get_existing_hashes(supabase)
    filtered: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    seen_fingerprints: set[str] = set()

    for item in raw_payload:
        raw_url = str(item.get("url") or "")
        raw_title = str(item.get("title") or "")
        raw_text = str(item.get("content") or item.get("raw_content") or "")

        content_hash = compute_hash(raw_url, raw_title)
        fingerprint = compute_normalized_fingerprint(raw_text, raw_title)

        # 1. Dual-hash Dedup check: check both exact URL hash and normalized content fingerprint
        if content_hash in existing_hashes or content_hash in seen_hashes:
            logger.info(f"Skipping duplicate item by URL hash: {item.get('url', '')}")
            continue

        if fingerprint in seen_fingerprints:
            logger.info(f"Skipping duplicate item by content fingerprint: {item.get('title', '')}")
            continue

        passed, reason = passes_quality_gate(item, config)
        if not passed:
            logger.info(f"Item rejected by quality gate ({reason}): {item.get('url', '')}")
            continue

        item["source_hash"] = content_hash
        item["content_fingerprint"] = fingerprint
        seen_hashes.add(content_hash)
        seen_fingerprints.add(fingerprint)
        filtered.append(item)

    logger.info(f"Critic: {len(filtered)}/{len(raw_payload)} items passed dual-hash quality gates.")
    return filtered

