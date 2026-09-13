"""Semantic Graph Processing Engine & Topological Authority OS.

SSOT: docs/research/authority.md §2-§3, AGENTS.md §5
References:
- getzep/graphiti (Temporal context & agent memory)
- semantica-agi/semantica (Hybrid GraphRAG)
- Ananyaiitbhilai/Text2Triple (SPO Extraction)
- DerwenAI/strwythura (Entity-Resolved Knowledge Graphs)

Coordinates:
1. Subject-Predicate-Object (SPO) triplet ingestion with edge confidence & provenance.
2. NetworkX graph topology with PageRank & Degree Centrality calculations.
3. Information Gap detection for underserved canonical entities.
4. Wikidata API resolver with fuzzy matching and persistent JSON caching.
5. Schema.org JSON-LD linked-data serialization for Google Knowledge Graph & LLM AEO.
"""

from __future__ import annotations

import difflib
import json
import logging
import math
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import urllib.parse
import urllib.request

logger = logging.getLogger("semantic_graph_engine")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# ── Auto-load .env.local if present ───────────────────────────────────────────
_root = Path(__file__).resolve().parent.parent
_env_file = _root / ".env.local"
if _env_file.exists():
    try:
        with open(_env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'").strip('"')
                    if k and k not in os.environ:
                        os.environ[k] = v
    except Exception as e:
        logger.debug("Failed loading .env.local: %s", e)

# ── Import NetworkX with Graceful Pure-Python Fallback ────────────────────────
try:
    import networkx as nx
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False
    logger.warning("networkx not installed. Falling back to built-in power-iteration PageRank.")


# ── Built-in Pure-Python PageRank & Centrality (Zero-Dependency Engine) ──────
def python_pagerank(
    nodes: List[str],
    edges: List[Tuple[str, str, float]],
    alpha: float = 0.85,
    max_iter: int = 100,
    tol: float = 1e-6,
) -> Dict[str, float]:
    """Pure-Python power iteration PageRank algorithm.
    
    Guarantees deterministic execution even in minimal environments without networkx.
    """
    if not nodes:
        return {}

    n = len(nodes)
    node_set = set(nodes)
    node_indices = {node: i for i, node in enumerate(nodes)}

    # Build adjacency lists and out-degree weights
    out_weights: Dict[str, float] = {node: 0.0 for node in nodes}
    in_edges: Dict[str, List[Tuple[str, float]]] = {node: [] for node in nodes}

    for u, v, w in edges:
        if u in node_set and v in node_set:
            out_weights[u] += w
            in_edges[v].append((u, w))

    # Initialize uniform probability distribution
    p = [1.0 / n] * n

    for _ in range(max_iter):
        p_next = [(1.0 - alpha) / n] * n
        dangling_sum = sum(p[node_indices[node]] for node in nodes if out_weights[node] == 0.0)
        dangling_contrib = alpha * dangling_sum / n

        for i, node in enumerate(nodes):
            rank_sum = 0.0
            for u, w in in_edges[node]:
                rank_sum += p[node_indices[u]] * (w / out_weights[u])
            p_next[i] += alpha * rank_sum + dangling_contrib

        # Check convergence
        err = sum(abs(p_next[i] - p[i]) for i in range(n))
        p = p_next
        if err < tol:
            break

    return {nodes[i]: round(p[i], 5) for i in range(n)}


def python_directed_degree_centrality(
    nodes: List[str],
    edges: List[Tuple[str, str, float]],
) -> Tuple[Dict[str, float], Dict[str, float], Dict[str, float]]:
    """Pure-Python directed degree centrality (total, in-degree, out-degree)."""
    if not nodes or len(nodes) <= 1:
        zero_map = {n: 0.0 for n in nodes}
        return zero_map, zero_map, zero_map

    denom = len(nodes) - 1
    total_degrees: Dict[str, int] = {node: 0 for node in nodes}
    in_degrees: Dict[str, int] = {node: 0 for node in nodes}
    out_degrees: Dict[str, int] = {node: 0 for node in nodes}

    for u, v, _ in edges:
        if u in out_degrees:
            out_degrees[u] += 1
            total_degrees[u] += 1
        if v in in_degrees:
            in_degrees[v] += 1
            total_degrees[v] += 1

    dc = {node: round(total_degrees[node] / (2 * denom), 5) for node in nodes}
    in_dc = {node: round(in_degrees[node] / denom, 5) for node in nodes}
    out_dc = {node: round(out_degrees[node] / denom, 5) for node in nodes}
    return dc, in_dc, out_dc


# ── Wikidata Entity Resolver ──────────────────────────────────────────────────
class WikidataResolver:
    """Disambiguates and matches canonical entity terms to Wikidata QIDs."""

    CACHE_FILE = _root / "data" / "wikidata_entity_cache.json"

    def __init__(self):
        self.cache: Dict[str, Dict[str, Any]] = self._load_cache()

    def _load_cache(self) -> Dict[str, Dict[str, Any]]:
        if self.CACHE_FILE.exists():
            try:
                with open(self.CACHE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("Could not read wikidata cache: %s", e)
        return {}

    def _save_cache(self) -> None:
        try:
            self.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning("Could not save wikidata cache: %s", e)

    def resolve(self, entity_name: str, aliases: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """Resolves an entity name against Wikidata API with strict fuzzy similarity gating."""
        norm_key = entity_name.strip().lower()
        if norm_key in self.cache:
            return self.cache[norm_key]

        logger.info("[Wikidata] Resolving entity: '%s'...", entity_name)
        url = (
            "https://www.wikidata.org/w/api.php?action=wbsearchentities"
            f"&search={urllib.parse.quote(entity_name)}"
            "&language=en&format=json&limit=5"
        )
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "GroundworkSemanticEngine/1.0 (https://gworky.com; contact@gworky.com)",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                search_results = data.get("search", [])

                candidates_to_match = [entity_name.lower()] + [a.lower() for a in (aliases or [])]

                best_candidate: Optional[Dict[str, Any]] = None
                best_score = 0.0

                for res in search_results:
                    qid = res.get("id", "")
                    label = res.get("label", "").lower()
                    desc = res.get("description", "")
                    aliases_res = [a.lower() for a in res.get("aliases", [])]

                    # Match against name and aliases
                    for c_name in candidates_to_match:
                        ratio = difflib.SequenceMatcher(None, c_name, label).ratio()
                        for a in aliases_res:
                            ratio = max(ratio, difflib.SequenceMatcher(None, c_name, a).ratio())

                        if ratio > best_score:
                            best_score = ratio
                            best_candidate = {
                                "wikidata_id": qid,
                                "wikidata_label": res.get("label", ""),
                                "wikidata_uri": f"https://www.wikidata.org/entity/{qid}",
                                "description": desc,
                                "match_confidence": round(ratio, 3),
                            }

                # Strict Confidence Gate: Only accept if match score >= 0.85
                if best_candidate and best_score >= 0.85:
                    logger.info(
                        "[Wikidata] Matched '%s' -> %s (%s) [Score: %.2f]",
                        entity_name,
                        best_candidate["wikidata_id"],
                        best_candidate["wikidata_label"],
                        best_score,
                    )
                    self.cache[norm_key] = best_candidate
                    self._save_cache()
                    return best_candidate
                else:
                    logger.debug("[Wikidata] No high-confidence match for '%s' (Best: %.2f)", entity_name, best_score)
                    self.cache[norm_key] = None
                    self._save_cache()
                    return None

        except Exception as e:
            logger.warning("[Wikidata] API resolution error for '%s': %s", entity_name, e)
            return None


# ── Master Semantic Graph Engine ──────────────────────────────────────────────
class SemanticGraphEngine:
    """Core Knowledge Graph Orchestrator for Groundwork.
    
    Transforms unstructured entities into structured mathematical graphs,
    computes topological PageRank, resolves global Wikidata IDs, and produces
    deployment-ready Schema.org JSON-LD.
    """

    def __init__(self):
        self.schema_ns = "https://schema.org/"
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: List[Dict[str, Any]] = []
        self.resolver = WikidataResolver()

    def ingest_node(
        self,
        node_id: str,
        name: str,
        pillar: str = "money",
        entity_type: str = "concept",
        description: str = "",
        aliases: Optional[List[str]] = None,
        tool_slug: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Ingests or updates a canonical entity node."""
        self.nodes[node_id] = {
            "id": node_id,
            "name": name,
            "pillar": pillar,
            "entity_type": entity_type,
            "description": description,
            "aliases": aliases or [],
            "tool_slug": tool_slug,
            "metadata": metadata or {},
        }

    def ingest_triplet(
        self,
        subject: str,
        predicate: str,
        obj: str,
        confidence: float = 0.9,
        properties: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Ingests a Subject-Predicate-Object relationship with edge confidence."""
        edge = {
            "source": subject,
            "predicate": predicate,
            "target": obj,
            "weight": max(0.1, min(1.0, float(confidence))),
            "properties": properties or {},
        }
        self.edges.append(edge)

    def resolve_wikidata_ids(self) -> Dict[str, str]:
        """Resolves Wikidata QIDs for all ingested nodes."""
        resolved: Dict[str, str] = {}
        for node_id, node in self.nodes.items():
            res = self.resolver.resolve(node["name"], aliases=node.get("aliases", []))
            if res and res.get("wikidata_id"):
                node["metadata"]["wikidata_id"] = res["wikidata_id"]
                node["metadata"]["wikidata_uri"] = res["wikidata_uri"]
                resolved[node_id] = res["wikidata_id"]
        return resolved

    def compute_topical_authority(self) -> Dict[str, Dict[str, float]]:
        """Computes PageRank and Degree Centrality across the entire graph.
        
        Uses NetworkX if available, otherwise delegates to the deterministic
        pure-Python power-iteration engine.
        """
        all_nodes = list(self.nodes.keys())
        all_edges = [(e["source"], e["target"], e["weight"]) for e in self.edges]

        if not all_nodes:
            return {}

        if HAS_NETWORKX:
            g = nx.DiGraph()
            for n in all_nodes:
                g.add_node(n)
            for u, v, w in all_edges:
                g.add_edge(u, v, weight=w)

            try:
                pr = nx.pagerank(g, weight="weight", alpha=0.85, max_iter=100)
            except Exception:
                pr = {n: 1.0 / len(all_nodes) for n in all_nodes}

            dc = nx.degree_centrality(g)
            in_dc = nx.in_degree_centrality(g)
            out_dc = nx.out_degree_centrality(g)
        else:
            pr = python_pagerank(all_nodes, all_edges)
            dc, in_dc, out_dc = python_directed_degree_centrality(all_nodes, all_edges)

        report: Dict[str, Dict[str, float]] = {}
        for n in all_nodes:
            report[n] = {
                "pagerank": round(pr.get(n, 0.0), 5),
                "degree_centrality": round(dc.get(n, 0.0), 5),
                "in_degree_centrality": round(in_dc.get(n, 0.0), 5),
                "out_degree_centrality": round(out_dc.get(n, 0.0), 5),
            }

        # Sort descending by PageRank
        return dict(sorted(report.items(), key=lambda item: item[1]["pagerank"], reverse=True))

    def detect_information_gaps(self, top_n: int = 10) -> List[Dict[str, Any]]:
        """Identifies underserved peripheral entities that lack robust connective edges."""
        authority = self.compute_topical_authority()
        gaps: List[Dict[str, Any]] = []

        for node_id, metrics in authority.items():
            node = self.nodes.get(node_id, {})
            # Entities with low in-degree centrality (< 0.15) but high strategic value
            if metrics["in_degree_centrality"] < 0.15:
                gaps.append({
                    "node_id": node_id,
                    "name": node.get("name", node_id),
                    "pillar": node.get("pillar", "general"),
                    "in_degree_centrality": metrics["in_degree_centrality"],
                    "pagerank": metrics["pagerank"],
                    "suggested_action": f"Produce cluster companion guides linking into '{node.get('name', node_id)}'",
                })

        return sorted(gaps, key=lambda g: (g["in_degree_centrality"], -g["pagerank"]))[:top_n]

    def serialize_to_schema_jsonld(self, node_id: str) -> Dict[str, Any]:
        """Compiles an entity node into a rich, deployment-ready Schema.org JSON-LD object."""
        node = self.nodes.get(node_id)
        if not node:
            return {}

        schema_type_map = {
            "financial_instrument": "FinancialProduct",
            "calculation_tool": "WebApplication",
            "medical_condition": "MedicalCondition",
            "technology": "SoftwareApplication",
            "concept": "DefinedTerm",
            "lifestyle": "Thing",
        }

        s_type = schema_type_map.get(node.get("entity_type", "concept"), "Thing")
        canonical_url = f"https://gworky.com/tools/{node['tool_slug']}" if node.get("tool_slug") else f"https://gworky.com/search?q={urllib.parse.quote(node['name'])}"

        payload: Dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": s_type,
            "@id": f"{canonical_url}#entity",
            "name": node["name"],
            "url": canonical_url,
            "description": node.get("description", ""),
        }

        # Add Wikidata sameAs if available
        wiki_id = node.get("metadata", {}).get("wikidata_id")
        if wiki_id:
            payload["sameAs"] = f"https://www.wikidata.org/entity/{wiki_id}"

        # Add connected outgoing relations
        outgoing = [e for e in self.edges if e["source"] == node_id]
        if outgoing:
            related_items = []
            for e in outgoing[:5]:
                t_node = self.nodes.get(e["target"])
                if t_node:
                    related_items.append({
                        "@type": schema_type_map.get(t_node.get("entity_type", "concept"), "Thing"),
                        "name": t_node["name"],
                    })
            if related_items:
                payload["isRelatedTo"] = related_items

        return payload


if __name__ == "__main__":
    # Self-test & demonstration
    engine = SemanticGraphEngine()
    engine.ingest_node("mortgage-refinancing", "Mortgage Refinancing", pillar="money", entity_type="financial_instrument", tool_slug="mortgage-refinance-calculator", aliases=["refi", "home loan refinancing"])
    engine.ingest_node("hysa", "High-Yield Savings Account", pillar="money", entity_type="financial_instrument", tool_slug="hysa-compound-interest-calculator", aliases=["HYSA"])
    engine.ingest_node("emergency-fund", "Emergency Fund", pillar="money", entity_type="concept")
    engine.ingest_node("debt-snowball", "Debt Snowball Method", pillar="money", entity_type="concept", tool_slug="debt-payoff-calculator")

    engine.ingest_triplet("hysa", "partOf", "emergency-fund", confidence=0.95)
    engine.ingest_triplet("mortgage-refinancing", "relatedTo", "debt-snowball", confidence=0.85)
    engine.ingest_triplet("debt-snowball", "reliesOn", "emergency-fund", confidence=0.90)

    auth = engine.compute_topical_authority()
    print("--- TOPOLOGICAL AUTHORITY REPORT ---")
    print(json.dumps(auth, indent=2))

    gaps = engine.detect_information_gaps()
    print("--- DETECTED INFORMATION GAPS ---")
    print(json.dumps(gaps, indent=2))
