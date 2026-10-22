"""Set TEST_DATABASE_URL to run integration tests in an isolated temporary schema."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

from eval_tool.backfill import backfill
from eval_tool.config import load_config
from eval_tool.db import connect, migrate
from eval_tool.models import HealthcheckResult, TraceSummary
from eval_tool.postgres_sink import PostgresResultSink
from eval_tool.report import save_healthcheck
from eval_tool.scheduler import EvaluationScheduler, HealthcheckScheduler

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL not set")
class PostgresTests(unittest.TestCase):
    def setUp(self):
        from psycopg import sql
        self.base_url = os.environ["TEST_DATABASE_URL"]
        self.schema = "monitoring_test_" + uuid.uuid4().hex
        with connect(self.base_url) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        from psycopg.conninfo import make_conninfo
        self.temp = tempfile.TemporaryDirectory()
        self.config = replace(load_config(ROOT),
                              database_url=make_conninfo(self.base_url, options=f"-c search_path={self.schema}"),
                              runs_dir=Path(self.temp.name),
                              database_store_transcript=False,
                              database_redact_transcript=True)
        migrate(self.config.database_url, ROOT)

    def tearDown(self):
        from psycopg import sql
        with connect(self.base_url) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
        self.temp.cleanup()

    def test_backfill_is_idempotent_and_failed_timing_stays_null(self):
        migrate(self.config.database_url, ROOT)
        data = json.loads(next((ROOT / "data" / "runs").glob("*_health_*.json")).read_text(encoding="utf-8"))
        result = HealthcheckResult.model_validate(data)
        save_healthcheck(result, self.config.runs_dir)
        self.assertEqual(backfill(self.config, self.config.runs_dir), (1, 0))
        self.assertEqual(backfill(self.config, self.config.runs_dir), (1, 0))
        with connect(self.config.database_url) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM runs").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT count(*) FROM transcript_turns").fetchone()[0], 1)
            content = conn.execute("SELECT content FROM transcript_turns").fetchone()[0]
            self.assertIn("DATABASE_STORE_TRANSCRIPT=false", content)

    def test_raw_transcript_can_be_stored_without_redaction(self):
        config = replace(
            self.config,
            database_store_transcript=True,
            database_redact_transcript=False,
        )
        stamp = "2026-10-21T08:00:00+07:00"
        answer = "Contact raw.user@example.com with token secret-123"
        result = HealthcheckResult(
            run_id="raw-transcript-test",
            agent="leave",
            checked_at=stamp,
            status="Healthy",
            answer=answer,
            trace=TraceSummary(request_started_at=stamp, total_duration_ms=1),
            conversation_id="conv-1",
            dify_log_url="https://aistudio.example.com/app/leave-id/logs?conversation_id=conv-1",
        )
        PostgresResultSink(config).save(result)
        with connect(config.database_url) as conn:
            content = conn.execute(
                "SELECT content FROM transcript_turns WHERE run_id=%s",
                (result.run_id,),
            ).fetchone()[0]
            conversation_id, log_url = conn.execute(
                "SELECT conversation_id,dify_log_url FROM runs WHERE run_id=%s",
                (result.run_id,),
            ).fetchone()
        self.assertEqual(content, answer)
        self.assertEqual(conversation_id, "conv-1")
        self.assertEqual(log_url, result.dify_log_url)

    def test_restart_does_not_run_same_slot_twice_and_errors_do_not_stop_agents(self):
        def run(agent, **kwargs):
            stamp = "2026-10-21T08:00:00+07:00"
            result = HealthcheckResult(run_id=f"fake-{agent}", agent=agent, checked_at=stamp,
                                       status="Error" if agent == "leave" else "Healthy",
                                       trace=TraceSummary(request_started_at=stamp,total_duration_ms=1),
                                       batch_id=kwargs["batch_id"], trigger="scheduled", scheduled_for=kwargs["scheduled_for"])
            PostgresResultSink(self.config).save(result)
        runner = Mock()
        runner.healthcheck.side_effect = run
        scheduler = HealthcheckScheduler(self.config, runner)
        scheduler.configure()
        stamp = datetime.fromisoformat("2026-10-21T08:00:00+07:00")
        self.assertEqual(scheduler.tick(stamp), 1)
        restarted = HealthcheckScheduler(self.config, runner)
        self.assertEqual(restarted.tick(stamp), 0)
        self.assertEqual(runner.healthcheck.call_count, 4)
        with connect(self.config.database_url) as conn:
            self.assertEqual(conn.execute("SELECT completed_agents,failed_agents,status FROM batch_progress").fetchone(), (4, 1, "completed"))

    def test_exception_for_one_agent_marks_partial_and_continues(self):
        runner = Mock()
        runner.healthcheck.side_effect = [OSError("disk"), None, None, None]
        scheduler = HealthcheckScheduler(self.config, runner)
        scheduler.configure()
        scheduler.tick(datetime.fromisoformat("2026-10-21T12:30:00+07:00"))
        self.assertEqual(runner.healthcheck.call_count, 4)
        with connect(self.config.database_url) as conn:
            self.assertEqual(conn.execute("SELECT status FROM scheduled_batches").fetchone()[0], "partial")

    def test_active_batch_lock_prevents_overlap(self):
        runner = Mock()
        scheduler = HealthcheckScheduler(self.config, runner)
        scheduler.configure()
        with connect(self.config.database_url) as conn:
            conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (f"healthcheck:{self.config.environment}",))
            conn.commit()
            try:
                self.assertEqual(scheduler.tick(datetime.fromisoformat("2026-10-21T08:00:00+07:00")), 0)
                runner.healthcheck.assert_not_called()
            finally:
                conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (f"healthcheck:{self.config.environment}",))

    def test_evaluation_scheduler_claims_each_slot_once(self):
        runner = Mock()
        scheduler = EvaluationScheduler(self.config, runner)
        scheduler.configure()
        stamp = datetime.fromisoformat("2026-10-21T17:30:00+07:00")
        self.assertEqual(scheduler.tick(stamp), 1)
        self.assertEqual(EvaluationScheduler(self.config, runner).tick(stamp), 0)
        self.assertEqual(runner.run_case.call_count, 8)
        with connect(self.config.database_url) as conn:
            row = conn.execute(
                "SELECT status,cardinality(expected_cases) FROM scheduled_evaluation_batches"
            ).fetchone()
            self.assertEqual(row, ("completed", 8))
