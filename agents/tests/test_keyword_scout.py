"""Offline unit tests for the Keyword Scout agent (no network, no DB).

Run with:  pytest agents/tests/
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from keyword_scout import (
    ALLOWED_INTENTS,
    KeywordCandidate,
    _dedupe_candidates,
    classify_intent,
    clean_html,
    enrich_intents_with_llm,
    extract_candidates,
    extract_snippets,
    filter_candidates,
    normalize_keyword,
    run_keyword_scout,
    search_searxng,
    tokenize,
    upsert_keywords,
)

# ─── tokenization ─────────────────────────────────────────────────────────────


def test_tokenize_filters_stopwords_and_digits():
    tokens = tokenize("The best way to refinance a mortgage in 2026")
    assert tokens == ["best", "refinance", "mortgage"]
    assert "the" not in tokens
    assert "2026" not in tokens


def test_tokenize_drops_one_char_tokens():
    assert tokenize("a b c best mortgage") == ["best", "mortgage"]


# ─── candidate extraction & scoring ───────────────────────────────────────────


def test_extract_candidates_builds_bigrams():
    counter = extract_candidates(
        [
            "Best mortgage refinance rates",
            "Best mortgage refinance rates",
        ]
    )
    assert counter["mortgage refinance"] == 2
    assert counter["best mortgage"] == 2
    assert counter["refinance"] == 2  # unigrams counted too


def test_filter_candidates_drops_single_words_and_low_signal():
    counter = Counter({"mortgage refinance": 3, "refinance": 3, "best mortgage": 1})
    assert filter_candidates(counter, min_signal=2) == ["mortgage refinance"]


def test_clean_html_strips_tags_and_entities():
    assert clean_html("Best <b>mortgage</b> rates") == "Best mortgage rates"
    assert clean_html("Compare &amp; save") == "Compare & save"


def test_extract_snippets_pulls_title_and_content():
    results = [{"title": "Best <b>mortgage</b> rates", "content": "Compare &amp; top offers."}]
    snippets = extract_snippets(results)
    assert "<b>" not in snippets[0]
    assert "&amp;" not in snippets[1]


def test_normalize_keyword():
    assert normalize_keyword(" Refinance  Mortgage! ") == "refinance mortgage"


# ─── intent heuristics ────────────────────────────────────────────────────────


def test_classify_intent_informational():
    assert classify_intent("how to refinance a mortgage") == "informational"


def test_classify_intent_commercial():
    assert classify_intent("best mortgage rates") == "commercial"
    assert classify_intent("buy life insurance online") == "commercial"


def test_classify_intent_transactional():
    assert classify_intent("life insurance quotes") == "transactional"


def test_classify_intent_navigational():
    assert classify_intent("credit karma login") == "navigational"


# ─── pydantic validation ──────────────────────────────────────────────────────


def test_keyword_candidate_defaults():
    candidate = KeywordCandidate(
        keyword="best mortgage rates",
        normalized="best mortgage rates",
        pillar="money",
        signal=3,
    )
    assert candidate.status == "pending"
    assert candidate.intent == "informational"
    assert candidate.source == "llm_scout"


def test_keyword_candidate_empty_rejected():
    with pytest.raises(Exception):
        KeywordCandidate(keyword="", normalized="", pillar="money", signal=3)


def test_keyword_candidate_zero_signal_rejected():
    with pytest.raises(Exception):
        KeywordCandidate(keyword="best rates", normalized="best rates", pillar="money", signal=0)


def test_keyword_candidate_invalid_intent_coerced():
    candidate = KeywordCandidate(
        keyword="best rates",
        normalized="best rates",
        pillar="money",
        signal=1,
        intent="nonsense",
    )
    assert candidate.intent == "informational"


def test_keyword_candidate_invalid_pillar_coerced():
    candidate = KeywordCandidate(
        keyword="best rates",
        normalized="best rates",
        pillar="bogus",
        signal=1,
    )
    assert candidate.pillar == "money"


# ─── dedup helper ─────────────────────────────────────────────────────────────


def test_dedupe_candidates_keeps_highest_signal():
    a = KeywordCandidate(keyword="refinance mortgage", normalized="refinance mortgage", pillar="money", signal=2)
    b = KeywordCandidate(keyword="refinance mortgage", normalized="refinance mortgage", pillar="money", signal=5)
    deduped = _dedupe_candidates([a, b])
    assert len(deduped) == 1
    assert deduped[0].signal == 5


# ─── SearXNG search (network mocked) ──────────────────────────────────────────


class Response:
    def __init__(self, body=b"{}"):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


def test_search_searxng_parses_results(monkeypatch):
    calls = []

    def open_url(request, timeout):
        calls.append(request.full_url)
        return Response(b'{"results": [{"title": "T", "content": "C"}]}')

    monkeypatch.setattr("keyword_scout.urllib.request.urlopen", open_url)
    results = search_searxng("https://searx.be", "refinance mortgage", results_per_query=10, timeout=3, retries=0)

    assert len(results) == 1
    assert "format=json" in calls[0]
    assert "language=en" in calls[0]
    assert "number_of_results=10" in calls[0]


def test_search_searxng_retries_once_then_succeeds(monkeypatch):
    calls = []

    def open_url(request, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("timed out")
        return Response(b'{"results": []}')

    monkeypatch.setattr("keyword_scout.urllib.request.urlopen", open_url)
    monkeypatch.setattr("keyword_scout.time.sleep", lambda _: None)

    assert search_searxng("https://searx.be", "q", 10, timeout=3, retries=1) == []
    assert len(calls) == 2


def test_search_searxng_raises_after_exhaustion(monkeypatch):
    def open_url(request, timeout):
        raise OSError("network down")

    monkeypatch.setattr("keyword_scout.urllib.request.urlopen", open_url)
    with pytest.raises(Exception):
        search_searxng("https://searx.be", "q", 10, timeout=3, retries=0)


# ─── LLM enrichment (LLM mocked) ──────────────────────────────────────────────


class FakeMessage:
    def __init__(self, content):
        self.message = type("Inner", (), {"content": content})()


class FakeResponse:
    def __init__(self, content):
        self.choices = [FakeMessage(content)]


def test_enrich_intents_with_llm_parses(monkeypatch):
    monkeypatch.setattr(
        "keyword_scout.litellm.completion",
        lambda **kwargs: FakeResponse('{"keywords": [{"keyword": "best mortgage rates", "intent": "commercial"}]}'),
    )
    intents = enrich_intents_with_llm(["best mortgage rates"], ["mock/model"], 0.2, 800)
    assert intents == {"best mortgage rates": "commercial"}


def test_enrich_intents_with_llm_falls_back_empty(monkeypatch):
    monkeypatch.setattr(
        "keyword_scout.litellm.completion",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("provider down")),
    )
    assert enrich_intents_with_llm(["best mortgage rates"], ["mock/model"], 0.2, 800) == {}


# ─── Supabase persistence (DB mocked) ─────────────────────────────────────────


class FakeTable:
    def __init__(self):
        self.select_calls = []
        self.upsert_calls = []
        self.existing_data = []

    def select(self, columns):
        self.select_calls.append(columns)
        return self

    def in_(self, column, values):
        return self

    def upsert(self, payload, on_conflict=None):
        self.upsert_calls.append((payload, on_conflict))
        return self

    def execute(self):
        return type("Result", (), {"data": self.existing_data})()


class FakeSupabase:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return self.tables.setdefault(name, FakeTable())


def test_upsert_keywords_preserves_existing_status():
    supabase = FakeSupabase()
    supabase.table("keywords").existing_data = [
        {"normalized": "best mortgage rates", "pillar": "money", "status": "approved"},
    ]
    rows = [
        {
            "keyword": "best mortgage rates",
            "normalized": "best mortgage rates",
            "pillar": "money",
            "source": "llm_scout",
            "intent": "commercial",
            "signal": 5,
            "status": "pending",
        },
        {
            "keyword": "life insurance quotes",
            "normalized": "life insurance quotes",
            "pillar": "money",
            "source": "llm_scout",
            "intent": "transactional",
            "signal": 3,
            "status": "pending",
        },
    ]

    inserted = upsert_keywords(supabase, rows)

    assert inserted == 2
    payload, on_conflict = supabase.table("keywords").upsert_calls[-1]
    assert on_conflict == "normalized,pillar"
    statuses = {r["normalized"]: r["status"] for r in payload}
    assert statuses["best mortgage rates"] == "approved"
    assert statuses["life insurance quotes"] == "pending"


def test_upsert_keywords_empty_returns_zero():
    assert upsert_keywords(FakeSupabase(), []) == 0


# ─── full run (search + DB mocked, no LLM) ────────────────────────────────────


def test_run_keyword_scout_extracts_and_upserts(monkeypatch):
    fake_results = [
        {"title": "Best mortgage refinance rates", "content": "Compare mortgage refinance rates for 2026"},
        {"title": "Mortgage refinance closing costs", "content": "See the best mortgage refinance rates today"},
    ]
    monkeypatch.setattr("keyword_scout.search_searxng", lambda *args, **kwargs: fake_results)
    supabase = FakeSupabase()

    config = {
        "keyword_scout": {
            "searxng_instance": "https://searx.be",
            "max_queries_per_run": 2,
            "results_per_query": 10,
            "min_signal": 1,
            "enrich_with_llm": False,
            "seeds": {"money": ["refinance mortgage"], "body": ["lower blood pressure"]},
        }
    }

    stats = run_keyword_scout(config, supabase)

    assert stats["queries_run"] == 2
    assert stats["upserted"] >= 1
    payload, on_conflict = supabase.table("keywords").upsert_calls[-1]
    assert on_conflict == "normalized,pillar"
    assert all(row["status"] == "pending" for row in payload)
    assert all(row["intent"] in ALLOWED_INTENTS for row in payload)
    assert all(row["source"] == "llm_scout" for row in payload)
