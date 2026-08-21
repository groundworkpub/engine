import argparse
import csv
import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Heuristics aligned with T3.8 + §4.2: flag suspicious sources for the monthly audit.
SPAM_TLD_PATTERNS = re.compile(
    r"\.(top|xyz|club|work|site|online|icu|buzz|click|loan|bet|casino|rest)\b",
    re.IGNORECASE,
)
SPAM_WORD_PATTERNS = re.compile(
    r"\b(casino|poker|gambl|pharma|viagra|payday|loan-?now|free-?(money|cash)|escort|adult)\b",
    re.IGNORECASE,
)
CITY_SPAM_PATTERNS = re.compile(r"\b(city|metro|regional)\b", re.IGNORECASE)
EXACT_ANCHOR_PATTERNS = re.compile(
    r"\b(best|cheap|compare|top|review)s?\s+(mortgage|refinanc|solar|insurance|credit|debt|loan)\b",
    re.IGNORECASE,
)


def _is_tokens_suspicious(tokens: str) -> bool:
    if not tokens:
        return False
    return bool(SPAM_WORD_PATTERNS.search(tokens))


def _classify_row(row: dict[str, str]) -> tuple[bool, list[str]]:
    """Return (is_suspicious, reasons). Handles both GSC-style and generic CSV columns.

    ``_read_backlinks_csv`` lowercases every key, so lookups here use the
    normalized names ("linking page", "anchor text", "links", "total").
    """
    domain = (row.get("source_url") or row.get("domain") or row.get("linking page") or "").strip()
    anchor = (row.get("anchor_text") or row.get("anchor") or row.get("anchor text") or "").strip()
    tokens = (row.get("tokens") or row.get("total") or row.get("links") or "").strip()

    reasons: list[str] = []
    if SPAM_TLD_PATTERNS.search(domain):
        reasons.append("spam-TLD domain")
    if CITY_SPAM_PATTERNS.search(domain):
        reasons.append("city-spam pattern")
    if _is_tokens_suspicious(anchor):
        reasons.append("suspicious anchor")
    if EXACT_ANCHOR_PATTERNS.search(anchor):
        reasons.append("exact-match keyword anchor")
    if _is_tokens_suspicious(tokens):
        reasons.append("suspicious domain tokens")
    return bool(reasons), reasons


def _read_backlinks_csv(path: str) -> list[dict[str, str]]:
    """Read a GSC links export (CSV) into normalized rows; tolerate header variants."""
    rows: list[dict[str, str]] = []
    with open(path, newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({k.strip().lower(): (v or "").strip() for k, v in row.items() if k})
    return rows


def run_link_audit(input_path: str, output_dir: str) -> dict[str, Any]:
    """Analyze a GSC backlink export and emit a disavow-ready report.

    Returns the audit summary dict. A `.txt` disavow file (domains only, per
    Google's disavow spec) is written next to the JSON report when suspicious
    links are found. This is a *recommendation* — the human reviews before
    any disavow is actually uploaded (guardrail: disavow conservatively).
    """
    rows = _read_backlinks_csv(input_path)
    flagged: list[dict[str, str | list[str]]] = []
    for row in rows:
        suspicious, reasons = _classify_row(row)
        if not suspicious:
            continue
        domain = (row.get("source_url") or row.get("domain") or row.get("linking page") or "").strip()
        if not domain:
            continue
        flagged.append(
            {
                "domain": domain,
                "anchor": row.get("anchor_text") or row.get("anchor") or "",
                "reasons": reasons,
            }
        )

    # Aggregate by domain for the disavow file (Google disavows at domain level).
    by_domain: dict[str, list[str]] = {}
    for item in flagged:
        domain = item["domain"]
        assert isinstance(domain, str)
        by_domain.setdefault(domain, [])
        by_domain[domain].extend(r for r in item["reasons"] if r not in by_domain[domain])

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "rows_analyzed": len(rows),
        "flagged_links": len(flagged),
        "flagged_domains": len(by_domain),
        "by_domain": by_domain,
    }

    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d")
    report_path = os.path.join(output_dir, f"link-audit-{timestamp}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    if by_domain:
        disavow_path = os.path.join(output_dir, f"disavow-recommended-{timestamp}.txt")
        with open(disavow_path, "w", encoding="utf-8") as f:
            f.write("# Groundwork disavow RECOMMENDATION — review before uploading to GSC.\n")
            f.write("# Human approval required (docs/LINK-BUILDING-IMPLEMENTATION-PLAN.md §2.4).\n")
            for domain in sorted(by_domain):
                f.write(f"domain:{domain}\n")
        summary["disavow_file"] = disavow_path

    logger.info(
        "link_audit: rows=%s flagged_links=%s flagged_domains=%s",
        len(rows),
        len(flagged),
        len(by_domain),
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Groundwork Link Audit — monthly GSC backlink toxicity scan (T3.8)")
    parser.add_argument(
        "--input",
        default=os.path.join(os.path.dirname(__file__), "input", "gsc-backlinks.csv"),
        help="Path to GSC backlinks export CSV",
    )
    parser.add_argument(
        "--output",
        default=os.path.join(os.path.dirname(__file__), "output"),
        help="Output directory for the audit report + disavow recommendation",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    if not os.path.exists(args.input):
        logger.error("input CSV not found: %s — export GSC Links report first", args.input)
        return 1
    summary = run_link_audit(args.input, args.output)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
