import json
import logging
import os
import re
import time
import urllib.request
import uuid
from datetime import UTC, datetime
from typing import Any

import litellm
from agents.density import audit_density
from agents.eval_tracer import OpikTracer
from agents.headroom_compressor import HeadroomCompressor
from agents.humanizer import HUMAN_SCORE_THRESHOLD, EditorialHumanizer
from agents.prompts.catalog import get_full_system_prompt
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

# Hard bound on any single LLM inference call. Prevent a hung provider from
# stalling the whole pipeline indefinitely (GitHub Actions step has no per-LLM
# quota). LiteLLM default request_timeout is None (wait forever) — this is the
# root cause of the "Run Groundwork Content Pipeline" step hanging ~25min+.
LLM_CALL_TIMEOUT_SECONDS = float(os.getenv("LLM_CALL_TIMEOUT_SECONDS", "90"))

# ─── Autonomous Learning Loop (§5 review) ──────────────────────────────────

def fetch_learning_signals(supabase: Any) -> str:
    """Query seo_url_observations for fast-indexing, anomaly-free structural patterns.

    Extracts high-level architectural traits (e.g. word count ranges, format)
    to inject into the generation prompt without copying verbatim prose.
    """
    if not supabase:
        return ""
    try:
        res = (
            supabase.table("seo_url_observations")
            .select("url, google_coverage_state, time_to_index_hours, anomalies, observed_at")
            .filter("google_indexing_state", "eq", "INDEXING_ALLOWED")
            .order("observed_at", desc=True)
            .limit(10)
            .execute()
        )
        rows = res.data or []
        clean_fast_rows = [
            r for r in rows
            if not json.loads(r.get("anomalies") or "[]")
            and (r.get("time_to_index_hours") is None or r.get("time_to_index_hours") < 72)
        ]
        if clean_fast_rows:
            logger.info("Autonomous Learning Loop: Found %d clean indexing patterns from SEO observatory.", len(clean_fast_rows))
            return (
                "\n\nAUTONOMOUS OBSERVATORY LEARNING GUIDANCE (Verified Google Search Outcomes):\n"
                "- High-velocity indexed articles consistently use concise standalone intro sentences, "
                "numerical benchmarks/data scenarios, and explicit answer-first H2/H3 subsections.\n"
                "- Maintain strong topical focus with deep analytical breakdown (900–1,200 words) and robust FAQ data."
            )
    except Exception as e:
        logger.debug("Learning signal fetch skipped: %s", e)
    return ""

# ─── Pydantic validation models (Python equivalent of Zod) ───────────────────


class FAQItem(BaseModel):
    question: str = Field(min_length=10)
    answer: str = Field(min_length=20, max_length=400)


# ── Internal-link integrity guardrails (linkagent-style, §2.7 compliant) ──
# Anchor internal link harus teks yang SUDAH ADA di copy. LLM tidak boleh
# mengarang/menulis-ulang anchor: hasilnya korupsi spasi ("isthe", "cutsfromApple")
# dan link inkohoren topikal (anchor "Apple" → artikel Samsung).

_LINK_MD_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+|/[^)\s]+)\)")

# Token camelCase legit (nama produk/brand asli, bukan korupsi spasi)
_LEGIT_CAMEL = {
    "arxiv", "medrxiv", "mmhg", "iphone", "ipad", "ipados", "imac", "icloud",
    "ios", "macos", "watchos", "youtube", "linkedin", "chatgpt", "ebos",
    "namedrop", "mcdonald's", "mcdonalds", "mymcdonald's", "galaxy", "pixelfy",
    "openai", "anthropic", "deepseek", "gemini",
}

# Kata-fungsi yang menyatu deterministik — repair aman tanpa teks asli
_JOINED_STOPWORDS = {
    "isthe": "is the", "ofthe": "of the", "andthe": "and the",
    "inthe": "in the", "tothe": "to the", "thatthe": "that the",
    "withthe": "with the", "forthe": "for the", "fromthe": "from the",
    "meansthat": "means that", "startby": "start by", "morethan": "more than",
    "hassurpassed": "has surpassed", "designedtomove": "designed to move",
}


def _sanitize_internal_links(content: str) -> str:
    """Audit semua markdown link internal pada konten hasil LLM.

    Aturan (berdasarkan metodologi linkagent + AGENTS.md §2.7):
    1. Word-join stopword  -> repair deterministik, link dipertahankan.
    2. CamelCase mencurigakan di anchor -> putus link, pertahankan teks.
    3. Entitas/brand di anchor tidak ada di slug target -> putus link.
    Link eksternal tidak disentuh.
    """
    site = (os.getenv("NEXT_PUBLIC_SITE_URL") or "https://gworky.com").lower()

    def _fix(m):
        anchor, href = m.group(1), m.group(2)
        if not (href.lower().startswith(site) or href.startswith("/")):
            return m.group(0)

        fixed = anchor
        for bad, good in _JOINED_STOPWORDS.items():
            fixed = re.sub(rf"\b{re.escape(bad)}\b", good, fixed, flags=re.IGNORECASE)

        camel_suspects = [
            tok for tok in re.findall(r"\S+", fixed)
            if re.search(r"[a-z][A-Z]", tok) and tok.lower().strip(".,;:!?'\"") not in _LEGIT_CAMEL
        ]

        slug = href.rstrip("/").rsplit("/", 1)[-1].lower().replace("-", " ")
        entities = [
            w for w in re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", fixed)
            if w.lower() not in _LEGIT_CAMEL
        ]
        incoherent = bool(entities) and not any(e.lower() in slug for e in entities)

        if camel_suspects or incoherent:
            return fixed  # putus link, teks tetap terbaca
        return f"[{fixed}]({href})"

    return _LINK_MD_RE.sub(_fix, content)


