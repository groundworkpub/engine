#!/usr/bin/env python3
"""Unit tests for agents/egress_public_pool.py."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from agents.egress_public_pool import (
    ProxyDatabase,
    PublicProxyPool,
)


class TestEgressPublicPool(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_proxy_pool.db"
        self.db = ProxyDatabase(db_path=self.db_path)
        self.pool = PublicProxyPool(db=self.db)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_database_init_and_add_raw(self) -> None:
        raw_list = [
            "http://1.2.3.4:8080",
            "1.2.3.5:3128",
            "socks5://1.2.3.6:1080",
            "invalid_line",
        ]
        added = self.db.add_raw_proxies(raw_list)
        self.assertEqual(added, 3)
        stats = self.db.get_stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["healthy"], 0)

    def test_proxy_validation_result_update(self) -> None:
        url = "http://1.2.3.4:8080"
        self.db.add_raw_proxies([url])

        # Mark healthy
        self.db.update_proxy_result(url, is_healthy=True, latency_ms=120.5, anonymity="elite")
        healthy = self.db.get_healthy_proxies()
        self.assertEqual(len(healthy), 1)
        self.assertEqual(healthy[0]["latency_ms"], 120.5)
        self.assertEqual(healthy[0]["anonymity"], "elite")

        # Best proxy retrieval
        best = self.pool.get_best_proxy()
        self.assertEqual(best, url)

        # Circuit breaker test: mark fail 2x
        self.db.update_proxy_result(url, is_healthy=False, latency_ms=9999.0)
        self.db.update_proxy_result(url, is_healthy=False, latency_ms=9999.0)
        stats = self.db.get_stats()
        self.assertEqual(stats["healthy"], 0)
        self.assertIsNone(self.pool.get_best_proxy())

    @patch("httpx.AsyncClient.get")
    def test_harvest_feeds(self, mock_get: AsyncMock) -> None:
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = "10.0.0.1:8080\n10.0.0.2:8080\n# comment"
        mock_get.return_value = mock_response

        added = asyncio.run(self.pool.harvest_feeds())
        self.assertGreaterEqual(added, 2)
        stats = self.db.get_stats()
        self.assertGreaterEqual(stats["total"], 2)


if __name__ == "__main__":
    unittest.main()
