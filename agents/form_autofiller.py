"""Groundwork Directory Form Autofiller & Payload Generator (Category 3).

Generates standardized profiles and a 1-click JavaScript bookmarklet to instantly
autofill complex startup and tool directory submission forms (Product Hunt, Crunchbase,
BetaList, SaaSHub, AlternativeTo) bypassing manual data entry friction.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("form_autofiller")

SITE_URL = "https://gworky.com"

STANDARD_PROFILE = {
    "name": "Groundwork",
    "tagline": "Evidence-based research, calculators, and decision models for modern living",
    "description": "Groundwork replaces guesswork with empirical research across five life domains: Money, Body, Home, Life, and Tech. Featuring 20 interactive calculation models and citable datasets.",
    "website": SITE_URL,
    "tools_url": f"{SITE_URL}/tools",
    "citations_url": f"{SITE_URL}/citations",
    "press_url": f"{SITE_URL}/press",
    "category": "Productivity & Decision Tools / FinTech / Health Tech",
    "pricing": "Free / Open Access",
    "founder": "Elena Vance & Groundwork Research Syndicate",
    "contact_email": "press@gworky.com",
    "twitter": "@gworky",
    "doi": "10.5281/zenodo.22011566",
}


def generate_bookmarklet() -> str:
    """Generates a minified 1-click JavaScript bookmarklet for browser form autofilling."""
    js_code = f"""
    javascript:(function(){{
        var d = {json.dumps(STANDARD_PROFILE)};
        function setVal(selectors, val) {{
            for (var i = 0; i < selectors.length; i++) {{
                var el = document.querySelector(selectors[i]);
                if (el) {{
                    el.value = val;
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    break;
                }}
            }}
        }}
        setVal(['input[name*="name"]', 'input[name*="title"]', 'input[id*="name"]', 'input[placeholder*="Name"]'], d.name);
        setVal(['input[name*="tagline"]', 'input[name*="headline"]', 'input[placeholder*="Tagline"]'], d.tagline);
        setVal(['textarea[name*="desc"]', 'textarea[name*="about"]', 'textarea[id*="desc"]'], d.description);
        setVal(['input[name*="url"]', 'input[name*="website"]', 'input[type="url"]'], d.website);
        setVal(['input[name*="email"]', 'input[type="email"]'], d.contact_email);
        alert('✅ Groundwork Profile Data Autofilled Successfully!');
    }})();
    """.strip().replace("\n", " ")
    return js_code


def main() -> None:
    parser = argparse.ArgumentParser(description="Directory Form Autofill Generator")
    parser.add_argument("--json", action="store_true", help="Output raw JSON profile payload")
    parser.add_argument("--bookmarklet", action="store_true", help="Output 1-click JS bookmarklet")
    args = parser.parse_args()

    out_file = Path("agents/output/directory_profile_payload.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(STANDARD_PROFILE, indent=2), encoding="utf-8")

    if args.bookmarklet:
        bm = generate_bookmarklet()
        print("\n" + "=" * 60)
        print("⚡ 1-CLICK BROWSER AUTOFILL BOOKMARKLET")
        print("=" * 60)
        print(bm)
        print("\n*Drag or copy the above code into your browser bookmarks bar.*")
        return

    print("\n" + "=" * 60)
    print("📋 STANDARDIZED DIRECTORY SUBMISSION PAYLOAD")
    print("=" * 60)
    print(json.dumps(STANDARD_PROFILE, indent=2))
    print(f"\nSaved to: {out_file}")


if __name__ == "__main__":
    main()