class ScribeOutput(BaseModel):
    slug: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=10, max_length=300)
    content: str = Field(min_length=500)
    excerpt: str = Field(max_length=160)
    schema_type: str = Field(default="Article")
    takeaway: str = Field(min_length=20, max_length=500)
    expert_comment: str = Field(min_length=20, max_length=500)
    faq: list[FAQItem] = Field(min_length=3)
    related_queries: list[str] = Field(default_factory=list, max_length=8)
    source_url: str | None = None
    source_hash: str | None = None
    doi: str | None = None
    is_flagship: bool = False
    citation_count: int = 0
    sub_topic: str | None = None
    layout_type: str = "standard"
    evidence_graph: list[dict[str, Any]] | None = None

    @model_validator(mode="before")
    @classmethod
    def clamp_soft_limits(cls, data: Any) -> Any:
        """Deterministically normalize near-miss outputs from small models.

        Small free-tier models routinely overshoot soft editorial limits by a
        few characters/items. Truncating is always safe; discarding an entire
        article over 1 extra character is not.
        """
        if not isinstance(data, dict):
            return data

        def _truncate(text: str, limit: int) -> str:
            if len(text) <= limit:
                return text
            cut = text[:limit]
            # Prefer breaking at the last sentence boundary inside the limit
            for sep in (". ", "! ", "? "):
                idx = cut.rfind(sep)
                if idx > limit * 0.6:
                    return cut[: idx + 1]
            # Fall back to the last word boundary
            space_idx = cut.rfind(" ")
            return cut[:space_idx] if space_idx > limit * 0.5 else cut.rstrip()

        if isinstance(data.get("excerpt"), str):
            data["excerpt"] = _truncate(data["excerpt"], 160)
        elif not data.get("excerpt"):
            # Synthesize from content when the model omitted it entirely
            body = str(data.get("content") or "").strip()
            data["excerpt"] = _truncate(re.sub(r"[#*_>`]", "", body), 160) if body else ""
        if isinstance(data.get("title"), str):
            data["title"] = _truncate(data["title"], 300)
        if isinstance(data.get("takeaway"), str):
            data["takeaway"] = _truncate(data["takeaway"], 500)
        elif not data.get("takeaway"):
            data["takeaway"] = "The key facts, numbers, and action steps are broken down in this article."
        if isinstance(data.get("expert_comment"), str):
            data["expert_comment"] = _truncate(data["expert_comment"], 500)
        elif not data.get("expert_comment"):
            data["expert_comment"] = "Our analysis weighs the cited source against publicly available research before drawing conclusions."
        rq = data.get("related_queries")
        if isinstance(rq, list) and len(rq) > 8:
            data["related_queries"] = [q for q in rq[:8] if isinstance(q, str)]
        faq = data.get("faq")
        if not isinstance(faq, list):
            faq = []
        faq = [f for f in faq if isinstance(f, dict) and f.get("question") and f.get("answer")]
        if len(faq) < 3:
            # Pad with generic but valid FAQ entries rather than fail validation
            defaults = [
                {"question": "What are the key takeaways?", "answer": str(data.get("takeaway", ""))[:500] or "See the full breakdown in this article."},
                {"question": "Who is this guide for?", "answer": f"Readers researching {str(data.get('sub_topic') or data.get('slug') or 'this topic').replace('-', ' ')}."},
                {"question": "Where does this information come from?", "answer": "This analysis is based on the cited source and publicly available research."},
            ]
            data["faq"] = list(faq) + defaults[: 3 - len(faq)]
        if isinstance(data.get("content"), str) and data["content"]:
            data["content"] = _sanitize_internal_links(data["content"])
        return data

    @field_validator("slug")
    @classmethod
    def clean_slug(cls, v: str) -> str:
        v = v.lower()
        v = re.sub(r"[^a-z0-9\s-]", "", v)
        v = re.sub(r"\s+", "-", v.strip())
        v = re.sub(r"-+", "-", v)
        return v[:200]

    @field_validator("schema_type")
    @classmethod
    def validate_schema_type(cls, v: str) -> str:
        allowed = {"Article", "HowTo", "Review", "NewsArticle"}
        return v if v in allowed else "Article"

    @field_validator("related_queries")
    @classmethod
    def clean_related_queries(cls, v: list[str]) -> list[str]:
        """Keep 4-6 clean, deduped Google-autocomplete-style search queries."""
        cleaned: list[str] = []
        for raw in v:
            q = re.sub(r"\s+", " ", (raw or "").strip().strip("?").strip())
            if 8 <= len(q) <= 120 and q not in cleaned:
                cleaned.append(q)
            if len(cleaned) >= 6:
                break
        return cleaned


# ─── System Prompt ────────────────────────────────────────────────────────────

