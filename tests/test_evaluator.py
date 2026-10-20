from __future__ import annotations

import unittest

from deepeval.models import DeepEvalBaseLLM
from pydantic import BaseModel

from eval_tool.bootstrap import load_cases
from eval_tool.config import load_config
from eval_tool.evaluator import Evaluator
from eval_tool.models import TranscriptTurn


class DummyLLM(DeepEvalBaseLLM):
    def load_model(self):
        return None

    def get_model_name(self):
        return "dummy"

    def generate(self, prompt: str, schema: type[BaseModel]):
        raise AssertionError("This test must not call the model")

    async def a_generate(self, prompt: str, schema: type[BaseModel]):
        raise AssertionError("This test must not call the model")


class EvaluatorTests(unittest.TestCase):
    def test_builds_conversational_metrics_and_skips_missing_context(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        config = load_config(root)
        case = load_cases(root / "data" / "seeds" / "leave.yaml")[0]
        transcript = [
            TranscriptTurn(role="user", content="xin nghỉ"),
            TranscriptTurn(role="assistant", content="Bạn nghỉ ngày nào?"),
        ]
        evaluator = Evaluator(DummyLLM())
        metrics, skipped = evaluator._build_metrics(
            config.get_agent("leave"), transcript
        )
        names = {metric.__class__.__name__ for metric in metrics}
        self.assertIn("GoalAccuracyMetric", names)
        self.assertIn("ConversationCompletenessMetric", names)
        self.assertNotIn("ToolUseMetric", names)
        skipped_names = {item.name for item in skipped}
        self.assertIn("ToolUseMetric", skipped_names)
        self.assertIn("TurnFaithfulnessMetric", skipped_names)

        converted = evaluator._to_deepeval_case(
            case, config.get_agent("leave"), transcript
        )
        self.assertEqual(converted.name, case.id)
        self.assertEqual(len(converted.turns), 2)


if __name__ == "__main__":
    unittest.main()
