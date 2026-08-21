import unittest

from agents.humanizer import EditorialHumanizer


class TestEditorialHumanizer(unittest.TestCase):
    def test_find_slop_words(self):
        text = "In conclusion, it is crucial to delve into this testament to modern technology. Furthermore, it is a game-changer."
        slop = EditorialHumanizer.find_slop_words(text)
        self.assertIn("crucial", [s.lower() for s in slop])
        self.assertIn("delve", [s.lower() for s in slop])
        self.assertIn("in conclusion", [s.lower() for s in slop])

    def test_sanitize_text(self):
        text = "Furthermore, it is crucial to delve into the data. In conclusion, this approach is a game-changer."
        sanitized = EditorialHumanizer.sanitize_text(text)
        self.assertNotIn("crucial", sanitized.lower())
        self.assertNotIn("delve", sanitized.lower())
        self.assertNotIn("in conclusion", sanitized.lower())
        self.assertIn("essential", sanitized.lower())
        self.assertIn("explore", sanitized.lower())

    def test_calculate_burstiness(self):
        # Monotonous sentences (uniform lengths ~10 words)
        monotonous_text = "The quick brown fox jumps over the lazy dog today. The quick brown fox jumps over the lazy dog again. The quick brown fox jumps over the lazy dog now."
        mono_stats = EditorialHumanizer.calculate_burstiness(monotonous_text)
        self.assertFalse(mono_stats["is_natural"])

        # Natural human writing with varied sentence lengths (3 words, 25 words, 5 words)
        natural_text = "Rates dropped suddenly. When comparing fixed vs adjustable mortgages across federal reserve historical datasets over thirty years, borrowers who locked sub-six-percent rates saved thirty thousand dollars over fifteen years. Timing matters greatly here."
        nat_stats = EditorialHumanizer.calculate_burstiness(natural_text)
        self.assertTrue(nat_stats["burstiness_score"] > 0.4)

    def test_humanize_article_payload(self):
        payload = {
            "title": "Why it is crucial to delve into refinancing",
            "content": "Furthermore, this stands as a testament to smart planning.",
            "excerpt": "In conclusion, explore your options.",
            "takeaway": "It is crucial to calculate closing fees.",
            "expert_comment": "This is a game-changer for homeowners.",
            "faq": [{"question": "Is this crucial?", "answer": "Furthermore, yes."}],
        }
        humanized = EditorialHumanizer.humanize_article_payload(payload)
        self.assertNotIn("crucial", humanized["title"].lower())
        self.assertNotIn("testament", humanized["content"].lower())
        self.assertNotIn("furthermore", humanized["faq"][0]["answer"].lower())


if __name__ == "__main__":
    unittest.main()
