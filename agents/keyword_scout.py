"""Agent 4 — The Keyword Scout.

Mines candidate keywords per pillar from SearXNG public instances (JSON API),
scores them by signal (result frequency), optionally classifies intent with
LiteLLM, and upserts validated rows into the Supabase ``keywords`` table.

Best-effort throughout: network, LLM, and DB failures are logged and skipped,
never raised to the caller. Run as ``python keyword_scout.py``.
"""

import html
import json
import logging
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from typing import Any, cast

import litellm
import yaml
from pydantic import BaseModel, Field, field_validator
from supabase import create_client

logger = logging.getLogger(__name__)

# ─── Pydantic validation model (Python equivalent of Zod) ────────────────────

ALLOWED_INTENTS = {"informational", "commercial", "transactional", "navigational"}
ALLOWED_PILLARS = {"money", "body", "home", "life", "tech"}


class KeywordCandidate(BaseModel):
    keyword: str = Field(min_length=2, max_length=120)
    normalized: str = Field(min_length=2, max_length=120)
    pillar: str
    source: str = "llm_scout"
    intent: str = "informational"
    signal: int = Field(ge=1)
    status: str = "pending"

    @field_validator("keyword")
    @classmethod
    def clean_keyword(cls, v: str) -> str:
        v = re.sub(r"\s+", " ", v.strip())
        if not v:
            raise ValueError("keyword must not be empty")
        return v[:120]

    @field_validator("normalized")
    @classmethod
    def clean_normalized(cls, v: str) -> str:
        return re.sub(r"\s+", " ", v.strip())[:120]

    @field_validator("pillar")
    @classmethod
    def validate_pillar(cls, v: str) -> str:
        return v if v in ALLOWED_PILLARS else "money"

    @field_validator("intent")
    @classmethod
    def validate_intent(cls, v: str) -> str:
        return v if v in ALLOWED_INTENTS else "informational"


# ─── Defaults (overridden by config.yml) ─────────────────────────────────────

DEFAULT_SEARXNG_INSTANCE = "https://searx.be"
DEFAULT_FALLBACK_CHAIN = [
    "gemini/gemini-3.1-flash-lite",
    "groq/llama-3.3-70b-versatile",
    "openrouter/openai/gpt-oss-20b:free",
]
DEFAULT_TIMEOUT_SECONDS = 8
DEFAULT_RETRIES = 1
RETRY_BACKOFF_SECONDS = 0.5
MIN_TOKEN_LENGTH = 2
ENRICH_TOP_N = 10
USER_AGENT = "GroundworkKeywordScout/1.0"

DEFAULT_SEEDS: dict[str, list[str]] = {
    "money": [
        "refinance mortgage rates",
        "high yield savings account",
        "life insurance quotes",
        "how to pay off credit card debt",
    ],
    "body": [
        "best treadmill for home",
        "how to lower blood pressure",
        "daily protein intake",
        "sleep hygiene tips",
    ],
    "home": [
        "solar panel cost",
        "heat pump installation",
        "whole home generator",
        "smart door lock",
    ],
    "life": [
        "travel insurance comparison",
        "estate planning checklist",
        "how to negotiate salary",
        "best car insurance rates",
    ],
    "tech": [
        "best mesh wifi router",
        "ai note taking apps",
        "how to build a pc",
        "smart home hub comparison",
    ],
}

STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "then",
        "else",
        "for",
        "nor",
        "not",
        "so",
        "yet",
        "as",
        "at",
        "by",
        "in",
        "of",
        "on",
        "to",
        "up",
        "down",
        "with",
        "without",
        "about",
        "after",
        "before",
        "between",
        "into",
        "through",
        "during",
        "over",
        "under",
        "again",
        "further",
        "once",
        "here",
        "there",
        "all",
        "any",
        "both",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "only",
        "own",
        "same",
        "than",
        "too",
        "very",
        "can",
        "will",
        "just",
        "should",
        "now",
        "do",
        "does",
        "did",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "having",
        "i",
        "you",
        "he",
        "she",
        "it",
        "we",
        "they",
        "them",
        "his",
        "her",
        "its",
        "our",
        "your",
        "their",
        "this",
        "that",
        "these",
        "those",
        "am",
        "from",
        "off",
        "out",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "whose",
        "why",
        "how",
        "get",
        "got",
        "gets",
        "getting",
        "use",
        "used",
        "using",
        "make",
        "made",
        "making",
        "see",
        "seen",
        "know",
        "like",
        "want",
        "would",
        "could",
        "may",
        "might",
        "must",
        "way",
        "ways",
        "one",
        "two",
        "also",
        "even",
        "much",
        "many",
        "well",
        "back",
        "still",
    }
)

# ─── Intent heuristics (used when LLM enrichment is off or fails) ─────────────

