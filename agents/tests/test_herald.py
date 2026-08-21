"""Unit tests for the Herald (Agent 4) — offline, no network/DB touches."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from herald import (
    _canonical_url,
    _excerpt_hook,
    _is_config_error,
    _link_facets,
    _truncate,
    amplify_article,
    build_bluesky_copy,
    build_pinterest_description,
    dispatch,
    generate_micro_copy,
    pinterest_boards,
    publish_to_bluesky,
)

ARTICLE = {
    "slug": "mortgage-refinance-guide",
    "title": "How to refinance your mortgage without paying too much",
    "excerpt": "Refinancing can cut your rate by a full point, but only if you do it at the right time. Here is what the data says.",
    "pillar": "money",
}


def test_excerpt_hook_first_sentence() -> None:
    hook = _excerpt_hook(ARTICLE)
    assert hook.startswith("Refinancing can cut your rate by a full point")
    assert hook.endswith(".")


def test_bluesky_copy_within_300() -> None:
    copy = build_bluesky_copy(ARTICLE)
    assert len(copy) <= 300
    assert _canonical_url(ARTICLE) in copy


def test_pinterest_description_has_link() -> None:
    copy = build_pinterest_description(ARTICLE)
    assert "Save this for later" in copy
    assert _canonical_url(ARTICLE) in copy


def test_truncate_keeps_word_boundary() -> None:
    long_text = "word " * 200
    result = _truncate(long_text, 100)
    assert len(result) <= 100
    assert result.endswith("…")


def test_generate_micro_copy_platforms() -> None:
    for platform in ("bluesky", "pinterest"):
        copy = generate_micro_copy(ARTICLE, platform)
        assert isinstance(copy, str) and copy


def test_publish_bluesky_missing_creds_skips() -> None:
    result = publish_to_bluesky("hi", "https://gworky.com/article/x", {})
    assert result["skipped"] is True
    assert "BSKY_HANDLE" in result["error"]


def test_publish_bluesky_bad_session_is_config_error(monkeypatch: pytest.MonkeyPatch) -> None:

    def fake_session(
        url: str, method: str = "GET", body: dict | None = None, headers: dict | None = None, timeout: int = 30
    ) -> tuple[int, dict, None]:
        return 401, {"error": "AuthenticationRequired", "message": "Invalid identifier or password"}, None

    monkeypatch.setattr("herald._http_json", fake_session)
    result = publish_to_bluesky(
        "hi",
        "https://gworky.com/article/x",
        {"BSKY_HANDLE": "x.gworky.com", "BSKY_APP_PASSWORD": "xxxx-xxxx-xxxx-xxxx"},
    )
    assert result["ok"] is False
    assert not result.get("skipped")
    assert result["status"] == 401
    assert "Invalid identifier or password" in result["error"]
    assert _is_config_error(result)


def test_publish_bluesky_dry_run_checks_session_but_never_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[tuple] = []

    def fake_session(env: dict) -> tuple[dict, None]:
        return {"accessJwt": "jwt", "did": "did:plc:abc"}, None

    def fake_http(*args, **kwargs) -> tuple[int, dict, None]:
        called.append((args, kwargs))
        return 200, {"uri": "at://did:plc:abc/app.bsky.feed.post/1"}, None

    monkeypatch.setattr("herald._bluesky_session", fake_session)
    monkeypatch.setattr("herald._http_json", fake_http)
    creds = {"BSKY_HANDLE": "elena.gworky.com", "BSKY_APP_PASSWORD": "p", "HERALD_DRY_RUN": "1"}
    result = publish_to_bluesky("hi", "https://gworky.com/article/x", creds)
    assert result["ok"] is True
    assert result["post_id"] is None
    assert called == []


def test_pinterest_boards_missing_token_skips() -> None:
    result = pinterest_boards({})
    assert result["skipped"] is True


def test_dispatch_pinterest_missing_board_skips() -> None:
    result = dispatch(ARTICLE, "pinterest", {})
    assert result["ok"] is False
    assert result["skipped"] is True
    assert "PINTEREST" in result["error"]


def test_dispatch_unknown_platform_skips() -> None:
    result = dispatch(ARTICLE, "devto", {})
    assert result["skipped"] is True


def test_amplify_skipped_rows_not_recorded() -> None:
    rows = amplify_article(None, ARTICLE, ("bluesky", "pinterest"), {})
    assert rows == []


def test_config_error_not_recorded() -> None:
    assert _is_config_error({"status": 403, "error": "oauth1-permissions: forbidden"})
    assert _is_config_error({"status": 401, "error": "unauthorized"})
    assert not _is_config_error({"status": 422, "error": "duplicate content"})
    assert not _is_config_error({"status": None, "error": "timed out"})


def test_link_facets_byte_offsets() -> None:
    link = "https://gworky.com/article/mortgage-refinance-guide"
    text = f"Check this out\n\n{link}"
    facets = _link_facets(text, link)
    assert len(facets) == 1
    facet = facets[0]
    assert facet["features"][0]["uri"] == link
    assert facet["index"]["byteStart"] == len(b"Check this out\n\n")
    assert facet["index"]["byteEnd"] == len(text.encode("utf-8"))
