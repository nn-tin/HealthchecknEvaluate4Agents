from __future__ import annotations

from .config import AppConfig
from .db import connect
from .models import CaseRunResult, HealthcheckResult
from .redaction import Redactor


class PostgresResultSink:
    def __init__(self, config: AppConfig):
        self.config = config
        self.redactor = Redactor(config)

    def save(self, result: CaseRunResult | HealthcheckResult) -> None:
        health = isinstance(result, HealthcheckResult)
        traces = [result.trace] if health else result.traces
        first = traces[0] if traces else None
        conversation_id = result.conversation_id or _conversation_id_from_events(result.events)
        dify_log_url = result.dify_log_url or self.config.dify_log_url(
            result.agent, conversation_id
        )
        row = dict(
            run_id=result.run_id, kind="healthcheck" if health else "evaluation",
            agent=result.agent, environment=result.environment or self.config.environment,
            case_id=None if health else result.case_id,
            batch_id=result.batch_id,
            trigger=result.trigger, scheduled_for=result.scheduled_for,
            started_at=result.trace.request_started_at if health else result.started_at,
            finished_at=result.checked_at if health else result.finished_at,
            status=result.status, verdict=None if health else result.evaluation.verdict,
            score=None if health else result.evaluation.average_score,
            duration_ms=result.trace.total_duration_ms if health else result.duration_seconds * 1000,
            first_event_ms=first.first_event_ms if first else None,
            ttft_ms=first.first_answer_token_ms if first else None,
            stream_complete=all(t.stream_complete for t in traces) if traces else None,
            error=self.redactor.text(result.error if health else result.evaluation.error),
            conversation_id=conversation_id or None,
            dify_log_url=dify_log_url or None,
            # A host path is not an HTTP link. Detail UI uses run_id to open the local JSON.
            result_file=result.result_file.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if result.result_file else None,
        )
        with connect(self.config.database_url) as conn:
            columns = list(row)
            conn.execute(
                f"INSERT INTO runs ({','.join(columns)}) VALUES ({','.join(['%s'] * len(columns))}) "
                "ON CONFLICT (run_id) DO UPDATE SET " + ','.join(f"{c}=EXCLUDED.{c}" for c in columns if c != 'run_id'),
                list(row.values()),
            )
            for table in ("metric_results", "rule_results", "tool_calls", "transcript_turns"):
                conn.execute(f"DELETE FROM {table} WHERE run_id=%s", (result.run_id,))
            for turn_index, trace in enumerate(traces):
                for tool_index, tool in enumerate(trace.tool_calls):
                    conn.execute(
                        "INSERT INTO tool_calls VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (result.run_id, turn_index, tool_index, tool.name, tool.status,
                         tool.started_at, tool.ended_at, tool.duration_ms, tool.timing_source),
                    )
            if health:
                turns = [("assistant", result.answer, result.trace.total_duration_ms)]
            else:
                turns = [(t.role, t.content, t.latency_ms) for t in result.transcript]
                for metric in result.evaluation.metric_results:
                    conn.execute("INSERT INTO metric_results VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                                 (result.run_id, metric.name, metric.score, metric.threshold, metric.success,
                                  metric.status, self.redactor.text(metric.reason), self.redactor.text(metric.error)))
                for rule in result.evaluation.rule_results:
                    conn.execute("INSERT INTO rule_results VALUES (%s,%s,%s,%s,%s)",
                                 (result.run_id, rule.name, rule.success, rule.critical, self.redactor.text(rule.reason)))
            for index, (role, content, latency) in enumerate(turns):
                if not self.config.database_store_transcript:
                    text = "[Content retained in local JSON; DATABASE_STORE_TRANSCRIPT=false]"
                elif self.config.database_redact_transcript:
                    text = self.redactor.text(content)
                else:
                    text = content
                conn.execute("INSERT INTO transcript_turns VALUES (%s,%s,%s,%s,%s)",
                             (result.run_id, index, role, text, latency))


def _conversation_id_from_events(events) -> str:
    """Recover conversation IDs from legacy JSON results during backfill."""
    for event in reversed(events):
        conversation_id = event.payload.get("conversation_id")
        if conversation_id:
            return str(conversation_id)
    return ""