INFORMATIONAL_RE = re.compile(
    r"\b(how|what|why|when|where|which|who|is|are|does|do|can|should|vs|versus)\b|how to",
    re.IGNORECASE,
)
COMMERCIAL_RE = re.compile(
    r"\b(best|cheap|affordable|top|review|reviews|compare|comparison|rating|recommend|worth|deals|discount|buy)\b",
    re.IGNORECASE,
)
TRANSACTIONAL_RE = re.compile(
    r"\b(purchase|price|cost|quote|quotes|apply|order|sign up|calculator)\b",
    re.IGNORECASE,
)
NAVIGATIONAL_RE = re.compile(
    r"\b(login|sign in|contact|official|download|near me|hours|directions|phone)\b",
    re.IGNORECASE,
)


def classify_intent(keyword: str) -> str:
    """Heuristic search-intent classification for a keyword phrase."""
    kw = keyword.lower()
    if INFORMATIONAL_RE.search(kw) or kw.endswith("?"):
        return "informational"
    if COMMERCIAL_RE.search(kw):
        return "commercial"
    if TRANSACTIONAL_RE.search(kw):
        return "transactional"
    if NAVIGATIONAL_RE.search(kw):
        return "navigational"
    return "informational"


# ─── Text extraction & scoring ────────────────────────────────────────────────

HTML_TAG_RE = re.compile(r"<[^>]+>")


def clean_html(text: str) -> str:
    """Strip HTML tags/entities and collapse whitespace."""
    text = html.unescape(text)
    text = HTML_TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokenize(text: str) -> list[str]:
    """Lowercase, split into alphanumeric tokens, filter noise."""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in tokens if t not in STOPWORDS and len(t) >= MIN_TOKEN_LENGTH and not t.isdigit()]


def extract_snippets(results: list[dict[str, Any]]) -> list[str]:
    """Pull cleaned titles + content snippets from SearXNG result dicts."""
    snippets: list[str] = []
    for result in results:
        title = clean_html(str(result.get("title", "") or ""))
        content = clean_html(str(result.get("content", "") or ""))
        if title:
            snippets.append(title)
        if content:
            snippets.append(content)
    return snippets


def extract_candidates(snippets: list[str]) -> Counter[str]:
    """Count unigrams + bigrams across all snippets (signal = frequency)."""
    counter: Counter[str] = Counter()
    for snippet in snippets:
        tokens = tokenize(snippet)
        counter.update(tokens)
        counter.update(" ".join(pair) for pair in zip(tokens, tokens[1:], strict=False))
    return counter


def filter_candidates(counter: Counter[str], min_signal: int = 2) -> list[str]:
    """Drop single-word candidates and phrases below the signal threshold."""
    return [keyword for keyword, signal in counter.items() if signal >= min_signal and len(keyword.split()) >= 2]


def normalize_keyword(keyword: str) -> str:
    """Canonical form used as the dedup key alongside pillar."""
    normalized = keyword.lower().strip()
    normalized = re.sub(r"[^a-z0-9\s-]", "", normalized)
    return re.sub(r"\s+", " ", normalized).strip()[:120]


# ─── SearXNG search ───────────────────────────────────────────────────────────


def search_searxng(
    instance: str,
    query: str,
    results_per_query: int = 10,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    retries: int = DEFAULT_RETRIES,
    language: str = "en",
) -> list[dict[str, Any]]:
    """Query the SearXNG JSON API. Retries once, then raises to the caller."""
    params: dict[str, Any] = {"q": query, "format": "json", "language": language}
    if results_per_query:
        params["number_of_results"] = results_per_query
    url = f"{instance.rstrip('/')}/search?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    attempts = retries + 1
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            results = cast(list[dict[str, Any]], data.get("results") or [])
            return results[:results_per_query] if results_per_query else results
        except Exception as e:
            if attempt == attempts - 1:
                raise
            logger.warning(f"SearXNG fetch failed for '{query}' (attempt {attempt + 1}/{attempts}): {e}")
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
    return []


# ─── LLM enrichment (best-effort intent classification) ───────────────────────

KEYWORD_INTENT_SYSTEM_PROMPT = (
    "You are an SEO keyword analyst for Groundwork, an evidence-based media "
    "platform for adults making financial, health, home, life, tech, and career decisions.\n\n"
    "Classify each keyword into exactly one search-intent bucket:\n"
    "- informational: the searcher wants to learn/understand (how, what, why questions)\n"
    "- commercial: the searcher is comparing options before buying (best, review, compare, cheap)\n"
    "- transactional: the searcher is ready to act or buy (buy, price, quote, apply, purchase)\n"
    "- navigational: the searcher wants to reach a specific site/page (login, contact, official, download)\n\n"
    "Return strict JSON only:\n"
    '{"keywords": [{"keyword": "<exact keyword>", "intent": "<intent>"}]}'
)


