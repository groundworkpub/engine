"""Tests for egress hardening (H4 geo-coherence) and human-physics (H6 inter-key).

These guard the pure-logic helpers added to `egress_selector.verify_geo_coherence`
and `organic_simulator.HumanPhysics.inter_key_delay` / `type_with_physics`.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from egress_selector import _GEO_LABEL, verify_geo_coherence
from organic_simulator import HumanPhysics

# ─── H4: geo / ASN coherence guard ───────────────────────────────────────────


def test_coherent_geo_passes():
    res = verify_geo_coherence("us", {"country": "United States", "city": "New York", "asn": "AS7018"})
    assert res["coherent"] is True
    assert res["mismatch"] is None


def test_mismatch_country_flagged():
    res = verify_geo_coherence("us", {"country": "United Kingdom"})
    assert res["coherent"] is False
    assert "geo-mismatch" in res["mismatch"]


def test_missing_ip_context_is_permissive():
    # No ip_context → unverified, NOT a hard fail.
    res = verify_geo_coherence("gb", None)
    assert res["coherent"] is None
    assert res["mismatch"] == "unverified-no-ip-context"


def test_uk_alias_normalized():
    # 'uk' and 'gb' both map to United Kingdom.
    assert _GEO_LABEL["uk"] == "United Kingdom"
    res = verify_geo_coherence("uk", {"country": "United Kingdom"})
    assert res["coherent"] is True


def test_missing_country_key_flagged_soft():
    res = verify_geo_coherence("us", {"city": "Austin"})
    assert res["coherent"] is None
    assert res["mismatch"] == "missing-ip-country"


# ─── H6: inter-key / typing physics ──────────────────────────────────────────


def test_inter_key_delay_within_human_bounds():
    delays = [HumanPhysics.inter_key_delay() for _ in range(200)]
    assert all(0.03 <= d <= 0.25 for d in delays)


def test_type_with_physics_scales_with_length():
    short = HumanPhysics.type_with_physics("hi")
    long = HumanPhysics.type_with_physics("a much longer field value")
    assert 0.3 < long > short


def test_type_with_physics_adds_space_pause():
    # Spaces/separators add extra latency, so with-space should take longer
    # than the same number of plain characters.
    spaced = HumanPhysics.type_with_physics("a b c d")
    assert spaced > 0.2
