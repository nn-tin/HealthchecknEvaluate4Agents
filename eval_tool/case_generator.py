from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .io_utils import dump_data
from .models import EvaluationCase, GeneratedCaseBatch
from .qwen_client import QwenClient
from .time_utils import SAIGON_TZ


class CaseGenerator:
    def __init__(
        self,
        qwen: QwenClient,
        prompt_file: str | Path,
        output_dir: str | Path,
    ):
        self.qwen = qwen
        self.prompt_template = Path(prompt_file).read_text(encoding="utf-8")
        self.output_dir = Path(output_dir)

    def generate(
        self,
        *,
        agent_id: str,
        system_prompt: str,
        seed_cases: list[EvaluationCase],
        count: int,
        category: str,
    ) -> list[EvaluationCase]:
        if count < 1 or count > 20:
            raise ValueError("count must be between 1 and 20")
        seed_json = json.dumps(
            [case.model_dump(mode="json") for case in seed_cases],
            ensure_ascii=False,
            indent=2,
        )
        prompt = self.prompt_template.format(
            agent_id=agent_id,
            count=count,
            category=category,
            system_prompt=system_prompt,
            seed_cases=seed_json,
        )
        result = self.qwen.structured(prompt, GeneratedCaseBatch)
        cases = [case for case in result.cases if case.agent == agent_id]
        if not cases:
            raise ValueError("Qwen did not return any case for the selected agent")
        timestamp = datetime.now(SAIGON_TZ).strftime(
            "%Y%m%d_%H%M%S"
        )
        dump_data(
            {"cases": [case.model_dump(mode="json") for case in cases]},
            self.output_dir / f"{agent_id}-{timestamp}.yaml",
        )
        return cases
