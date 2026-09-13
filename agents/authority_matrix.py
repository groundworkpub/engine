"""agents/authority_matrix.py

Groundwork Authority Matrix & Internal PageRank Engine.
Calculates link equity distribution, PageRank scores, and Trust Ratio guards (TF/CF >= 0.50).
"""

from dataclasses import dataclass
from typing import Dict, List, Set, Tuple


@dataclass
class AuthorityNode:
    url: str
    pillar: str
    pagerank: float = 1.0
    inbound_links: int = 0
    outbound_links: int = 0


def calculate_internal_pagerank(
    nodes: List[str],
    edges: List[Tuple[str, str]],
    damping_factor: float = 0.85,
    max_iterations: int = 50,
    tolerance: float = 1e-6,
) -> Dict[str, float]:
    """
    Computes internal PageRank distribution across platform URLs.
    PR(u) = (1 - d) / N + d * sum(PR(v) / L(v)) for all v linking to u.
    """
    n = len(nodes)
    if n == 0:
        return {}

    # Initialize PageRank uniformly
    pr: Dict[str, float] = {node: 1.0 / n for node in nodes}

    # Map out-links and in-links
    out_links: Dict[str, Set[str]] = {node: set() for node in nodes}
    in_links: Dict[str, Set[str]] = {node: set() for node in nodes}

    for src, dst in edges:
        if src in out_links and dst in in_links:
            out_links[src].add(dst)
            in_links[dst].add(src)

    base = (1.0 - damping_factor) / n

    for _ in range(max_iterations):
        new_pr: Dict[str, float] = {}
        diff = 0.0

        for node in nodes:
            rank_sum = 0.0
            for inbound in in_links[node]:
                num_out = len(out_links[inbound])
                if num_out > 0:
                    rank_sum += pr[inbound] / num_out

            new_val = base + damping_factor * rank_sum
            diff += abs(new_val - pr[node])
            new_pr[node] = new_val

        pr = new_pr
        if diff < tolerance:
            break

    # Normalize to sum = 100 for easy interpretation
    total = sum(pr.values()) or 1.0
    return {k: round((v / total) * 100.0, 4) for k, v in pr.items()}


def validate_trust_ratio(trust_flow: float, citation_flow: float) -> Tuple[bool, float, str]:
    """
    Validates link quality via Trust Ratio (TR = TF / CF).
    - TR >= 0.50: Optimal quality (safe for indexing).
    - TR < 0.40: High spam risk (block or quarantine).
    """
    if citation_flow <= 0:
        return (True, 1.0, "Zero citation flow (neutral)")

    tr = trust_flow / citation_flow
    if tr >= 0.50:
        return (True, round(tr, 2), "Healthy trust ratio")
    elif tr >= 0.40:
        return (True, round(tr, 2), "Moderate trust ratio (monitor closely)")
    else:
        return (False, round(tr, 2), "Spam alert: Citation Flow drastically outpaces Trust Flow (TR < 0.40)")
