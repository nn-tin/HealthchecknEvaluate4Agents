from __future__ import annotations

from pathlib import Path


def connect(database_url: str):
    if not database_url:
        raise ValueError("DATABASE_URL is required; see observability/.env.example")
    import psycopg
    return psycopg.connect(database_url, connect_timeout=5)


def migrate(database_url: str, project_root: Path) -> None:
    with connect(database_url) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(72419001)")
        conn.execute((project_root / "migrations" / "001_initial.sql").read_text(encoding="utf-8"))
