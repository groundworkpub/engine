"""Smoke tests for the Scribe pipeline logic (no LLM/network required).

Run with:  pip install -r requirements.txt pytest && pytest agents/tests/
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from critic import compute_hash, run_critic
from pipeline import _sanitize_error
from scouter import fetch_feed, run_scouter
from scribe import FAQItem, ScribeOutput, build_jsonld, ping_bing, slugify

# ─── ScribeOutput validation ──────────────────────────────────────────────────


def test_slug_is_cleaned():
    out = ScribeOutput(
        slug="How To Refinance A Mortgage!",
        title="How to refinance a mortgage in 2026",
        content="word " * 200,
        excerpt="x" * 120,
        schema_type="Article",
        takeaway="A direct takeaway that is long enough for validation.",
        expert_comment="A concise expert comment that is long enough for validation.",
        faq=[
            FAQItem(
                question=f"What is question number {i}?",
                answer="A sufficiently long answer here for validation purposes.",
            )
            for i in range(3)
        ],
    )
    assert out.slug == "how-to-refinance-a-mortgage"
    assert out.slug.islower()


def test_schema_type_whitelisted():
    out = ScribeOutput(
        slug="x",
        title="A valid title here",
        content="word " * 200,
        excerpt="x" * 120,
        schema_type="NotARealType",
        takeaway="A direct takeaway that is long enough for validation.",
        expert_comment="A concise expert comment that is long enough for validation.",
        faq=[
            FAQItem(
                question=f"What is question number {i}?",
                answer="A sufficiently long answer here for validation purposes.",
            )
            for i in range(3)
        ],
    )
    assert out.schema_type == "Article"


def test_short_content_rejected():
    with pytest.raises(Exception):
        ScribeOutput(
            slug="x",
            title="A valid title here",
            content="too short",
            excerpt="x" * 120,
            schema_type="Article",
            takeaway="A direct takeaway that is long enough for validation.",
            expert_comment="A concise expert comment that is long enough for validation.",
            faq=[],
        )


# ─── JSON-LD builder ──────────────────────────────────────────────────────────


def test_jsonld_article_shape():
    out = ScribeOutput(
        slug="x",
        title="A valid title here",
        content="word " * 200,
        excerpt="x" * 120,
        schema_type="Article",
        takeaway="A direct takeaway that is long enough for validation.",
        expert_comment="A concise expert comment that is long enough for validation.",
        faq=[FAQItem(question=f"Question {i}?", answer="An answer long enough to pass validation.") for i in range(3)],
    )
    ld = build_jsonld(out, "https://example.com/source")
    graph = ld.get("@graph", [])
    article = next(n for n in graph if n.get("@type") == "Article") if graph else ld
    assert article["@type"] == "Article"
    assert article["headline"] == "A valid title here"
    assert article["publisher"]["name"] == "Groundwork"
    # FAQ block becomes a @graph entry
    graph_types = [node.get("@type") for node in graph]
    assert "FAQPage" in graph_types


def test_jsonld_serializable():
    out = ScribeOutput(
        slug="x",
        title="A valid title here",
        content="word " * 200,
        excerpt="x" * 120,
        schema_type="Article",
        takeaway="A direct takeaway that is long enough for validation.",
        expert_comment="A concise expert comment that is long enough for validation.",
        faq=[
            FAQItem(
                question=f"What is question number {i}?",
                answer="A sufficiently long answer here for validation purposes.",
            )
            for i in range(3)
        ],
    )
    ld = build_jsonld(out, "")
    json.dumps(ld)  # must not raise


# ─── slugify helper ───────────────────────────────────────────────────────────


def test_slugify_truncates_to_80():
    long = "this is a very long title that definitely exceeds the eighty character limit for a url slug"
    assert len(slugify(long)) <= 80


def test_slugify_strips_special_chars():
    assert slugify("Refinance: What to Know (2026)") == "refinance-what-to-know-2026"


# ─── error sanitization ───────────────────────────────────────────────────────


def test_sanitize_redacts_api_keys():
    err = Exception("authentication failed: api_key=sk-1234567890abcdef token=abc.def.ghi")
    cleaned = _sanitize_error(err)
    assert "sk-1234567890abcdef" not in cleaned
    assert "[REDACTED]" in cleaned


def test_sanitize_redacts_bearer_tokens():
    err = Exception("unauthorized: token eyJhbGciOiJIUzI1NiJ9.payload was rejected")
    cleaned = _sanitize_error(err)
    assert "eyJhbGciOiJIUzI1NiJ9" not in cleaned
    assert "token [REDACTED]" in cleaned


def test_fetch_feed_retries_with_bounded_timeout(monkeypatch):
    calls = []

    class MockResponse:
        status_code = 200
        content = b"<rss />"

    def mock_get(self, url):
        calls.append((url, self.timeout.read))
        if len(calls) == 1:
            raise TimeoutError("timed out")
        return MockResponse()

    import httpx
    monkeypatch.setattr(httpx.Client, "get", mock_get)
    monkeypatch.setattr("scouter.time.sleep", lambda _: None)
    monkeypatch.setattr("scouter.feedparser.parse", lambda payload: type("Feed", (), {"entries": []})())

    fetch_feed("https://example.com/feed.xml", timeout=3, retries=1)

    assert calls == [
        ("https://example.com/feed.xml", 3.0),
        ("https://example.com/feed.xml", 3.0),
    ]


def test_scouter_skips_failed_source_and_continues(monkeypatch):
    sources = [
        {"name": "Broken", "feed_url": "https://broken.test", "pillar": "tech"},
        {"name": "Good", "feed_url": "https://good.test", "pillar": "tech"},
    ]
    good_entry = type(
        "Entry",
        (),
        {"link": "https://good.test/article", "title": "A useful article title", "summary": "x" * 250},
    )()
    feeds = iter([OSError("network down"), type("Feed", (), {"entries": [good_entry], "bozo": False})()])

    def fetch(*args):
        result = next(feeds)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("scouter.fetch_feed", fetch)
    supabase = type(
        "Supabase",
        (),
        {
            "table": lambda self, _: self,
            "select": lambda self, _: self,
            "execute": lambda self: type("Result", (), {"data": []})(),
        },
    )()

    result = run_scouter({"sources": sources}, supabase)

    assert [item["url"] for item in result] == ["https://good.test/article"]


def test_critic_deduplicates_items_and_sets_hash(monkeypatch):
    item = {"url": "https://example.com/a", "title": "A sufficiently long title", "raw_content": "x" * 500}
    supabase = type(
        "Supabase",
        (),
        {
            "table": lambda self, _: self,
            "select": lambda self, _: self,
            "execute": lambda self: type("Result", (), {"data": []})(),
        },
    )()

    result = run_critic([item.copy(), item.copy()], supabase, {})

    assert len(result) == 1
    assert result[0]["source_hash"] == compute_hash(item["url"], item["title"])


def test_pipeline_records_scribes_published_count(monkeypatch):
    """The pipeline must report the real number of items Scribe auto-published,
    not a hardcoded 0 (content_audit_report.md Bug 1)."""
    import pipeline

    captured = {}
    monkeypatch.setattr(pipeline, "run_scouter", lambda config, supabase: [{"url": "u"}])
    monkeypatch.setattr(pipeline, "run_critic", lambda raw, supabase, config: raw)
    monkeypatch.setattr(pipeline, "run_scribe", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        pipeline,
        "create_client",
        lambda *args: type(
            "Client",
            (),
            {
                "table": lambda self, name: type(
                    "Table",
                    (),
                    {
                        "insert": lambda self, value: type(
                            "Exec", (), {"execute": lambda self: captured.update(value)}
                        )()
                    },
                )()
            },
        )(),
    )
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "key")

    pipeline.main()

    assert captured["items_published"] == 1


def test_ping_bing_posts_to_webmaster_api(monkeypatch):
    """ping_bing must POST to the Bing SubmitUrlBatch endpoint with apikey
    query param and cap the batch at 500 URLs."""
    import urllib.request

    captured = {}

    class FakeResponse:
        status = 200

    def fake_urlopen(req, timeout=15):
        captured["url"] = req.full_url
        captured["data"] = json.loads(req.data)
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("BING_WEBMASTER_KEY", "testkey")

    ping_bing("https://gworky.com", ["https://gworky.com/a", "https://gworky.com/b"])

    assert "apikey=testkey" in captured["url"]
    assert captured["url"].startswith("https://ssl.bing.com/webmaster/api.svc/json/SubmitUrlBatch")
    assert captured["data"] == {
        "siteUrl": "https://gworky.com",
        "urlList": ["https://gworky.com/a", "https://gworky.com/b"],
    }


def test_ping_bing_caps_batch_at_500(monkeypatch):
    """Bing's SubmitUrlBatch API allows at most 500 URLs per request."""
    import urllib.request

    captured = {}

    class FakeResponse:
        status = 200

    def fake_urlopen(req, timeout=15):
        captured["data"] = json.loads(req.data)
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("BING_WEBMASTER_KEY", "testkey")

    ping_bing("https://gworky.com", [f"https://gworky.com/u{i}" for i in range(600)])

    assert len(captured["data"]["urlList"]) == 500


def test_ping_bing_skips_without_key(monkeypatch):
    """Without BING_WEBMASTER_KEY set, ping_bing must no-op (best-effort)."""
    import urllib.request

    called = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: called.append(a))
    monkeypatch.delenv("BING_WEBMASTER_KEY", raising=False)

    ping_bing("https://gworky.com", ["https://gworky.com/a"])

    assert called == []
