from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from eval_tool.bootstrap import load_cases
from eval_tool.config import load_config
from eval_tool.models import (
    DifyTurnResult,
    EvaluationSummary,
    SimulatorDecision,
    TraceSummary,
)
from eval_tool.runner import EvaluationRunner


class FakeDifyClient:
    def __init__(self):
        self.calls = 0

    def send_message(self, message, **kwargs):
        self.calls += 1
        return DifyTurnResult(
            answer=f"assistant-{self.calls}",
            conversation_id="conv-1",
            trace=TraceSummary(
                request_started_at="2026-10-13T10:00:00+07:00",
                first_event_ms=1,
                first_answer_token_ms=2,
                message_end_ms=3,
                total_duration_ms=3,
                stream_complete=True,
            ),
        )


class FakeSimulator:
    def __init__(self):
        self.calls = 0

    def next_message(self, case, transcript, force_continue=False):
        self.calls += 1
        if self.calls == 1:
            return SimulatorDecision(message="Việc gia đình")
        return SimulatorDecision(should_stop=True, reason="done")


class FakeEvaluator:
    def evaluate(self, case, agent, transcript, traces):
        return EvaluationSummary(verdict="PASS", average_score=1.0)


class RunnerTests(unittest.TestCase):
    def test_healthcheck_saves_trace(self):
        root = Path(__file__).resolve().parents[1]
        config = load_config(root)
        fake_client = FakeDifyClient()
        with tempfile.TemporaryDirectory() as temp_dir:
            config = replace(config, runs_dir=Path(temp_dir))
            runner = EvaluationRunner(
                config,
                dify_factory=lambda _: fake_client,
            )
            result = runner.healthcheck("leave")
            self.assertEqual(result.status, "Healthy")
            self.assertEqual(result.trace.first_answer_token_ms, 2)
            self.assertTrue(Path(result.result_file).exists())

    def test_runs_minimum_multi_turn_conversation_and_saves_result(self):
        root = Path(__file__).resolve().parents[1]
        config = load_config(root)
        case = load_cases(root / "data" / "seeds" / "leave.yaml")[0]
        fake_client = FakeDifyClient()
        with tempfile.TemporaryDirectory() as temp_dir:
            config = replace(config, runs_dir=Path(temp_dir))
            runner = EvaluationRunner(
                config,
                FakeSimulator(),
                FakeEvaluator(),
                dify_factory=lambda _: fake_client,
            )
            result = runner.run_case(case)
            user_turns = [turn for turn in result.transcript if turn.role == "user"]
            self.assertEqual(len(user_turns), 2)
            self.assertEqual(result.evaluation.verdict, "PASS")
            self.assertTrue(Path(result.result_file).exists())


if __name__ == "__main__":
    unittest.main()
