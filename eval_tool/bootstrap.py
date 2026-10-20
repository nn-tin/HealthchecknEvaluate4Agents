from __future__ import annotations

from pathlib import Path

from .config import AppConfig
from .evaluator import Evaluator
from .io_utils import load_data
from .models import CaseCollection, EvaluationCase
from .qwen_client import QwenClient, QwenLLM
from .runner import EvaluationRunner
from .user_simulator import UserSimulator


def build_qwen(config: AppConfig, *, temperature: float = 0.0) -> QwenLLM:
    return QwenLLM(
        model_name=config.qwen_model,
        base_url=config.qwen_base_url,
        api_key=config.qwen_api_key,
        max_tokens=config.qwen_max_tokens,
        temperature=temperature,
    )


def build_runner(config: AppConfig) -> EvaluationRunner:
    llm = build_qwen(config, temperature=0.0)
    qwen = QwenClient(llm)
    simulator = UserSimulator(
        qwen,
        config.project_root / "prompts" / "simulate_user.md",
    )
    evaluator = Evaluator(llm)
    return EvaluationRunner(config, simulator, evaluator)


def load_cases(path: str | Path) -> list[EvaluationCase]:
    raw = load_data(path)
    if isinstance(raw, list):
        raw = {"cases": raw}
    return CaseCollection.model_validate(raw).cases
