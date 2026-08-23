"""Unit tests for the GSC rank tracker pure logic.

No network/DB — covers GSC row → RankRow mapping and query filtering.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rank_tracker import (
    _is_trackable_query,
    map_gsc_rows_to_rankings,
)


def _gsc_row(query, url, clicks=10, impressions=100, position=3.0):
    return {
        "keys": [query, url],
        "clicks": clicks,
        "impressions": impressions,
        "position": position,
    }


def test_maps_and_dedupes_by_keyword_url():
    rows = [
        _gsc_row("mortgage refinance", "https://gworky.com/article/x", 10, 100, 3.5),
        _gsc_row("mortgage refinance", "https://gworky.com/article/x", 5, 80, 2.0),  # better position
        _gsc_row("solar payback", "https://gworky.com/tools/solar", 20, 200, 1.0),
    ]
    out = map_gsc_rows_to_rankings(rows)
    # best position kept per (keyword,url)
    assert len(out) == 2
    by_kw = {r.keyword: r for r in out}
    assert by_kw["mortgage refinance"].position == 2.0
    assert by_kw["solar payback"].clicks == 20


def test_filters_noise_and_navigational():
    rows = [
        _gsc_row("site:gworky.com", "https://gworky.com/", 1, 5, 1.0),  # filtered
        _gsc_row("gworky", "https://gworky.com/", 2, 10, 1.0),  # brand — filtered
        _gsc_row("", "https://gworky.com/", 1, 5, 1.0),  # empty — filtered
        _gsc_row("solar calculator", "https://gworky.com/tools/solar", 8, 200, 1.5),  # keep
    ]
    out = map_gsc_rows_to_rankings(rows)
    assert [r.keyword for r in out] == ["solar calculator"]


def test_min_impressions_filters():
    rows = [_gsc_row("tiny", "https://gworky.com/", 1, 1, 2.0)]
    assert map_gsc_rows_to_rankings(rows, min_impressions=5) == []


def test_is_trackable_query():
    assert _is_trackable_query("best solar payback calculator")
    assert not _is_trackable_query("site:gworky.com")
    assert not _is_trackable_query("groundwork login")
    assert not _is_trackable_query("")