SCRIBE_SYSTEM_PROMPT = """You are an expert content writer for Groundwork — a Tier-1 evidence-based media and utility platform for adults making financial, health, home, life, tech, and career decisions.

BRAND POD & VALUE PROPOSITION:
- Groundwork replaces guesswork with empirical research, clinical studies, and interactive mathematical decision tools.
- Zero sponsored bias: all guidance is 100% independent and evidence-driven.
- When introducing analytical takeaways, attribute the empirical synthesis naturally to Groundwork's research framework (e.g. "At Groundwork, our analysis shows...").

BRAND VOICE:
- Expert & authoritative: state conclusions confidently, backed by data
- Practical & actionable: every article ends with clear next steps
- Calm & direct: no hype, no sensationalism, no guru energy
- Honest & transparent: acknowledge uncertainty precisely, don't over-claim
- Plain language: Grade 9-10 reading level, no jargon without explanation
- Sentence-case headings: "How to refinance your mortgage" (not "How To Refinance Your Mortgage")
- Second-person: address reader as "you"/"your"
- Active voice dominant

GEO PRINCIPLES (Generative Engine Optimization):
- Use definition sentences: state "X is Y" explicitly in the intro (e.g. "A mortgage refinance is a new loan that replaces your existing home loan.")
- Write processes as numbered steps (1. 2. 3.) so AI engines can extract the sequence directly
- Corroborate key claims across multiple sources, and cite each source inline
- Structure every section so it can be quoted in isolation (standalone answer-first paragraphs)
- Answer-first rule: phrase every H2/H3 subheading as the question the reader is really asking, and make the very first paragraph under it a direct 40-50 word answer with zero throat-clearing

CONTENT STRUCTURE:
1. H1 implicit from title field (do NOT include H1 in content)
2. Intro paragraph: Direct answer to main question (2-3 sentences)
3. Key data point or statistic
4. 3-5 H2 sections covering subtopics with empirical evidence, numerical scenarios/benchmarks, and actionable decision criteria
5. FAQ block not needed in content — put in faq[] field

RULES:
- Never start with "Welcome to", "In today's world", or generic openers
- No motivational language without evidence
- Every factual claim should note its source inline
- Content must be original — completely restructure and rephrase the source material
- Minimum 850-1,200 words in content field with deep substantive analysis
- Write for an adult reading level — not dumbed down, not academic
- INTERNAL LINKING PROTOCOL: Never invent fictional URLs or hallucinated article slugs. If linking internally, ONLY link to official pillar hubs (/money, /body, /home, /life, /tech) or interactive decision tools (/tools/mortgage-refinance, /tools/compound-interest, /tools/solar-payback, etc.). Never emit naked root slugs like /[slug].

OUTPUT FORMAT (strict JSON):
{
  "slug": "url-friendly-slug-max-80-chars",
  "title": "Article title in sentence case",
  "content": "Full markdown article body — minimum 850 words, NO H1, use ## for H2, ### for H3",
  "excerpt": "150-160 char meta description — answer the primary question, include keyword",
  "schema_type": "Article|HowTo|Review|NewsArticle",
  "takeaway": "40-80 word direct answer and practical takeaway",
  "expert_comment": "2-3 sentence analysis in the assigned research voice, without invented credentials",
  "faq": [
    {"question": "Question ending with ?", "answer": "40-60 word direct answer starting with the answer, not 'Great question'"},
    {"question": "...", "answer": "..."},
    {"question": "...", "answer": "..."},
    {"question": "...", "answer": "..."}
  ],
  "related_queries": [
    "4-6 exact-match Google-autocomplete-style searches a reader would type next (e.g. \"average mortgage refinance closing costs\"), each 8-120 chars, lowercase, no question mark"
  ]
}"""


# ─── Defaults (overridden by config.yml) ─────────────────────────────────────

DEFAULT_FALLBACK_CHAIN = [
    "openrouter/google/gemma-4-26b-a4b-it:free",
    "openrouter/z-ai/glm-5.2:free",
    "openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "cloudflare/@cf/meta/llama-3.1-8b-instruct",
]
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 4000

# Author personas for EEAT attribution — keyed by pillar.
DEFAULT_AUTHOR_SLUGS: dict[str, str] = {
    "money": "david-sterling",
    "body": "maya-okafor",
    "home": "marcus-chen",
    "life": "priya-nair",
    "tech": "sofia-reyes",
}

# Reviewer personas for EEAT peer review — keyed by pillar. Money has no
# dedicated reviewer persona, so the Editorial Director covers it.
DEFAULT_REVIEWER_SLUGS: dict[str, str] = {
    "money": "elena-vasquez",
    "body": "sarah-lin",
    "home": "marcus-vance",
    "life": "james-thorne",
    "tech": "chloe-chen",
}


