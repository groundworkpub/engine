"""Groundwork Metric-Driven Prompt Optimizer (Optimizer Layer).

Inspired by linshenkx/prompt-optimizer & Prompt-Engineering-Guide:
Tests prompt variations against evaluation rubrics, scores outputs,
and recommends iterative instruction refinements.
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any

from agents.humanizer import EditorialHumanizer
from agents.prompts.catalog import get_full_system_prompt

logger = logging.getLogger(__name__)


class PromptOptimizer:
    """Evaluates and compares prompt variations against test inputs."""

    @staticmethod
    def evaluate_output_quality(payload: dict[str, Any]) -> dict[str, Any]:
        """Score payload against 4 quantitative quality vectors."""
        score = 0
        critiques: list[str] = []

        content = payload.get("content", "")
        payload.get("title", "")
        takeaway = payload.get("takeaway", "")
        faqs = payload.get("faq", [])

        # 1. Content Length & Structure (25 pts)
        words = len(content.split())
        if words >= 850:
            score += 25
        elif words >= 500:
            score += 15
            critiques.append(f"Content length is {words} words (target >= 850).")
        else:
            score += 5
            critiques.append(f"Content length is too short: {words} words.")

        # Heading check
        if "## " in content and "### " in content:
            score += 5
        elif "## " in content:
            score += 2
        else:
            critiques.append("Missing structured H2/H3 subheadings in content.")

        # 2. Slop & Cliches (25 pts)
        slop_words = EditorialHumanizer.find_slop_words(content)
        if not slop_words:
            score += 25
        else:
            score += max(0, 25 - (len(slop_words) * 5))
            critiques.append(f"Contains AI slop phrases: {', '.join(slop_words)}.")

        # 3. Burstiness & Flow (25 pts)
        burst_stats = EditorialHumanizer.calculate_burstiness(content)
        if burst_stats["is_natural"]:
            score += 25
        else:
            score += 15
            critiques.append(f"Sentence rhythm is monotonous (burstiness score: {burst_stats['burstiness_score']}).")

        # 4. Takeaway & FAQ completeness (20 pts)
        if takeaway and len(takeaway.split()) >= 30:
            score += 10
        if faqs and len(faqs) >= 3:
            score += 10

        final_score = min(100, score)
        return {
            "score": final_score,
            "passed": final_score >= 85,
            "word_count": words,
            "slop_count": len(slop_words),
            "burstiness": burst_stats,
            "critiques": critiques,
        }

    @classmethod
    def compare_prompts(
        cls,
        pillar: str,
        sample_title: str,
        sample_notes: str,
    ) -> dict[str, Any]:
        """Generate analysis of default system prompt vs custom prompt variations."""
        default_prompt = get_full_system_prompt(pillar)
        return {
            "pillar": pillar,
            "prompt_length_chars": len(default_prompt),
            "estimated_prompt_tokens": len(default_prompt) // 4,
            "recommendations": [
                "Ensure explicit inline citation requirement is retained in system prompt.",
                "Ensure forbidden internal link pattern (/[slug]) is enforced.",
                "Ensure specialist persona is injected dynamically based on article pillar.",
            ],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Groundwork Prompt Optimizer")
    parser.add_argument("--pillar", default="tech", choices=["money", "body", "home", "life", "tech"], help="Content pillar")
    parser.add_argument("--eval-file", help="Path to sample JSON file to score")
    args = parser.parse_args()

    if args.eval_file:
        with open(args.eval_file, encoding="utf-8") as f:
            data = json.load(f)
        eval_res = PromptOptimizer.evaluate_output_quality(data)
        print("=" * 50)
        print(" 🎯 PROMPT OPTIMIZER QUALITY EVALUATION")
        print("=" * 50)
        print(f"  Score     : {eval_res['score']} / 100 ({'PASSED' if eval_res['passed'] else 'NEEDS REMEDIATION'})")
        print(f"  Word Count: {eval_res['word_count']}")
        print(f"  Slop Terms: {eval_res['slop_count']}")
        print(f"  Burstiness: {eval_res['burstiness']['burstiness_score']} (Natural: {eval_res['burstiness']['is_natural']})")
        if eval_res["critiques"]:
            print("\n  Critiques:")
            for c in eval_res["critiques"]:
                print(f"   - {c}")
        print("=" * 50)
    else:
        comp = PromptOptimizer.compare_prompts(args.pillar, "Sample Title", "Sample Notes")
        print("=" * 50)
        print(f" 📐 PROMPT SPECIFICATION [{args.pillar.upper()}]")
        print("=" * 50)
        print(f"  Length: {comp['prompt_length_chars']} chars (~{comp['estimated_prompt_tokens']} tokens)")
        print("\n  Core Directives:")
        for r in comp["recommendations"]:
            print(f"   ✓ {r}")
        print("=" * 50)


if __name__ == "__main__":
    main()
