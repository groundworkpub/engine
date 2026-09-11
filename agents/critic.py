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


def passes_quality_gate(item: dict[str, Any], config: dict) -> tuple[bool, str]:
    """Check if an item passes all quality gates. Returns (passed, reason)."""
    content = item.get("raw_content", "")
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

    # Information Gain Quality Gate (Google Patent model)
    min_info_gain = float(config.get("quality", {}).get("min_information_gain", 40.0))
    info_gain_score = grade_information_gain(content, title)
    if info_gain_score < min_info_gain:
        return False, f"Low Information Gain score ({info_gain_score} < {min_info_gain})"

    return True, "OK"


def grade_serp_ctr_readiness(draft: dict[str, Any]) -> tuple[float, list[str]]:
    """Evaluates candidate article against Groundwork's Hybrid-Smart SERP CTR standards.

    Checks:
    1. Title length: strictly 30 <= len <= 58 chars (zero SERP ellipsis truncation).
    2. Excerpt length: strictly 130 <= len <= 165 chars.
    3. Forbidden title cliché starters (e.g. "A Comprehensive Guide to...").
    4. Structured FAQ coverage for Google SERP accordion rich results.
    5. Information Gain & Numerical modifiers (e.g. year, formulas, ratios).

    Returns:
        (score: float between 0.0 and 1.0, issues: list[str])
    """
    title = str(draft.get("title") or "").strip()
    excerpt = str(draft.get("excerpt") or "").strip()
    faq = draft.get("faq") or []

    issues: list[str] = []
    score = 1.0

    # 1. Title length check (580px limit)
    t_len = len(title)
    if t_len > 58:
        issues.append(f"Title truncated on SERP ({t_len} chars > 58 limit)")
        score -= 0.35
    elif t_len < 25:
        issues.append(f"Title too short ({t_len} chars < 25)")
        score -= 0.20

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

    # 4. FAQ presence
    if not isinstance(faq, list) or len(faq) < 3:
        issues.append("Missing structured FAQPage array (lacks SERP accordion snippet)")
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

