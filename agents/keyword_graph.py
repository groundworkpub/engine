"""Agent 5 — The Cartographer.

Builds the keyword graph: expands per-pillar seeds through the free Google
Autocomplete endpoint (no API key), clusters candidates with union-find over
char-bigram similarity + co-occurrence edges, and persists validated rows into
the Supabase ``keywords`` table.

Design SSOT: docs/KEYWORD-GRAPH.md

Best-effort throughout: network and DB failures are logged and skipped, never
raised to the caller. Run as ``python keyword_graph.py``.
"""

import json
import logging
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from itertools import combinations
from typing import Any

import yaml
from pydantic import BaseModel, Field
from supabase import create_client

from keyword_scout import classify_intent, normalize_keyword, upsert_keywords

logger = logging.getLogger(__name__)

# ─── Pydantic validation model (Python equivalent of Zod) ────────────────────

# Supabase keywords.source enum is strictly ("human", "llm_scout").
DB_SOURCE = "llm_scout"
GRAPH_SOURCE = "keyword_graph"


class GraphNode(BaseModel):
    keyword: str = Field(min_length=2, max_length=120)
    normalized: str = Field(min_length=2, max_length=120)
    pillar: str
    intent: str = "informational"
    signal: int = Field(ge=1)
    is_branded: bool = False
    cluster_id: str = ""
    degree: int = 0
    source: str = GRAPH_SOURCE


class GraphEdge(BaseModel):
    a: str
    b: str
    weight: float = Field(gt=0)
    kind: str  # similarity | co-occurrence


# ─── Defaults (overridden by config.yml) ─────────────────────────────────────

DEFAULT_SUGGEST_ENDPOINT = "https://suggestqueries.google.com/complete/search"
DEFAULT_SUGGEST_CLIENT = "firefox"
DEFAULT_SUGGEST_HL = "en"
DEFAULT_SUGGEST_GL = "us"
DEFAULT_TIMEOUT_SECONDS = 8
DEFAULT_RETRIES = 1
RETRY_BACKOFF_SECONDS = 0.5
USER_AGENT = "GroundworkKeywordGraph/1.0"
MIN_SUGGEST_LENGTH = 2
MAX_SUGGEST_LENGTH = 120
MIN_SIMILARITY = 0.35
CO_OCCURRENCE_MIN = 2
MAX_NODES = 500
MIN_CLUSTER_SIZE = 3
ALPHABET = "abcdefghijklmnopqrstuvwxyz"
BRAND_TERMS = ("gworky", "groundwork")
BIGRAM_PAD = "^"

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


# ─── Google Autocomplete fetch ───────────────────────────────────────────────


def fetch_suggestions(
    query: str,
    endpoint: str = DEFAULT_SUGGEST_ENDPOINT,
    client: str = DEFAULT_SUGGEST_CLIENT,
    *,
    hl: str = DEFAULT_SUGGEST_HL,
    gl: str = DEFAULT_SUGGEST_GL,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    retries: int = DEFAULT_RETRIES,
) -> list[str]:
    """Query the free Google Autocomplete endpoint. Raises on final failure."""
    params: dict[str, Any] = {"client": client, "q": query}
    if hl:
        params["hl"] = hl
    if gl:
        params["gl"] = gl
    url = f"{endpoint.rstrip('/')}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    attempts = retries + 1
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            raw = data[1] if isinstance(data, list) and len(data) > 1 else data
            if isinstance(raw, list):
                return [str(item) for item in raw if isinstance(item, str)]
            return []
        except Exception as e:
            if attempt == attempts - 1:
                raise
            logger.warning(f"Suggest fetch failed for '{query}' (attempt {attempt + 1}/{attempts}): {e}")
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
    return []


# Noise markers dropped from suggestions: non-English fragments and
# out-of-region geo markers. UK/AU/NZ are target markets so they stay.
NOISE_MARKERS = (
    "adalah",
    "bca",
    "dalam",
    "untuk",
    "indonesia",
    "singapore",
    "hong kong",
    "malaysia",
    "india",
    "philippines",
    "germany",
    "france",
    "spain",
    "canada",
    "mexico",
    "brazil",
    "japan",
    "china",
    "korea",
    "vietnam",
    "thailand",
    "saudi",
    "dubai",
)


