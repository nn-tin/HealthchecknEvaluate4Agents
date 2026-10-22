from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def load_data(path: str | Path) -> Any:
    """Load YAML when PyYAML is installed, otherwise JSON-compatible YAML."""
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text)
    except ModuleNotFoundError:
        return json.loads(text)


def dump_data(data: Any, path: str | Path) -> Path:
    """Write YAML when available and JSON otherwise."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml  # type: ignore

        content = yaml.safe_dump(
            data,
            allow_unicode=True,
            sort_keys=False,
            width=100,
        )
    except ModuleNotFoundError:
        content = json.dumps(data, ensure_ascii=False, indent=2)
    target.write_text(content, encoding="utf-8")
    return target


def dump_json(data: Any, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=".run-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2, default=str)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target
