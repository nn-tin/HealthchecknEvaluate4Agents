from __future__ import annotations

import unittest

from pydantic import ValidationError

from eval_tool.models import CaseLimits, EvaluationCase, SimulatorDecision


class ModelTests(unittest.TestCase):
    def test_case_is_always_multi_turn(self):
        case = EvaluationCase.model_validate(
            {
                "id": "x",
                "agent": "leave",
                "name": "x",
                "tags": [],
                "user_plan": {
                    "initial_message": "xin nghỉ",
                    "intent": "tạo đơn nghỉ",
                    "follow_up_behavior": "trả lời lý do rồi xác nhận",
                },
                "expected": {"outcome": "đơn được tạo"},
            }
        )
        self.assertIn("multi-turn", case.tags)
        self.assertGreaterEqual(case.limits.min_user_turns, 2)

    def test_rejects_single_turn_limit(self):
        with self.assertRaises(ValidationError):
            CaseLimits(min_user_turns=1, max_turns=2)

    def test_max_turns_cannot_be_below_minimum(self):
        with self.assertRaises(ValidationError):
            CaseLimits(min_user_turns=4, max_turns=3)

    def test_simulator_requires_message_while_continuing(self):
        with self.assertRaises(ValidationError):
            SimulatorDecision(should_stop=False, message="  ")

    def test_simulator_allows_empty_message_when_stopping(self):
        decision = SimulatorDecision(should_stop=True, reason="done")

        self.assertEqual(decision.message, "")


if __name__ == "__main__":
    unittest.main()
