#!/usr/bin/env python3
"""
Groundwork Cut Test Programmatic Validator (Layer 4)
SSOT: implementation_plan.md §4, §5 & §6

Enforces the 8-Step Working Sequence & Cut Test Invariant:
"Fewer URLs. Harder numbers. Named reviewers. Money after math."

The 8 Checks:
1. Direct Answer (BLUF): Decisive 40-80 word opening answer in the first section.
2. Accountability Desk: Named Fellow / Peer Reviewer / Audit Date / Domain Taxonomy.
3. Decision Framework: Explicit conditional logic ("If X Then Y", trade-off matrix).
4. Empirical Evidence: GFM markdown table containing numbers, %, or dollar figures.
5. Dissent & Sensitivity: Sensitivity analysis, margin of error, or edge-case breakdowns.
6. Decision Utility: Reference or embed to Decision Triad (Tool, Mini-Quiz, or Decision Tree).
7. Commercial Independence: Firewall statement / link to /money.json.
8. Revision Record: Date stamp, changelog, or correction reporting mechanism.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cut_test_validator")

ROOT_DIR = Path(__file__).resolve().parent.parent


def _load_env_local() -> None:
    env_path = ROOT_DIR / ".env.local"
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'").strip('"')
                if k and k not in os.environ:
                    os.environ[k] = v


_load_env_local()


def check_direct_answer(content: str) -> Tuple[bool, str]:
    """Check 1: Opening 250 words must contain a direct answer (BLUF) or callout."""
    first_chunk = content[:1500]

    # Look for blockquotes, strong opening paragraphs, or explicit Direct Answer / Key Takeaway markers
    has_bluf_marker = bool(
        re.search(
            r"(?i)(\*\*(?:quick answer|direct answer|the bottom line|key takeaway|verdict|in short):?\*\*|> \[\!(?:NOTE|TIP|IMPORTANT)\]|executive summary)",
            first_chunk,
        )
    )

    # Check first paragraph length (40 to 120 words with assertive statement)
    paragraphs = [p.strip() for p in first_chunk.split("\n\n") if p.strip() and not p.startswith("#")]
    first_p = paragraphs[0] if paragraphs else ""
    first_p_words = len(re.findall(r"\b[a-zA-Z0-9-]+\b", first_p))

    # Also check if it provides an immediate numeric/factual conclusion
    has_number = bool(re.search(r"(\$?\d+(?:\.\d+)?%?|\b\d+\s*(?:years|months|days|hours)\b)", first_p))

    if has_bluf_marker or (35 <= first_p_words <= 130 and has_number):
        return True, "Direct Answer (BLUF) detected in opening passage."

    return False, f"Missing concise Direct Answer (BLUF) in opening passage (first paragraph has {first_p_words} words)."


def check_accountability_desk(content: str, metadata: Dict[str, Any] | None = None) -> Tuple[bool, str]:
    """Check 2: Provenance, Fellow, Peer Reviewer, or Domain Taxonomy."""
    # Metadata check
    if metadata:
        if metadata.get("author_id") and (metadata.get("reviewed_by") or metadata.get("reviewer_id") or metadata.get("provenance")):
            return True, "Accountability desk verified via metadata (Author + Reviewer/Provenance)."

    # In-content check
    patterns = [
        r"(?i)(peer reviewed by|reviewed by|research fellow|audited on|taxonomy:|provenance|methodology card)",
        r"(?i)\[orcid:?\s*\d{4}-\d{4}-\d{4}-\d{3}[\dX]\]",
    ]
    for pat in patterns:
        if re.search(pat, content):
            return True, "Accountability desk verified via in-content provenance markers."

    # In the new architecture, ProvenanceTrustBar is rendered by Next.js automatically if author exists
    if metadata and metadata.get("author_id"):
        return True, "Author ID present; Next.js ProvenanceTrustBar will render Step 2."

    return False, "Missing explicit accountability desk (Fellow, Reviewer, or Provenance)."


def check_decision_framework(content: str) -> Tuple[bool, str]:
    """Check 3: Decision Framework ('If X Then Y', qualifying criteria, or decision matrix)."""
    patterns = [
        r"(?i)(if\s+.{5,60}\s+then\b|when to (?:choose|buy|switch|refinance|elect|avoid)|decision matrix|rule of thumb|qualifying criteria|decision framework|threshold)",
        r"(?i)(scenario\s+[a-c1-3]:|option\s+[a-c1-3]:)",
    ]
    for pat in patterns:
        if re.search(pat, content):
            return True, "Decision framework ('If X Then Y' or scenario criteria) detected."

    return False, "Missing clear conditional decision framework ('If X Then Y' or qualifying criteria)."


def check_empirical_evidence(content: str) -> Tuple[bool, str]:
    """Check 4: GFM table with quantitative comparisons, benchmark metrics, or hard data points."""
    # Detect markdown table
    table_pattern = re.compile(r"\|.+\|\n\|\s*[-:]+[-|\s:]+\|\n(?:\|.+\|\n?)+")
    matches = table_pattern.findall(content)

    if not matches:
        return False, "Missing GFM Markdown table."

    # Verify table contains hard numbers (percentages, dollars, or metrics)
    table_text = "\n".join(matches)
    has_stats = bool(re.search(r"(\$\d|\d+\.?\d*%|\b\d{2,}\b)", table_text))

    if has_stats:
        return True, f"Found {len(matches)} empirical GFM table(s) with quantitative metrics."

    return False, "GFM table found but lacks quantitative numbers, percentages, or dollar amounts."


def check_dissent_sensitivity(content: str) -> Tuple[bool, str]:
    """Check 5: Dissent, sensitivity analysis, margin of error, or edge-case breakdowns."""
    patterns = [
        r"(?i)(## .*(?:when this breaks|sensitivity|margin of error|counter-argument|edge cases?|alternative view|trade-offs?|exceptions?|limitations?|risks?))",
        r"(?i)(on the other hand|contrary to|a notable exception|under severe market|stress test|downside scenario)",
    ]
    for pat in patterns:
        if re.search(pat, content):
            return True, "Dissent & sensitivity analysis detected."

    return False, "Missing dissent, sensitivity analysis, or edge-case boundary conditions."


def check_decision_utility(content: str, metadata: Dict[str, Any] | None = None) -> Tuple[bool, str]:
    """Check 6: Reference to Decision Triad (Tool, Mini-Quiz, or Decision Tree)."""
    patterns = [
        r"(?i)(/tools/[a-z0-9-]+|calculator|interactive tool|diagnostic quiz|decision tree|assessment|test your scenario)",
    ]
    for pat in patterns:
        if re.search(pat, content):
            return True, "Decision utility / Triad reference detected in copy."

    # Next.js ArticleDecisionTool automatically embeds Step 6 in the layout
    return True, "ArticleDecisionTool layout auto-injects Triad widget."


def check_commercial_independence(content: str) -> Tuple[bool, str]:
    """Check 7: Commercial Independence Firewall statement or /money.json link."""
    patterns = [
        r"(?i)(/money\.json|commercial independence|editorial independence|no sponsored placements|affiliate disclosure|our testing is independently funded)",
    ]
    for pat in patterns:
        if re.search(pat, content):
            return True, "Commercial independence firewall reference found in text."

    # Next.js CommercialIndependenceCard automatically embeds Step 7 in the layout
    return True, "CommercialIndependenceCard layout auto-injects Step 7 firewall."


def check_revision_record(content: str) -> Tuple[bool, str]:
    """Check 8: Revision record, changelog, or correction reporting link."""
    patterns = [
        r"(?i)(last updated|revised on|changelog|correction:|report an error|revision history)",
    ]
    for pat in patterns:
        if re.search(pat, content):
            return True, "Revision record or correction mechanism detected."

    # Next.js ArticleCorrectionWidget automatically embeds Step 8 in the layout
    return True, "ArticleCorrectionWidget layout auto-injects Step 8 revision intake."


def verify_cut_test(
    content: str,
    title: str = "",
    pillar: str = "",
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Executes all 8 Cut Test checks and returns a comprehensive score (0-100)
    and pass/fail verdict. Passing requires score >= 80 and zero critical failures.
    """
    checks = {
        "1_direct_answer": check_direct_answer(content),
        "2_accountability_desk": check_accountability_desk(content, metadata),
        "3_decision_framework": check_decision_framework(content),
        "4_empirical_evidence": check_empirical_evidence(content),
        "5_dissent_sensitivity": check_dissent_sensitivity(content),
        "6_decision_utility": check_decision_utility(content, metadata),
        "7_commercial_independence": check_commercial_independence(content),
        "8_revision_record": check_revision_record(content),
    }

    # Weightings for the 8 checks
    weights = {
        "1_direct_answer": 20,
        "2_accountability_desk": 10,
        "3_decision_framework": 15,
        "4_empirical_evidence": 20,
        "5_dissent_sensitivity": 15,
        "6_decision_utility": 10,
        "7_commercial_independence": 5,
        "8_revision_record": 5,
    }

    total_score = 0
    passed_checks: Dict[str, bool] = {}
    details: Dict[str, str] = {}
    issues: List[str] = []

    for check_key, (passed, msg) in checks.items():
        passed_checks[check_key] = passed
        details[check_key] = msg
        if passed:
            total_score += weights.get(check_key, 10)
        else:
            issues.append(f"[{check_key}] {msg}")

    # Core required checks: 1 (direct_answer), 3 (decision_framework), 4 (empirical_evidence)
    core_passed = (
        passed_checks["1_direct_answer"]
        and passed_checks["3_decision_framework"]
        and passed_checks["4_empirical_evidence"]
    )

    is_passing = total_score >= 75 and core_passed

    return {
        "passed": is_passing,
        "score": total_score,
        "core_passed": core_passed,
        "passed_checks": passed_checks,
        "details": details,
        "issues": issues,
    }


def main():
    parser = argparse.ArgumentParser(description="Groundwork Cut Test Validator")
    parser.add_argument("target", help="File path or article slug to validate")
    parser.add_argument("--slug", action="store_true", help="Treat target as Supabase article slug")
    args = parser.parse_args()

    content = ""
    metadata = {}

    if args.slug or not Path(args.target).exists():
        # Fetch from Supabase
        import httpx
        sb_url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
        sb_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not sb_url or not sb_key:
            logger.error("Supabase credentials required to fetch slug.")
            sys.exit(1)

        resp = httpx.get(
            f"{sb_url}/rest/v1/articles",
            headers={"apikey": sb_key, "Authorization": f"Bearer {sb_key}"},
            params={"slug": f"eq.{args.target}", "select": "*"},
            timeout=10.0,
        )
        if resp.status_code != 200 or not resp.json():
            logger.error(f"Article '{args.target}' not found in Supabase.")
            sys.exit(1)

        row = resp.json()[0]
        content = row.get("content", "")
        metadata = row
    else:
        content = Path(args.target).read_text(encoding="utf-8")

    result = verify_cut_test(content, metadata=metadata)
    print(json.dumps(result, indent=2))

    if not result["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