def enrich_intents_with_llm(
    keywords: list[str],
    fallback_chain: list[str],
    temperature: float,
    max_tokens: int,
) -> dict[str, str]:
    """Classify intent for the given keywords via LiteLLM. Never raises."""
    if not keywords:
        return {}
    user_prompt = "Classify the search intent of these keywords:\n" + "\n".join(f"- {kw}" for kw in keywords)
    for model in fallback_chain:
        try:
            response = litellm.completion(
                model=model,
                messages=[
                    {"role": "system", "content": KEYWORD_INTENT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            choices = getattr(response, "choices", None)
            message = choices[0].message if choices else None
            text = getattr(message, "content", None) if message is not None else getattr(response, "content", None)
            if not (isinstance(text, str) and text.strip()):
                raise RuntimeError("empty LLM response")
            data = json.loads(text)
            items = data.get("keywords", []) if isinstance(data, dict) else data
            if not isinstance(items, list):
                raise RuntimeError("unexpected LLM JSON shape")
            result: dict[str, str] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                kw = str(item.get("keyword", "")).strip()
                intent = str(item.get("intent", "informational")).strip()
                if kw in keywords and intent in ALLOWED_INTENTS:
                    result[kw] = intent
            return result
        except Exception as e:
            logger.warning(f"Intent classification via LLM failed with {model}: {e}")
    logger.warning("All LLM providers failed for intent enrichment — falling back to heuristics")
    return {}


# ─── Supabase persistence ─────────────────────────────────────────────────────


def fetch_existing_statuses(
    supabase: Any,
    keys: list[tuple[str, str]],
) -> dict[tuple[str, str], str]:
    """Read current status for (normalized, pillar) pairs, if present."""
    if not keys:
        return {}
    normals = sorted({n for n, _ in keys})
    result = supabase.table("keywords").select("normalized,pillar,status").in_("normalized", normals).execute()
    data = getattr(result, "data", None) or []
    return {
        (row.get("normalized"), row.get("pillar")): row.get("status")
        for row in data
        if row.get("normalized") and row.get("pillar")
    }


def upsert_keywords(supabase: Any, rows: list[dict[str, Any]]) -> int:
    """Upsert keyword rows on (normalized, pillar). Preserves existing status.

    An existing approved/published keyword is never downgraded — the current
    status is carried over; brand-new rows are inserted as 'pending'.
    """
    if not rows:
        return 0
    try:
        existing = fetch_existing_statuses(supabase, [(r["normalized"], r["pillar"]) for r in rows])
    except Exception as e:
        logger.warning(f"Could not read existing keyword statuses: {e} — treating all as new")
        existing = {}
    payload = []
    for row in rows:
        current = existing.get((row["normalized"], row["pillar"]))
        row["status"] = current if current else "pending"
        payload.append(row)
    try:
        supabase.table("keywords").upsert(payload, on_conflict="normalized,pillar").execute()
    except Exception as e:
        logger.exception(f"Keyword upsert failed: {e}")
        return 0
    return len(payload)


# ─── Main agent ───────────────────────────────────────────────────────────────


def _dedupe_candidates(candidates: list[KeywordCandidate]) -> list[KeywordCandidate]:
    """Keep the highest-signal candidate per (normalized, pillar)."""
    seen: dict[tuple[str, str], KeywordCandidate] = {}
    for candidate in candidates:
        key = (candidate.normalized, candidate.pillar)
        if key not in seen or candidate.signal > seen[key].signal:
            seen[key] = candidate
    return sorted(seen.values(), key=lambda c: c.signal, reverse=True)


def run_keyword_scout(config: dict, supabase: Any) -> dict[str, int]:
    """Agent 4: mine, score, classify, and upsert keyword candidates."""
    ks_cfg = config.get("keyword_scout", {}) or {}
    seeds_map = ks_cfg.get("seeds") or DEFAULT_SEEDS
    instance = ks_cfg.get("searxng_instance", DEFAULT_SEARXNG_INSTANCE)
    max_queries = int(ks_cfg.get("max_queries_per_run", 15))
    results_per_query = int(ks_cfg.get("results_per_query", 10))
    min_signal = int(ks_cfg.get("min_signal", 2))
    enrich = bool(ks_cfg.get("enrich_with_llm", True))
    timeout = int(ks_cfg.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    retries = int(ks_cfg.get("retries", DEFAULT_RETRIES))
    max_candidates = int(ks_cfg.get("max_candidates_per_run", 60))
    llm_cfg = config.get("llm", {}) or {}
    fallback_chain = llm_cfg.get("fallback_chain", DEFAULT_FALLBACK_CHAIN)

    stats = {
        "queries_run": 0,
        "queries_failed": 0,
        "results_total": 0,
        "candidates_extracted": 0,
        "candidates_valid": 0,
        "candidates_unique": 0,
        "upserted": 0,
    }
    raw_rows: list[dict[str, Any]] = []

    try:
        for pillar, pillar_seeds in seeds_map.items():
            if queries_cap_hit(stats["queries_run"], max_queries, len(raw_rows), max_candidates):
                break
            if isinstance(pillar_seeds, str):
                pillar_seeds = [pillar_seeds]
            for seed in pillar_seeds:
                if queries_cap_hit(stats["queries_run"], max_queries, len(raw_rows), max_candidates):
                    break
                stats["queries_run"] += 1
                logger.info(f"Keyword Scout querying SearXNG: '{seed}' (pillar={pillar})")
                try:
                    results = search_searxng(instance, seed, results_per_query, timeout=timeout, retries=retries)
                except Exception as e:
                    stats["queries_failed"] += 1
                    logger.warning(f"SearXNG query failed for seed '{seed}': {e}")
                    continue
                if not results:
                    logger.info(f"No results returned for seed '{seed}'")
                    continue
                stats["results_total"] += len(results)
                counter = extract_candidates(extract_snippets(results))
                for keyword in filter_candidates(counter, min_signal):
                    if len(raw_rows) >= max_candidates:
                        break
                    raw_rows.append(
                        {
                            "keyword": keyword,
                            "normalized": normalize_keyword(keyword),
                            "pillar": pillar,
                            "signal": counter[keyword],
                        }
                    )
    except Exception as e:
        logger.exception(f"Keyword Scout query loop failed: {e}")

    stats["candidates_extracted"] = len(raw_rows)

    try:
        if not raw_rows:
            logger.info("Keyword Scout found no candidates this run")
            return stats

        valid_rows: list[KeywordCandidate] = []
        for row in raw_rows:
            try:
                valid_rows.append(KeywordCandidate.model_validate(row))
            except Exception as e:
                logger.warning(f"Skipping invalid keyword row {row.get('keyword')!r}: {e}")
        stats["candidates_valid"] = len(valid_rows)

        valid_rows = _dedupe_candidates(valid_rows)
        stats["candidates_unique"] = len(valid_rows)

        llm_intents: dict[str, str] = {}
        if enrich and valid_rows:
            top = [c.keyword for c in valid_rows[:ENRICH_TOP_N]]
            llm_intents = enrich_intents_with_llm(
                top,
                fallback_chain,
                float(llm_cfg.get("temperature", 0.2)),
                800,
            )
        for candidate in valid_rows:
            candidate.intent = llm_intents.get(candidate.keyword) or classify_intent(candidate.keyword)

        stats["upserted"] = upsert_keywords(supabase, [c.model_dump() for c in valid_rows])
    except Exception as e:
        logger.exception(f"Keyword Scout finalize failed: {e}")

    logger.info(
        f"Keyword Scout complete: {stats['upserted']} upserted from {len(raw_rows)} candidates "
        f"({stats['queries_failed']} failed queries)"
    )
    return stats


def queries_cap_hit(queries_run: int, max_queries: int, candidates: int, max_candidates: int) -> bool:
    """True when either the query budget or candidate budget is exhausted."""
    return queries_run >= max_queries or candidates >= max_candidates


def _sanitize_error(error: Exception) -> str:
    """Scrub potential secrets from an error message before persisting to DB."""
    text = str(error)[:2000]
    text = re.sub(r"(?i)(api[_-]?key|token|secret|authorization|password)\s*[=:]\s*\S+", r"\1=[REDACTED]", text)
    text = re.sub(r"(?i)\b(token|secret|apikey|api_key)\s+\S+", r"\1 [REDACTED]", text)
    text = re.sub(r"Bearer\s+\S+", "Bearer [REDACTED]", text)
    return text


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yml")
    with open(config_path) as f:
        config = yaml.safe_load(f)

    supabase_url = os.environ["SUPABASE_URL"]
    supabase_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    supabase = create_client(supabase_url, supabase_key)

    run_log: dict[str, Any] = {
        "agent": "keyword_scout",
        "status": "running",
        "items_processed": 0,
        "items_published": 0,
        "run_at": datetime.now(UTC).isoformat(),
    }

    try:
        stats = run_keyword_scout(config, supabase)
        run_log["status"] = "success"
        run_log["items_processed"] = stats.get("upserted", 0)
        print(f"Keyword Scout summary: {json.dumps(stats)}")
    except Exception as e:
        run_log["status"] = "error"
        run_log["error_log"] = _sanitize_error(e)
        logger.exception(f"Keyword Scout FAILED: {e}")
        sys.exit(1)
    finally:
        try:
            supabase.table("pipeline_runs").insert(run_log).execute()
        except Exception as e:
            logger.warning(f"Failed to log keyword scout run: {e}")


if __name__ == "__main__":
    main()
