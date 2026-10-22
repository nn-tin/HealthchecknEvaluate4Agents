from __future__ import annotations

import logging
from typing import Protocol

from .config import AppConfig
from .models import CaseRunResult, HealthcheckResult
from .report import save_case_run, save_healthcheck

Result = CaseRunResult | HealthcheckResult
log = logging.getLogger(__name__)


class ResultSink(Protocol):
    def save(self, result: Result) -> None: ...


class JsonResultSink:
    def __init__(self, directory):
        self.directory = directory

    def save(self, result: Result) -> None:
        if isinstance(result, HealthcheckResult):
            save_healthcheck(result, self.directory)
        else:
            save_case_run(result, self.directory)


class CompositeResultSink:
    """JSON is durable first. Database failures can be repaired by backfill."""

    def __init__(self, primary: ResultSink, secondary: ResultSink | None = None):
        self.primary, self.secondary = primary, secondary

    def save(self, result: Result) -> None:
        self.primary.save(result)
        if self.secondary:
            try:
                self.secondary.save(result)
            except Exception:
                # Driver exceptions may contain the DSN or business payload.
                log.error("Database write failed for run %s; JSON retained, run backfill to retry", result.run_id)


def build_sink(config: AppConfig) -> ResultSink:
    names = {s.strip() for s in config.result_sinks.split(",") if s.strip()}
    if names - {"json", "postgres"} or "json" not in names:
        raise ValueError("RESULT_SINK must be json or json,postgres (JSON backup is required)")
    secondary = None
    if "postgres" in names and config.database_url:
        from .postgres_sink import PostgresResultSink
        secondary = PostgresResultSink(config)
    return CompositeResultSink(JsonResultSink(config.runs_dir), secondary)
