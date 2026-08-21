import unittest

from agents.prompt_optimizer import PromptOptimizer
from agents.prompts.catalog import get_full_system_prompt, get_pillar_persona
from agents.prompts.guardrails import CRITIC_EVALUATION_GUARDRAILS, SCRIBE_BASE_GUARDRAILS


class TestPromptGuardrails(unittest.TestCase):
    def test_guardrails_structure(self):
        self.assertIn("STRICT ANTI-HALLUCINATION", SCRIBE_BASE_GUARDRAILS)
        self.assertIn("INTERNAL LINKING PROTOCOL", SCRIBE_BASE_GUARDRAILS)
        self.assertIn("STRICT JSON OUTPUT REQUIREMENT", SCRIBE_BASE_GUARDRAILS)
        self.assertIn("The Critic", CRITIC_EVALUATION_GUARDRAILS)

    def test_pillar_personas(self):
        money_persona = get_pillar_persona("money")
        self.assertIn("SENIOR FINANCIAL QUANT", money_persona)

        body_persona = get_pillar_persona("body")
        self.assertIn("CLINICAL RESEARCH", body_persona)

        home_persona = get_pillar_persona("home")
        self.assertIn("HOME SYSTEMS", home_persona)

        full_prompt = get_full_system_prompt("money")
        self.assertIn(SCRIBE_BASE_GUARDRAILS, full_prompt)
        self.assertIn("SENIOR FINANCIAL QUANT", full_prompt)

    def test_prompt_optimizer_scoring(self):
        payload = {
            "title": "How to Refinance a Mortgage in 2026",
            "content": "## Direct Answer\n\nRefinancing replaces an existing loan with new terms. Rates change constantly. Borrowers must evaluate closing costs, points, and break-even timelines before committing to large loan principal modifications.\n\n### Numerical Breakdown\n\nWhen interest rates drop 0.75 percentage points, monthly savings on a $400,000 loan exceed $190. Over five years, that totals more than eleven thousand dollars. Timing is critical for long-term equity growth across all thirty-year amortization structures.",
            "takeaway": "Homeowners should calculate break-even timelines before committing to loan origination fees of 2% to 4%.",
            "faq": [
                {"question": "What is the break even period?", "answer": "The break even period is total closing costs divided by monthly savings."},
                {"question": "Does refinancing hurt credit score?", "answer": "A small temporary dip occurs during hard credit inquiries."},
                {"question": "How long does the process take?", "answer": "Typically thirty to forty-five days from application to closing."}
            ]
        }
        res = PromptOptimizer.evaluate_output_quality(payload)
        self.assertGreaterEqual(res["score"], 60)
        self.assertEqual(res["slop_count"], 0)


if __name__ == "__main__":
    unittest.main()
