"""Unit tests for Google SERP scanner (patchright backend) pure helpers.

Covers parsing/proxy/guard logic that needs no live browser. Browser launch +
real Google scan is exercised separately (requires residential egress).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google_serp import (
    _is_bot_gated,
    _proxy_to_parts,
    extract_domain,
    is_google_owned,
    parse_serp_links,
)


def test_extract_domain_strips_www_and_path():
    assert extract_domain("https://www.nerdwallet.com/blog/x") == "nerdwallet.com"
    assert extract_domain("https://bankrate.com/college") == "bankrate.com"
    assert extract_domain("") == ""


def test_is_google_owned_subdomains():
    assert is_google_owned("google.com")
    assert is_google_owned("www.google.com")
    assert is_google_owned("maps.google.com")
    assert is_google_owned("consent.google.com")
    assert not is_google_owned("nerdwallet.com")


def test_parse_serp_links_ranks_dedupes_and_filters():
    raw = [
        {"href": "https://www.nerdwallet.com/x", "title": "NerdWallet"},
        {"href": "https://nerdwallet.com/y", "title": "dup"},  # dedupe
        {"href": "https://www.google.com/search?q=z", "title": "google"},  # filtered
        {"href": "https://maps.google.com", "title": "maps"},  # filtered
        {"href": "https://bankrate.com/z", "title": "Bankrate"},
    ]
    out = parse_serp_links(raw, top_n=15)
    domains = [r["domain"] for r in out]
    assert domains == ["nerdwallet.com", "bankrate.com"]
    assert [r["rank"] for r in out] == [1, 2]
    assert out[0]["title"] == "NerdWallet"


def test_parse_serp_links_respects_top_n_and_skips_non_http():
    raw = [{"href": f"https://site{i}.com", "title": f"s{i}"} for i in range(20)]
    assert len(parse_serp_links(raw, top_n=5)) == 5
    raw2 = [
        {"href": "javascript:void(0)", "title": "no"},
        {"href": "#a", "title": "no"},
        {"href": "https://good.com", "title": "yes"},
    ]
    assert [r["domain"] for r in parse_serp_links(raw2)] == ["good.com"]


def test_proxy_to_parts_splits_credentials():
    p = _proxy_to_parts("http://LOGIN__cr.us:PASS@gw.dataimpulse.com:823")
    assert p is not None
    assert p["server"] == "http://gw.dataimpulse.com:823"
    assert p["username"] == "LOGIN__cr.us"
    assert p["password"] == "PASS"
    assert _proxy_to_parts(None) is None
    assert _proxy_to_parts("") is None


def test_is_bot_gated():
    assert _is_bot_gated("unusual traffic from your computer network")
    assert _is_bot_gated("Sistem kami telah mendeteksi adanya lalu lintas yang tidak wajar")
    assert _is_bot_gated("Please verify you are a human")
    assert not _is_bot_gated("Normal Google results here")