def valid_suggestion(text: str, noise_markers: Iterable[str] = NOISE_MARKERS) -> bool:
    """Suggestions must be non-empty, English-script, length-bounded, noise-free."""
    if not text:
        return False
    stripped = text.strip()
    if not (MIN_SUGGEST_LENGTH <= len(stripped) <= MAX_SUGGEST_LENGTH):
        return False
    if not re.search(r"[a-z]", stripped, re.IGNORECASE):
        return False
    lowered = stripped.lower()
    return not any(marker in lowered for marker in noise_markers)


# ─── Char-bigram similarity ──────────────────────────────────────────────────


def char_bigrams(text: str) -> frozenset[str]:
    """Padding bigrams: 'solar' -> {^s, so, ol, la, ar, r$}."""
    lowered = re.sub(r"[^a-z0-9]+", "", text.lower())
    if not lowered:
        return frozenset()
    padded = BIGRAM_PAD + lowered + BIGRAM_PAD
    return frozenset(padded[i : i + 2] for i in range(len(padded) - 1))


def bigram_similarity(a: str, b: str) -> float:
    """Jaccard similarity over character bigrams. 1.0 for identical text."""
    left = char_bigrams(a)
    right = char_bigrams(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


# ─── Union-find clustering ───────────────────────────────────────────────────


class UnionFind:
    """Disjoint-set with path halving and rank union."""

    def __init__(self, items: Iterable[str]) -> None:
        self.parent: dict[str, str] = {item: item for item in items}
        self.rank: dict[str, int] = {item: 0 for item in items}

    def find(self, item: str) -> str:
        parent = self.parent.get(item, item)
        if parent != item:
            root = self.find(parent)
            self.parent[item] = root  # path halving
            return root
        return item

    def union(self, a: str, b: str) -> None:
        root_a = self.find(a)
        root_b = self.find(b)
        if root_a == root_b:
            return
        if self.rank[root_a] < self.rank[root_b]:
            root_a, root_b = root_b, root_a
        self.parent[root_b] = root_a
        if self.rank[root_a] == self.rank[root_b]:
            self.rank[root_a] += 1

    def components(self) -> dict[str, list[str]]:
        buckets: dict[str, list[str]] = {}
        for item in self.parent:
            buckets.setdefault(self.find(item), []).append(item)
        return buckets


# ─── Cluster assembly ────────────────────────────────────────────────────────


def build_edges(
    nodes: dict[tuple[str, str], GraphNode],
    co_occurrence: Counter[tuple[str, str]],
    min_similarity: float,
    co_occurrence_min: int,
) -> list[GraphEdge]:
    """Similarity + co-occurrence edges. Co-occurrence dominates (unweighted)."""
    edges: list[GraphEdge] = []
    keys = list(nodes)
    for (norm_a, pillar_a), (norm_b, pillar_b) in combinations(keys, 2):
        if pillar_a != pillar_b:
            continue
        co_count = co_occurrence.get((norm_a, norm_b), 0)
        if co_count >= co_occurrence_min:
            edges.append(GraphEdge(a=norm_a, b=norm_b, weight=1.0, kind="co-occurrence"))
            continue
        sim = bigram_similarity(norm_a, norm_b)
        if sim >= min_similarity:
            edges.append(GraphEdge(a=norm_a, b=norm_b, weight=round(sim, 4), kind="similarity"))
    return edges


def assign_clusters(
    nodes: dict[tuple[str, str], GraphNode],
    edges: list[GraphEdge],
    min_cluster_size: int,
) -> dict[str, int]:
    """Union-find components → cluster ids. Returns cluster_size map."""
    by_normalized: dict[str, str] = {norm: pillar for norm, pillar in nodes}
    uf = UnionFind(by_normalized)
    for edge in edges:
        if edge.a in by_normalized and edge.b in by_normalized:
            uf.union(edge.a, edge.b)
    components = uf.components()
    cluster_sizes: dict[str, int] = {}
    for cluster_id, members in components.items():
        if len(members) < min_cluster_size:
            for member in members:
                nodes[(member, by_normalized[member])].cluster_id = member
            continue
        cluster_sizes[cluster_id] = len(members)
        for member in members:
            nodes[(member, by_normalized[member])].cluster_id = cluster_id
    return cluster_sizes


# ─── Main agent ──────────────────────────────────────────────────────────────


def run_keyword_graph(config: dict, supabase: Any) -> dict[str, int]:
    """Agent 5: expand, cluster, score, and upsert keyword candidates."""
    kg_cfg = config.get("keyword_graph", {}) or {}
    scout_cfg = config.get("keyword_scout", {}) or {}
    seeds_map = kg_cfg.get("seeds") or scout_cfg.get("seeds") or DEFAULT_SEEDS
    endpoint = kg_cfg.get("suggest_endpoint", DEFAULT_SUGGEST_ENDPOINT)
    client = kg_cfg.get("suggest_client", DEFAULT_SUGGEST_CLIENT)
    hl = kg_cfg.get("suggest_hl", DEFAULT_SUGGEST_HL)
    gl = kg_cfg.get("suggest_gl", DEFAULT_SUGGEST_GL)
    noise_markers = tuple(set(NOISE_MARKERS) | set(kg_cfg.get("noise_markers") or ()))
    max_suggestions = int(kg_cfg.get("max_suggestions_per_seed", 20))
    alphabet_expand = bool(kg_cfg.get("alphabet_expand", False))
    alphabet_chars = int(kg_cfg.get("alphabet_expand_chars", 0))
    min_similarity = float(kg_cfg.get("min_similarity", MIN_SIMILARITY))
    co_occurrence_min = int(kg_cfg.get("co_occurrence_min", CO_OCCURRENCE_MIN))
    max_nodes = int(kg_cfg.get("max_nodes", MAX_NODES))
    min_cluster_size = int(kg_cfg.get("min_cluster_size", MIN_CLUSTER_SIZE))
    brand_terms = tuple(kg_cfg.get("brand_terms") or BRAND_TERMS)
    timeout = int(kg_cfg.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    retries = int(kg_cfg.get("retries", DEFAULT_RETRIES))

    stats = {
        "queries_run": 0,
        "queries_failed": 0,
        "suggestions_fetched": 0,
        "nodes_built": 0,
        "edges_built": 0,
        "clusters": 0,
        "upserted": 0,
    }

    expansions: list[tuple[str, list[str]]] = []
    try:
        for pillar, pillar_seeds in seeds_map.items():
            if isinstance(pillar_seeds, str):
                pillar_seeds = [pillar_seeds]
            for seed in pillar_seeds:
                queries = [seed]
                if alphabet_expand and alphabet_chars > 0:
                    queries.extend(f"{seed} {ch}" for ch in ALPHABET[:alphabet_chars])
                for query in queries:
                    stats["queries_run"] += 1
                    try:
                        suggestions = fetch_suggestions(
                            query, endpoint, client, hl=hl, gl=gl, timeout=timeout, retries=retries
                        )
                    except Exception as e:
                        stats["queries_failed"] += 1
                        logger.warning(f"Suggest query failed for '{query}': {e}")
                        continue
                    stats["suggestions_fetched"] += len(suggestions)
                    expansions.append((pillar, suggestions))
    except Exception as e:
        logger.exception(f"Keyword Graph expansion loop failed: {e}")

    # ── build node pool (dedup on normalized+pillar) ────────────────────────
    nodes: dict[tuple[str, str], GraphNode] = {}
    co_occurrence: Counter[tuple[str, str]] = Counter()
    try:
        for pillar, suggestions in expansions:
            pool = [s.strip() for s in suggestions if valid_suggestion(s, noise_markers)]
            if not pool:
                continue
            pool = pool[:max_suggestions]
            for text in pool:
                normalized = normalize_keyword(text)
                if not normalized:
                    continue
                key = (normalized, pillar)
                if key in nodes:
                    nodes[key].signal += 1
                elif len(nodes) < max_nodes:
                    nodes[key] = GraphNode(
                        keyword=text[:120],
                        normalized=normalized,
                        pillar=pillar,
                        intent=classify_intent(text),
                        signal=1,
                        is_branded=any(brand in text.lower() for brand in brand_terms),
                    )
            for a, b in combinations((normalize_keyword(s) for s in pool if valid_suggestion(s, noise_markers)), 2):
                if a and b and a != b:
                    co_occurrence[(a, b)] += 1
    except Exception as e:
        logger.exception(f"Keyword Graph node assembly failed: {e}")

    stats["nodes_built"] = len(nodes)

    # ── edges + clusters ────────────────────────────────────────────────────
    edges: list[GraphEdge] = []
    cluster_sizes: dict[str, int] = {}
    try:
        if nodes:
            edges = build_edges(nodes, co_occurrence, min_similarity, co_occurrence_min)
            stats["edges_built"] = len(edges)
            cluster_sizes = assign_clusters(nodes, edges, min_cluster_size)
            stats["clusters"] = len(cluster_sizes)

            degree: Counter[str] = Counter()
            for edge in edges:
                degree[edge.a] += 1
                degree[edge.b] += 1
            for (normalized, _pillar), node in nodes.items():
                node.degree = degree.get(normalized, 0)
    except Exception as e:
        logger.exception(f"Keyword Graph clustering failed: {e}")

    # ── persist to Supabase (source must match keywords.source enum) ────────
    try:
        if nodes:
            rows = [
                {
                    "keyword": node.keyword,
                    "normalized": node.normalized,
                    "pillar": node.pillar,
                    "source": DB_SOURCE,
                    "intent": node.intent,
                    "signal": node.signal,
                    "status": "pending",
                }
                for node in nodes.values()
            ]
            stats["upserted"] = upsert_keywords(supabase, rows)
    except Exception as e:
        logger.exception(f"Keyword Graph upsert failed: {e}")

    # ── artifacts ───────────────────────────────────────────────────────────
    try:
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
        os.makedirs(output_dir, exist_ok=True)
        graph_artifact = {
            "generated_at": datetime.now(UTC).isoformat(),
            "stats": stats,
            "nodes": [node.model_dump() for node in nodes.values()],
            "edges": [edge.model_dump() for edge in edges],
        }
        with open(os.path.join(output_dir, "keyword-graph.json"), "w") as f:
            json.dump(graph_artifact, f, indent=2)
        clusters_artifact = {
            "generated_at": graph_artifact["generated_at"],
            "cluster_count": stats["clusters"],
            "clusters": [],
        }
        by_cluster: dict[str, list[dict[str, Any]]] = {}
        for node in nodes.values():
            by_cluster.setdefault(node.cluster_id, []).append(
                {
                    "keyword": node.keyword,
                    "normalized": node.normalized,
                    "pillar": node.pillar,
                    "intent": node.intent,
                    "signal": node.signal,
                    "is_branded": node.is_branded,
                    "degree": node.degree,
                }
            )
        for cluster_id, members in by_cluster.items():
            clusters_artifact["clusters"].append({"cluster_id": cluster_id, "size": len(members), "keywords": members})
        clusters_artifact["clusters"].sort(key=lambda c: c["size"], reverse=True)
        with open(os.path.join(output_dir, "keyword-clusters.json"), "w") as f:
            json.dump(clusters_artifact, f, indent=2)
    except Exception as e:
        logger.exception(f"Keyword Graph artifact write failed: {e}")

    logger.info(
        f"Keyword Graph complete: {stats['upserted']} upserted, "
        f"{stats['nodes_built']} nodes, {stats['edges_built']} edges, "
        f"{stats['clusters']} clusters ({stats['queries_failed']} failed queries)"
    )
    return stats


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
        "agent": "keyword_graph",
        "status": "running",
        "items_processed": 0,
        "items_published": 0,
        "run_at": datetime.now(UTC).isoformat(),
    }

    try:
        stats = run_keyword_graph(config, supabase)
        run_log["status"] = "success"
        run_log["items_processed"] = stats.get("upserted", 0)
        print(f"Keyword Graph summary: {json.dumps(stats)}")
    except Exception as e:
        run_log["status"] = "error"
        run_log["error_log"] = _sanitize_error(e)
        logger.exception(f"Keyword Graph FAILED: {e}")
        sys.exit(1)
    finally:
        try:
            supabase.table("pipeline_runs").insert(run_log).execute()
        except Exception as e:
            logger.warning(f"Failed to log keyword graph run: {e}")


if __name__ == "__main__":
    main()
