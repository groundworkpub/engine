"""Tests for the upgraded humanizer: new-era patterns, 5-D scoring, 2-pass gate."""

import unittest

from agents.humanizer import HUMAN_SCORE_THRESHOLD, EditorialHumanizer, HumanScore


class TestNewEraPatterns(unittest.TestCase):
    def test_p45_em_dash_detected(self):
        score = EditorialHumanizer.audit_text(
            "This is a test — with many dashes — scattered around — everywhere. " * 8
        )
        self.assertTrue(any("P45" in f for f in score.findings))

    def test_p46_negation_contrast(self):
        text = (
            "Refinancing is not just about lowering rates but about reshaping your "
            "financial plan. Borrowers save money. Rates matter greatly here."
        )
        score = EditorialHumanizer.audit_text(text)
        self.assertTrue(any("P46" in f for f in score.findings))

    def test_p47_triad(self):
        text = (
            "The new loan product is faster, cheaper, and more reliable than the "
            "previous generation of offerings on the market. Borrowers who compare "
            "at least three written offers typically save money over a fifteen-year term."
        )
        score = EditorialHumanizer.audit_text(text)
        self.assertTrue(any("P47" in f for f in score.findings))

    def test_p48_vague_authority(self):
        text = (
            "Experts say that refinancing works for most homeowners. Studies show "
            "savings can be real. Compare your own numbers before you commit."
        )
        score = EditorialHumanizer.audit_text(text)
        self.assertTrue(any("P48" in f for f in score.findings))

    def test_p49_rhetorical_opener(self):
        score = EditorialHumanizer.audit_text(
            "Have you ever wondered why rates move? They follow bond markets. "
            "That is the short version of a long story."
        )
        self.assertTrue(any("P49" in f for f in score.findings))

    def test_p50_bold_stuffing(self):
        text = (
            "**APR**: the annual cost of credit expressed as a percentage. "
            "**Points**: prepaid interest paid at closing to reduce the rate. "
            "**Escrow**: funds held by a third party for taxes and insurance. "
            "Lenders quote all three together on every official loan estimate form."
        )
        score = EditorialHumanizer.audit_text(text)
        self.assertTrue(any("P50" in f for f in score.findings))


class TestFiveDimensionScore(unittest.TestCase):
    def test_slop_heavy_text_scores_low_lexical(self):
        slop_rich = (
            "In conclusion, it is crucial to delve into this pivotal tapestry. "
            "Furthermore, this game-changer will revolutionize your journey. "
            "Moreover, it is important to note that a plethora of options exists."
        )
        score = EditorialHumanizer.audit_text(slop_rich)
        self.assertLess(score.lexical, 60)
        self.assertLess(score.overall, HUMAN_SCORE_THRESHOLD)

    def test_concrete_human_text_scores_high(self):
        human = (
            "Rates fell 0.4% last quarter. I refinanced my own mortgage in March 2025 "
            "and cut $180 off the monthly payment. Don't wait for perfect timing; "
            "run your break-even math first. A 2-year payback is usually worth it."
        )
        score = EditorialHumanizer.audit_text(human)
        self.assertGreaterEqual(score.specificity, 70)
        self.assertGreaterEqual(score.overall, HUMAN_SCORE_THRESHOLD)

    def test_score_dimensions_present(self):
        score = EditorialHumanizer.audit_text("Short text under twenty words.")
        self.assertIsInstance(score, HumanScore)
        self.assertTrue(0 <= score.overall <= 100)

    def test_gate_threshold_respected(self):
        self.assertEqual(HUMAN_SCORE_THRESHOLD, 70.0)


class TestTwoPassPipeline(unittest.TestCase):
    def test_humanize_with_audit_returns_clean_payload_and_score(self):
        payload = {
            "title": "Why it is crucial to delve into refinancing",
            "content": "Furthermore, this stands as a testament to smart planning.",
            "excerpt": "In conclusion, explore your options.",
        }
        clean, score = EditorialHumanizer.humanize_with_audit(payload)
        self.assertNotIn("crucial", clean["title"].lower())
        self.assertNotIn("_human_score", clean)  # schema stays unpolluted
        self.assertGreater(score.overall, 0)

    def test_pass1_reduces_score_findings(self):
        raw = (
            "It is crucial to delve into mortgage rates. Furthermore, delves into "
            "the data reveal a game-changer. In conclusion, borrowers must navigate "
            "the complexities of lending. This paradigm shift is a testament to "
            "modern technology and its vital role in home buying decisions today."
        )
        before = EditorialHumanizer.audit_text(raw)
        sanitized = EditorialHumanizer.sanitize_text(raw)
        after = EditorialHumanizer.audit_text(sanitized)
        self.assertGreaterEqual(after.lexical, before.lexical)


class TestRegressionExistingBehavior(unittest.TestCase):
    """Original suite semantics must keep passing after the upgrade."""

    def test_find_slop_words_original(self):
        text = "In conclusion, it is crucial to delve into this testament to modern technology. Furthermore, it is a game-changer."
        slop = EditorialHumanizer.find_slop_words(text)
        lowered = [s.lower() for s in slop]
        self.assertIn("crucial", lowered)
        self.assertIn("delve", lowered)
        self.assertIn("in conclusion", lowered)

    def test_sanitize_text_original(self):
        text = "Furthermore, it is crucial to delve into the data. In conclusion, this approach is a game-changer."
        sanitized = EditorialHumanizer.sanitize_text(text)
        self.assertNotIn("crucial", sanitized.lower())
        self.assertNotIn("delve", sanitized.lower())
        self.assertIn("essential", sanitized.lower())

    def test_burstiness_original(self):
        mono = "The quick brown fox jumps over the lazy dog today. The quick brown fox jumps over the lazy dog again. The quick brown fox jumps over the lazy dog now."
        self.assertFalse(EditorialHumanizer.calculate_burstiness(mono)["is_natural"])

    def test_payload_original(self):
        payload = {
            "title": "Why it is crucial to delve into refinancing",
            "content": "Furthermore, this stands as a testament to smart planning.",
            "faq": [{"question": "Is this crucial?", "answer": "Furthermore, yes."}],
        }
        out = EditorialHumanizer.humanize_article_payload(payload)
        self.assertNotIn("crucial", out["title"].lower())
        self.assertNotIn("furthermore", out["faq"][0]["answer"].lower())


if __name__ == "__main__":
    unittest.main()
