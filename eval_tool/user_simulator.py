from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import EvaluationCase, SimulatorDecision, TranscriptTurn
from .qwen_client import QwenClient


def _raw_completion_content(value: Any) -> str:
    raw_response = getattr(value, "_raw_response", None)
    if raw_response is None:
        return "<unavailable>"

    choices = getattr(raw_response, "choices", None)
    if choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if content is not None:
            return str(content)

    if hasattr(raw_response, "model_dump_json"):
        return raw_response.model_dump_json()
    return repr(raw_response)


class UserSimulator:
    def __init__(self, qwen: QwenClient, prompt_file: str | Path):
        self.qwen = qwen
        prompt_path = Path(prompt_file).resolve()
        self.prompt_template = prompt_path.read_text(encoding="utf-8")
        self.log_file = prompt_path.parents[1] / "data" / "logs" / "user-simulator.jsonl"

    def _log_decision(
        self,
        *,
        case_id: str,
        force_continue: bool,
        decision: SimulatorDecision,
    ) -> None:
        raw_content = _raw_completion_content(decision)
        record = {
            "timestamp": datetime.now().astimezone().isoformat(),
            "case_id": case_id,
            "force_continue": force_continue,
            "raw_response": raw_content,
            "parsed_decision": decision.model_dump(mode="json"),
        }
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with self.log_file.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

        print(
            f"[user-simulator] raw_response={raw_content}",
            file=sys.stderr,
            flush=True,
        )
        print(
            "[user-simulator] parsed_decision="
            f"{decision.model_dump_json()}",
            file=sys.stderr,
            flush=True,
        )

    def next_message(
        self,
        case: EvaluationCase,
        transcript: list[TranscriptTurn],
        *,
        force_continue: bool = False,
    ) -> SimulatorDecision:
        transcript_json = json.dumps(
            [
                {"role": turn.role, "content": turn.content}
                for turn in transcript
            ],
            ensure_ascii=False,
            indent=2,
        )
        prompt = self.prompt_template.format(
            intent=case.user_plan.intent,
            follow_up_behavior=case.user_plan.follow_up_behavior,
            transcript=transcript_json,
            force_continue=str(force_continue).lower(),
        )
        decision = self.qwen.structured(prompt, SimulatorDecision)
        self._log_decision(
            case_id=case.id,
            force_continue=force_continue,
            decision=decision,
        )
        if force_continue and decision.should_stop:
            raise ValueError(
                "Simulator attempted to stop before min_user_turns was reached"
            )
        if not decision.should_stop and not decision.message.strip():
            raise ValueError("Simulator returned an empty user message")
        return decision
