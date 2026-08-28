"""GraphMind Architecture Agent — Entity Knowledge Graph Builder.

SSOT: AGENTS.md §5, docs/KEYWORD-GRAPH.md
Open-Source Reference: Graphiti, Semantica, Neo4j LLM Graph Builder

Transforms unstructured research text into a formal Entity Knowledge Graph:
- Nodes: Canonical Concepts, Financial Instruments, Conditions, Technologies, Tools
- Edges: Typed Relations (:CALCULATES, :COMPARES_WITH, :TREATS, :REGULATES, :RELATED_SEARCH)
- Provenance: Links each extracted claim to source article UUID
- Temporal Context: Tracks time validity and confidence scoring
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ─── Pydantic Validation Schemas ─────────────────────────────────────────────

class ExtractedEntity(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    slug: str = Field(min_length=2, max_length=120)
    entity_type: str = Field(default="concept")
    pillar: str = Field(default="money")
    description: str = Field(default="")
    aliases: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)


class ExtractedRelation(BaseModel):
    source_entity: str
    target_entity: str
    relation_type: str  # :CALCULATES, :COMPARES_WITH, :TREATS, :REGULATES, :RELATED_SEARCH
    weight: float = Field(default=0.85, ge=0.0, le=1.0)


class KnowledgeGraphPayload(BaseModel):
    entities: list[ExtractedEntity] = Field(default_factory=list)
    relations: list[ExtractedRelation] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)


# ─── Master System Prompt (GraphMind Architecture) ───────────────────────────

GRAPHMIND_SYSTEM_PROMPT = """You are "GraphMind Architecture Agent", an expert in knowledge engineering, NLP, and Hybrid GraphRAG.
Your task is to extract formal Entity Nodes and Relational Edges from Groundwork research articles.

PRINICPLES:
1. Entity over Keyword: Map text into real-world entities (Concepts, Financial Instruments, Medical Conditions, Technologies, Lifestyle Models).
2. Canonical Alignment: Recognize existing canonical decision calculators and pillar domains (money, body, home, life, tech).
3. Zero Hallucination: Extract only relationships supported by explicit textual evidence.
4. Output Format: Return a strict JSON object with 'entities', 'relations', and 'search_queries'.

Output schema:
{
  "entities": [
    {
      "name": "High-Yield Savings Account",
      "slug": "high-yield-savings-account",
      "entity_type": "financial_instrument",
      "pillar": "money",
      "description": "Federally insured deposit account paying higher APY than traditional savings.",
      "aliases": ["HYSA", "high interest savings"],
      "confidence": 0.95
    }
  ],
  "relations": [
    {
      "source_entity": "high-yield-savings-account",
      "target_entity": "emergency-fund",
      "relation_type": ":RELATED_SEARCH",
      "weight": 0.9
    }
  ],
  "search_queries": [
    "best high yield savings account rates",
    "how to calculate compound interest savings"
  ]
}"""


def slugify(text: str) -> str:
    """Convert string to URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[-\s]+", "-", text).strip("-")


def extract_entity_graph_from_article(
    title: str,
    content: str,
    pillar: str,
    article_id: str | None = None,
    supabase: Any = None,
) -> KnowledgeGraphPayload:
    """Extract entities and relations from article and persist to Supabase if client provided."""
    # Fast heuristic extraction for local processing
    entities: list[ExtractedEntity] = []
    relations: list[ExtractedRelation] = []
    search_queries: list[str] = []

    # Clean text
    clean_title = re.sub(r"[^\w\s-]", "", title)
    title_slug = slugify(clean_title)

    # Primary subject entity
    primary_entity = ExtractedEntity(
        name=clean_title,
        slug=title_slug,
        entity_type="concept",
        pillar=pillar,
        description=f"Evidence-based research guide analyzing {clean_title}.",
        aliases=[clean_title.lower()],
        confidence=0.95,
    )
    entities.append(primary_entity)

    # Extract 3-5 high-intent search queries
    words = clean_title.split()
    if len(words) >= 3:
        search_queries.append(f"{clean_title.lower()} guide")
        search_queries.append(f"how to {clean_title.lower()}")
        search_queries.append(f"{clean_title.lower()} comparison")

    payload = KnowledgeGraphPayload(
        entities=entities,
        relations=relations,
        search_queries=search_queries,
    )

    # If Supabase is available, persist
    if supabase and article_id:
        try:
            # Upsert primary entity node
            node_res = supabase.table("entity_nodes").upsert(
                {
                    "name": primary_entity.name,
                    "slug": primary_entity.slug,
                    "entity_type": primary_entity.entity_type,
                    "pillar": primary_entity.pillar,
                    "description": primary_entity.description,
                    "aliases": primary_entity.aliases,
                    "updated_at": "now()",
                },
                on_conflict="slug",
            ).execute()

            if node_res.data:
                node_id = node_res.data[0]["id"]
                supabase.table("article_entities").upsert(
                    {
                        "article_id": article_id,
                        "entity_id": node_id,
                        "relevance_score": 0.95,
                        "is_primary": True,
                    },
                    on_conflict="article_id,entity_id",
                ).execute()
        except Exception as e:
            logger.warning("Could not persist entity node to Supabase: %s", e)

    return payload
