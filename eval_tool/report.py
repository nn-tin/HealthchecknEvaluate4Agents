from __future__ import annotations

import re
from pathlib import Path

from .io_utils import dump_json
from .models import CaseRunResult, HealthcheckResult


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "run"


def save_case_run(result: CaseRunResult, directory: str | Path) -> Path:
    target = Path(directory) / f"{_safe(result.run_id)}_{_safe(result.case_id)}.json"
    result.result_file = str(target.resolve())
    dump_json(result.model_dump(mode="json"), target)
    return target


def save_healthcheck(result: HealthcheckResult, directory: str | Path) -> Path:
    target = Path(directory) / f"{_safe(result.run_id)}_health_{_safe(result.agent)}.json"
    result.result_file = str(target.resolve())
    dump_json(result.model_dump(mode="json"), target)
    return target
