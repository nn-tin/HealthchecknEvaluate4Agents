from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from eval_tool.config import load_config
from eval_tool.models import HealthcheckResult, TraceSummary
from eval_tool.models import DifyTurnResult
from eval_tool.bootstrap import load_cases
from eval_tool.redaction import Redactor
from eval_tool.result_sink import CompositeResultSink, JsonResultSink
from eval_tool.runner import EvaluationRunner
from eval_tool.scheduler import Schedule

ROOT = Path(__file__).resolve().parents[1]


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.config = replace(load_config(ROOT), database_url="", result_sinks="json")

    def test_slots_use_vietnam_timezone_and_grace(self):
        schedule = Schedule(self.config)
        for stamp, expected in [
            ("2026-10-21T00:59:59+00:00", None),
            ("2026-10-21T01:00:00+00:00", "08:00"),
            ("2026-10-21T01:15:00+00:00", "08:00"),
            ("2026-10-21T01:15:01+00:00", None),
            ("2026-10-21T05:30:00+00:00", "12:30"),
            ("2026-10-21T05:45:01+00:00", None),
        ]:
            with self.subTest(stamp=stamp):
                due = schedule.due(datetime.fromisoformat(stamp))
                self.assertEqual(due[0].strftime("%H:%M") if due else None, expected)

    def test_weekday_filter_and_coalescing(self):
        schedule = Schedule(replace(self.config, healthcheck_weekdays="mon-fri"))
        self.assertEqual(schedule.due(datetime.fromisoformat("2026-10-24T08:00:00+07:00")), [])
        schedule = Schedule(replace(self.config, healthcheck_schedules="08:00,08:05"))
        due = schedule.due(datetime.fromisoformat("2026-10-21T08:10:00+07:00"))
        self.assertEqual([s.strftime("%H:%M") for s in due], ["08:05"])

    def test_evaluation_schedule_uses_independent_slots(self):
        schedule = Schedule(self.config, "evaluation")
        self.assertEqual(schedule.due(datetime.fromisoformat("2026-10-21T17:29:59+07:00")), [])
        due = schedule.due(datetime.fromisoformat("2026-10-21T17:30:00+07:00"))
        self.assertEqual([s.strftime("%H:%M") for s in due], ["17:30"])

    def test_factory_failure_creates_error_json_with_schedule_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            config = replace(self.config, runs_dir=Path(directory))
            runner = EvaluationRunner(config, dify_factory=Mock(side_effect=TimeoutError("secret-url")))
            result = runner.healthcheck("leave", batch_id="batch", scheduled_for="2026-10-21T08:00:00+07:00")
            self.assertEqual(result.status, "Error")
            self.assertFalse(result.trace.stream_complete)
            self.assertEqual(result.trigger, "scheduled")
            self.assertNotIn("secret-url", result.error)
            self.assertEqual(json.loads(Path(result.result_file).read_text())["batch_id"], "batch")

    def test_database_failure_keeps_json(self):
        with tempfile.TemporaryDirectory() as directory:
            secondary = Mock()
            secondary.save.side_effect = RuntimeError("private-connection-string")
            sink = CompositeResultSink(JsonResultSink(directory), secondary)
            result = HealthcheckResult(run_id="unit", agent="leave", checked_at=datetime.now(timezone.utc).isoformat(),
                                       status="Healthy", trace=TraceSummary(request_started_at=datetime.now(timezone.utc).isoformat(), total_duration_ms=1))
            with self.assertLogs("eval_tool.result_sink", level="ERROR") as messages:
                sink.save(result)
            self.assertTrue(Path(result.result_file).exists())
            self.assertNotIn("private-connection-string", " ".join(messages.output))

    def test_incomplete_stream_is_error_even_with_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            config = replace(self.config, runs_dir=Path(directory))
            client = Mock()
            client.send_message.return_value = DifyTurnResult(
                answer="partial response", trace=TraceSummary(
                    request_started_at="2026-10-21T08:00:00+07:00", total_duration_ms=1, stream_complete=False))
            result = EvaluationRunner(config, dify_factory=lambda _: client).healthcheck("leave")
            self.assertEqual(result.status, "Error")
            self.assertIn("Incomplete", result.error)

    def test_evaluation_factory_error_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            config = replace(self.config, runs_dir=Path(directory))
            runner = EvaluationRunner(config, simulator=Mock(), evaluator=Mock(),
                                      dify_factory=Mock(side_effect=TimeoutError("private")))
            result = runner.run_case(load_cases(ROOT / "data" / "seeds" / "leave.yaml")[0])
            self.assertEqual(result.evaluation.verdict, "ERROR")
            self.assertTrue(Path(result.result_file).exists())
            self.assertNotIn("private", result.evaluation.error)

    def test_database_redaction(self):
        redactor = Redactor(self.config)
        text = redactor.text("test@example.com empId=4249 submitRef=abc Bearer abc123 app-123456")
        for secret in ("test@example.com", "4249", "abc123", "app-123456"):
            self.assertNotIn(secret, text)

    def test_invalid_schedule_and_unknown_agent_rejected(self):
        for changes in ({"healthcheck_agents": "unknown"}, {"healthcheck_schedules": "25:00"},
                        {"misfire_grace_minutes": 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Schedule(replace(self.config, **changes))
        for changes in ({"evaluation_agents": "unknown"}, {"evaluation_schedules": "25:00"},
                        {"evaluation_misfire_grace_minutes": 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Schedule(replace(self.config, **changes), "evaluation")
