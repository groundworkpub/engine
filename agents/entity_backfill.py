"""Entity Knowledge Graph Backfill Runner (GraphMind).

SSOT: AGENTS.md §5, docs/KEYWORD-GRAPH.md
Iterates through published articles and backfills missing entity nodes and edges
into `entity_nodes` and `article_entities`.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

# Ensure agent directory is on sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)

try:
    from entity_graph_builder import extract_entity_graph_from_article
except ImportError:
    from agents.entity_graph_builder import extract_entity_graph_from_article

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("entity_backfill")


def get_supabase_client() -> Client:
    load_dotenv(".env.local")
    url = os.getenv("NEXT_PUBLIC_SUPABASE_URL") or os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        logger.error("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY.")
        sys.exit(1)
    return create_client(url, key)


def run_backfill(
    limit: int = 50,
    pillar: str | None = None,
    dry_run: bool = False,
    all_articles: bool = False,
) -> int:
    supabase = get_supabase_client()

    # Step 1: Query published articles
    query = (
        supabase.table("articles")
        .select("id, title, excerpt, content, pillar, slug, published_at")
        .eq("status", "published")
        .order("published_at", desc=True)
    )
    if pillar and pillar != "all":
        query = query.eq("pillar", pillar)
    if not all_articles:
        query = query.limit(limit)

    res = query.execute()
    articles: list[dict[str, Any]] = res.data or []
    logger.info("Found %d candidate articles for entity backfill", len(articles))

    if not articles:
        logger.info("No articles found to process.")
        return 0

    processed = 0
    for idx, art in enumerate(articles, 1):
        art_id = art["id"]
        title = art.get("title", "")
        art_pillar = art.get("pillar", "money")
        content = art.get("content", "")

        logger.info(
            "[%d/%d] Processing article %s ('%s', pillar: %s)",
            idx,
            len(articles),
            art_id[:8],
            title[:40],
            art_pillar,
        )

        if dry_run:
            payload = extract_entity_graph_from_article(
                title=title,
                content=content,
                pillar=art_pillar,
                article_id=None,
                supabase=None,
            )
            logger.info("  [DRY-RUN] Extracted %d entities, %d queries", len(payload.entities), len(payload.search_queries))
        else:
            try:
                extract_entity_graph_from_article(
                    title=title,
                    content=content,
                    pillar=art_pillar,
                    article_id=art_id,
                    supabase=supabase,
                )
                processed += 1
            except Exception as e:
                logger.warning("  Failed to backfill entity for article %s: %s", art_id, e)

    logger.info("Entity backfill complete. Processed: %d/%d articles", processed, len(articles))
    return processed


def main() -> None:
    parser = argparse.ArgumentParser(description="Entity Knowledge Graph Backfill Runner")
    parser.add_argument("--limit", type=int, default=50, help="Max articles to process")
    parser.add_argument("--pillar", type=str, default=None, help="Filter by pillar (money, body, home, life, tech, or all)")
    parser.add_argument("--all", action="store_true", dest="all_articles", help="Process all published articles")
    parser.add_argument("--dry-run", action="store_true", help="Run extraction without writing to database")
    args = parser.parse_args()

    run_backfill(
        limit=args.limit,
        pillar=args.pillar,
        dry_run=args.dry_run,
        all_articles=args.all_articles,
    )


if __name__ == "__main__":
    main()