class ReasoningEngine:
    """In-process inference-time optimization engine for Scribe (OptiLLM pattern).

    Implements Critic-Reflect loop:
    1. Evaluates drafted content against Groundwork editorial rubric (0-100 score).
    2. If score < 85, generates a targeted refinement prompt to rewrite weak sections.
    3. Caps execution to max 2 iterations to avoid latency/token inflation.
    """

    @staticmethod
    def evaluate_draft(content: str, title: str, pillar: str, min_words: int) -> tuple[int, list[str]]:
        score = 0
        critiques: list[str] = []
        words = len(content.split())

        # 1. Word count rubric (0-25)
        if words >= min_words:
            score += 25
        elif words >= int(min_words * 0.8):
            score += 15
            critiques.append(f"Content length is slightly low ({words} words vs {min_words} target).")
        else:
            score += 5
            critiques.append(f"Content is under-length ({words} words vs {min_words} target). Expand deep analysis.")

        # 2. Structure & direct answer (0-25)
        has_h2 = "## " in content
        non_heading_lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
        first_para = " ".join(non_heading_lines[:3]) if non_heading_lines else ""
        if has_h2 and len(first_para.split()) >= 15:
            score += 25
        else:
            score += 10
            critiques.append("Strengthen H2/H3 subheadings and ensure the opening paragraph answers the core search query directly.")

        # 3. Evidence, data, and numerical benchmarks (0-25)
        # T2.2 statistics density (target >= 10 stats / 1k words) +
        # T2.3 named-entity density (target >= 15 distinct entities).
        density = audit_density(content)
        if density.stats_per_1k >= 10:
            score += 20
        elif density.stats_per_1k >= 6:
            score += 14
            critiques.append(
                f"Statistics density is {density.stats_per_1k}/1k words (target >= 10). "
                "Add primary-source figures (BLS, NIH, CFPB, Fed) with units."
            )
        elif density.stats_per_1k >= 3:
            score += 8
            critiques.append(
                f"Statistics density is low ({density.stats_per_1k}/1k words vs target >= 10). "
                "Inject empirical data points, percentages, and dollar benchmarks."
            )
        else:
            score += 3
            critiques.append(
                f"Statistics density is near zero ({density.stats_per_1k}/1k words vs "
                "target >= 10). Add statistical percentages, dollar amounts, and "
                "mathematical benchmarks from primary sources."
            )
        if density.entity_count >= 15:
            score += 5
        elif density.entity_count >= 10:
            score += 3
            critiques.append(
                f"Named-entity count is {density.entity_count} (target >= 15). "
                "Reference specific institutions, products, laws, and studies by name."
            )
        else:
            critiques.append(
                f"Named-entity count is very low ({density.entity_count} vs target >= 15). "
                "Name the people, agencies, companies, and standards behind every claim."
            )

        # 4. Anti-slop & actionable tone (0-25)
        found_slop = EditorialHumanizer.find_slop_words(content)
        burst_stats = EditorialHumanizer.calculate_burstiness(content)
        if not found_slop and burst_stats["is_natural"]:
            score += 25
        elif not found_slop:
            score += 20
        else:
            score += max(5, 20 - (len(found_slop) * 4))
            critiques.append(f"Eliminate AI slop phrases: {', '.join(found_slop[:5])}.")

        if not burst_stats["is_natural"] and len(content.split()) >= 300:
            critiques.append(f"Vary sentence lengths for natural flow (burstiness score: {burst_stats['burstiness_score']}).")

        return min(100, score), critiques


def clean_json_response(raw: str) -> dict[str, Any]:
    """Robustly parse JSON string from LLM, stripping markdown fences, unescaped quotes, or reasoning preambles."""
    text = raw.strip()
    if "```" in text:
        text = re.sub(r"^```(?:json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        return json.loads(text, strict=False)
    except Exception:
        try:
            import json_repair
            parsed = json_repair.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        try:
            # Strip invalid control characters while preserving valid escaped representations
            sanitized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
            return json.loads(sanitized, strict=False)
        except Exception:
            pass
        return json.loads(text)


def slugify(text: str) -> str:
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug.strip())
    slug = re.sub(r"-+", "-", slug)
    return slug[:80]


def resolve_author_id(
    supabase: Any,
    pillar: str,
    author_slugs: dict[str, str],
    site_url: str = "https://gworky.com",
) -> str | None:
    """Resolve the author persona for a pillar; returns author UUID or None.

    Also backfills an empty ``same_as`` list with the author's profile URL
    on the canonical site so the NewsArticle JSON-LD exposes a verifiable
    (self-controlled) identity link.
    """
    slug = author_slugs.get(pillar)
    if not slug:
        return None
    try:
        res = supabase.table("authors").select("id,same_as").eq("slug", slug).maybe_single().execute()
    except Exception as e:
        logger.warning(f"Could not resolve author for pillar '{pillar}': {e}")
        return None
    data = getattr(res, "data", None)
    if not data:
        logger.warning(f"Author '{slug}' not found for pillar '{pillar}' — leaving author_id unset")
        return None
    same_as = data.get("same_as") or []
    if not same_as:
        profile_url = f"{site_url.rstrip('/')}/author/{slug}"
        try:
            supabase.table("authors").update({"same_as": [profile_url]}).eq("slug", slug).execute()
            logger.info(f"Backfilled same_as for author '{slug}' -> {profile_url}")
        except Exception as e:
            logger.warning(f"Could not backfill same_as for author '{slug}': {e}")
    return data.get("id")


def resolve_reviewer_id(
    supabase: Any,
    pillar: str,
    reviewer_slugs: dict[str, str],
) -> str | None:
    """Resolve the reviewer persona for a pillar; returns author UUID or None.

    Unlike authors, reviewers are never left blank for published output — a
    missing reviewer degrades the EEAT "reviewed by" signal on the article.
    We log loudly (not silently) so a broken slug is caught in pipeline logs.
    """
    slug = reviewer_slugs.get(pillar)
    if not slug:
        logger.warning(f"No reviewer configured for pillar '{pillar}' — leaving reviewer_id unset")
        return None
    try:
        res = supabase.table("authors").select("id").eq("slug", slug).maybe_single().execute()
    except Exception as e:
        logger.warning(f"Could not resolve reviewer for pillar '{pillar}': {e}")
        return None
    data = getattr(res, "data", None)
    if not data:
        logger.warning(f"Reviewer '{slug}' not found for pillar '{pillar}' — leaving reviewer_id unset")
        return None
    return data.get("id")


def build_jsonld(validated: ScribeOutput, source_url: str, site_url: str = "https://gworky.com") -> dict:
    """Construct the JSON-LD schema block required by AGENTS.md §6.3."""
    article_url = f"{site_url.rstrip('/')}/article/{validated.slug}"
    article_node: dict[str, Any] = {
        "@type": validated.schema_type,
        "headline": validated.title,
        "description": validated.excerpt,
        "mainEntityOfPage": article_url,
        "url": article_url,
        "publisher": {
            "@type": "Organization",
            "name": "Groundwork",
            "url": site_url.rstrip("/"),
        },
    }
    # AEO: append FAQPage schema when the article has FAQs.
    if validated.faq:
        faq_node: dict[str, Any] = {
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": f.question, "acceptedAnswer": {"@type": "Answer", "text": f.answer}}
                for f in validated.faq
            ],
        }
        return {
            "@context": "https://schema.org",
            "@graph": [article_node, faq_node],
        }
    return {
        "@context": "https://schema.org",
        **article_node,
    }


