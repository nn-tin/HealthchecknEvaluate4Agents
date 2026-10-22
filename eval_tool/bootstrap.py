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


def load_agent_cases(config: AppConfig, agent_ids: list[str]) -> list[EvaluationCase]:
    """Load every configured seed case for the requested agents."""
    cases: list[EvaluationCase] = []
    for agent_id in agent_ids:
        source = config.project_root / "data" / "seeds" / f"{agent_id}.yaml"
        if not source.is_file():
            raise ValueError(f"Missing evaluation cases for {agent_id}: {source}")
        agent_cases = [case for case in load_cases(source) if case.agent == agent_id]
        if not agent_cases:
            raise ValueError(f"No evaluation cases for agent {agent_id} in {source}")
        cases.extend(agent_cases)
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Evaluation case IDs must be unique")
    return cases
