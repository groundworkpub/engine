"""Unit tests for Agent 4 Herald Unified Master Bundles."""

import unittest

from herald import (
    build_5part_thread,
    build_shortform_video_script,
    build_web2_syndication_summary,
    generate_media_master_bundle,
    generate_text_master_bundle,
    publish_bluesky_thread,
    publish_to_bluesky,
    publish_to_buffer,
    publish_to_mastodon,
    publish_to_wordpress,
)


class TestHeraldMasterBundles(unittest.TestCase):
    def setUp(self):
        self.sample_article = {
            "slug": "how-to-refinance-mortgage-2026",
            "title": "How to Refinance Your Mortgage in 2026",
            "excerpt": "A complete guide on mortgage refinancing rates, closing costs, and breakeven timelines.",
            "takeaway": "Refinancing makes financial sense only if you lower your rate by at least 0.75% and plan to stay past the 3-year breakeven point.",
            "pillar": "money",
        }

    def test_build_5part_thread(self):
        thread = build_5part_thread(self.sample_article)
        self.assertEqual(len(thread), 5)
        self.assertTrue(thread[0].startswith("1/5"))
        self.assertTrue(thread[1].startswith("2/5"))
        self.assertTrue(thread[2].startswith("3/5"))
        self.assertTrue(thread[3].startswith("4/5"))
        self.assertTrue(thread[4].startswith("5/5"))
        self.assertIn("https://gworky.com/article/how-to-refinance-mortgage-2026", thread[4])

    def test_build_web2_syndication_summary(self):
        summary = build_web2_syndication_summary(self.sample_article)
        self.assertEqual(summary["title"], "How to Refinance Your Mortgage in 2026")
        self.assertIn("Canonical Research Source", summary["content"])
        self.assertIn("https://gworky.com/article/how-to-refinance-mortgage-2026", summary["content"])
        self.assertEqual(summary["canonical_url"], "https://gworky.com/article/how-to-refinance-mortgage-2026")

    def test_build_shortform_video_script(self):
        script = build_shortform_video_script(self.sample_article)
        self.assertEqual(script["duration_seconds"], 60)
        self.assertEqual(len(script["scenes"]), 4)
        self.assertIn("tiktok", script["captions"])
        self.assertIn("reels", script["captions"])
        self.assertIn("shorts", script["captions"])

    def test_generate_media_master_bundle(self):
        bundle = generate_media_master_bundle(self.sample_article)
        self.assertEqual(bundle["slug"], "how-to-refinance-mortgage-2026")
        self.assertIn("speech_summary", bundle)
        self.assertIn("video_script", bundle)
        self.assertIn("podcast_metadata", bundle)
        self.assertEqual(bundle["podcast_metadata"]["pillar"], "money")

    def test_generate_text_master_bundle(self):
        bundle = generate_text_master_bundle(self.sample_article)
        self.assertEqual(bundle["slug"], "how-to-refinance-mortgage-2026")
        self.assertEqual(len(bundle["thread_posts"]), 5)
        self.assertIn("web2_summary", bundle)

    def test_dry_run_dispatchers(self):
        env = {
            "HERALD_DRY_RUN": "1",
            "BUFFER_ACCESS_TOKEN": "mock_token",
            "BSKY_HANDLE": "mock.bsky.social",
            "BSKY_APP_PASSWORD": "mock_password",
            "WP_SITE_URL": "https://example-wp.com",
            "WP_USERNAME": "admin",
            "WP_APP_PASSWORD": "mock_app_password",
            "MASTODON_ACCESS_TOKEN": "mock_masto_token",
        }
        res_buffer = publish_to_buffer("Title", "Body", env=env)
        self.assertTrue(res_buffer["ok"])

        res_bsky = publish_to_bluesky("Text", "https://gworky.com/article/test", env=env)
        self.assertTrue(res_bsky["ok"])

        res_thread = publish_bluesky_thread(self.sample_article, env=env)
        self.assertTrue(res_thread["ok"])

        res_wp = publish_to_wordpress(self.sample_article, env=env)
        self.assertTrue(res_wp["ok"])

        res_masto = publish_to_mastodon("Text", env=env)
        self.assertTrue(res_masto["ok"])


if __name__ == "__main__":
    unittest.main()
