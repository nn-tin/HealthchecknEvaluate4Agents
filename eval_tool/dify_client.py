from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Any

import requests
from requests import Response
from requests.exceptions import RequestException

from .models import DifyTurnResult, RawEvent, ToolTrace, TraceSummary
from .time_utils import SAIGON_TZ


def _now_iso() -> str:
    return datetime.now(SAIGON_TZ).isoformat()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _maybe_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def _event_data(event: dict[str, Any]) -> dict[str, Any]:
    data = _as_dict(event.get("data"))
    return {**event, **data}


def _event_id(event: dict[str, Any], fallback: str) -> str:
    data = _event_data(event)
    return str(
        data.get("tool_call_id")
        or data.get("node_execution_id")
        or data.get("node_id")
        or data.get("id")
        or fallback
    )


def _tool_name(event: dict[str, Any]) -> str:
    data = _event_data(event)
    return str(
        data.get("tool")
        or data.get("tool_name")
        or data.get("title")
        or data.get("name")
        or "unknown_tool"
    )


def _duration_from_event_ms(event: dict[str, Any]) -> float | None:
    data = _event_data(event)
    for key in ("elapsed_time_ms", "duration_ms"):
        value = data.get(key)
        if isinstance(value, (int, float)):
            return round(float(value), 3)
    for key in ("elapsed_time", "duration"):
        value = data.get(key)
        if isinstance(value, (int, float)):
            return round(float(value) * 1000, 3)
    return None


def _extract_retrieval_texts(event: dict[str, Any]) -> list[str]:
    data = _event_data(event)
    resources = (
        data.get("retriever_resources")
        or _as_dict(data.get("metadata")).get("retriever_resources")
        or []
    )
    contexts: list[str] = []
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        for key in ("content", "segment", "text"):
            value = resource.get(key)
            if isinstance(value, str) and value.strip():
                contexts.append(value.strip())
                break
    return contexts


class _TraceCollector:
    def __init__(self, started_iso: str, started_monotonic: float):
        self.started_iso = started_iso
        self.started_monotonic = started_monotonic
        self.raw_events: list[RawEvent] = []
        self.tool_calls: list[ToolTrace] = []
        self.active: dict[str, tuple[ToolTrace, float]] = {}
        self.agent_thought_ids: set[str] = set()
        self.first_event_ms: float | None = None
        self.first_answer_token_ms: float | None = None
        self.message_end_ms: float | None = None
        self.stream_complete = False

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self.started_monotonic) * 1000, 3)

    def add(self, event: dict[str, Any]) -> None:
        elapsed = self.elapsed_ms()
        received_at = _now_iso()
        event_name = str(event.get("event", "unknown"))
        if self.first_event_ms is None:
            self.first_event_ms = elapsed
        answer = event.get("answer") or _event_data(event).get("answer")
        if answer and self.first_answer_token_ms is None:
            self.first_answer_token_ms = elapsed
        if event_name == "message_end":
            self.stream_complete = True
            self.message_end_ms = elapsed

        self.raw_events.append(
            RawEvent(
                index=len(self.raw_events),
                event=event_name,
                received_at=received_at,
                elapsed_ms=elapsed,
                payload=event,
            )
        )
        self._collect_tool_event(event, event_name, received_at, elapsed)

    def _collect_tool_event(
        self,
        event: dict[str, Any],
        event_name: str,
        received_at: str,
        elapsed: float,
    ) -> None:
        data = _event_data(event)
        lowered = event_name.lower()
        node_type = str(data.get("node_type") or data.get("type") or "").lower()
        is_tool_node = "tool" in node_type or "tool" in lowered

        if lowered == "agent_thought" and (
            data.get("tool") or data.get("tool_name")
        ):
            trace_id = _event_id(event, f"agent-thought-{len(self.tool_calls)}")
            if trace_id in self.agent_thought_ids:
                return
            self.agent_thought_ids.add(trace_id)
            duration = _duration_from_event_ms(event)
            status: str = "error" if data.get("error") else "success"
            started_at = None
            if duration is not None:
                started_at = (
                    datetime.fromisoformat(received_at)
                    - timedelta(milliseconds=duration)
                ).isoformat()
            self.tool_calls.append(
                ToolTrace(
                    tool_call_id=trace_id,
                    name=_tool_name(event),
                    started_at=started_at,
                    ended_at=received_at,
                    duration_ms=duration,
                    status=status,
                    input=_maybe_json(
                        data.get("tool_input") or data.get("input")
                    ),
                    output=_maybe_json(
                        data.get("observation")
                        or data.get("tool_output")
                        or data.get("output")
                    ),
                    timing_source=(
                        "event_elapsed" if duration is not None else "unavailable"
                    ),
                )
            )
            return

        start_names = {"tool_start", "tool_started", "node_started"}
        finish_names = {
            "tool_end",
            "tool_finished",
            "tool_error",
            "node_finished",
        }
        if lowered in start_names and is_tool_node:
            trace_id = _event_id(event, f"tool-{len(self.tool_calls)}")
            trace = ToolTrace(
                tool_call_id=trace_id,
                name=_tool_name(event),
                started_at=received_at,
                status="running",
                input=_maybe_json(data.get("inputs") or data.get("input")),
            )
            self.tool_calls.append(trace)
            self.active[trace_id] = (trace, elapsed)
            return

        if lowered in finish_names and is_tool_node:
            trace_id = _event_id(event, "")
            active = self.active.pop(trace_id, None)
            if active is None:
                trace = ToolTrace(
                    tool_call_id=trace_id or None,
                    name=_tool_name(event),
                    ended_at=received_at,
                )
                self.tool_calls.append(trace)
                started_elapsed = None
            else:
                trace, started_elapsed = active
                trace.ended_at = received_at
            event_duration = _duration_from_event_ms(event)
            if event_duration is not None:
                trace.duration_ms = event_duration
                trace.timing_source = "event_elapsed"
            elif started_elapsed is not None:
                trace.duration_ms = round(elapsed - started_elapsed, 3)
                trace.timing_source = "event_pair"
            trace.status = (
                "error"
                if lowered in {"tool_error"} or data.get("error")
                else "success"
            )
            trace.output = _maybe_json(
                data.get("outputs") or data.get("output") or data.get("observation")
            )

    def summary(self) -> TraceSummary:
        total = self.elapsed_ms()
        for trace, _ in self.active.values():
            trace.status = "unknown"
        return TraceSummary(
            request_started_at=self.started_iso,
            first_event_ms=self.first_event_ms,
            first_answer_token_ms=self.first_answer_token_ms,
            message_end_ms=self.message_end_ms,
            total_duration_ms=total,
            stream_complete=self.stream_complete,
            tool_calls=self.tool_calls,
        )


class DifyClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        connect_timeout: float = 15.0,
        read_timeout: float = 180.0,
        max_retries: int = 2,
        retry_backoff: float = 1.0,
        session: requests.Session | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_retries = max(1, max_retries)
        self.retry_backoff = retry_backoff
        self.session = session or requests.Session()

    def send_message(
        self,
        message: str,
        *,
        conversation_id: str = "",
        inputs: dict[str, Any] | None = None,
        user: str,
    ) -> DifyTurnResult:
        payload = {
            "inputs": inputs or {},
            "query": message,
            "user": user,
            "conversation_id": conversation_id,
            "response_mode": "streaming",
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_error = "Unknown Dify error"
        for attempt in range(1, self.max_retries + 1):
            started_iso = _now_iso()
            collector = _TraceCollector(started_iso, time.perf_counter())
            try:
                response = self.session.post(
                    f"{self.base_url}/chat-messages",
                    json=payload,
                    headers=headers,
                    timeout=(self.connect_timeout, self.read_timeout),
                    stream=True,
                )
                if response.status_code != 200:
                    last_error = f"Dify HTTP {response.status_code}: {response.text[:500]}"
                    if response.status_code >= 500 and attempt < self.max_retries:
                        time.sleep(self.retry_backoff * attempt)
                        continue
                    return self._error_result(collector, last_error)
                try:
                    result = self._consume_stream(response, collector)
                finally:
                    close = getattr(response, "close", None)
                    if callable(close):
                        close()
                if not result.trace.stream_complete and attempt < self.max_retries:
                    last_error = "Dify stream ended before message_end"
                    time.sleep(self.retry_backoff * attempt)
                    continue
                return result
            except RequestException as exc:
                last_error = str(exc)
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff * attempt)
                    continue
                return self._error_result(collector, last_error)
        return self._error_result(collector, last_error)

    def _consume_stream(
        self, response: Response, collector: _TraceCollector
    ) -> DifyTurnResult:
        answer_parts: list[str] = []
        conversation_id = ""
        message_id: str | None = None
        contexts: list[str] = []
        error: str | None = None

        for raw in response.iter_lines(decode_unicode=True):
            line = (raw or "").strip()
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            collector.add(event)
            data = _event_data(event)
            if chunk := data.get("answer"):
                answer_parts.append(str(chunk))
            conversation_id = str(
                data.get("conversation_id") or conversation_id or ""
            )
            message_id = data.get("message_id") or message_id
            contexts.extend(_extract_retrieval_texts(event))
            if event.get("event") in {"error", "workflow_failed"}:
                error = str(data.get("message") or data.get("error") or "Dify error")
            if event.get("event") == "message_end":
                break

        return DifyTurnResult(
            answer="".join(answer_parts),
            conversation_id=conversation_id,
            message_id=message_id,
            retrieval_context=list(dict.fromkeys(contexts)),
            raw_events=collector.raw_events,
            trace=collector.summary(),
            error=error,
        )

    @staticmethod
    def _error_result(collector: _TraceCollector, error: str) -> DifyTurnResult:
        return DifyTurnResult(
            trace=collector.summary(),
            raw_events=collector.raw_events,
            error=error,
        )