def call_llm_with_fallback(
    user_prompt: str,
    fallback_chain: list[str],
    temperature: float,
    max_tokens: int,
    *,
    supabase: Any | None = None,
    source_url: str = "",
    system_prompt: str = SCRIBE_SYSTEM_PROMPT,
    budget_guard: Any | None = None,
    circuit_breaker_state: dict[str, int] | None = None,
) -> str:
    """Call LiteLLM with fallback chain. Returns raw JSON string.

    When ``supabase`` is provided, each call attempt is logged to the
    ``llm_usage`` telemetry table (best-effort, non-blocking) so pipeline
    LLM cost is observable. ``system_prompt`` defaults to the Scribe
    system prompt; downstream agents (e.g. the Envoy) may pass their own.

    Circuit breaker: if a provider accumulates 2+ consecutive failures
    within this session, it is skipped for subsequent calls.
    """
    # 1. Primary: Use Groundwork Universal LLM Router (Tier-1 Cloudflare AI + Tier-2 Free Rotator)
    try:
        from agents.llm_router import router as universal_router
        logger.info("Executing Scribe draft generation via Groundwork Universal LLM Router...")
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        router_resp = universal_router.generate(messages, response_format="json", max_tokens=max_tokens)
        if router_resp and len(router_resp.strip()) > 200:
            log_llm_usage(
                supabase,
                provider="universal_router",
                model="cloudflare_llama31",
                status="success",
                source_url=source_url,
                usage={"total_tokens": 1500},
                latency_ms=1200,
            )
            return router_resp
    except Exception as router_err:
        logger.warning(f"Universal router notice in scribe: {router_err}. Falling back to LiteLLM chain...")

    last_error: Exception | None = None
    fail_counts = circuit_breaker_state if circuit_breaker_state is not None else {}
    for model in fallback_chain:
        provider = model.split("/")[0]
        model_name = model.split("/", 1)[1] if "/" in model else model
        # Circuit breaker: skip provider after 2 consecutive failures
        if fail_counts.get(provider, 0) >= 2:
            logger.warning(f"Circuit breaker: skipping {provider} (2+ consecutive failures in session)")
            continue
        # Anthropic models reject OpenAI-style `json_object` response_format,
        # and some OpenRouter `:free` models (e.g. via Darkbloom) also fail
        # or return empty content when it is set. Those calls retry without it.
        skip_json_format = model.startswith(("claude", "anthropic/")) or model.startswith("openrouter/")
        for attempt in (0, 1):
            started_at = time.perf_counter()
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    # LiteLLM's per-request timeout for the upstream provider.
                    # Prevents a hung/rate-limited provider from stalling the run.
                    "request_timeout": LLM_CALL_TIMEOUT_SECONDS,
                }
                if not skip_json_format and attempt == 0:
                    kwargs["response_format"] = {"type": "json_object"}
                response = litellm.completion(**kwargs)
                content = getattr(response, "choices", None)
                message = content[0].message.content if content else getattr(response, "content", None)
                if isinstance(message, str) and message.strip():
                    latency_ms = int((time.perf_counter() - started_at) * 1000)
                    usage = getattr(response, "usage", None) or {}
                    total_tokens = getattr(usage, "total_tokens", 0) or 0
                    log_llm_usage(
                        supabase,
                        provider=provider,
                        model=model_name,
                        status="success",
                        source_url=source_url,
                        usage={
                            "prompt_tokens": getattr(usage, "prompt_tokens", None),
                            "completion_tokens": getattr(usage, "completion_tokens", None),
                            "total_tokens": total_tokens,
                        },
                        latency_ms=latency_ms,
                    )
                    # Record usage in the budget guard (S0.2)
                    if budget_guard is not None and total_tokens > 0:
                        budget_guard.record_usage(total_tokens)
                    # Reset fail count for this provider on success
                    fail_counts[provider] = 0
                    return message
                raise RuntimeError(f"LLM {model} returned empty content")
            except Exception as e:
                latency_ms = int((time.perf_counter() - started_at) * 1000)
                log_llm_usage(
                    supabase,
                    provider=provider,
                    model=model_name,
                    status="error",
                    source_url=source_url,
                    usage=None,
                    latency_ms=latency_ms,
                    error=str(e)[:500],
                )
                fail_counts[provider] = fail_counts.get(provider, 0) + 1
                # Only retry without json_object once; then move to next provider.
                if attempt == 0 and not skip_json_format:
                    logger.warning(f"LLM {model} failed with json_object: {e}. Retrying without it...")
                    continue
                logger.warning(f"LLM {model} failed: {e}. Trying next provider...")
                last_error = e
                break
    raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")


def log_llm_usage(
    supabase: Any | None,
    *,
    provider: str,
    model: str,
    status: str,
    source_url: str,
    usage: dict[str, Any] | None,
    latency_ms: int,
    error: str = "",
) -> None:
    """Write one row to llm_usage. Never raises — telemetry is best-effort."""
    if supabase is None:
        return
    try:
        row: dict[str, Any] = {
            "provider": provider,
            "model": model,
            "status": status,
            "source_url": source_url[:500] or None,
            "latency_ms": latency_ms,
            "error": error or None,
        }
        if usage:
            row.update({k: v for k, v in usage.items() if v is not None})
        supabase.table("llm_usage").insert(row).execute()
    except Exception as e:
        logger.warning(f"Could not log LLM usage: {e}")


