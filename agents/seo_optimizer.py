"""Agentic SEO Optimizer (Groundwork AI Pipeline).

Synthesizes best practices from:
- TheCraigHewitt/seomachine (Intent detection, density balancing, LSI injection)
- TaskAGI/semantic-seo-automation (Entity relation mapping, Google KG alignment)
- addyosmani/agentic-seo & ALwrity (LLM-first SEO/AEO/GEO tri-signal compliance)

Runs autonomously in the pipeline after Scribe or as a standalone CLI optimizer.
"""

import html
import json
import logging
import os
import re
import sys
import urllib.request
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
load_dotenv(".env.local")

from pydantic import BaseModel, Field, field_validator
from supabase import Client, create_client
from agents.humanizer import EditorialSanitizer
from agents.llm_router import call_llm, call_llm_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("seo_optimizer")

# ─── Pydantic Data Models ───────────────────────────────────────────────────

class OptimizedContentResult(BaseModel):
    title: str = Field(min_length=10, max_length=120)
    excerpt: str = Field(min_length=30, max_length=200)
    content: str = Field(min_length=100)
    primary_intent: str = Field(default="informational")
    aeo_summary: str = Field(default="")
    lsi_keywords_injected: List[str] = Field(default_factory=list)
    seo_score: int = Field(ge=0, le=100, default=95)
    geo_benchmark_present: bool = True

    @field_validator("primary_intent")
    @classmethod
    def validate_intent(cls, v: str) -> str:
        valid = {"informational", "commercial", "transactional", "navigational"}
        if v.lower() not in valid:
            return "informational"
        return v.lower()


# ─── Optimization Prompts ───────────────────────────────────────────────────

SEO_OPTIMIZER_SYSTEM_PROMPT = """You are the Senior Editorial & Search Optimization Engine for Groundwork (gworky.com).
Your mission is to upgrade articles to achieve authoritative rankings in search engines and AI answer engines (AEO/GEO).

Follow these strict Editorial and Optimization Guidelines:

1. Direct Empirical Answer:
   - Provide a clear, declarative direct answer in the introductory section answering the core query immediately.
   - Use natural language without meta-labels (never create headings titled "AEO Summary Box" or "Direct Answer").

2. Evidence-Based Structured Data:
   - Include a clear Markdown comparative benchmark or data table with specific numeric data points and ranges.
   - Ground factual claims in real-world benchmarks (e.g. Federal Reserve, IRS, BLS, published clinical trials).

3. Natural Semantic Entities:
   - Integrate relevant domain entities and terminology smoothly into prose without keyword stuffing.
   - Maintain an authoritative, sophisticated tone for educated adults (ages 35–48 US/UK/AU).
   - NEVER create headings titled "LSI Keywords", "Target Keywords", or list keywords as bullet points.

4. Strict Fourth-Wall Compliance (AGENTS.md §2.1 #7):
   - Zero meta-commentary: NEVER write "Here is the article", "As an AI", "Following guidelines", "title:", "content:", etc.
   - Headings must use clean sentence case ("How to calculate solar payback in 2026", NOT Title Case).

Return ONLY valid JSON matching this schema:
{
  "title": "Optimized Title in Sentence Case Under 60 Chars",
  "excerpt": "Compelling 150-160 character meta description answering the user query.",
  "content": "Full clean markdown article starting directly with the narrative prose, subheadings, and data table.",
  "primary_intent": "informational",
  "aeo_summary": "40-60 word concise direct answer for search snippets.",
  "lsi_keywords_injected": ["keyword1", "keyword2", "keyword3"],
  "seo_score": 95,
  "geo_benchmark_present": true
}
"""


def get_supabase_client() -> Optional[Client]:
    """Initialize Supabase client using env vars."""
    url = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    if not url or not key:
        logger.warning("Supabase credentials missing.")
        return None
    try:
        return create_client(url, key)
    except Exception as e:
        logger.error(f"Failed to create Supabase client: {e}")
        return None


