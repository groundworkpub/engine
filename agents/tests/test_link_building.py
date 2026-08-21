"""Smoke tests for the link-building agents (no network/DB/LLM required).

Run with:  pip install -r requirements.txt pytest && pytest agents/tests/
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import data_study
import envoy
import link_audit
import link_watch
import syndicator

# ─── link_watch: mention classification ───────────────────────────────────────


def test_classify_priority_roundup_rank():
    assert link_watch._classify_priority("10 Best Mortgage Calculators", "x") == "roundup"


def test_classify_priority_report_rank():
    assert link_watch._classify_priority("New Research on Refinancing", "y") == "report"


def test_classify_priority_news_rank():
    assert link_watch._classify_priority("Groundwork announces launch", "z") == "news"


def test_page_links_to_brand_true():
    assert link_watch._page_links_to_brand('<a href="https://gworky.com/tools">here</a>') is True


def test_page_links_to_brand_false():
    assert link_watch._page_links_to_brand("<p>no link here</p>") is False


def test_run_link_watch_dry_run_returns_counts():
    processed, inserted, failures = link_watch.run_link_watch(None, feeds=[], dry_run=True)
    assert (processed, inserted, failures) == (0, 0, 0)


# ─── link_audit: toxicity heuristics ──────────────────────────────────────────


def test_tokens_suspicious_detects_gambling():
    assert link_audit._is_tokens_suspicious("casino poker bonus") is True


def test_tokens_suspicious_clean():
    assert link_audit._is_tokens_suspicious("finance guide") is False


def test_classify_row_spam_tld():
    suspicious, reasons = link_audit._classify_row(
        {"source_url": "https://payday.casino", "anchor_text": "cheap loans"}
    )
    assert suspicious is True
    assert any("spam-TLD" in r for r in reasons)


def test_classify_row_clean():
    suspicious, reasons = link_audit._classify_row(
        {"source_url": "https://example.com/guide", "anchor_text": "Groundwork"}
    )
    assert suspicious is False
    assert reasons == []


def test_run_link_audit_writes_output(tmp_path):
    input_path = tmp_path / "backlinks.csv"
    input_path.write_text(
        "Linking page,Anchor text,Links\n"
        "https://safe.example.com/article,Groundwork,1\n"
        "https://bad.casino,free money,1\n"
    )
    result = link_audit.run_link_audit(str(input_path), str(tmp_path))
    assert result["rows_analyzed"] == 2
    assert result["flagged_links"] == 1
    assert result["flagged_domains"] == 1
    assert (tmp_path / "disavow-recommended").exists() or any(
        p.name.startswith("disavow-recommended") for p in tmp_path.iterdir()
    )


# ─── data_study: aggregation + report ─────────────────────────────────────────

TOOLS_FIXTURE = [
    {"slug": "refinance-calculator", "title": "Refi", "pillar": "money", "usage_count": 120},
    {"slug": "solar-calculator", "title": "Solar", "pillar": "home", "usage_count": 80},
    {"slug": "retire-calculator", "title": "Retire", "pillar": "money", "usage_count": 40},
]


def test_data_study_aggregate():
    study = data_study._aggregate(TOOLS_FIXTURE)
    assert study["total_tools"] == 3
    assert study["total_usage"] == 240
    assert study["top_5_tools"][0]["slug"] == "refinance-calculator"
    assert study["pillars"]["money"]["total_usage"] == 160


def test_data_study_render_markdown_has_key_sections():
    study = data_study._aggregate(TOOLS_FIXTURE)
    md = data_study._render_markdown(study)
    assert "Total interactive calculators: **3**" in md
    assert "| 1 | Refi |" in md
    assert "gworky.com/tools" in md


def test_run_data_study_writes_md_and_json(tmp_path):
    tools_processed, report_path = data_study.run_data_study(None, output_dir=str(tmp_path))
    assert tools_processed == 0
    assert Path(report_path).exists()
    assert any(p.name.endswith(".json") for p in tmp_path.iterdir())


# ─── envoy: journalist query ingestion (Tier 4 Digital PR) ─────────────────────


def test_envoy_normalize_source_known():
    assert envoy._normalize_source("Source of Sources") == "source_of_sources"
    assert envoy._normalize_source("Qwoted") == "qwoted"
    assert envoy._normalize_source("unrecognized-platform") == "haro"


def test_envoy_classify_pillar_money():
    assert envoy._classify_pillar("Mortgage rates are rising again", "") == "money"


def test_envoy_classify_pillar_body():
    assert envoy._classify_pillar("", "Tips for lowering blood pressure naturally") == "body"


def test_envoy_classify_pillar_none():
    assert envoy._classify_pillar("Local craft fair this weekend", "") is None


def test_envoy_extract_queries_input_text(tmp_path):
    path = tmp_path / "envoy_digest.txt"
    path.write_text(
        "Looking for experts on solar panel payback periods\n"
        "What should homeowners expect in 2026?\n\n"
        "Any data on high-yield savings account rates?\n"
        "Deadline Friday.\n",
        encoding="utf-8",
    )
    queries = envoy._extract_queries_input(str(path), "haro")
    assert len(queries) == 2
    assert queries[0]["title"].startswith("Looking for experts")


def test_envoy_target_asset_maps_pillar():
    assert envoy._target_asset("money") == "research/money-index"
    assert envoy._target_asset("tech") == "research/tech-index"


def test_run_envoy_dry_run_returns_counts(tmp_path):
    input_file = tmp_path / "queries.csv"
    input_file.write_text(
        "url,title,summary\n"
        "https://x.example/q1,Heat pump rebates in 2026,Details about energy incentives\n"
        "https://x.example/q2,City art exhibit,No relation to our pillars\n",
        encoding="utf-8",
    )
    processed, drafted, failed = envoy.run_envoy(
        None,
        feeds=[],
        input_files=[str(input_file)],
        fallback_chain=[],
        temperature=0.4,
        max_tokens=600,
        draft_commentary=False,
        dry_run=True,
    )
    assert processed == 1  # only the home-pillar query
    assert drafted == 0
    assert failed == 0


# ─── syndicator: canonical buffer syndication (Tier 2) ─────────────────────────


def test_syndicator_build_brief_includes_canonical():
    article = {
        "slug": "solar-payback",
        "title": "Solar payback in 2026",
        "excerpt": "What to expect.",
        "takeaway": "Plan for 8-12 years.",
        "pillar": "home",
        "word_count": 900,
    }
    brief = syndicator._build_brief(article)
    assert "https://gworky.com/article/solar-payback" in brief
    assert brief.startswith("# Solar payback in 2026")


def test_syndicator_run_dry_run_counts():
    processed, created = syndicator.run_syndicator(
        None, platforms=["medium"], min_words=800, limit=10, live=False, dry_run=True
    )
    assert (processed, created) == (0, 0)
