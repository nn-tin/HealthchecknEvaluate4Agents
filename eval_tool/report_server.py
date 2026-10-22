"""Local report downloads and browser-triggered manual monitoring jobs."""
from __future__ import annotations

import base64
import hmac
import json
import logging
import os
import re
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlsplit

from .bootstrap import build_runner, load_agent_cases
from .config import AppConfig, load_config
from .db import connect
from .runner import EvaluationRunner
from .time_utils import SAIGON_TZ

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "data" / "runs"


def _latest_run_target(config: AppConfig, kind: str, agent: str, target: str) -> str | None:
    """Resolve a stable card link to the latest stored run or its Dify log."""
    if kind not in {"healthcheck", "evaluation"} or agent not in config.agents:
        return None
    with connect(config.database_url) as conn:
        row = conn.execute(
            "SELECT run_id,dify_log_url FROM runs "
            "WHERE kind=%s AND agent=%s AND environment=%s "
            "ORDER BY started_at DESC,run_id DESC LIMIT 1",
            (kind, agent, config.environment),
        ).fetchone()
    if not row:
        return None
    if target == "run":
        return "http://localhost:3000/d/ta-run-detail/run-detail?var-run_id=" + quote(
            row[0], safe=""
        )
    if target == "log" and row[1]:
        parsed = urlsplit(row[1])
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return row[1]
    return None


def _agent_ids(value: str, config: AppConfig) -> list[str]:
    agents = list(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))
    unknown = set(agents) - config.agents.keys()
    if not agents or unknown:
        raise ValueError(f"Invalid agent list: {value}")
    return agents


def _with_advisory_lock(config: AppConfig, kind: str, task: Callable[[], dict]) -> dict:
    """Keep manual and scheduled runs of the same kind from overlapping."""
    lock_name = f"{kind}:{config.environment}"
    with connect(config.database_url) as conn:
        locked = conn.execute(
            "SELECT pg_try_advisory_lock(hashtext(%s))", (lock_name,)
        ).fetchone()[0]
        conn.commit()
        if not locked:
            raise RuntimeError(f"A {kind} run is already in progress")
        try:
            return task()
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (lock_name,))


def run_healthchecks(config: AppConfig, agents: list[str] | None = None) -> dict:
    agents = agents or _agent_ids(config.healthcheck_agents, config)

    def task() -> dict:
        runner = EvaluationRunner(config)
        results = [runner.healthcheck(agent) for agent in agents]
        return {
            "total": len(results),
            "errors": sum(result.status == "Error" for result in results),
        }

    return _with_advisory_lock(config, "healthcheck", task)


def run_evaluations(config: AppConfig) -> dict:
    agents = _agent_ids(config.evaluation_agents, config)
    cases = load_agent_cases(config, agents)

    def task() -> dict:
        results = build_runner(config).run_cases(cases)
        return {
            "total": len(results),
            "errors": sum(result.status == "error" for result in results),
        }

    return _with_advisory_lock(config, "evaluation", task)


class ManualJobs:
    """Run one long-lived manual job at a time without blocking the HTTP request."""

    def __init__(self, config: AppConfig):
        self.config = config
        self._lock = threading.Lock()
        self._state: dict = {"status": "idle"}

    def start(self, kind: str, agent: str | None = None) -> bool:
        target = {"healthcheck": run_healthchecks, "evaluation": run_evaluations}.get(kind)
        if target is None:
            raise ValueError(f"Unknown manual job: {kind}")
        if agent is not None:
            if kind != "healthcheck" or agent not in self.config.agents:
                raise ValueError(f"Invalid agent for {kind}: {agent}")
            target = lambda config: run_healthchecks(config, [agent])
        with self._lock:
            if self._state["status"] == "running":
                return False
            self._state = {
                "kind": kind,
                "agent": agent,
                "status": "running",
                "started_at": datetime.now(SAIGON_TZ).isoformat(),
            }
            threading.Thread(
                target=self._run,
                args=(kind, target),
                name=f"manual-{kind}",
                daemon=True,
            ).start()
            return True

    def _run(self, kind: str, target: Callable[[AppConfig], dict]) -> None:
        try:
            detail = target(self.config)
            state = {"kind": kind, "status": "completed", **detail}
        except Exception as exc:
            log.exception("Manual %s job failed", kind)
            state = {
                "kind": kind,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
        state["finished_at"] = datetime.now(SAIGON_TZ).isoformat()
        with self._lock:
            self._state = state

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)


class Reports(BaseHTTPRequestHandler):
    jobs: ManualJobs
    config: AppConfig
    allowed_origins: frozenset[str]

    def _origin_allowed(self) -> bool:
        return self.headers.get("Origin", "") in self.allowed_origins

    def _send_json(self, status: int, payload: dict, *, cors: bool = False) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if cors:
            self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Vary", "Origin")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_OPTIONS(self):
        path = urlsplit(self.path).path
        if not path.startswith("/actions/") or not self._origin_allowed():
            self.send_error(403)
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-Grafana-Action, X-Grafana-Device-Id",
        )
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def do_POST(self):
        path = urlsplit(self.path).path
        match = re.fullmatch(
            r"/actions/(healthcheck|evaluation)(?:/([A-Za-z0-9_-]+))?", path
        )
        if not match:
            self.send_error(404)
            return
        if not self._origin_allowed():
            self.send_error(403)
            return
        if int(self.headers.get("Content-Length", "0")) > 4096:
            self.send_error(413)
            return
        try:
            started = self.jobs.start(match[1], match[2])
        except ValueError as exc:
            self._send_json(400, {"accepted": False, "message": str(exc)}, cors=True)
            return
        if started:
            self._send_json(
                202,
                {
                    "accepted": True,
                    "job": match[1],
                    "agent": match[2],
                    "message": "Run started",
                },
                cors=True,
            )
        else:
            self._send_json(
                409,
                {"accepted": False, "message": "Another manual run is already in progress"},
                cors=True,
            )

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/healthz":
            self._send_json(200, {"status": "ok"})
            return
        if path == "/actions/status":
            if not self._origin_allowed():
                self.send_error(403)
                return
            self._send_json(200, self.jobs.status(), cors=True)
            return

        latest = re.fullmatch(
            r"/latest/(healthcheck|evaluation)/([A-Za-z0-9_-]+)/(run|log)", path
        )
        if latest:
            destination = _latest_run_target(
                self.config, latest[1], latest[2], latest[3]
            )
            if not destination:
                self.send_error(404, "No stored run or log found")
                return
            self.send_response(302)
            self.send_header("Location", destination)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            return

        expected = "Basic " + base64.b64encode(
            f"reports:{os.environ['REPORT_PASSWORD']}".encode()
        ).decode()
        if not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Private run reports"')
            self.end_headers()
            return
        match = re.fullmatch(r"/runs/([A-Za-z0-9_.-]+)\.json", path)
        if not match:
            self.send_error(404)
            return
        files = list(RUNS.glob(match[1] + "_*.json"))
        if len(files) != 1 or not files[0].is_file():
            self.send_error(404)
            return
        content = files[0].read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{files[0].name}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def main() -> None:
    if not os.getenv("REPORT_PASSWORD"):
        raise SystemExit("REPORT_PASSWORD is required")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    Reports.config = load_config(ROOT)
    Reports.jobs = ManualJobs(Reports.config)
    origins = os.getenv(
        "CONTROL_ALLOWED_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    )
    Reports.allowed_origins = frozenset(item.strip() for item in origins.split(",") if item.strip())
    ThreadingHTTPServer(("0.0.0.0", 8081), Reports).serve_forever()


if __name__ == "__main__":
    main()
