#!/usr/bin/env python3
"""Unit tests for scripts/seo_mcp_server.py and SEO observer tools."""

import sys
import unittest
from unittest.mock import MagicMock, patch

from scripts.seo_mcp_server import (
    TOOLS_REGISTRY,
    tool_check_cannibalization,
    tool_extract_paa,
    tool_score_aeo,
)


class TestSEOMCPServer(unittest.TestCase):
    def test_tools_registry_completeness(self) -> None:
        expected_tools = {
            "seo_inspect_url",
            "seo_submit_urls_for_indexing",
            "seo_get_decaying_articles",
            "seo_check_cannibalization",
            "seo_score_aeo",
            "seo_extract_paa",
            "seo_run_audit_summary",
        }
        self.assertEqual(set(TOOLS_REGISTRY.keys()), expected_tools)
        for _name, meta in TOOLS_REGISTRY.items():
            self.assertIn("description", meta)
            self.assertIn("parameters", meta)
            self.assertTrue(callable(meta["handler"]))

    def test_tool_score_aeo(self) -> None:
        target = "agents.seo_observer.supa_select" if "agents.seo_observer" in sys.modules else "seo_observer.supa_select"
        with patch(target) as mock_select:
            mock_select.return_value = [{
                "title": "Solar Tax Credit Guide",
                "content": "## Direct Answer\nThe federal solar tax credit allows 30% deduction.\n\n### Key Benchmarks\nData shows $7,500 average savings.\n\n### FAQ\nIs it active?",
                "schema_data": {"@type": "FAQPage"},
            }]
            res = tool_score_aeo("solar-tax-credit")
            self.assertEqual(res["status"], "success")
            self.assertGreaterEqual(res["aeo_score"], 70)
            self.assertIn("grade", res)

    def test_tool_check_cannibalization(self) -> None:
        target = "agents.seo_observer.supa_select" if "agents.seo_observer" in sys.modules else "seo_observer.supa_select"
        with patch(target) as mock_select:
            mock_select.return_value = [
                {"slug": "solar-credit-1", "title": "Home Solar Tax Credit 2026"},
                {"slug": "solar-credit-2", "title": "Solar Tax Credit Savings Guide"},
            ]
            res = tool_check_cannibalization()
            self.assertEqual(res["status"], "success")
            self.assertGreaterEqual(res["total_conflicts"], 1)

    def test_tool_extract_paa(self) -> None:
        target = "agents.seo_observer.httpx.Client" if "agents.seo_observer" in sys.modules else "seo_observer.httpx.Client"
        with patch(target) as mock_client_cls:
            mock_client = MagicMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = ["solar", ["How much is solar tax credit?", "Does solar qualify?"]]
            mock_client.__enter__.return_value.get.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            res = tool_extract_paa("solar tax credit")
            self.assertEqual(res["status"], "success")
            self.assertIn("How much is solar tax credit?", res["paa_questions"])


if __name__ == "__main__":
    unittest.main()
