from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToolDefinition(BaseModel):
    name: str
    description: str = ""


class AgentConfig(BaseModel):
    agent_id: str
    display_name: str
    api_key_env: str
    prompt_file: str
    chatbot_role: str
    relevant_topics: list[str] = Field(default_factory=list)
    available_tools: list[ToolDefinition] = Field(default_factory=list)
    default_inputs: dict[str, Any] = Field(default_factory=dict)
    healthcheck_probe: str
    timeout_seconds: float = 180.0


class UserPlan(BaseModel):
    initial_message: str = Field(min_length=1)
    intent: str = Field(min_length=1)
    follow_up_behavior: str = Field(min_length=1)


class ExpectedBehavior(BaseModel):
    outcome: str = Field(min_length=1)
    must: list[str] = Field(default_factory=list)
    must_not: list[str] = Field(default_factory=list)


class CaseLimits(BaseModel):
    min_user_turns: int = Field(default=2, ge=2)
    max_turns: int = Field(default=6, ge=2, le=20)
    timeout_seconds: float = Field(default=180.0, gt=0)

    @model_validator(mode="after")
    def validate_turn_limits(self) -> "CaseLimits":
        if self.max_turns < self.min_user_turns:
            raise ValueError("max_turns must be >= min_user_turns")
        return self


class EvaluationCase(BaseModel):
    id: str = Field(min_length=1)
    agent: Literal["leave", "late", "helpdesk", "bookroom"]
    name: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=lambda: ["multi-turn"])
    user_plan: UserPlan
    expected: ExpectedBehavior
    limits: CaseLimits = Field(default_factory=CaseLimits)

    @model_validator(mode="after")
    def ensure_multi_turn_tag(self) -> "EvaluationCase":
        if "multi-turn" not in self.tags:
            self.tags.insert(0, "multi-turn")
        return self


class CaseCollection(BaseModel):
    cases: list[EvaluationCase]


class RawEvent(BaseModel):
    index: int
    event: str
    received_at: str
    elapsed_ms: float
    payload: dict[str, Any]


class ToolTrace(BaseModel):
    tool_call_id: str | None = None
    name: str
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: float | None = None
    status: Literal["running", "success", "error", "unknown"] = "unknown"
    input: Any = None
    output: Any = None
    timing_source: Literal["event_pair", "event_elapsed", "unavailable"] = (
        "unavailable"
    )


class TraceSummary(BaseModel):
    request_started_at: str
    first_event_ms: float | None = None
    first_answer_token_ms: float | None = None
    message_end_ms: float | None = None
    total_duration_ms: float
    stream_complete: bool = False
    tool_calls: list[ToolTrace] = Field(default_factory=list)


class DifyTurnResult(BaseModel):
    answer: str = ""
    conversation_id: str = ""
    message_id: str | None = None
    retrieval_context: list[str] = Field(default_factory=list)
    raw_events: list[RawEvent] = Field(default_factory=list)
    trace: TraceSummary
    error: str | None = None


class TranscriptTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    retrieval_context: list[str] = Field(default_factory=list)
    tools_called: list[ToolTrace] = Field(default_factory=list)
    latency_ms: float | None = None


class SimulatorDecision(BaseModel):
    message: str = ""
    should_stop: bool = False
    reason: str = ""

    @model_validator(mode="after")
    def require_message_while_continuing(self) -> "SimulatorDecision":
        if not self.should_stop and not self.message.strip():
            raise ValueError("message is required when should_stop is false")
        return self


class MetricResult(BaseModel):
    name: str
    score: float | None = None
    threshold: float | None = None
    success: bool | None = None
    reason: str = ""
    status: Literal["completed", "skipped", "error"] = "completed"
    error: str | None = None


class RuleResult(BaseModel):
    name: str
    success: bool
    critical: bool = False
    reason: str = ""


class EvaluationSummary(BaseModel):
    verdict: Literal["PASS", "FAIL", "ERROR"]
    metric_results: list[MetricResult] = Field(default_factory=list)
    rule_results: list[RuleResult] = Field(default_factory=list)
    average_score: float | None = None
    error: str | None = None


class CaseRunResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    case_id: str
    agent: str
    started_at: str
    finished_at: str
    duration_seconds: float
    status: Literal["completed", "error"]
    transcript: list[TranscriptTurn]
    events: list[RawEvent]
    traces: list[TraceSummary]
    evaluation: EvaluationSummary
    result_file: str | None = None


class HealthcheckResult(BaseModel):
    run_id: str
    agent: str
    checked_at: str
    status: Literal["Healthy", "Slow", "Error"]
    answer: str = ""
    error: str | None = None
    trace: TraceSummary
    events: list[RawEvent] = Field(default_factory=list)
    result_file: str | None = None


class GeneratedCaseBatch(BaseModel):
    cases: list[EvaluationCase]
