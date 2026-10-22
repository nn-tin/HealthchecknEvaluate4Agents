from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import CaseRunResult, HealthcheckResult
from .postgres_sink import PostgresResultSink

log = logging.getLogger(__name__)


def backfill(config, directory: Path, *, only_missing: bool = False) -> tuple[int, int]:
    sink = PostgresResultSink(config)
    existing = set()
    if only_missing:
        from .db import connect
        with connect(config.database_url) as conn:
            existing = {row[0] for row in conn.execute("SELECT run_id FROM runs")}
    saved = errors = 0
    for source in sorted(directory.glob("*.json")):
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
            if data.get("run_id") in existing:
                continue
            model = HealthcheckResult if "checked_at" in data else CaseRunResult
            result = model.model_validate(data)
            sink.save(result)
            saved += 1
        except Exception:
            errors += 1
            log.error("Cannot import %s (invalid result or database unavailable)", source.name)
    return saved, errors
