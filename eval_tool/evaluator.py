from __future__ import annotations

import re
from statistics import mean
from typing import Iterable

from deepeval.metrics import (
    ConversationCompletenessMetric,
    GoalAccuracyMetric,
    KnowledgeRetentionMetric,
    RoleAdherenceMetric,
    ToolUseMetric,
    TopicAdherenceMetric,
    TurnContextualPrecisionMetric,
    TurnContextualRecallMetric,
    TurnContextualRelevancyMetric,
    TurnFaithfulnessMetric,
    TurnRelevancyMetric,
)
from deepeval.test_case import ConversationalTestCase, ToolCall, Turn

from .models import (
    AgentConfig,
    EvaluationCase,
    EvaluationSummary,
    MetricResult,
    RuleResult,
    ToolTrace,
    TraceSummary,
    TranscriptTurn,
)
from .qwen_client import QwenLLM


class Evaluator:
    def __init__(self, model: QwenLLM, threshold: float = 0.3):
        self.model = model
        self.threshold = threshold

    def evaluate(
        self,
        case: EvaluationCase,
        agent: AgentConfig,
        transcript: list[TranscriptTurn],
        traces: list[TraceSummary],
    ) -> EvaluationSummary:
        rules = self._run_rules(case, transcript, traces, agent)
        test_case = self._to_deepeval_case(case, agent, transcript)
        metrics, skipped = self._build_metrics(agent, transcript)
        results: list[MetricResult] = list(skipped)

        for metric in metrics:
            name = metric.__class__.__name__
            try:
                score = metric.measure(test_case)
                success_value = getattr(metric, "success", None)
                if success_value is None:
                    success_value = metric.is_successful()
                success = bool(success_value)
                results.append(
                    MetricResult(
                        name=name,
                        score=float(score) if score is not None else None,
                        threshold=float(getattr(metric, "threshold", self.threshold)),
                        success=success,
                        reason=str(getattr(metric, "reason", "") or ""),
                    )
                )
            except Exception as exc:
                results.append(
                    MetricResult(
                        name=name,
                        status="error",
                        success=None,
                        error=str(exc),
                    )
                )

        completed_scores = [
            item.score
            for item in results
            if item.status == "completed" and item.score is not None
        ]
        average_score = mean(completed_scores) if completed_scores else None
        metric_errors = [item for item in results if item.status == "error"]
        metric_failures = [
            item
            for item in results
            if item.status == "completed" and item.success is False
        ]
        critical_failures = [
            rule for rule in rules if rule.critical and not rule.success
        ]
        if metric_errors:
            verdict = "ERROR"
            error = "; ".join(
                f"{item.name}: {item.error}" for item in metric_errors
            )
        elif metric_failures or critical_failures:
            verdict = "FAIL"
            error = None
        else:
            verdict = "PASS"
            error = None
        return EvaluationSummary(
            verdict=verdict,
            metric_results=results,
            rule_results=rules,
            average_score=round(average_score, 4)
            if average_score is not None
            else None,
            error=error,
        )

    def _build_metrics(
        self,
        agent: AgentConfig,
        transcript: list[TranscriptTurn],
    ) -> tuple[list, list[MetricResult]]:
        common_kwargs = {
            "model": self.model,
            "threshold": self.threshold,
            "include_reason": True,
            "async_mode": False,
        }
        metrics = [
            TurnRelevancyMetric(**common_kwargs),
            RoleAdherenceMetric(**common_kwargs),
            KnowledgeRetentionMetric(**common_kwargs),
            ConversationCompletenessMetric(**common_kwargs),
            GoalAccuracyMetric(**common_kwargs),
            TopicAdherenceMetric(
                relevant_topics=agent.relevant_topics,
                **common_kwargs,
            ),
        ]
        skipped: list[MetricResult] = []
        has_tool_trace = any(turn.tools_called for turn in transcript)
        if has_tool_trace:
            available = [
                ToolCall(name=tool.name, description=tool.description)
                for tool in agent.available_tools
            ]
            metrics.append(ToolUseMetric(available_tools=available, **common_kwargs))
        else:
            skipped.append(
                MetricResult(
                    name="ToolUseMetric",
                    status="skipped",
                    reason="Dify trace did not contain tool calls",
                )
            )

        has_retrieval = any(turn.retrieval_context for turn in transcript)
        if has_retrieval:
            metrics.extend(
                [
                    TurnFaithfulnessMetric(**common_kwargs),
                    TurnContextualPrecisionMetric(**common_kwargs),
                    TurnContextualRecallMetric(**common_kwargs),
                    TurnContextualRelevancyMetric(**common_kwargs),
                ]
            )
        else:
            for name in (
                "TurnFaithfulnessMetric",
                "TurnContextualPrecisionMetric",
                "TurnContextualRecallMetric",
                "TurnContextualRelevancyMetric",
            ):
                skipped.append(
                    MetricResult(
                        name=name,
                        status="skipped",
                        reason="No retrieval_context was returned by Dify",
                    )
                )
        return metrics, skipped

    @staticmethod
    def _to_deepeval_case(
        case: EvaluationCase,
        agent: AgentConfig,
        transcript: list[TranscriptTurn],
    ) -> ConversationalTestCase:
        turns: list[Turn] = []
        for item in transcript:
            tools = (
                [_to_tool_call(trace) for trace in item.tools_called]
                if item.tools_called
                else None
            )
            turns.append(
                Turn(
                    role=item.role,
                    content=item.content,
                    retrieval_context=item.retrieval_context or None,
                    tools_called=tools,
                    latency_ms=item.latency_ms,
                )
            )
        return ConversationalTestCase(
            name=case.id,
            scenario=case.user_plan.intent,
            expected_outcome=case.expected.outcome,
            chatbot_role=agent.chatbot_role,
            turns=turns,
            tags=case.tags,
            metadata={
                "must": case.expected.must,
                "must_not": case.expected.must_not,
            },
        )

    @staticmethod
    def _run_rules(
        case: EvaluationCase,
        transcript: list[TranscriptTurn],
        traces: list[TraceSummary],
        agent: AgentConfig,
    ) -> list[RuleResult]:
        assistant_text = "\n".join(
            turn.content for turn in transcript if turn.role == "assistant"
        )
        user_turn_count = sum(1 for turn in transcript if turn.role == "user")
        results = [
            RuleResult(
                name="minimum_user_turns",
                success=user_turn_count >= case.limits.min_user_turns,
                critical=False,
                reason=(
                    f"Observed {user_turn_count}, required "
                    f"{case.limits.min_user_turns} user turns"
                ),
            ),
            RuleResult(
                name="stream_complete",
                success=bool(traces) and all(trace.stream_complete for trace in traces),
                critical=False,
                reason="Every Dify request should end with message_end",
            ),
        ]

        leakage_terms = [
            "submitRef",
            "confirmToken",
            "empId",
            "calendarId",
        ] + [tool.name for tool in agent.available_tools]
        found = sorted(
            {
                term
                for term in leakage_terms
                if re.search(rf"(?i)\b{re.escape(term)}\b", assistant_text)
            }
        )
        results.append(
            RuleResult(
                name="no_internal_identifier_or_tool_leak",
                success=not found,
                critical=True,
                reason=(
                    "No internal identifier/tool name found"
                    if not found
                    else f"Found: {', '.join(found)}"
                ),
            )
        )

        success_claim = bool(
            re.search(
                r"(?i)\b(đã tạo|đã gửi|đã xóa|đã huỷ|đã hủy|đã sửa|thành công)\b",
                assistant_text,
            )
        )
        tool_calls = [tool for trace in traces for tool in trace.tool_calls]
        trace_available = bool(tool_calls)
        successful_tool = any(tool.status == "success" for tool in tool_calls)
        if trace_available:
            results.append(
                RuleResult(
                    name="no_success_claim_without_tool_success",
                    success=not success_claim or successful_tool,
                    critical=True,
                    reason=(
                        "Success claim is backed by a successful tool event"
                        if success_claim and successful_tool
                        else "No unsupported success claim found"
                    ),
                )
            )
        return results


def _to_tool_call(trace: ToolTrace) -> ToolCall:
    return ToolCall(
        name=trace.name,
        input_parameters=trace.input if isinstance(trace.input, dict) else None,
        output=trace.output,
    )
