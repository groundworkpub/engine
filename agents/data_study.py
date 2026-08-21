import argparse
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

OUTPUT_DIR = "output"


def _supabase() -> Any:
    from supabase import create_client  # lazy: keeps module importable offline

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are required")
    return create_client(url, key)


def _fetch_tools(supabase: Any) -> list[dict[str, Any]]:
    resp = supabase.table("tools").select("slug, title, description, pillar, usage_count, created_at").execute()
    return [dict(row) for row in (resp.data or [])]


def _aggregate(tools: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll calculator telemetry up into study aggregates (T3.1)."""
    by_pillar: dict[str, list[dict[str, Any]]] = {}
    for tool in tools:
        by_pillar.setdefault(tool.get("pillar") or "other", []).append(tool)

    pillar_stats = {}
    for pillar, rows in sorted(by_pillar.items()):
        usage = [int(r.get("usage_count") or 0) for r in rows]
        pillar_stats[pillar] = {
            "tools": len(rows),
            "total_usage": sum(usage),
            "avg_usage": round(sum(usage) / len(rows), 1) if rows else 0,
            "top_tool": max(rows, key=lambda r: int(r.get("usage_count") or 0)).get("slug"),
        }

    ranked = sorted(tools, key=lambda r: int(r.get("usage_count") or 0), reverse=True)
    top5 = [
        {
            "slug": r.get("slug"),
            "title": r.get("title"),
            "pillar": r.get("pillar"),
            "usage_count": int(r.get("usage_count") or 0),
        }
        for r in ranked[:5]
    ]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "total_tools": len(tools),
        "total_usage": sum(int(r.get("usage_count") or 0) for r in tools),
        "pillars": pillar_stats,
        "top_5_tools": top5,
    }


def _render_markdown(study: dict[str, Any]) -> str:
    """Render a journalist-ready data study brief from the aggregates."""
    lines: list[str] = []
    lines.append(f"# Groundwork Calculator Study — {study['generated_at'][:10]}")
    lines.append("")
    lines.append("**Method:** Groundwork collects anonymous aggregate usage counts from its")
    lines.append("interactive decision calculators. This study reports the top-performing")
    lines.append("utilities by total interactions across all pillars.")
    lines.append("")
    lines.append(f"- Total interactive calculators: **{study['total_tools']}**")
    lines.append(f"- Total recorded interactions: **{study['total_usage']:,}**")
    lines.append("")
    lines.append("## Interactions by pillar")
    lines.append("")
    lines.append("| Pillar | Calculators | Total interactions | Avg / calculator | Top calculator |")
    lines.append("|---|---|---:|---:|---|")
    for pillar, stats in study["pillars"].items():
        lines.append(
            f"| {pillar} | {stats['tools']} | {stats['total_usage']:,} | {stats['avg_usage']:,} | {stats['top_tool']} |"
        )
    lines.append("")
    lines.append("## Top 5 calculators by interactions")
    lines.append("")
    lines.append("| Rank | Calculator | Pillar | Interactions |")
    lines.append("|---|---:|---|---:|")
    for i, tool in enumerate(study["top_5_tools"], start=1):
        lines.append(f"| {i} | {tool['title']} | {tool['pillar']} | {tool['usage_count']:,} |")
    lines.append("")
    lines.append("## Quote")
    lines.append("")
    lines.append('"Money and home decisions dominate interactive demand — readers run the math')
    lines.append('on refinancing, budgeting, and solar before committing." — Groundwork Data Desk')
    lines.append("")
    lines.append("---")
    lines.append("*Republish with attribution to [gworky.com](https://gworky.com/tools).*")
    lines.append("")
    return "\n".join(lines)


def _log_run(supabase: Any, status: str, items_processed: int, items_published: int, error_log: str | None) -> None:
    try:
        supabase.table("pipeline_runs").insert(
            {
                "agent": "data_study",
                "status": status,
                "items_processed": items_processed,
                "items_published": items_published,
                "error_log": error_log,
                "run_at": datetime.now(UTC).isoformat(),
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to write pipeline_runs: %s", exc)


def run_data_study(supabase: Any, output_dir: str = OUTPUT_DIR) -> tuple[int, str]:
    """Fetch calculator telemetry, aggregate, and write the study markdown.

    Returns (tools_processed, report_path).
    """
    tools = _fetch_tools(supabase) if supabase is not None else []
    study = _aggregate(tools)
    report = _render_markdown(study)

    os.makedirs(output_dir, exist_ok=True)
    date_stamp = study["generated_at"][:10]
    report_path = os.path.join(output_dir, f"data-study-{date_stamp}.md")
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write(report)

    # Machine-readable copy alongside the markdown (for future pipeline steps).
    json_path = os.path.join(output_dir, f"data-study-{date_stamp}.json")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(study, handle, indent=2)

    return len(tools), report_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a proprietary data study from calculator telemetry (T3.1).")
    parser.add_argument("--dry-run", action="store_true", help="Aggregate from empty set without DB")
    parser.add_argument("--output", default=OUTPUT_DIR, help="Output directory (default: output)")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args()

    supabase = None
    if not args.dry_run:
        try:
            supabase = _supabase()
        except RuntimeError as exc:
            logger.error("%s", exc)
            return 1

    try:
        processed, report_path = run_data_study(supabase, output_dir=args.output)
    except Exception as exc:  # noqa: BLE001
        logger.error("Data study failed: %s", exc)
        if supabase is not None:
            _log_run(supabase, "error", 0, 0, str(exc))
        return 1

    if supabase is not None:
        _log_run(supabase, "success", processed, 1, None)
    logger.info("Wrote %s (%d tools processed)", report_path, processed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