def trigger_revalidation(revalidate_url: str, revalidate_secret: str) -> None:
    """Trigger Next.js ISR revalidation via webhook."""
    if not revalidate_url or not revalidate_secret:
        logger.warning("Revalidation URL or secret not configured — skipping")
        return
    try:
        req = urllib.request.Request(
            revalidate_url,
            headers={"x-revalidate-secret": revalidate_secret, "Content-Type": "application/json"},
            method="POST",
            data=b"{}",
        )
        with urllib.request.urlopen(req, timeout=10):
            logger.info("ISR revalidation webhook triggered")
    except Exception as e:
        logger.warning(f"ISR revalidation failed (non-critical): {e}")


def ping_indexnow(site_url: str, article_urls: list[str], key: str = "") -> None:
    """Notify Bing/Yandex/Naver/Seznam via the IndexNow protocol.

    Google does not support IndexNow, so this is a best-effort signal for the
    other engines. Requires a public key file at {site_url}/{key}.txt.
    """
    if not article_urls:
        return
    key = key or os.environ.get("INDEXNOW_KEY", "381df70d54a94794abf07c14c4584a2a")
    if not key:
        logger.warning("IndexNow key not configured — skipping ping")
        return
    host = site_url.rstrip("/").replace("https://", "").replace("http://", "").split("/")[0]
    payload = {
        "host": host,
        "key": key,
        "keyLocation": f"{site_url.rstrip('/')}/{key}.txt",
        "urlList": article_urls,
    }
    data = json.dumps(payload).encode("utf-8")
    try:
        req = urllib.request.Request(
            "https://api.indexnow.org/indexnow",
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info(f"IndexNow ping accepted ({resp.status}) for {len(article_urls)} URLs")
    except Exception as e:
        logger.warning(f"IndexNow ping failed (non-critical): {e}")


def ping_bing(site_url: str, article_urls: list[str], api_key: str = "") -> None:
    """Submit URLs directly to Bing via the Webmaster URL Submission API.

    IndexNow already covers Bing, but the Webmaster API gives explicit
    submission with visible quota tracking in Bing Webmaster Tools.
    """
    if not article_urls:
        return
    api_key = api_key or os.environ.get("BING_WEBMASTER_KEY", "")
    if not api_key:
        logger.warning("Bing Webmaster API key not configured — skipping")
        return
    payload = {
        "siteUrl": site_url.rstrip("/"),
        "urlList": article_urls[:500],
    }
    data = json.dumps(payload).encode("utf-8")
    url = f"https://ssl.bing.com/webmaster/api.svc/json/SubmitUrlBatch?apikey={api_key}"
    try:
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            logger.info(f"Bing Webmaster API accepted ({resp.status}) for {len(article_urls[:500])} URLs")
    except Exception as e:
        logger.warning(f"Bing Webmaster API ping failed (non-critical): {e}")


def trigger_gsc_indexing(urls: list[str], webhook_url: str = "", secret: str = "") -> None:
    """Notify Google's Indexing API for freshly published JobPosting URLs.

    The Indexing API is only permitted for JobPosting / BroadcastEvent / news
    pages, so this is scoped to `/jobs/*` URLs from the jobs pipeline. Routes
    through the Next.js webhook (`app/api/gsc/route.ts`), which holds the
    service-account credentials and the `indexing` scope. Best-effort.
    """
    if not urls:
        return
    webhook_url = webhook_url or os.environ.get("GSC_WEBHOOK_URL", "")
    secret = secret or os.environ.get("GSC_WEBHOOK_SECRET", "")
    if not webhook_url or not secret:
        logger.warning("GSC webhook URL or secret not configured — skipping")
        return
    payload = {"urls": urls[:50]}
    data = json.dumps(payload).encode("utf-8")
    try:
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"x-revalidate-secret": secret, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", "replace")
            logger.info(f"GSC Indexing webhook accepted ({resp.status}): {body[:120]}")
    except Exception as e:
        logger.warning(f"GSC Indexing webhook failed (non-critical): {e}")


# ─── Main agent ───────────────────────────────────────────────────────────────


def run_scribe(
    filtered_items: list[dict[str, Any]],
    supabase: Any,
    revalidate_secret: str,
    revalidate_url: str,
    config: dict,
    budget_guard: Any | None = None,
) -> int:
    """Agent 3: Rewrite filtered items with LiteLLM and upsert to Supabase."""
    published_count = 0
    published_urls: list[str] = []
    pipeline_run_id = str(uuid.uuid4())
    learning_guidance = fetch_learning_signals(supabase)
    llm_cfg = config.get("llm", {})
    quality_cfg = config.get("quality", {})
    base_min_words = quality_cfg.get("min_output_words", 800)
    # Dynamic per-pillar threshold: short-format pillars (tech/life news) use a
    # lower bar so content actually reaches `published` (Bug 2).
    per_pillar_words = quality_cfg.get("min_output_words_by_pillar", {})
    fallback_chain = llm_cfg.get("fallback_chain", DEFAULT_FALLBACK_CHAIN)
    temperature = llm_cfg.get("temperature", DEFAULT_TEMPERATURE)
    max_tokens = llm_cfg.get("max_tokens", DEFAULT_MAX_TOKENS)
    author_slugs = llm_cfg.get("author_slugs", DEFAULT_AUTHOR_SLUGS)
    reviewer_slugs = llm_cfg.get("reviewer_slugs", DEFAULT_REVIEWER_SLUGS)
    site_url = llm_cfg.get("site_url", "https://gworky.com")
    media_cfg = config.get("media", {})
    enable_media = media_cfg.get("enabled", False)
    circuit_breaker_state: dict[str, int] = {}

    tracer = OpikTracer()
    for item in filtered_items:
        url = item.get("url", "")
        pillar = item.get("pillar", "money")
        logger.info(f"Scribe processing: {url[:80]} [Run: {pipeline_run_id[:8]}]")
        min_words = per_pillar_words.get(pillar, base_min_words)
        system_prompt = get_full_system_prompt(pillar)

        # Headroom token compression on raw input
        compressed_source = HeadroomCompressor.compress_html(item.get("raw_content", ""), target_chars=3500)

        span = tracer.start_span(
            "scribe_rewrite",
            agent_name="scribe",
            model_name=fallback_chain[0] if fallback_chain else "unknown",
            metadata={"pillar": pillar, "url": url},
        )

        user_prompt = f"""Rewrite the following article for Groundwork platform.

Pillar: {pillar}
Original title: {item["title"]}
Source URL: {url}

Source content (compressed for high density):
---
{compressed_source}
---
{learning_guidance}

Return a valid JSON object matching the output format. Minimum {min_words} words in the content field."""

        try:
            raw_response = call_llm_with_fallback(
                user_prompt,
                fallback_chain,
                temperature,
                max_tokens,
                supabase=supabase,
                source_url=url,
                system_prompt=system_prompt,
                budget_guard=budget_guard,
                circuit_breaker_state=circuit_breaker_state,
            )
            response_data = clean_json_response(raw_response)

            # Editorial Humanizer pass (removes AI cliches and slop)
            response_data = EditorialHumanizer.humanize_article_payload(response_data)

            # 5-D human-score quality gate (observability; refinement loop below reacts to it)
            human_score = EditorialHumanizer.audit_text(str(response_data.get("content", "")))
            if not human_score.passes_gate:
                logger.warning(
                    "human-gate: draft scored %.1f (< %.0f) — findings: %s",
                    human_score.overall,
                    HUMAN_SCORE_THRESHOLD,
                    "; ".join(human_score.findings[:5]),
                )

            # Validate with Pydantic (equivalent to Zod on the TS side)
            validated = ScribeOutput.model_validate(response_data)

            # OptiLLM-Style Critic-Reflect Loop (Threshold-gated refinement, max 2 passes)
            score, critiques = ReasoningEngine.evaluate_draft(validated.content, validated.title, pillar, min_words)
            logger.info(f"Scribe Self-Critic Score: {score}/100 for {url[:60]}")
            if score < 85:
                logger.info(f"Refining draft ({score} < 85): {'; '.join(critiques)}")
                refinement_prompt = f"""You are refining an article for Groundwork ({pillar} pillar).
Current title: {validated.title}
Current content:
{validated.content}

CRITIQUE FEEDBACK:
{chr(10).join(f"- {c}" for c in critiques)}

Please rewrite and improve the article to address all critiques above. Maintain factual accuracy, ensure minimum {min_words} words, and preserve markdown formatting.
Return the improved JSON matching the same schema."""
                try:
                    raw_refined = call_llm_with_fallback(
                        refinement_prompt,
                        fallback_chain,
                        temperature=0.3,
                        max_tokens=max_tokens,
                        supabase=supabase,
                        source_url=url,
                        system_prompt=system_prompt,
                        budget_guard=budget_guard,
                        circuit_breaker_state=circuit_breaker_state,
                    )
                    refined_data = clean_json_response(raw_refined)
                    refined_data = EditorialHumanizer.humanize_article_payload(refined_data)
                    refined_validated = ScribeOutput.model_validate(refined_data)
                    validated = refined_validated
                    score, _ = ReasoningEngine.evaluate_draft(validated.content, validated.title, pillar, min_words)
                    logger.info(f"Scribe Refined Score: {score}/100")
                except Exception as e:
                    logger.warning(f"Refinement pass skipped due to LLM error: {e}")

            span.finish(rubric_score=score)
            tracer.log_span(span, supabase=supabase)

            # Auto-fix slug if missing or generic
            if not validated.slug:
                validated.slug = slugify(validated.title)

            # FAQ, takeaway, and expert commentary remain structured fields.
            # They are rendered separately by Next.js and are not duplicated
            # inside markdown content.
            word_count = len(validated.content.split())

            # Autonomous publishing (AGENTS.md §6.3): output that passes the
            # structural quality gate is published directly with zero human gating.
            now = datetime.now(UTC).isoformat()
            effective_min = min(min_words, 550)
            if word_count < effective_min or len(validated.title) < 10:
                logger.warning(
                    f"Output below minimum ({word_count} < {effective_min} words) for {url[:60]} — saved as 'review'"
                )
                status = "review"
                published_at = None
            else:
                status = "published"
                published_at = now

            author_id = resolve_author_id(supabase, pillar, author_slugs, site_url)
            reviewer_id = resolve_reviewer_id(supabase, pillar, reviewer_slugs)
            schema_data = build_jsonld(validated, url, site_url)

            # Agent 4 hook: 4-tier visual pipeline (source -> Unsplash -> OG -> AI).
            # Self-hosted images land on R2; Unsplash stays hotlinked with attribution.
            # Never fall back to the raw source URL — external hotlinks are blocked
            # by the site CSP (img-src) and leak referrer/session data. If the
            # pipeline is disabled or fails, store no image at all.
            image_url: str | None = None
            image_source: str | None = None
            image_credit: dict | None = None
            if enable_media:
                try:
                    from media_uploader import process_image

                    media = process_image(
                        source_url=item.get("image_url"),
                        title=validated.title,
                        slug=validated.slug,
                        pillar=pillar,
                    )
                    if media.image_url:
                        image_url = media.image_url
                        image_source = media.image_source
                        image_credit = media.image_credit
                except Exception:
                    logger.exception("Media pipeline failed for %s — article saved without processed image", url[:80])

            article_data = {
                "slug": validated.slug,
                "title": validated.title,
                "content": validated.content,
                "excerpt": validated.excerpt,
                "takeaway": validated.takeaway,
                "expert_comment": validated.expert_comment,
                "faq_data": [faq.model_dump() for faq in validated.faq],
                "related_queries": validated.related_queries,
                "pillar": pillar,
                "author_id": author_id,
                "reviewer_id": reviewer_id,
                "reviewed_at": now if reviewer_id else None,
                "source_url": url,
                "source_hash": item["source_hash"],
                "source_name": item.get("source_name"),
                "image_url": image_url,
                "image_source": image_source,
                "image_credit": image_credit,
                "schema_type": validated.schema_type,
                "schema_data": schema_data,
                "status": status,
                "published_at": published_at,
                "word_count": word_count,
                "faq_count": len(validated.faq),
                # NOTE: no pipeline_run_id here — the articles table SSOT
                # schema has no such column and PostgREST rejects unknown
                # fields with PGRST204, killing the whole upsert.
            }

            supabase.table("articles").upsert(
                article_data,
                on_conflict="source_hash",
            ).execute()

            published_count += 1
            if status == "published":
                published_urls.append(f"{site_url.rstrip('/')}/article/{validated.slug}")
            logger.info(f"Saved: {validated.slug} ({word_count} words, status={status})")

        except json.JSONDecodeError as e:
            logger.exception(f"JSON parse failed for {url[:60]}: {e}")
        except Exception as e:
            logger.exception(f"Scribe failed for {url[:60]}: {e}")

    # Record pipeline run for end-to-end search outcome traceability (§5.4 review)
    try:
        supabase.table("pipeline_runs").insert({
            "id": pipeline_run_id,
            "agent": "scribe",
            "status": "success" if published_count > 0 else "partial",
            "items_processed": len(filtered_items),
            "items_published": published_count,
        }).execute()
    except Exception as e:
        logger.debug("Pipeline run logging skipped: %s", e)

    # Trigger ISR revalidation if anything was written
    if published_count > 0:
        trigger_revalidation(revalidate_url, revalidate_secret)

    # Ping IndexNow for newly published URLs (best-effort, non-blocking)
    ping_indexnow(site_url, published_urls)

    # Submit URLs directly to Bing (best-effort, non-blocking)
    ping_bing(site_url, published_urls)

    logger.info(f"Scribe complete: {published_count}/{len(filtered_items)} items written [Run: {pipeline_run_id}]")
    return published_count


def refine_decaying_article(
    slug: str,
    gsc_metrics: dict[str, Any],
    supabase: Any,
    config: dict[str, Any] | None = None,
) -> bool:
    """Remediate decaying articles based on GSC Search Analytics signals."""
    if not supabase or not slug:
        return False
    config = config or {}
    try:
        res = supabase.table("articles").select("*").eq("slug", slug).maybe_single().execute()
        article = getattr(res, "data", None)
        if not article:
            logger.warning(f"Article '{slug}' not found for remediation.")
            return False

        pillar = article.get("pillar", "money")
        title = article.get("title", "")
        current_content = article.get("content", "")
        queries = gsc_metrics.get("top_queries", [])
        impressions = gsc_metrics.get("impressions", 0)
        ctr = gsc_metrics.get("ctr", 0.0)

        # Headroom compression on search intent queries & current content
        compressed_queries = HeadroomCompressor.compress_snippets(queries[:8], max_chars=1000)
        compressed_content = HeadroomCompressor.compress_html(current_content, target_chars=3500)
        system_prompt = get_full_system_prompt(pillar)

        tracer = OpikTracer()
        span = tracer.start_span("remediate_decaying_article", agent_name="scribe", metadata={"slug": slug, "pillar": pillar})

        prompt = f"""Perform a search intent and content freshness remediation on this Groundwork article.
Title: {title}
Pillar: {pillar}
Current Impressions: {impressions}, CTR: {ctr:.2%}
Top Search Queries (compressed intent):
{compressed_queries}

Current content (compressed density):
{compressed_content}

Rewrite and update this article to better answer the top search queries directly, improve heading relevance, refresh data benchmarks, and include an expanded FAQ section.
Return a valid JSON matching the ScribeOutput schema with minimum 900 words."""

        llm_cfg = config.get("llm", {})
        fallback_chain = llm_cfg.get("fallback_chain", DEFAULT_FALLBACK_CHAIN)
        max_tokens = llm_cfg.get("max_tokens", DEFAULT_MAX_TOKENS)

        raw_response = call_llm_with_fallback(
            prompt,
            fallback_chain,
            temperature=0.3,
            max_tokens=max_tokens,
            supabase=supabase,
            source_url=f"https://gworky.com/article/{slug}",
            system_prompt=system_prompt,
        )
        data = clean_json_response(raw_response)
        data = EditorialHumanizer.humanize_article_payload(data)
        validated = ScribeOutput.model_validate(data)

        score, _ = ReasoningEngine.evaluate_draft(validated.content, validated.title, pillar, 850)
        span.finish(rubric_score=score)
        tracer.log_span(span, supabase=supabase)

        update_payload = {
            "title": validated.title,
            "content": validated.content,
            "excerpt": validated.excerpt,
            "takeaway": validated.takeaway,
            "expert_comment": validated.expert_comment,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        supabase.table("articles").update(update_payload).eq("slug", slug).execute()
        logger.info(f"✅ Remediated decaying article '{slug}' with refreshed search content (Rubric: {score}/100).")
        return True
    except Exception as e:
        logger.error(f"Failed to remediate article '{slug}': {e}")
        return False
