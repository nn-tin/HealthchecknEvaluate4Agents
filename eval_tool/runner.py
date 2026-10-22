from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Callable

from .config import AppConfig
from .dify_client import DifyClient
from .evaluator import Evaluator
from .models import (
    CaseRunResult,
    DifyTurnResult,
    EvaluationCase,
    EvaluationSummary,
    HealthcheckResult,
    TranscriptTurn,
    TraceSummary,
)
from .result_sink import ResultSink, build_sink
from .time_utils import SAIGON_TZ
from .user_simulator import UserSimulator


def _now() -> datetime:
    return datetime.now(SAIGON_TZ)


def _run_id(prefix: str) -> str:
    stamp = _now().strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_{prefix}_{uuid.uuid4().hex[:6]}"


class EvaluationRunner:
    def __init__(
        self,
        config: AppConfig,
        simulator: UserSimulator | None = None,
        evaluator: Evaluator | None = None,
        dify_factory: Callable[[str], DifyClient] | None = None,
        result_sink: ResultSink | None = None,
    ):
        self.config = config
        self.simulator = simulator
        self.evaluator = evaluator
        self.dify_factory = dify_factory or self._default_dify_factory
        self.result_sink = result_sink or build_sink(config)

    def _default_dify_factory(self, agent_id: str) -> DifyClient:
        agent = self.config.get_agent(agent_id)
        return DifyClient(
            self.config.dify_api_url,
            self.config.api_key_for(agent_id),
            read_timeout=agent.timeout_seconds,
        )

    def healthcheck(self, agent_id: str, *, batch_id: str | None = None,
                    scheduled_for: str | None = None) -> HealthcheckResult:
        run_id = _run_id("health")
        agent = self.config.get_agent(agent_id)
        started_at, started = _now(), time.perf_counter()
        try:
            client = self.dify_factory(agent_id)
            turn = client.send_message(
                agent.healthcheck_probe,
                inputs=self.config.resolved_inputs(agent_id),
                user=f"healthcheck-{run_id}-{agent_id}",
            )
        except Exception as exc:
            turn = DifyTurnResult(
                error=f"{type(exc).__name__}: healthcheck request failed",
                trace=TraceSummary(request_started_at=started_at.isoformat(),
                                   total_duration_ms=round((time.perf_counter() - started) * 1000, 3)),
            )
        if turn.error or not turn.trace.stream_complete or not turn.answer.strip():
            status = "Error"
        elif turn.trace.total_duration_ms > self.config.slow_threshold_seconds * 1000:
            status = "Slow"
        else:
            status = "Healthy"
        result = HealthcheckResult(
            run_id=run_id,
            agent=agent_id,
            checked_at=_now().isoformat(),
            status=status,
            answer=turn.answer,
            error=turn.error or ("Incomplete stream or empty answer" if status == "Error" else None),
            trace=turn.trace,
            events=turn.raw_events,
            environment=self.config.environment,
            batch_id=batch_id,
            trigger="scheduled" if batch_id else "manual",
            scheduled_for=scheduled_for,
            conversation_id=turn.conversation_id,
            dify_log_url=self.config.dify_log_url(agent_id, turn.conversation_id),
        )
        self.result_sink.save(result)
        return result

    def run_case(self, case: EvaluationCase, *, batch_id: str | None = None,
                 scheduled_for: str | None = None) -> CaseRunResult:
        if self.simulator is None or self.evaluator is None:
            raise RuntimeError("Simulator and evaluator are required to run evaluation cases")
        run_id = _run_id(case.agent)
        started_at = _now()
        started = time.perf_counter()
        agent = self.config.get_agent(case.agent)
        client = None
        transcript: list[TranscriptTurn] = []
        all_events = []
        traces = []
        conversation_id = ""
        user_turns = 0
        next_message = case.user_plan.initial_message
        run_error: str | None = None

        while user_turns < case.limits.max_turns:
            transcript.append(TranscriptTurn(role="user", content=next_message))
            user_turns += 1
            request_at, request_start = _now(), time.perf_counter()
            try:
                if client is None:
                    client = self.dify_factory(case.agent)
                turn = client.send_message(
                    next_message,
                    conversation_id=conversation_id,
                    inputs=self.config.resolved_inputs(case.agent),
                    user=f"evaluation-{run_id}-{case.id}",
                )
            except Exception as exc:
                turn = DifyTurnResult(
                    error=f"{type(exc).__name__}: evaluation request failed",
                    trace=TraceSummary(request_started_at=request_at.isoformat(),
                                       total_duration_ms=round((time.perf_counter()-request_start)*1000, 3)),
                )
            conversation_id = turn.conversation_id or conversation_id
            all_events.extend(turn.raw_events)
            traces.append(turn.trace)
            transcript.append(_assistant_turn(turn))
            if turn.error:
                run_error = turn.error
                break
            if user_turns >= case.limits.max_turns:
                break

            force_continue = user_turns < case.limits.min_user_turns
            try:
                decision = self.simulator.next_message(
                    case,
                    transcript,
                    force_continue=force_continue,
                )
            except Exception as exc:
                run_error = f"Simulator error: {exc}"
                break
            if decision.should_stop and not force_continue:
                break
            next_message = decision.message

        if run_error:
            evaluation = EvaluationSummary(verdict="ERROR", error=run_error)
            status = "error"
        else:
            try:
                evaluation = self.evaluator.evaluate(case, agent, transcript, traces)
            except Exception as exc:
                evaluation = EvaluationSummary(verdict="ERROR", error=f"{type(exc).__name__}: evaluator failed")
            status = "completed" if evaluation.verdict != "ERROR" else "error"

        finished_at = _now()
        result = CaseRunResult(
            run_id=run_id,
            case_id=case.id,
            agent=case.agent,
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat(),
            duration_seconds=round(time.perf_counter() - started, 3),
            status=status,
            environment=self.config.environment,
            transcript=transcript,
            events=all_events,
            traces=traces,
            evaluation=evaluation,
            batch_id=batch_id,
            trigger="scheduled" if batch_id else "manual",
            scheduled_for=scheduled_for,
            conversation_id=conversation_id,
            dify_log_url=self.config.dify_log_url(case.agent, conversation_id),
        )
        self.result_sink.save(result)
        return result

    def run_cases(self, cases: list[EvaluationCase], *, batch_id: str | None = None,
                  scheduled_for: str | None = None) -> list[CaseRunResult]:
        return [self.run_case(case, batch_id=batch_id, scheduled_for=scheduled_for) for case in cases]


def _assistant_turn(result: DifyTurnResult) -> TranscriptTurn:
    content = result.answer or (f"[Dify error] {result.error}" if result.error else "")
    return TranscriptTurn(
        role="assistant",
        content=content,
        retrieval_context=result.retrieval_context,
        tools_called=result.trace.tool_calls,
        latency_ms=result.trace.total_duration_ms,
    )