def optimize_article_content(
    title: str,
    content: str,
    pillar: str,
    target_keyword: Optional[str] = None,
) -> Optional[OptimizedContentResult]:
    """Execute LLM-driven SEO, AEO, and GEO optimization pass with fail-closed schema validation."""
    user_prompt = f"""Optimize the following article for Groundwork's {pillar.upper()} pillar:

Target Topic: {target_keyword or title}
Current Title: {title}

Current Content:
{content}
"""

    messages = [
        {"role": "system", "content": SEO_OPTIMIZER_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    logger.info("Executing Agentic SEO optimization via Universal LLM Router...")
    
    # 1. First attempt: Structured JSON generator
    json_data = call_llm_json(messages, max_tokens=4096)
    if json_data and isinstance(json_data, dict):
        try:
            # Sanitize content before Pydantic parsing
            if "content" in json_data and isinstance(json_data["content"], str):
                json_data["content"] = EditorialSanitizer.sanitize_article_prose(json_data["content"])
            if "title" in json_data and isinstance(json_data["title"], str):
                json_data["title"] = EditorialSanitizer.sanitize_article_prose(json_data["title"])
            if "excerpt" in json_data and isinstance(json_data["excerpt"], str):
                json_data["excerpt"] = EditorialSanitizer.sanitize_article_prose(json_data["excerpt"])
            return OptimizedContentResult(**json_data)
        except Exception as err:
            logger.warning(f"JSON validation failed: {err}. Attempting raw recovery...")

    # 2. Second attempt: Raw LLM generation with cJSON-style bracket extraction & json_repair
    raw_response = call_llm(messages, response_format="json", max_tokens=4096)
    if not raw_response:
        logger.error("Failed to receive response from AI engine.")
        return None

    try:
        import json_repair
        parsed = json_repair.loads(raw_response)
        if isinstance(parsed, dict) and "content" in parsed:
            parsed["content"] = EditorialSanitizer.sanitize_article_prose(str(parsed["content"]))
            if "title" in parsed:
                parsed["title"] = EditorialSanitizer.sanitize_article_prose(str(parsed["title"]))
            if "excerpt" in parsed:
                parsed["excerpt"] = EditorialSanitizer.sanitize_article_prose(str(parsed["excerpt"]))
            return OptimizedContentResult(**parsed)
    except Exception as e:
        logger.error(f"Failed to parse and repair JSON response: {e}")

    # Fail-closed invariant: NEVER dump raw response into content on failure
    logger.error("Optimization failed to produce valid schema. Rejecting update (Fail-Closed).")
    return None


def run_batch_seo_optimization(limit: int = 1) -> None:
    """Scan Supabase for recent published articles and apply SEO/AEO/GEO optimization."""
    supabase = get_supabase_client()
    if not supabase:
        logger.error("Cannot run batch optimization without Supabase client.")
        return

    logger.info(f"Scanning up to {limit} articles for SEO/AEO/GEO optimization...")
    try:
        res = (
            supabase.table("articles")
            .select("id, slug, title, content, pillar, excerpt")
            .eq("status", "published")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        articles = res.data or []
        logger.info(f"Found {len(articles)} articles to evaluate.")

        for art in articles:
            art_id = art["id"]
            slug = art["slug"]
            title = art["title"]
            content = art["content"]
            pillar = art.get("pillar", "money")

            logger.info(f"Optimizing article: {slug} ({title})")
            opt_result = optimize_article_content(title, content, pillar)
            if not opt_result:
                continue

            # Update Supabase article
            update_data = {
                "excerpt": opt_result.excerpt,
                "content": opt_result.content,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            supabase.table("articles").update(update_data).eq("id", art_id).execute()
            logger.info(f"Successfully upgraded [{slug}] with SEO score {opt_result.seo_score}/100.")
            print(f"\n--- OPTIMIZATION DATA REPORT FOR [{slug}] ---")
            print(f"Title: {opt_result.title}")
            print(f"Primary Intent: {opt_result.primary_intent}")
            print(f"AEO Summary: {opt_result.aeo_summary}")
            print(f"LSI Keywords: {opt_result.lsi_keywords_injected}")
            print(f"SEO Score: {opt_result.seo_score}/100")
            print(f"GEO Benchmark Table Present: {opt_result.geo_benchmark_present}")
            print("----------------------------------------------\n")

    except Exception as e:
        logger.error(f"Error during batch optimization: {e}")


if __name__ == "__main__":
    limit_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    run_batch_seo_optimization(limit=limit_arg)
