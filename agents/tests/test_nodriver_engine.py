"""Unit tests for the SERP recon engine (nodriver backend) — pure logic only.

These cover the parsing/proxy helpers that don't require a live browser. Browser
launch/Google-scan is exercised separately (and requires egress, per the module's
"no unproxied SERP hit" invariant).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nodriver_engine import (
    _is_bot_gated,
    _proxy_to_host_port,
    extract_domain,
    parse_serp_links,
)


def test_extract_domain_strips_www_and_path():
    assert extract_domain("https://www.nerdwallet.com/blog/foo") == "nerdwallet.com"
    assert extract_domain("https://bankrate.com/college") == "bankrate.com"
    assert extract_domain("") == ""


def test_parse_serp_links_ranks_and_dedupes():
    links = [
        {"href": "https://www.nerdwallet.com/x", "title": "NerdWallet"},
        {"href": "https://nerdwallet.com/y", "title": "dup"},  # dedup domain
        {"href": "https://www.google.com/search?q=x", "title": "google"},  # filtered
        {"href": "https://bankrate.com/z", "title": "Bankrate"},
        {"href": "https://maps.google.com", "title": "maps"},  # filtered
    ]
    out = parse_serp_links(links, top_n=15)
    domains = [r.domain for r in out]
    assert domains == ["nerdwallet.com", "bankrate.com"]
    assert [r.rank for r in out] == [1, 2]
    assert out[0].title == "NerdWallet"


def test_parse_serp_links_respects_top_n():
    links = [{"href": f"https://site{i}.com", "title": f"s{i}"} for i in range(20)]
    out = parse_serp_links(links, top_n=5)
    assert len(out) == 5


def test_parse_serp_links_skips_non_http():
    links = [
        {"href": "javascript:void(0)", "title": "no"},
        {"href": "#anchor", "title": "no"},
        {"href": "https://good.com", "title": "yes"},
    ]
    out = parse_serp_links(links)
    assert [r.domain for r in out] == ["good.com"]


def test_is_bot_gated():
    assert _is_bot_gated("unusual traffic from your network")
    assert _is_bot_gated("Sistem kami telah mendeteksi adanya lalu lintas yang tidak wajar")
    assert not _is_bot_gated("Normal Google SERP results here")


def test_proxy_to_host_port_strips_scheme_and_creds():
    h, p, _s = _proxy_to_host_port("http://user:pass@gw.dataimpulse.com:823")
    assert h == "gw.dataimpulse.com"
    assert p == 823
    # no creds / no proxy
    assert _proxy_to_host_port(None) == (None, None, None)
    assert _proxy_to_host_port("") == (None, None, None)
