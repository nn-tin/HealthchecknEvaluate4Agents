from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

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
    database_url: str = ""
    result_sinks: str = "json,postgres"
    environment: str = "staging"
    database_store_transcript: bool = False
    database_redact_transcript: bool = True
    healthcheck_schedules: str = "08:00,12:30"
    healthcheck_timezone: str = "Asia/Ho_Chi_Minh"
    healthcheck_agents: str = "leave,late,helpdesk,bookroom"
    healthcheck_weekdays: str = "mon-sun"
    misfire_grace_minutes: int = 15
    evaluation_schedules: str = ""
    evaluation_timezone: str = "Asia/Ho_Chi_Minh"
    evaluation_agents: str = "leave,late,helpdesk,bookroom"
    evaluation_weekdays: str = "mon-sun"
    evaluation_misfire_grace_minutes: int = 15

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

    def dify_log_url(self, agent_id: str, conversation_id: str) -> str:
        """Build the AI Studio log URL for one Dify conversation."""
        if not conversation_id:
            return ""
        agent = self.get_agent(agent_id)
        link_env = agent.app_link_env or agent.api_key_env.removesuffix("_API_KEY") + "_LINK"
        app_link = os.getenv(link_env, "").strip().rstrip("/")
        if not app_link:
            return ""
        return f"{app_link}/logs?{urlencode({'conversation_id': conversation_id})}"

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

    healthcheck_timezone = os.getenv("HEALTHCHECK_TIMEZONE", "Asia/Ho_Chi_Minh")
    healthcheck_weekdays = os.getenv("HEALTHCHECK_WEEKDAYS", "mon-sun")
    healthcheck_grace = os.getenv("HEALTHCHECK_MISFIRE_GRACE_MINUTES", "15")
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
        database_url=os.getenv("DATABASE_URL", ""),
        result_sinks=os.getenv("RESULT_SINK", "json,postgres"),
        environment=os.getenv("ENVIRONMENT", "staging"),
        database_store_transcript=os.getenv("DATABASE_STORE_TRANSCRIPT", "false").lower() == "true",
        database_redact_transcript=os.getenv("DATABASE_REDACT_TRANSCRIPT", "true").lower() == "true",
        healthcheck_schedules=os.getenv("HEALTHCHECK_SCHEDULES", "08:00,12:30"),
        healthcheck_timezone=healthcheck_timezone,
        healthcheck_agents=os.getenv("HEALTHCHECK_AGENTS", "leave,late,helpdesk,bookroom"),
        healthcheck_weekdays=healthcheck_weekdays,
        misfire_grace_minutes=int(healthcheck_grace),
        evaluation_schedules=os.getenv("EVALUATION_SCHEDULES", ""),
        evaluation_timezone=os.getenv("EVALUATION_TIMEZONE", healthcheck_timezone),
        evaluation_agents=os.getenv("EVALUATION_AGENTS", "leave,late,helpdesk,bookroom"),
        evaluation_weekdays=os.getenv("EVALUATION_WEEKDAYS", healthcheck_weekdays),
        evaluation_misfire_grace_minutes=int(os.getenv("EVALUATION_MISFIRE_GRACE_MINUTES", healthcheck_grace)),
    )
