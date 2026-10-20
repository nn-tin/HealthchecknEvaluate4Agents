from __future__ import annotations

import json
import unittest

from eval_tool.dify_client import DifyClient


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, events):
        self.events = events

    def iter_lines(self, decode_unicode=True):
        for event in self.events:
            yield "data: " + json.dumps(event, ensure_ascii=False)


class FakeSession:
    def __init__(self, events):
        self.events = events
        self.last_headers = None

    def post(self, url, json, headers, timeout, stream):
        self.last_headers = headers
        return FakeResponse(self.events)


class DifyClientTests(unittest.TestCase):
    def test_collects_stream_timing_and_tool_pair(self):
        events = [
            {
                "event": "node_started",
                "data": {
                    "node_execution_id": "tool-1",
                    "node_type": "tool",
                    "title": "listEvents",
                    "inputs": {"date": "2026-10-13"},
                },
            },
            {
                "event": "node_finished",
                "data": {
                    "node_execution_id": "tool-1",
                    "node_type": "tool",
                    "title": "listEvents",
                    "outputs": {"count": 1},
                },
            },
            {
                "event": "message",
                "answer": "Bạn có một lịch.",
                "conversation_id": "conv-1",
                "message_id": "msg-1",
            },
            {"event": "message_end", "conversation_id": "conv-1"},
        ]
        session = FakeSession(events)
        client = DifyClient("http://dify/v1", "secret", session=session)
        result = client.send_message("xem lịch", user="test")

        self.assertEqual(result.answer, "Bạn có một lịch.")
        self.assertTrue(result.trace.stream_complete)
        self.assertIsNotNone(result.trace.first_answer_token_ms)
        self.assertEqual(len(result.trace.tool_calls), 1)
        tool = result.trace.tool_calls[0]
        self.assertEqual(tool.name, "listEvents")
        self.assertEqual(tool.status, "success")
        self.assertEqual(tool.timing_source, "event_pair")
        self.assertIsNotNone(tool.duration_ms)
        self.assertEqual(tool.input, {"date": "2026-10-13"})
        self.assertEqual(tool.output, {"count": 1})

    def test_agent_thought_without_timing_is_unavailable(self):
        events = [
            {
                "event": "agent_thought",
                "id": "thought-1",
                "tool": "get_calendar",
                "tool_input": "{\"phrase\": \"mai\"}",
                "observation": "{\"date\": \"2026-10-14\"}",
            },
            {"event": "message", "answer": "Đã tra ngày."},
            {"event": "message_end"},
        ]
        client = DifyClient(
            "http://dify/v1", "secret", session=FakeSession(events)
        )
        result = client.send_message("ngày mai", user="test")
        tool = result.trace.tool_calls[0]
        self.assertIsNone(tool.duration_ms)
        self.assertEqual(tool.timing_source, "unavailable")
        self.assertEqual(tool.input, {"phrase": "mai"})


if __name__ == "__main__":
    unittest.main()
