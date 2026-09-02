"""Groundwork WordPress Authority Link Injector

Executes ethical, high-EEAT link building into high-authority WordPress targets.
Implements:
1. Pillar-matched Researcher Personas (Elena Vance, Marcus Reed, Dr. Sarah Lin, Alex Rivera, Diana Thorne).
2. Weighted Tier 2 Destination Selector (40% GitHub Pages DR 96, 30% Dev.to DR 91, 20% Blogger DR 99, 10% Zenodo DR 94).
3. LLM-powered Contextual Comment Synthesizer (Groq Llama 3.3 / Gemini Flash).
4. Dual-vector submission (HTML Form / REST API Comment + XML-RPC Pingback Fallback).
5. DataImpulse Residential Proxy integration.
6. Persistent logging in Supabase public.link_injection_logs.

Usage:
    python agents/wordpress_injector.py --dry-run --pillar money --limit 3
    python agents/wordpress_injector.py --target-url "https://target-blog.com/2026/mortgage-rates" --pillar money
    python agents/wordpress_injector.py --method pingback --target-url "https://target-blog.com/2026/solar-roi"
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sys
import time
import xmlrpc.client
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

# Load environment
def _load_env_local() -> None:
    root_env = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.local")
    if os.path.exists(root_env):
        with open(root_env, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip("'").strip('"')
                if k not in os.environ:
                    os.environ[k] = v

_load_env_local()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("wp_injector")

# Personas per Pillar (Using verified primary email groundworkpub@gmail.com)
PRIMARY_COMMENT_EMAIL = os.getenv("WP_COMMENT_EMAIL", "groundworkpub@gmail.com").strip()

PERSONA_MAP = {
    "money": {
        "name": "Elena Vance",
        "email": PRIMARY_COMMENT_EMAIL,
        "title": "Quantitative Finance & Actuarial Researcher",
    },
    "home": {
        "name": "Marcus Reed",
        "email": PRIMARY_COMMENT_EMAIL,
        "title": "Building Efficiency & Energy Modeling Specialist",
    },
    "body": {
        "name": "Dr. Sarah Lin",
        "email": PRIMARY_COMMENT_EMAIL,
        "title": "Biostatistician & Clinical Research Analyst",
    },
    "tech": {
        "name": "Alex Rivera",
        "email": PRIMARY_COMMENT_EMAIL,
        "title": "Distributed Systems Architect & ML Engineer",
    },
    "life": {
        "name": "Diana Thorne",
        "email": PRIMARY_COMMENT_EMAIL,
        "title": "Decision Analysis & Career Strategy Researcher",
    },
}

# Proxy Setup
DATAIMPULSE_HOST = os.getenv("DATAIMPULSE_HOST", "gw.dataimpulse.com")
DATAIMPULSE_PORT = os.getenv("DATAIMPULSE_PORT", "823")
DATAIMPULSE_LOGIN = os.getenv("DATAIMPULSE_LOGIN", "")
DATAIMPULSE_PASSWORD = os.getenv("DATAIMPULSE_PASSWORD", "")
DEFAULT_PROXY = (
    f"http://{DATAIMPULSE_LOGIN}__cr.us:{DATAIMPULSE_PASSWORD}@{DATAIMPULSE_HOST}:{DATAIMPULSE_PORT}"
    if DATAIMPULSE_LOGIN and DATAIMPULSE_PASSWORD
    else None
)

TIMEOUT = httpx.Timeout(20.0, connect=8.0)
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"


def get_http_client(use_proxy: bool = False) -> httpx.Client:
    """Returns an HTTP client optionally routed via DataImpulse."""
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/json,application/xhtml+xml"}
    proxy = DEFAULT_PROXY if use_proxy and DEFAULT_PROXY else None
    if proxy:
        return httpx.Client(timeout=TIMEOUT, headers=headers, proxy=proxy, follow_redirects=True)
    return httpx.Client(timeout=TIMEOUT, headers=headers, follow_redirects=True)


def get_db_connection():
    """Returns a direct PostgreSQL connection to Supabase."""
    import psycopg2
    return psycopg2.connect(
        host=os.getenv("SUPABASE_DB_HOST"),
        port=os.getenv("SUPABASE_DB_PORT", "6543"),
        user=os.getenv("SUPABASE_DB_USER"),
        password=os.getenv("SUPABASE_DB_PASSWORD"),
        dbname="postgres",
        sslmode="require",
    )


def select_weighted_tier2_destination(pillar: str) -> dict[str, str]:
    """Selects a Tier 2 target URL according to the agreed weighted distribution:

    40% GitHub Pages (DR 96)
    30% Dev.to (DR 91)
    20% Google Blogger (DR 99)
    10% CERN Zenodo DOI (DR 94)
    """
    roll = random.uniform(0, 100)

    # 1. 40% GitHub Pages
    if roll <= 40.0:
        return {
            "platform": "github_pages",
            "tier": "tier2",
            "dr": "96",
            "url": f"https://groundworkpub.github.io/#{pillar}",
            "anchor_name": f"Groundwork {pillar.capitalize()} Research Portal",
        }
    # 2. 30% Dev.to
    elif roll <= 70.0:
        return {
            "platform": "devto",
            "tier": "tier2",
            "dr": "91",
            "url": "https://dev.to/groundworkpub",
            "anchor_name": "Groundwork Open Engineering Series",
        }
    # 3. 20% Blogger
    elif roll <= 90.0:
        return {
            "platform": "blogger",
            "tier": "tier2",
            "dr": "99",
            "url": f"https://gworky.blogspot.com/search/label/{pillar}",
            "anchor_name": f"Groundwork {pillar.capitalize()} Intelligence",
        }
    # 4. 10% Zenodo DOI
    else:
        return {
            "platform": "zenodo",
            "tier": "tier2",
            "dr": "94",
            "url": "https://doi.org/10.5281/zenodo.22011566",
            "anchor_name": "Groundwork Open Science Dataset (CERN Zenodo DOI)",
        }


def extract_target_article_context(url: str, use_proxy: bool = False) -> dict[str, Any]:
    """Scrapes the target WordPress article title, text excerpt, and comment form IDs."""
    result = {
        "url": url,
        "title": "",
        "content_excerpt": "",
        "post_id": None,
        "comment_action_url": None,
    }

    try:
        with get_http_client(use_proxy=use_proxy) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                logger.warning(f"Target article returned HTTP {resp.status_code}")
                return result

            soup = BeautifulSoup(resp.text, "html.parser")

            # Extract Title
            title_tag = soup.find("h1") or soup.find("title")
            if title_tag:
                result["title"] = title_tag.get_text().strip()

            # Extract Post Content Excerpt
            paragraphs = soup.find_all("p")
            text_chunks = [p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 40]
            result["content_excerpt"] = " ".join(text_chunks[:5])[:1200]

            # Extract comment_post_ID (input field or body/article class regex fallback)
            post_id_input = soup.find("input", {"name": "comment_post_ID"})
            if post_id_input and post_id_input.get("value"):
                result["post_id"] = post_id_input.get("value")
            else:
                m_pid = re.search(r'(?:postid|post)-(\d+)', resp.text)
                if m_pid:
                    result["post_id"] = m_pid.group(1)

            # Extract comment form action
            comment_form = soup.find("form", {"id": re.compile(r"commentform", re.I)}) or soup.find("form", {"action": re.compile(r"wp-comments-post\.php", re.I)})
            if comment_form and comment_form.get("action"):
                result["comment_action_url"] = urljoin(url, comment_form.get("action"))
            else:
                result["comment_action_url"] = urljoin(url, "/wp-comments-post.php")

    except Exception as e:
        logger.warning(f"Error scraping target article {url}: {e}")

    return result


def synthesize_llm_comment(article_title: str, article_excerpt: str, persona: dict[str, str], pillar: str) -> str:
    """Uses Groq Llama 3.3 or Gemini to craft a scholarly, data-backed 2-paragraph comment."""
    prompt = f"""You are {persona['name']}, a {persona['title']} writing a thoughtful, expert public comment on an industry blog post.

