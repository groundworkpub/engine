"""Offline unit tests for the Keyword Graph agent (no network, no DB).

Run with:  pytest agents/tests/
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from keyword_graph import (
    GraphEdge,
    GraphNode,
    UnionFind,
    assign_clusters,
    bigram_similarity,
    build_edges,
    char_bigrams,
    fetch_suggestions,
    run_keyword_graph,
    valid_suggestion,
)

# ─── char-bigram similarity ──────────────────────────────────────────────────


def test_char_bigrams_padded():
    assert char_bigrams("solar") == frozenset({"^s", "so", "ol", "la", "ar", "r^"})


def test_char_bigrams_strips_non_alnum():
    assert char_bigrams("solar-panel!") == char_bigrams("solarpanel")


def test_char_bigrams_empty():
    assert char_bigrams("!!!") == frozenset()


def test_bigram_similarity_identical():
    assert bigram_similarity("solar panel", "solar panel") == 1.0


def test_bigram_similarity_unrelated_zero():
    assert bigram_similarity("mortgage rates", "vitamin d") == 0.0


def test_bigram_similarity_partial():
    score = bigram_similarity("solar panel cost", "solar panel rebate")
    assert 0.0 < score < 1.0


def test_bigram_similarity_empty_input():
    assert bigram_similarity("", "solar") == 0.0
    assert bigram_similarity("", "") == 0.0


# ─── suggestion validation ───────────────────────────────────────────────────


def test_valid_suggestion_accepts_english():
    assert valid_suggestion("best solar panel")
    assert valid_suggestion("solar")


def test_valid_suggestion_rejects_empty_and_short():
    assert not valid_suggestion("")
    assert not valid_suggestion("  ")
    assert not valid_suggestion("a")


def test_valid_suggestion_rejects_non_english_script():
    assert not valid_suggestion("太阳能电池板")


def test_valid_suggestion_rejects_noise_markers():
    assert not valid_suggestion("high yield savings account indonesia")
    assert not valid_suggestion("refinance mortgage singapore")
    assert valid_suggestion("high yield savings account rates")


def test_valid_suggestion_respects_custom_markers():
    custom = ("custommarker",)
    assert not valid_suggestion("best solar custommarker", custom)
    assert valid_suggestion("best solar panel", custom)


# ─── Google Autocomplete fetch (network mocked) ──────────────────────────────


class Response:
    def __init__(self, body=b"[]"):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


def test_fetch_suggestions_parses(monkeypatch):
    calls = []

    def open_url(request, timeout):
        calls.append(request.full_url)
        body = b'["best solar ",["best solar panel","best solar battery","best solar inverter"],[],{}]'
        return Response(body)

    monkeypatch.setattr("keyword_graph.urllib.request.urlopen", open_url)
    suggestions = fetch_suggestions("best solar", timeout=3, retries=0)

    assert suggestions == ["best solar panel", "best solar battery", "best solar inverter"]
    assert "q=best+solar" in calls[0]
    assert "client=firefox" in calls[0]
    assert "hl=en" in calls[0]
    assert "gl=us" in calls[0]


def test_fetch_suggestions_empty_response(monkeypatch):
    monkeypatch.setattr(
        "keyword_graph.urllib.request.urlopen",
        lambda request, timeout: Response(b'["q",[],[]]'),
    )
    assert fetch_suggestions("anything", timeout=3, retries=0) == []


def test_fetch_suggestions_retries_then_succeeds(monkeypatch):
    calls = []

    def open_url(request, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("timed out")
        return Response(b'["q",["solar tips"],[]]')

    monkeypatch.setattr("keyword_graph.urllib.request.urlopen", open_url)
    monkeypatch.setattr("keyword_graph.time.sleep", lambda _: None)

    assert fetch_suggestions("q", timeout=3, retries=1) == ["solar tips"]
    assert len(calls) == 2


def test_fetch_suggestions_raises_after_exhaustion(monkeypatch):
    def open_url(request, timeout):
        raise OSError("network down")

    monkeypatch.setattr("keyword_graph.urllib.request.urlopen", open_url)
    with pytest.raises(Exception):
        fetch_suggestions("q", timeout=3, retries=0)


# ─── union-find ──────────────────────────────────────────────────────────────


def test_union_find_components():
    uf = UnionFind(["a", "b", "c", "d"])
    uf.union("a", "b")
    uf.union("b", "c")
    components = uf.components()
    assert len(components) == 2
    assert set(next(iter(components.values()))) == {"a", "b", "c"}
    assert set(components["d"]) == {"d"}


# ─── edge building ───────────────────────────────────────────────────────────


def _node(normalized, pillar="money", signal=1):
    return GraphNode(keyword=normalized, normalized=normalized, pillar=pillar, signal=signal)


def test_build_edges_similarity_only():
    nodes = {
        ("solar panel cost", "home"): _node("solar panel cost", "home"),
        ("solar panel rebate", "home"): _node("solar panel rebate", "home"),
        ("mortgage rates", "money"): _node("mortgage rates"),
    }
    edges = build_edges(nodes, co_occurrence=Counter(), min_similarity=0.35, co_occurrence_min=2)
    assert len(edges) == 1
    assert edges[0].kind == "similarity"
    assert set([edges[0].a, edges[0].b]) == {"solar panel cost", "solar panel rebate"}


def test_build_edges_never_crosses_pillars():
    nodes = {
        ("solar panel cost", "home"): _node("solar panel cost", "home"),
        ("solar panel cost", "money"): _node("solar panel cost", "money"),
    }
    edges = build_edges(nodes, co_occurrence=Counter(), min_similarity=0.1, co_occurrence_min=1)
    assert edges == []


def test_build_edges_co_occurrence_dominates():
    nodes = {
        ("solar panel", "home"): _node("solar panel", "home"),
        ("heat pump", "home"): _node("heat pump", "home"),
    }

    co = Counter({("solar panel", "heat pump"): 3})
    edges = build_edges(nodes, co_occurrence=co, min_similarity=0.99, co_occurrence_min=2)
    assert len(edges) == 1
    assert edges[0].kind == "co-occurrence"


# ─── cluster assignment ──────────────────────────────────────────────────────


def test_assign_clusters_groups_connected_and_keeps_singletons():
    nodes = {
        ("solar panel cost", "home"): _node("solar panel cost", "home"),
        ("solar panel rebate", "home"): _node("solar panel rebate", "home"),
        ("mortgage rates", "money"): _node("mortgage rates"),
    }
    edges = [
        GraphEdge(a="solar panel cost", b="solar panel rebate", weight=0.8, kind="similarity"),
    ]
    cluster_sizes = assign_clusters(nodes, edges, min_cluster_size=2)
    assert len(cluster_sizes) == 1
    assert nodes[("solar panel cost", "home")].cluster_id == nodes[("solar panel rebate", "home")].cluster_id
    assert nodes[("mortgage rates", "money")].cluster_id == "mortgage rates"
    assert list(cluster_sizes.values()) == [2]


def test_assign_clusters_small_cluster_becomes_singletons():
    nodes = {
        ("a keyword", "home"): _node("a keyword", "home"),
        ("b keyword", "home"): _node("b keyword", "home"),
    }
    edges = [GraphEdge(a="a keyword", b="b keyword", weight=0.9, kind="similarity")]
    assign_clusters(nodes, edges, min_cluster_size=3)
    assert nodes[("a keyword", "home")].cluster_id == "a keyword"
    assert nodes[("b keyword", "home")].cluster_id == "b keyword"


# ─── full run (network + DB mocked) ──────────────────────────────────────────


class FakeTable:
    def __init__(self):
        self.upsert_calls = []

    def select(self, columns):
        return self

    def in_(self, column, values):
        return self

    def upsert(self, payload, on_conflict=None):
        self.upsert_calls.append((payload, on_conflict))
        return self

    def execute(self):
        return type("Result", (), {"data": []})()


class FakeSupabase:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return self.tables.setdefault(name, FakeTable())


def test_run_keyword_graph_expands_clusters_upserts(monkeypatch, tmp_path):
    responses = {
        "best solar panel": ["best solar panel cost", "best solar panel rebate", "best solar inverter"],
        "best solar inverter": ["solar inverter cost", "solar inverter comparison"],
    }

    def fake_fetch(query, endpoint=None, client=None, hl=None, gl=None, timeout=None, retries=None):
        return responses.get(query, [])

    monkeypatch.setattr("keyword_graph.fetch_suggestions", fake_fetch)
    supabase = FakeSupabase()

    config = {
        "keyword_scout": {"seeds": {"home": ["best solar panel", "best solar inverter"]}},
        "keyword_graph": {
            "min_similarity": 0.4,
            "co_occurrence_min": 2,
            "min_cluster_size": 2,
            "max_suggestions_per_seed": 20,
        },
    }

    stats = run_keyword_graph(config, supabase)

    assert stats["queries_run"] == 2
    assert stats["suggestions_fetched"] == 5
    assert stats["nodes_built"] >= 5
    assert stats["clusters"] >= 1
    assert stats["upserted"] >= 5

    payload, on_conflict = supabase.table("keywords").upsert_calls[-1]
    assert on_conflict == "normalized,pillar"
    assert all(row["source"] == "llm_scout" for row in payload)
    assert all(row["status"] == "pending" for row in payload)
    assert all(row["pillar"] == "home" for row in payload)


def test_run_keyword_graph_writes_artifacts(monkeypatch, tmp_path):
    def fake_fetch(query, endpoint=None, client=None, hl=None, gl=None, timeout=None, retries=None):
        return ["solar panel cost", "solar panel rebate"] if query == "solar panel" else []

    monkeypatch.setattr("keyword_graph.fetch_suggestions", fake_fetch)
    monkeypatch.setattr("keyword_graph.os.path.abspath", lambda p: str(tmp_path / "keyword_graph.py"))
    supabase = FakeSupabase()

    config = {"keyword_scout": {"seeds": {"home": ["solar panel"]}}, "keyword_graph": {"min_cluster_size": 2}}

    stats = run_keyword_graph(config, supabase)

    assert (tmp_path / "output" / "keyword-graph.json").exists()
    assert (tmp_path / "output" / "keyword-clusters.json").exists()
    assert stats["upserted"] >= 2
