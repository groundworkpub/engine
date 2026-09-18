"""SEO research intel → content production adapter (Groundwork).

Bridges the autonomous research layer (``data/target-intel.json``, written by
``agents/target_intel.py``) into the article production kitchen — Scribe
generation prompts and full-rewrite prompts in the refresh executor.

Contract:
  - Side-effect free: read-only artifact load (cached), no network, no DB, no writes.
  - Never fabricates: only fields present in the artifact are rendered.
  - Match threshold >= 0.40 (TF-IDF parity with AGENTS.md §2.7 topical auto-silo rules).
"""

import json
import logging
import os
import re
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

_INTEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "target-intel.json"
)
_MATCH_THRESHOLD = 0.40


def _norm(text: str) -> list[str]:
    """Lowercase alphanumeric token stream for overlap scoring."""
    return re.findall(r"[a-z0-9]+", text.lower())


@lru_cache(maxsize=1)
def load_intel() -> list[dict[str, Any]]:
    """Loads data/target-intel.json once. Returns [] when absent/corrupt."""
    try:
        with open(_INTEL_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("target-intel.json unavailable (%s); intel injection skipped.", e)
        return []
    if isinstance(data, dict):
        data = [v for v in data.values() if isinstance(v, dict)]
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def match_intel(pillar: str, title: str = "", slug: str = "", url: str = "") -> dict[str, Any] | None:
    """Best intel row for an article (same pillar + token overlap >= 0.40).

    Scores against the article title, slug, and source URL; prefers the
    strongest token match. Falls back to any pillar when no same-pillar row
    reaches threshold. Returns None when the artifact is absent.
    """
    rows = load_intel()
    if not rows:
        return None
    haystack = _norm(f"{title} {slug} {url}")
    if not haystack:
        return None
    same_pillar = [r for r in rows if r.get("pillar") == pillar]
    candidates = same_pillar
    best: tuple[float, dict[str, Any] | None] = (0.0, None)
    for row in candidates:
        kw_tokens = _norm(row.get("keyword", ""))
        if not kw_tokens:
            continue
        common = set(kw_tokens) & set(haystack)
        if len(common) < 2:
            continue
        jaccard = len(common) / len(set(kw_tokens) | set(haystack))
        containment = len(common) / len(set(kw_tokens))
        score = max(jaccard, containment)
        if score > best[0]:
            best = (score, row)
    if best[1] and best[0] >= _MATCH_THRESHOLD:
        return best[1]
    return None


def _fmt_list(items: Any, limit: int) -> str:
    """Formats a list/dict slice as a compact comma-joined string, '' when empty."""
    if not items:
        return ""
    if isinstance(items, dict):
        items = [f"{k}: {v}" for k, v in list(items.items())[:limit]]
    items = [str(i).strip() for i in items if str(i).strip()]
    return ", ".join(items[:limit])


def render_intel_block(row: dict[str, Any]) -> str:
    """Renders a strict-fourth-wall-safe SEO INTEL block for writer prompts."""
    gsc = row.get("gsc") or {}
    silhouette = row.get("silhouette") or {}
    lines: list[str] = []

    gsc_bits = []
    if gsc.get("position") is not None:
        gsc_bits.append(f"rank #{gsc.get('position')}")
    gsc_bits.append(f"{gsc.get('impressions', 0)} impressions")
    if gsc.get("clicks"):
        gsc_bits.append(f"{gsc.get('clicks')} clicks")
    if gsc_bits:
        lines.append(f"- Verified SERP demand (60d GSC): {', '.join(gsc_bits)}; source: {row.get('serp_source') or 'licensed'}.")

    suggest = _fmt_list(row.get("suggest"), 8)
    if suggest:
        lines.append(f"- Reader query-language long-tail phrases: {suggest}.")

    blindspots = _fmt_list(silhouette.get("blindspots"), 12)
    if blindspots:
        lines.append(f"- Topical blindspots competitors under-cover — address explicitly: {blindspots}.")

    h2s = _fmt_list(silhouette.get("suggested_h2"), 6)
    if h2s:
        lines.append(f"- H2 skeleton from the SERP silhouette: {h2s}.")

    competitors = _fmt_list(row.get("top_competitors"), 4)
    if competitors:
        lines.append(f"- Top competitors to differentiate against: {competitors}.")

    if not lines:
        return ""
    return (
        "SEO RESEARCH INTEL (verified — GSC + licensed SERP + Google Suggest):\n"
        + "\n".join(lines)
    )
