import unittest

from agents.headroom_compressor import HeadroomCompressor


class TestHeadroomCompressor(unittest.TestCase):
    def test_compress_html_removes_unwanted_tags(self):
        raw_html = """
        <html>
            <head><style>.ad { color: red; }</style></head>
            <body>
                <header><nav>Navigation Link 1 | Link 2</nav></header>
                <article>
                    <h1>Groundwork Financial Analysis</h1>
                    <p>Mortgage refinancing allows homeowners to replace existing loans with more favorable interest terms.</p>
                    <p>Our analysis indicates a 75-basis-point drop saves the average borrower $210 monthly.</p>
                </article>
                <aside><p>Sponsored Content: Sign up for free newsletter!</p></aside>
                <footer><p>Copyright 2026. All rights reserved.</p></footer>
            </body>
        </html>
        """
        compressed = HeadroomCompressor.compress_html(raw_html, target_chars=2000)
        self.assertIn("Mortgage refinancing", compressed)
        self.assertIn("75-basis-point drop", compressed)
        self.assertNotIn("Navigation Link", compressed)
        self.assertNotIn("Copyright 2026", compressed)
        self.assertNotIn(".ad { color: red; }", compressed)

    def test_compress_snippets(self):
        snippets = [
            {"title": "Mortgage Rates Today", "body": "30-year fixed mortgage rates average 6.45% nationally.", "url": "https://example.com/rates"},
            {"title": "Refinance Fees Guide", "body": "Closing costs range between 2% and 5% of loan principal.", "url": "https://example.com/fees"},
        ]
        result = HeadroomCompressor.compress_snippets(snippets, max_chars=500)
        self.assertIn("[1] [Mortgage Rates Today]", result)
        self.assertIn("6.45%", result)
        self.assertIn("[2] [Refinance Fees Guide]", result)

    def test_compression_stats(self):
        orig = "A" * 1000
        comp = "A" * 300
        stats = HeadroomCompressor.compression_stats(orig, comp)
        self.assertEqual(stats["original_chars"], 1000)
        self.assertEqual(stats["compressed_chars"], 300)
        self.assertEqual(stats["compression_ratio_pct"], 70.0)


if __name__ == "__main__":
    unittest.main()
