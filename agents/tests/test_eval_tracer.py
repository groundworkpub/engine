import tempfile
import unittest
from pathlib import Path

from agents.eval_tracer import OpikTracer


class TestOpikTracer(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.trace_file = Path(self.tmp_dir.name) / "test_traces.jsonl"
        self.tracer = OpikTracer(trace_file=self.trace_file)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_start_and_log_span(self):
        span = self.tracer.start_span(
            name="test_scribe_rewrite",
            agent_name="scribe",
            model_name="google/gemma-4-26b-a4b-it:free",
            provider="openrouter",
            metadata={"pillar": "money", "words": 920},
        )
        self.assertTrue(span.trace_id.startswith("trc_"))
        self.assertTrue(span.span_id.startswith("spn_"))

        # Finish and log span
        span.finish(rubric_score=92)
        self.assertEqual(span.rubric_score, 92)
        self.assertTrue(span.passed_evaluation)
        self.assertTrue(span.latency_ms >= 0.0)

        self.tracer.log_span(span)
        traces = self.tracer.get_recent_traces(limit=10)
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0]["rubric_score"], 92)
        self.assertEqual(traces[0]["agent_name"], "scribe")

    def test_summary_stats(self):
        span1 = self.tracer.start_span("run1", model_name="model_a")
        span1.finish(rubric_score=90)
        self.tracer.log_span(span1)

        span2 = self.tracer.start_span("run2", model_name="model_b")
        span2.finish(rubric_score=80)
        self.tracer.log_span(span2)

        summary = self.tracer.get_summary_stats()
        self.assertEqual(summary["total_spans"], 2)
        self.assertEqual(summary["avg_rubric_score"], 85.0)
        self.assertIn("model_a", summary["models_used"])
        self.assertIn("model_b", summary["models_used"])


if __name__ == "__main__":
    unittest.main()