Article Title: {article_title}
Article Summary: {article_excerpt}
Domain Pillar: {pillar.capitalize()}

Rules:
1. Write exactly 2 concise paragraphs (total 90-140 words).
2. Paragraph 1: Compliment a specific observation or technical nuance mentioned in the post, citing empirical reasons why it holds true in practical modeling.
3. Paragraph 2: Offer an additional statistical nuance, benchmark, or methodological context that adds genuine value to the discussion.
4. Tone: Rigorous, collegial, intellectual, zero corporate jargon, zero motivational fluff.
5. CRITICAL: Do NOT mention any URLs or websites inside the comment text itself (the URL is provided separately in the website field).
6. Return ONLY the comment text without quotation marks or preamble.
"""

    # Try Groq first
    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                    json={
                        "model": "llama-3.3-70b-versatile",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.5,
                        "max_tokens": 250,
                    },
                )
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"Groq comment generation failed: {e}")

    # Fallback to Gemini Flash
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key:
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_key}",
                    headers={"Content-Type": "application/json"},
                    json={"contents": [{"parts": [{"text": prompt}]}]},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        return candidates[0]["content"]["parts"][0]["text"].strip()
        except Exception as e:
            logger.warning(f"Gemini comment generation failed: {e}")

    # Fallback deterministic comment
    return (
        f"This breakdown offers a very clear structural assessment of the current trends in {article_title[:45]}. "
        "From an empirical standpoint, accounting for the underlying variance in multi-year baseline figures is essential "
        "to avoid premature optimization.\n\n"
        "In our recent benchmark analyses, cross-referencing these numbers against macroeconomic sensitivity curves "
        "showed that small delta shifts in the initial assumptions compound significantly over a 5-year horizon. "
        "Appreciate you highlighting these variables."
    )


def submit_wordpress_comment(
    target_info: dict[str, Any],
    comment_text: str,
    persona: dict[str, str],
    tier2_dest: dict[str, str],
    use_proxy: bool = False,
) -> tuple[bool, int, str]:
    """Submits the comment to the WordPress /wp-comments-post.php endpoint."""
    action_url = target_info.get("comment_action_url") or urljoin(target_info["url"], "/wp-comments-post.php")
    post_id = target_info.get("post_id") or "1"

    payload = {
        "author": persona["name"],
        "email": persona["email"],
        "url": tier2_dest["url"],
        "comment": comment_text,
        "comment_post_ID": post_id,
        "comment_parent": "0",
        "submit": "Post Comment",
    }

    headers = {
        "User-Agent": USER_AGENT,
        "Referer": target_info["url"],
        "Origin": f"{urlparse(target_info['url']).scheme}://{urlparse(target_info['url']).netloc}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    try:
        with get_http_client(use_proxy=use_proxy) as client:
            resp = client.post(action_url, data=payload, headers=headers)
            # WordPress returns 302 redirect back to the post URL with #comment-XXX fragment on success
            if resp.status_code in (200, 302):
                final_url = str(resp.url)
                is_unapproved = "unapproved=" in final_url or "moderation-hash=" in final_url or "awaiting moderation" in resp.text.lower()
                comment_id_match = re.search(r"#comment-(\d+)", final_url)
                comment_id = comment_id_match.group(1) if comment_id_match else None

                if is_unapproved:
                    logger.info(f"Comment received and queued for moderation (Comment ID: {comment_id or 'pending'})")
                    return True, resp.status_code, "moderated"
                else:
                    logger.info(f"Comment LIVE and approved (Comment ID: {comment_id or 'live'})")
                    return True, resp.status_code, "live"
            else:
                logger.warning(f"Comment submission failed with HTTP {resp.status_code}")
                return False, resp.status_code, f"http_{resp.status_code}"
    except Exception as e:
        logger.warning(f"Comment submission exception: {e}")
        return False, 500, str(e)


def submit_xmlrpc_pingback(
    xmlrpc_url: str,
    source_tier2_url: str,
    target_article_url: str,
    use_proxy: bool = False,
) -> tuple[bool, str]:
    """Triggers an XML-RPC pingback.ping call to the target WordPress site."""
    try:
        # Standard W3C Pingback payload:
        # pingback.ping(sourceURI, targetURI)
        server = xmlrpc.client.ServerProxy(xmlrpc_url)
        result = server.pingback.ping(source_tier2_url, target_article_url)
        logger.info(f"XML-RPC Pingback response from {xmlrpc_url}: {result}")
        return True, str(result)
    except Exception as e:
        logger.warning(f"XML-RPC Pingback failed on {xmlrpc_url}: {e}")
        return False, str(e)


def log_injection_to_supabase(
    target_url: str,
    method: str,
    tier2_dest: dict[str, str],
    persona: dict[str, str],
    pillar: str,
    status: str,
    http_code: int = 200,
) -> None:
    """Logs the injection result to Supabase public.link_injection_logs."""
    try:
        conn = get_db_connection()
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO public.link_injection_logs
              (source_slug, target_platform, tier_level, live_backlink_url, target_url, anchor_text, is_dofollow, status, metrics_snapshot)
            VALUES
              (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                pillar,
                f"wordpress_{method}",
                tier2_dest["tier"],
                target_url,
                tier2_dest["url"],
                persona["name"],
                True,
                "published" if status == "live" else "draft",
                json.dumps({
                    "author": persona["name"],
                    "dr": tier2_dest["dr"],
                    "platform": tier2_dest["platform"],
                    "moderation_state": status,
                    "http_code": http_code,
                    "anchor_name": tier2_dest["anchor_name"],
                }),
            ),
        )
        cur.close()
        conn.close()
        logger.info("Successfully recorded injection to Supabase link_injection_logs.")
    except Exception as e:
        logger.warning(f"Failed to log injection to Supabase: {e}")


def execute_wordpress_injection(
    target_url: str,
    pillar: str = "money",
    preferred_method: str = "auto",
    dry_run: bool = False,
    use_proxy: bool = False,
) -> dict[str, Any]:
    """Orchestrates the complete link injection flow."""
    pillar = pillar.lower()
    persona = PERSONA_MAP.get(pillar, PERSONA_MAP["money"])
    tier2_dest = select_weighted_tier2_destination(pillar)

    logger.info("=" * 60)
    logger.info(f"STARTING WORDPRESS INJECTION PIPELINE")
    logger.info(f" Target URL   : {target_url}")
    logger.info(f" Pillar       : {pillar.upper()}")
    logger.info(f" Persona      : {persona['name']} ({persona['title']})")
    logger.info(f" Tier 2 Buffer: [{tier2_dest['platform'].upper()} DR {tier2_dest['dr']}] -> {tier2_dest['url']}")
    logger.info("=" * 60)

    # 1. Scrape target context
    context = extract_target_article_context(target_url, use_proxy=use_proxy)
    article_title = context["title"] or target_url
    article_excerpt = context["content_excerpt"] or "Discussion on analytical frameworks and industry metrics."

    # 2. Synthesize expert comment
    comment_text = synthesize_llm_comment(article_title, article_excerpt, persona, pillar)
    logger.info(f"Synthesized Expert Comment ({len(comment_text.split())} words):\n{comment_text}\n")

    if dry_run:
        logger.info("[DRY-RUN] Verification complete. Skipping network transmission.")
        return {
            "status": "dry_run_success",
            "target": target_url,
            "persona": persona["name"],
            "tier2_url": tier2_dest["url"],
            "comment": comment_text,
        }

    # 3. Execution Vector
    success = False
    http_code = 0
    status = "failed"
    parsed = urlparse(target_url)
    xmlrpc_url = f"{parsed.scheme}://{parsed.netloc}/xmlrpc.php"

    if preferred_method in ("comment", "auto"):
        success, http_code, status = submit_wordpress_comment(
            context, comment_text, persona, tier2_dest, use_proxy=use_proxy
        )

    # Fallback to XML-RPC Pingback if comment failed or preferred_method == pingback
    if not success and preferred_method in ("pingback", "auto"):
        logger.info(f"Attempting XML-RPC Pingback fallback on {xmlrpc_url}...")
        success, status = submit_xmlrpc_pingback(
            xmlrpc_url, tier2_dest["url"], target_url, use_proxy=use_proxy
        )
        http_code = 200 if success else 500

    # 4. Log to Supabase
    log_injection_to_supabase(
        target_url,
        "comment" if preferred_method == "comment" or (preferred_method == "auto" and http_code in (200, 302)) else "pingback",
        tier2_dest,
        persona,
        pillar,
        status,
        http_code,
    )

    return {
        "status": status,
        "success": success,
        "target": target_url,
        "persona": persona["name"],
        "tier2_url": tier2_dest["url"],
        "http_code": http_code,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Groundwork WordPress Authority Link Injector")
    parser.add_argument("--target-url", type=str, help="Specific target WordPress article URL")
    parser.add_argument("--pillar", choices=["money", "body", "home", "life", "tech"], default="money")
    parser.add_argument("--method", choices=["comment", "pingback", "auto"], default="auto")
    parser.add_argument("--limit", type=int, default=1, help="Number of targets to process")
    parser.add_argument("--proxy", action="store_true", help="Route requests via DataImpulse residential proxy")
    parser.add_argument("--dry-run", action="store_true", help="Synthesize and inspect without posting")
    args = parser.parse_args()

    if args.target_url:
        res = execute_wordpress_injection(
            args.target_url,
            pillar=args.pillar,
            preferred_method=args.method,
            dry_run=args.dry_run,
            use_proxy=args.proxy,
        )
        print(json.dumps(res, indent=2))
        return

    # If no target specified, fetch candidates from Supabase
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT domain, pillar FROM public.wp_target_sites
        WHERE comments_enabled = true OR xmlrpc_enabled = true OR last_probed_at IS NOT NULL
        ORDER BY dr_rating DESC LIMIT %s
        """,
        (args.limit,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        # Fallback to seed domain
        logger.info("No probed candidates in Supabase. Using default seed for demonstration.")
        rows = [("cleantechnica.com", args.pillar)]

    for domain, pillar in rows:
        target_url = f"https://{domain}/"
        res = execute_wordpress_injection(
            target_url,
            pillar=pillar,
            preferred_method=args.method,
            dry_run=args.dry_run,
            use_proxy=args.proxy,
        )
        print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
