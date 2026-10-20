from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .io_utils import load_data
from .models import AgentConfig
from .time_utils import SAIGON_TZ


def _strip_bearer_prefix(value: str) -> str:
    value = value.strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return value


@dataclass(frozen=True)
class AppConfig:
    project_root: Path
    dify_api_url: str
    qwen_base_url: str
    qwen_api_key: str
    qwen_model: str
    qwen_max_tokens: int
    slow_threshold_seconds: float
    agents: dict[str, AgentConfig]
    runs_dir: Path
    generated_dir: Path

    def get_agent(self, agent_id: str) -> AgentConfig:
        try:
            return self.agents[agent_id]
        except KeyError as exc:
            raise KeyError(f"Unknown agent: {agent_id}") from exc

    def api_key_for(self, agent_id: str) -> str:
        agent = self.get_agent(agent_id)
        value = os.getenv(agent.api_key_env, "").strip()
        if not value:
            raise ValueError(
                f"Missing Dify API key in environment variable {agent.api_key_env}"
            )
        return value

    def resolved_inputs(self, agent_id: str) -> dict[str, Any]:
        values = dict(self.get_agent(agent_id).default_inputs)
        today = datetime.now(SAIGON_TZ).strftime("%Y-%m-%d")
        return {
            key: (today if value == "__TODAY__" else value)
            for key, value in values.items()
        }


def _root_from(path: str | Path | None) -> Path:
    if path is not None:
        return Path(path).resolve()
    return Path(__file__).resolve().parents[1]


def load_config(
    project_root: str | Path | None = None,
    agents_file: str | Path | None = None,
) -> AppConfig:
    root = _root_from(project_root)
    load_dotenv(root / ".env")
    source = Path(agents_file) if agents_file else root / "configs" / "agents.yaml"
    raw = load_data(source)
    agents: dict[str, AgentConfig] = {}
    for agent_id, item in raw.get("agents", {}).items():
        agents[agent_id] = AgentConfig(agent_id=agent_id, **item)
    if not agents:
        raise ValueError(f"No agents configured in {source}")

    return AppConfig(
        project_root=root,
        dify_api_url=os.getenv("DIFY_BASE_URL", "http://localhost:8000").rstrip("/"),
        qwen_base_url=os.getenv("QWEN_BASE_URL", "").rstrip("/"),
        qwen_api_key=_strip_bearer_prefix(os.getenv("QWEN_BEARER", "")),
        qwen_model=os.getenv("QWEN_MODEL", "qwen36"),
        qwen_max_tokens=int(os.getenv("QWEN_MAX_TOKENS", "8192")),
        slow_threshold_seconds=float(os.getenv("HEALTHCHECK_SLOW_SECONDS", "10")),
        agents=agents,
        runs_dir=root / "data" / "runs",
        generated_dir=root / "data" / "generated",
    )
