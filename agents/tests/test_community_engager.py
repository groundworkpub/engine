"""Tests for the Community Answer Engine (T3.2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents.community_engager import (
    RATE_LIMIT_PER_DAY,
    ThreadCandidate,
    load_state,
    save_state,
    score_candidate,
    today_key,
    within_rate_limit,
)


@pytest.fixture
def state_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    p = tmp_path / "state" / "community_engager_state.json"
    monkeypatch.setattr("agents.community_engager.STATE_PATH", p)
    return p


def _candidate(title: str, cluster: str = "home", **kw) -> ThreadCandidate:
    base = dict(
        platform="hn",
        thread_id="abc123",
        title=title,
        url="https://news.ycombinator.com/item?id=1",
        matched_cluster=cluster,
        pillar=cluster,
    )
    base.update(kw)
    return ThreadCandidate(**base)


class TestScoreCandidate:
    def test_question_signal_boosts_score(self):
        plain = score_candidate(_candidate("Solar panel discussion"))
        question = score_candidate(_candidate("Is a whole home generator worth it?"))
        assert question > plain

    def test_cluster_term_hits_increase_relevance(self):
        on_topic = score_candidate(_candidate("best solar installer recommendations"))
        off_topic = score_candidate(_candidate("my cat knocked over the tree"))
        assert on_topic > off_topic

    def test_engagement_is_capped(self):
        low = score_candidate(_candidate("worth it?", num_comments=1, score=0))
        high = score_candidate(_candidate("worth it?", num_comments=5000, score=5000))
        assert high > low
        # cap reached: adding more engagement beyond saturation changes nothing
        saturated = score_candidate(_candidate("worth it?", num_comments=10**9, score=10**9))
        assert saturated == high

    def test_freshness_decays_with_age(self):
        import time

        fresh = score_candidate(_candidate("worth it?", created_utc=time.time() - 3600))
        stale = score_candidate(_candidate("worth it?", created_utc=time.time() - 30 * 86400))
        assert fresh > stale


class TestRateLimit:
    def test_under_limit_allowed(self, state_path):
        state = {"daily_counts": {f"reddit:{today_key()}": 0}}
        assert within_rate_limit(state, "reddit") is True

    def test_at_limit_blocked(self, state_path):
        state = {"daily_counts": {f"reddit:{today_key()}": RATE_LIMIT_PER_DAY}}
        assert within_rate_limit(state, "reddit") is False

    def test_other_platform_unaffected(self, state_path):
        state = {"daily_counts": {f"reddit:{today_key()}": RATE_LIMIT_PER_DAY}}
        assert within_rate_limit(state, "hn") is True


class TestStatePersistence:
    def test_roundtrip(self, state_path):
        state = {"seen_threads": {"t1": {"url": "x"}}, "drafts": {}, "daily_counts": {}}
        save_state(state)
        assert load_state()["seen_threads"]["t1"]["url"] == "x"

    def test_missing_file_returns_fresh_state(self, state_path):
        fresh = load_state()
        assert fresh == {"seen_threads": {}, "daily_counts": {}, "drafts": {}}

    def test_corrupt_file_returns_fresh_state(self, state_path):
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("{not json")
        assert "seen_threads" in load_state()


class TestCallbackContract:
    def test_callback_data_shape_matches_keyboard(self):
        """Inline keyboard callback_data must stay parseable by CB_PATTERN."""
        from agents.community_engager import CB_PATTERN

        draft_id = "hn-43533990-1787296227"
        for prefix in ("approve", "reject"):
            m = CB_PATTERN.match(f"{prefix}_answer:{draft_id}")
            assert m is not None
            assert m.groups() == (prefix, draft_id)

    def test_noop_and_garbage_do_not_match(self):
        from agents.community_engager import CB_PATTERN

        assert CB_PATTERN.match("noop") is None
        assert CB_PATTERN.match("approve_pitch:xyz") is None


class TestDraftSerialization:
    def test_answer_draft_asdict_json_safe(self):
        from agents.community_engager import AnswerDraft

        d = AnswerDraft(
            draft_id="x-1",
            platform="hn",
            thread_url="https://u",
            thread_title="t",
            pillar="money",
            answer="a",
            mentions_brand=False,
        )
        assert json.loads(json.dumps(d.__dict__, default=str))["status"] == "pending"
