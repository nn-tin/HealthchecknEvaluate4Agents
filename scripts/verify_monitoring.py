"""Smoke-check a local Compose stack without calling Dify or Qwen."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval_tool.config import load_config
from eval_tool.db import connect


def main():
    credentials = dotenv_values(ROOT / "observability" / ".env")
    session = requests.Session()
    session.trust_env = False
    session.auth = ("admin", credentials["GRAFANA_ADMIN_PASSWORD"])
    base = "http://127.0.0.1:3000"
    response = session.get(base + "/api/health", timeout=15)
    response.raise_for_status()
    assert response.json()["database"] == "ok"
    health = session.get(base + "/api/datasources/uid/ticketagents-postgres/health", timeout=15)
    health.raise_for_status()
    assert health.json()["status"] == "OK", health.json()
    config = load_config(ROOT)
    with connect(config.database_url) as conn:
        run_id = conn.execute("SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()[0]
        count = conn.execute("SELECT count(*) FROM runs").fetchone()[0]
    total = 0
    for path in sorted((ROOT / "observability" / "grafana" / "dashboards").glob("*.json")):
        dashboard = json.loads(path.read_text(encoding="utf-8"))
        response = session.get(base + "/api/dashboards/uid/" + dashboard["uid"], timeout=15)
        response.raise_for_status()
        for panel in dashboard["panels"]:
            if not panel.get("targets"):
                continue  # Layout panels (such as row headers) have no SQL query.
            target = dict(panel["targets"][0])
            target["rawSql"] = (target["rawSql"].replace("${environment:sqlstring}", "'staging'")
                                 .replace("${agent:sqlstring}", "'leave','late','helpdesk','bookroom'")
                                 .replace("${run_id:sqlstring}", "'" + run_id.replace("'", "''") + "'"))
            response = session.post(base + "/api/ds/query", json={"from": "now-30d", "to": "now", "queries": [target]}, timeout=20)
            response.raise_for_status()
            result = response.json()["results"]["A"]
            assert not result.get("error"), (path.name, panel["title"], result.get("error"))
            total += 1
        print(f"Dashboard {dashboard['uid']}: all SQL queries passed")
    alerts = session.get(base + "/api/prometheus/grafana/api/v1/rules", timeout=20)
    alerts.raise_for_status()
    rules = [r for g in alerts.json()["data"]["groups"] for r in g["rules"] if r.get("name", "")]
    assert len(rules) >= 5, "Expected five provisioned alert rules"
    for rule in rules:
        assert rule.get("health") != "error", (rule.get("name"), rule.get("lastError"))
    # Confirm Grafana's actual credentials cannot write to application tables.
    import psycopg
    from psycopg.conninfo import make_conninfo
    reader_dsn = make_conninfo(config.database_url, user="grafanareader", password=credentials["GRAFANA_READER_PASSWORD"])
    with connect(reader_dsn) as conn:
        conn.execute("SELECT count(*) FROM runs")
        try:
            conn.execute("UPDATE runs SET status=status WHERE false")
        except psycopg.errors.InsufficientPrivilege:
            conn.rollback()
        else:
            raise AssertionError("Grafana reader unexpectedly has write permission")
    reports = "http://127.0.0.1:8081/runs/" + run_id + ".json"
    assert requests.get(reports, timeout=10).status_code == 401
    download = requests.get(reports, auth=("reports", credentials["REPORT_PASSWORD"]), timeout=10)
    download.raise_for_status()
    assert download.json()["run_id"] == run_id
    print(f"Verified {total} panels, {len(rules)} alert rules, read-only DB role, authenticated JSON download; runs={count}")


if __name__ == "__main__":
    main()
